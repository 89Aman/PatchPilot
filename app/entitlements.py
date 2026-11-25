"""Commercial SaaS entitlement gating & per-active-committer seat ledger.

Rules:
- When `BILLING_ENABLED=False` (self-hosted MIT / tests by default): all reviews
  and BYOK features are allowed without subscription checks.
- When `BILLING_ENABLED=True` (hosted commercial SaaS):
  - No free tier and no free trial: an active paid Pro subscription is required.
  - Subscribed workspaces support both Managed AI (zero-config server-side pool)
    and BYOK Vault (customer encrypted keys + sequential multi-key failover).
  - Billed per unique human PR author (`github_author_id`) per billing period;
    bots are excluded from seat counts; optional customer `seat_cap` is enforced.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Literal
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import ReviewUsageEvent, Workspace, WorkspaceActiveCommitter
from app.editions import TEAM_PRODUCT_SEAT_CEILING

logger = logging.getLogger("patchpilot.entitlements")

ACTIVE_SUBSCRIPTION_STATUSES = {"active", "past_due"}
PAID_PLAN_TIERS = {"pro", "team", "enterprise", "solo"}

_seat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_bot_author(author_login: str | None, author_type: str | None = None) -> bool:
    """Return True if the GitHub author represents a bot rather than a billable developer."""
    if (author_type or "").strip().lower() == "bot":
        return True
    login_lower = (author_login or "").strip().lower()
    return login_lower.endswith("[bot]")


def current_billing_period(workspace: Workspace, now: datetime | None = None) -> str:
    """Derive deterministic billing period key aligned to subscription cycle or calendar month."""
    ref_now = now or datetime.now(timezone.utc)
    period_start = _as_utc(workspace.current_period_start)
    if period_start is not None:
        return period_start.strftime("%Y-%m-%d")
    return ref_now.strftime("%Y-%m")


def workspace_billing_url(workspace: Workspace | None) -> str:
    """Build dashboard billing link dynamically from APP_BASE_URL without hardcoding domains."""
    base = settings.app_base_url
    path = f"/dashboard?workspace={workspace.id}&tab=billing" if workspace else "/dashboard"
    return f"{base}{path}" if base else path


def is_workspace_subscribed(workspace: Workspace | None, now: datetime | None = None) -> bool:
    """Return True when the workspace holds an active paid subscription."""
    if workspace is None:
        return False
    tier = (workspace.plan_tier or "").strip().lower()
    if tier not in PAID_PLAN_TIERS:
        return False
    status_clean = (workspace.subscription_status or "").strip().lower()
    if status_clean not in ACTIVE_SUBSCRIPTION_STATUSES:
        return False
    ref_now = now or datetime.now(timezone.utc)
    period_end = _as_utc(workspace.current_period_end)
    if workspace.cancel_at_period_end and period_end is not None and ref_now >= period_end:
        return False
    return True


def has_managed_llm_configured() -> bool:
    """Check whether server-side Managed AI provider credentials are configured."""
    return bool(
        settings.MANAGED_LLM_PRIMARY_API_KEY.strip()
        or settings.MANAGED_LLM_PRIMARY_PROVIDER.strip().lower() == "ollama"
        or settings.MANAGED_LLM_FALLBACK_API_KEY.strip()
    )


@dataclass(frozen=True)
class EntitlementDecision:
    allowed: bool
    effective_key_mode: Literal["byok", "managed"]
    allow_fallbacks: bool
    reason_code: str
    user_message: str = ""


async def _lock_workspace(db: AsyncSession, workspace_id: str) -> None:
    if db.get_bind().dialect.name == "sqlite":
        await db.execute(update(Workspace).where(Workspace.id == workspace_id).values(id=Workspace.id))
    else:
        await db.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())


async def admit_review(
    db: AsyncSession, workspace: Workspace | None, *, is_private: bool,
    github_author_id: int | None, github_author_login: str,
    author_type: str | None = None, has_byok_key: bool = False,
) -> EntitlementDecision:
    """Reserve the active author's seat before admitting work across workers."""
    reserve = (settings.BILLING_ENABLED and workspace is not None
               and github_author_id is not None and not is_bot_author(github_author_login, author_type))
    now = datetime.now(timezone.utc)
    if reserve:
        await _lock_workspace(db, workspace.id)
    decision = await evaluate_review_entitlement(
        db, workspace, is_private=is_private, github_author_id=github_author_id,
        github_author_login=github_author_login, author_type=author_type,
        has_byok_key=has_byok_key, now=now,
    )
    if reserve and decision.allowed:
        period = current_billing_period(workspace, now=now)
        exists = await db.scalar(select(WorkspaceActiveCommitter.github_author_id).where(
            WorkspaceActiveCommitter.workspace_id == workspace.id,
            WorkspaceActiveCommitter.billing_period == period,
            WorkspaceActiveCommitter.github_author_id == github_author_id,
        ))
        if exists is None:
            db.add(WorkspaceActiveCommitter(
                workspace_id=workspace.id, billing_period=period, github_author_id=github_author_id,
                github_author_login=github_author_login, review_count=0, managed_review_count=0,
                meter_event_id=f"seat:{workspace.id}:{period}:{github_author_id}",
                first_seen_at=now, last_seen_at=now,
            ))
        await db.commit()
    return decision


