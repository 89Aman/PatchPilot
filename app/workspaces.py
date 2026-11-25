"""Multi-tenant GitHub Workspace & RBAC authorization layer.

Separates web user identity (Clerk) from the billable GitHub Workspace
(Personal account or GitHub Organization installation keyed by immutable
`github_account_id`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import VaultConfig, Workspace, WorkspaceMember
from app.github_auth import (
    fetch_user_app_installations,
    fetch_user_org_memberships,
    fetch_user_profile,
)

logger = logging.getLogger("patchpilot.workspaces")

ADMIN_ROLES = {"owner", "admin"}


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def workspace_id_for_account(github_account_id: int) -> str:
    """Deterministic URL-safe tenant ID derived from immutable GitHub numeric account ID."""
    return f"ws_{int(github_account_id)}"


async def _link_legacy_vault(db: AsyncSession, workspace: Workspace) -> None:
    """Attach any existing owner-keyed VaultConfig row to its corresponding workspace."""
    owner_clean = workspace.github_login.strip().lower()
    if not owner_clean:
        return
    res = await db.execute(select(VaultConfig).where(VaultConfig.owner == owner_clean))
    cfg = res.scalar_one_or_none()
    if cfg and not cfg.workspace_id:
        cfg.workspace_id = workspace.id


async def upsert_workspace_from_installation(
    db: AsyncSession,
    installation: dict[str, Any],
    *,
    action: str = "created",
) -> Workspace | None:
    """Create or update a Workspace from a GitHub App installation object/webhook."""
    account = installation.get("account") or {}
    account_id_raw = account.get("id")
    login_raw = (account.get("login") or "").strip().lower()
    if not account_id_raw or not login_raw:
        return None

    account_id = int(account_id_raw)
    account_type = str(account.get("type") or "User")
    installation_id_raw = installation.get("id")
    installation_id = int(installation_id_raw) if installation_id_raw else None
    ws_id = workspace_id_for_account(account_id)

    action_lower = (action or "").lower().strip()
    if action_lower == "deleted":
        install_status = "uninstalled"
    elif action_lower == "suspend" or installation.get("suspended_at"):
        install_status = "suspended"
    else:
        install_status = "active"

    res = await db.execute(
        select(Workspace).where(Workspace.github_account_id == account_id)
    )
    ws = res.scalar_one_or_none()

    if ws is None:
        ws = Workspace(
            id=ws_id,
            github_account_id=account_id,
            github_login=login_raw,
            account_type=account_type,
            github_installation_id=installation_id if install_status != "uninstalled" else None,
            installation_status=install_status,
            key_mode="byok",
            plan_tier="unsubscribed",
            subscription_status="none",
        )
        db.add(ws)
    else:
        ws.github_login = login_raw
        ws.account_type = account_type
        ws.installation_status = install_status
        if install_status == "uninstalled":
            ws.github_installation_id = None
        elif installation_id is not None:
            ws.github_installation_id = installation_id
        ws.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await _link_legacy_vault(db, ws)
    await db.commit()
    await db.refresh(ws)
    return ws


async def ensure_personal_workspace(
    db: AsyncSession,
    *,
    github_user_id: int,
    github_login: str,
    installation_id: int | None = None,
) -> Workspace:
    """Ensure a personal GitHub account workspace exists even before App installation."""
    login_clean = github_login.strip().lower()
    ws_id = workspace_id_for_account(github_user_id)
    res = await db.execute(
        select(Workspace).where(Workspace.github_account_id == int(github_user_id))
    )
    ws = res.scalar_one_or_none()
    if ws is None:
        ws = Workspace(
            id=ws_id,
            github_account_id=int(github_user_id),
            github_login=login_clean,
            account_type="User",
            github_installation_id=installation_id,
            installation_status="active" if installation_id else "not_installed",
            key_mode="byok",
            plan_tier="unsubscribed",
            subscription_status="none",
        )
        db.add(ws)
    else:
        ws.github_login = login_clean
        if installation_id is not None:
            ws.github_installation_id = installation_id
            ws.installation_status = "active"
        ws.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await _link_legacy_vault(db, ws)
    return ws


async def sync_user_workspaces(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    github_token: str | None = None,
    github_user_id: int | None = None,
    github_login: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[tuple[Workspace, WorkspaceMember]]:
    """Sync all personal and GitHub Organization workspaces accessible to an authenticated user."""
    if not clerk_user_id:
        return []

    now = datetime.now(timezone.utc)
    resolved_user_id = github_user_id
    resolved_login = (github_login or "").strip().lower()

    installations: list[dict[str, Any]] = []
    org_roles: dict[str, str] = {}

    if github_token and github_token.strip():
        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=20.0)
            close_client = True
        try:
            if not resolved_user_id or not resolved_login:
                try:
                    profile = await fetch_user_profile(github_token, client=client)
                    if profile.get("id"):
                        resolved_user_id = int(profile["id"])
                    if profile.get("login"):
                        resolved_login = str(profile["login"]).strip().lower()
                except Exception as exc:
                    logger.warning("Failed fetching GitHub profile during workspace sync: %s", exc)

            installations = await fetch_user_app_installations(github_token, client=client)
            org_roles = await fetch_user_org_memberships(github_token, client=client)
        finally:
            if close_client:
                await client.aclose()

    discovered: dict[str, tuple[Workspace, str]] = {}

    # 1. Process installations returned by GitHub for this user
    for inst in installations:
        account = inst.get("account") or {}
        acc_id = account.get("id")
        acc_login = (account.get("login") or "").strip().lower()
        acc_type = str(account.get("type") or "User")
        if not acc_id or not acc_login:
            continue

        ws = await upsert_workspace_from_installation(db, inst, action="sync")
        if ws is None:
            continue

        if acc_type.lower() == "user" and (
            (resolved_user_id and int(acc_id) == int(resolved_user_id))
            or (resolved_login and acc_login == resolved_login)
        ):
            role = "owner"
        elif acc_type.lower() == "organization":
            gh_role = org_roles.get(acc_login, "member").lower()
            role = "admin" if gh_role == "admin" else "member"
        else:
            role = "member"

        discovered[ws.id] = (ws, role)

    # 2. Ensure personal workspace is always present if we know the user's GitHub numeric ID
    if resolved_user_id and resolved_login:
        personal_id = workspace_id_for_account(int(resolved_user_id))
        if personal_id not in discovered:
            personal_ws = await ensure_personal_workspace(
                db,
                github_user_id=int(resolved_user_id),
                github_login=resolved_login,
            )
            discovered[personal_ws.id] = (personal_ws, "owner")

    # 3. Upsert verified memberships and prune stale memberships if GitHub token was checked
    for ws_id, (ws, role) in discovered.items():
        res = await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == ws_id,
                WorkspaceMember.clerk_user_id == clerk_user_id,
            )
        )
        member = res.scalar_one_or_none()
        if member is None:
            member = WorkspaceMember(
                workspace_id=ws_id,
                clerk_user_id=clerk_user_id,
                github_user_id=resolved_user_id,
                github_login=resolved_login or None,
                role=role,
                verified_at=now,
            )
            db.add(member)
        else:
            member.github_user_id = resolved_user_id or member.github_user_id
            member.github_login = resolved_login or member.github_login
            member.role = role
            member.verified_at = now

    if github_token and github_token.strip():
        active_ids = list(discovered.keys())
        if active_ids:
            await db.execute(
                delete(WorkspaceMember).where(
                    WorkspaceMember.clerk_user_id == clerk_user_id,
                    WorkspaceMember.workspace_id.notin_(active_ids),
                )
            )
        else:
            await db.execute(
                delete(WorkspaceMember).where(
                    WorkspaceMember.clerk_user_id == clerk_user_id,
                )
            )

    await db.commit()

    return await list_user_workspaces(db, clerk_user_id)


async def list_user_workspaces(
    db: AsyncSession,
    clerk_user_id: str,
) -> list[tuple[Workspace, WorkspaceMember]]:
    """Return all cached workspaces for a Clerk user, personal first then alphabetical."""
    res = await db.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, Workspace.id == WorkspaceMember.workspace_id)
        .where(WorkspaceMember.clerk_user_id == clerk_user_id)
        .order_by(Workspace.account_type.desc(), Workspace.github_login.asc())
    )
    return list(res.all())


async def resolve_workspace_for_webhook(
    db: AsyncSession,
    payload: dict[str, Any],
) -> Workspace | None:
    """Resolve the exact tenant Workspace for an incoming GitHub PR/comment webhook.

    Never falls back to another tenant's workspace or vault.
    """
    installation_id_raw = (payload.get("installation") or {}).get("id")
    installation_id = int(installation_id_raw) if installation_id_raw else None
    repo = payload.get("repository") or {}
    owner_obj = repo.get("owner") or {}
    account_id_raw = owner_obj.get("id")
    account_id = int(account_id_raw) if account_id_raw else None
    owner_login = (owner_obj.get("login") or "").strip().lower()
    account_type = str(owner_obj.get("type") or "User")

    ws: Workspace | None = None
    if installation_id is not None:
        res = await db.execute(
            select(Workspace).where(Workspace.github_installation_id == installation_id)
        )
        ws = res.scalar_one_or_none()

    if ws is None and account_id is not None:
        res = await db.execute(
            select(Workspace).where(Workspace.github_account_id == account_id)
        )
        ws = res.scalar_one_or_none()

    if ws is None and owner_login:
        res = await db.execute(
            select(Workspace).where(func.lower(Workspace.github_login) == owner_login)
        )
        ws = res.scalar_one_or_none()

    if ws is not None:
        changed = False
        if owner_login and ws.github_login != owner_login:
            ws.github_login = owner_login
            changed = True
        if installation_id is not None and ws.github_installation_id != installation_id:
            ws.github_installation_id = installation_id
            ws.installation_status = "active"
            changed = True
        if changed:
            ws.updated_at = datetime.now(timezone.utc)
            await _link_legacy_vault(db, ws)
            await db.commit()
            await db.refresh(ws)
        return ws

    # Auto-register a workspace row for an installation that existed prior to migration
    if account_id is not None and owner_login:
        return await upsert_workspace_from_installation(
            db,
            {
                "id": installation_id,
                "account": {
                    "id": account_id,
                    "login": owner_login,
                    "type": account_type,
                },
            },
            action="created",
        )

    return None


async def authorize_workspace_member(
    db: AsyncSession,
    *,
    workspace_id: str,
    clerk_user_id: str,
    require_admin: bool = False,
    github_token: str | None = None,
    github_user_id: int | None = None,
    github_login: str | None = None,
) -> tuple[Workspace, WorkspaceMember] | None:
    """Verify a user is a member (or owner/admin) of the target workspace, refreshing stale caches."""
    res = await db.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, Workspace.id == WorkspaceMember.workspace_id)
        .where(
            Workspace.id == workspace_id,
            WorkspaceMember.clerk_user_id == clerk_user_id,
        )
    )
    pair = res.one_or_none()

    ttl = timedelta(seconds=max(60, int(settings.WORKSPACE_MEMBERSHIP_TTL_SECONDS)))
    now = datetime.now(timezone.utc)
    is_stale = pair is None or (
        _as_utc(pair[1].verified_at) is not None
        and (now - _as_utc(pair[1].verified_at)) > ttl
    )

    if is_stale and github_token and github_token.strip():
        await sync_user_workspaces(
            db,
            clerk_user_id=clerk_user_id,
            github_token=github_token,
            github_user_id=github_user_id,
            github_login=github_login,
        )
        res = await db.execute(
            select(Workspace, WorkspaceMember)
            .join(WorkspaceMember, Workspace.id == WorkspaceMember.workspace_id)
            .where(
                Workspace.id == workspace_id,
                WorkspaceMember.clerk_user_id == clerk_user_id,
            )
        )
        pair = res.one_or_none()

    if pair is None:
        return None

    ws, member = pair
    if require_admin and member.role.lower() not in ADMIN_ROLES:
        return None
    return ws, member


async def get_vault_for_workspace(
    db: AsyncSession,
    workspace: Workspace,
) -> VaultConfig | None:
    """Resolve the VaultConfig belonging strictly to a given Workspace (by ID or exact login)."""
    res = await db.execute(
        select(VaultConfig).where(VaultConfig.workspace_id == workspace.id)
    )
    cfg = res.scalar_one_or_none()
    if cfg is not None:
        return cfg

    owner_clean = workspace.github_login.strip().lower()
    res = await db.execute(
        select(VaultConfig).where(VaultConfig.owner == owner_clean)
    )
    cfg = res.scalar_one_or_none()
    if cfg is not None and (cfg.workspace_id is None or cfg.workspace_id == workspace.id):
        if cfg.workspace_id is None:
            cfg.workspace_id = workspace.id
            await db.commit()
        return cfg
    return None