async def evaluate_review_entitlement(
    db: AsyncSession,
    workspace: Workspace | None,
    *,
    is_private: bool,
    github_author_id: int | None,
    github_author_login: str,
    author_type: str | None = None,
    has_byok_key: bool = False,
    now: datetime | None = None,
) -> EntitlementDecision:
    """Evaluate whether a PR review or command is permitted and which key mode to use."""
    # Self-hosted open-source mode: no commercial gating.
    if not settings.BILLING_ENABLED:
        return EntitlementDecision(
            allowed=True,
            effective_key_mode="byok",
            allow_fallbacks=True,
            reason_code="self_hosted",
        )

    billing_link = workspace_billing_url(workspace)
    ws_label = workspace.github_login if workspace else "this repository owner"

    if workspace is None:
        return EntitlementDecision(
            allowed=False,
            effective_key_mode="byok",
            allow_fallbacks=False,
            reason_code="workspace_not_found",
            user_message=(
                "## PatchPilot Subscription Required\n\n"
                f"No active PatchPilot workspace was found for `{ws_label}`. "
                f"Sign in at the PatchPilot dashboard (`{billing_link}`) to connect your GitHub installation and activate a subscription."
            ),
        )

    if workspace.installation_status != "active":
        return EntitlementDecision(
            allowed=False,
            effective_key_mode="byok",
            allow_fallbacks=False,
            reason_code="installation_inactive",
            user_message=(
                "## PatchPilot Installation Inactive\n\n"
                f"The GitHub App installation for `{workspace.github_login}` is currently `{workspace.installation_status}`. "
                f"Re-enable the installation in GitHub settings to resume automated reviews."
            ),
        )

    ref_now = now or datetime.now(timezone.utc)
    if not is_workspace_subscribed(workspace, now=ref_now):
        return EntitlementDecision(
            allowed=False,
            effective_key_mode="byok",
            allow_fallbacks=False,
            reason_code="subscription_required",
            user_message=(
                "## PatchPilot Subscription Required\n\n"
                f"Automated AI code reviews for `{workspace.github_login}` require an active **PatchPilot Pro** subscription "
                "(billed per unique active PR author each month, with Managed AI and BYOK Vault both included).\n\n"
                f"**Activate your workspace:** Open `{billing_link}` and click **Subscribe to Pro**."
            ),
        )

    # Subscribed Pro workspace: verify optional seat_cap and fair-use guardrail
    period = current_billing_period(workspace, now=ref_now)
    author_bot = is_bot_author(github_author_login, author_type)
    existing_seat: WorkspaceActiveCommitter | None = None

    tier = (workspace.plan_tier or "").strip().lower()
    is_enterprise = tier == "enterprise"

    if github_author_id is not None and not author_bot:
        seat_res = await db.execute(
            select(WorkspaceActiveCommitter).where(
                WorkspaceActiveCommitter.workspace_id == workspace.id,
                WorkspaceActiveCommitter.billing_period == period,
                WorkspaceActiveCommitter.github_author_id == int(github_author_id),
            )
        )
        existing_seat = seat_res.scalar_one_or_none()

        if existing_seat is None:
            effective_cap: int | None = None if is_enterprise else TEAM_PRODUCT_SEAT_CEILING
            if workspace.seat_cap is not None:
                effective_cap = (
                    min(int(workspace.seat_cap), effective_cap)
                    if effective_cap is not None
                    else int(workspace.seat_cap)
                )

            if effective_cap is not None:
                seat_count = await db.scalar(
                    select(func.count()).select_from(WorkspaceActiveCommitter).where(
                        WorkspaceActiveCommitter.workspace_id == workspace.id,
                        WorkspaceActiveCommitter.billing_period == period,
                    )
                ) or 0
                if seat_count >= effective_cap:
                    cap_label = (
                        f"the {TEAM_PRODUCT_SEAT_CEILING}-developer product ceiling"
                        if effective_cap == TEAM_PRODUCT_SEAT_CEILING and (workspace.seat_cap is None or int(workspace.seat_cap) >= TEAM_PRODUCT_SEAT_CEILING)
                        else "its configured active committer cap"
                    )
                    return EntitlementDecision(
                        allowed=False,
                        effective_key_mode="byok",
                        allow_fallbacks=False,
                        reason_code="seat_cap_reached",
                        user_message=(
                            "## PatchPilot Active Committer Limit Reached\n\n"
                            f"Workspace `{workspace.github_login}` has reached {cap_label} "
                            f"(`{seat_count}/{effective_cap}` seats in period `{period}`).\n\n"
                            f"A workspace admin can raise or adjust the cap in Billing Settings: `{billing_link}`."
                        ),
                    )

    # Resolve key mode: honor explicit workspace preference ('managed' vs 'byok')
    desired_mode = (workspace.key_mode or "managed").strip().lower()
    if desired_mode == "managed":
        if not has_managed_llm_configured():
            if has_byok_key:
                return EntitlementDecision(
                    allowed=True,
                    effective_key_mode="byok",
                    allow_fallbacks=True,
                    reason_code="managed_unavailable_using_byok",
                )
            return EntitlementDecision(
                allowed=False,
                effective_key_mode="managed",
                allow_fallbacks=False,
                reason_code="managed_llm_not_configured",
                user_message=(
                    "## PatchPilot Managed AI Temporarily Unavailable\n\n"
                    f"Managed AI credentials are not configured on this server. "
                    f"Switch `{workspace.github_login}` to BYOK mode in `{billing_link}` or contact support."
                ),
            )

        cap_per_seat = max(1, int(settings.MANAGED_MONTHLY_REVIEW_CAP_PER_SEAT))
        if existing_seat is not None and existing_seat.managed_review_count >= cap_per_seat:
            if has_byok_key:
                return EntitlementDecision(
                    allowed=True,
                    effective_key_mode="byok",
                    allow_fallbacks=True,
                    reason_code="managed_fair_use_fallback_byok",
                )
            return EntitlementDecision(
                allowed=False,
                effective_key_mode="managed",
                allow_fallbacks=False,
                reason_code="managed_fair_use_exceeded",
                user_message=(
                    "## PatchPilot Monthly Managed AI Fair-Use Limit Reached\n\n"
                    f"Developer `@{github_author_login}` has reached the fair-use limit (`{cap_per_seat}` Managed AI reviews) "
                    f"for period `{period}`. Switch to BYOK mode in `{billing_link}` for unlimited self-funded reviews."
                ),
            )

        return EntitlementDecision(
            allowed=True,
            effective_key_mode="managed",
            allow_fallbacks=True,
            reason_code="ok_managed",
        )

    # Explicit BYOK mode on Pro subscription
    if not has_byok_key:
        if has_managed_llm_configured():
            return EntitlementDecision(
                allowed=True,
                effective_key_mode="managed",
                allow_fallbacks=True,
                reason_code="ok_managed_fallback",
            )
        return EntitlementDecision(
            allowed=False,
            effective_key_mode="byok",
            allow_fallbacks=False,
            reason_code="missing_byok_key",
            user_message=(
                "## PatchPilot Vault Key Required\n\n"
                f"Workspace `{workspace.github_login}` is set to BYOK mode but has no API key saved. "
                f"Add a provider API key or switch to Managed AI at `{billing_link}`."
            ),
        )

    return EntitlementDecision(
        allowed=True,
        effective_key_mode="byok",
        allow_fallbacks=True,
        reason_code="ok_byok",
    )


async def record_completed_review(
    db: AsyncSession,
    *,
    workspace: Workspace,
    event_id: str,
    repo_full_name: str,
    is_private: bool,
    pr_number: int,
    github_author_id: int | None,
    github_author_login: str,
    author_type: str | None = None,
    trigger_type: str = "pr_review",
    key_mode: str = "byok",
    provider: str = "",
    model: str = "",
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    now: datetime | None = None,
) -> WorkspaceActiveCommitter | None:
    """Persist review telemetry and upsert the per-active-committer seat ledger idempotently."""
    ref_now = now or datetime.now(timezone.utc)

    # 1. Record immutable usage audit row (idempotent on event_id)
    existing_evt = await db.get(ReviewUsageEvent, event_id)
    if existing_evt is None:
        db.add(
            ReviewUsageEvent(
                id=event_id,
                workspace_id=workspace.id,
                repo_full_name=repo_full_name,
                is_private=bool(is_private),
                pr_number=int(pr_number or 0),
                github_author_id=int(github_author_id) if github_author_id is not None else None,
                github_author_login=(github_author_login or "").strip(),
                trigger_type=trigger_type,
                key_mode=key_mode,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                created_at=ref_now,
            )
        )
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            # Already recorded by a prior delivery/retry
            return None

    # 2. Upsert active human committer seat for this billing cycle (bots excluded)
    if github_author_id is None or is_bot_author(github_author_login, author_type):
        await db.commit()
        return None

    period = current_billing_period(workspace, now=ref_now)
    author_id_int = int(github_author_id)
    meter_event_id = f"seat:{workspace.id}:{period}:{author_id_int}"
    is_managed = key_mode == "managed"

    tier = (workspace.plan_tier or "").strip().lower()
    is_enterprise = tier == "enterprise"
    effective_cap: int | None = None if is_enterprise else TEAM_PRODUCT_SEAT_CEILING
    if workspace.seat_cap is not None:
        effective_cap = (
            min(int(workspace.seat_cap), effective_cap)
            if effective_cap is not None
            else int(workspace.seat_cap)
        )

    async with _seat_locks[workspace.id]:
        await _lock_workspace(db, workspace.id)
        res = await db.execute(
            select(WorkspaceActiveCommitter).where(
                WorkspaceActiveCommitter.workspace_id == workspace.id,
                WorkspaceActiveCommitter.billing_period == period,
                WorkspaceActiveCommitter.github_author_id == author_id_int,
            )
        )
        seat = res.scalar_one_or_none()

        if seat is None:
            if effective_cap is not None:
                seat_count = await db.scalar(
                    select(func.count()).select_from(WorkspaceActiveCommitter).where(
                        WorkspaceActiveCommitter.workspace_id == workspace.id,
                        WorkspaceActiveCommitter.billing_period == period,
                    )
                ) or 0
                if seat_count >= effective_cap:
                    logger.warning(
                        "Seat cap reached for workspace %s (count=%s, cap=%s). Blocking author %s",
                        workspace.id,
                        seat_count,
                        effective_cap,
                        author_id_int,
                    )
                    return None

            seat = WorkspaceActiveCommitter(
                workspace_id=workspace.id,
                billing_period=period,
                github_author_id=author_id_int,
                github_author_login=(github_author_login or "").strip() or str(author_id_int),
                review_count=1,
                managed_review_count=1 if is_managed else 0,
                meter_event_id=meter_event_id,
                meter_reported_at=None,
                first_seen_at=ref_now,
                last_seen_at=ref_now,
            )
            db.add(seat)
            try:
                await db.commit()
                await db.refresh(seat)
                return seat
            except IntegrityError:
                await db.rollback()
                res = await db.execute(
                    select(WorkspaceActiveCommitter).where(
                        WorkspaceActiveCommitter.workspace_id == workspace.id,
                        WorkspaceActiveCommitter.billing_period == period,
                        WorkspaceActiveCommitter.github_author_id == author_id_int,
                    )
                )
                seat = res.scalar_one_or_none()

        if seat is not None:
            seat.review_count += 1
            if is_managed:
                seat.managed_review_count += 1
            if github_author_login:
                seat.github_author_login = github_author_login.strip()
            seat.last_seen_at = ref_now
            await db.commit()
            await db.refresh(seat)

        return seat
