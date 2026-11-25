"""Tests for multi-tenant GitHub Workspaces, Clerk auth session integrity, RBAC, and isolation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
os.environ["TESTING"] = "1"

import httpx
import pytest
from sqlalchemy import delete, select
from app.config import settings
from app.crypto import encrypt_secret
from app.db import (
    BillingWebhookEvent,
    SessionLocal,
    UserAccount,
    VaultConfig,
    Workspace,
    WorkspaceMember,
    init_db,
)
from app.github_auth import create_session_jwt, verify_session_jwt
from app.main import _get_owner_config, app
from app.webhooks_crypto import sign_standard_webhook
from app.workspaces import sync_user_workspaces, workspace_id_for_account


async def _clean_workspace_tables() -> None:
    await init_db()
    async with SessionLocal() as db:
        await db.execute(delete(WorkspaceMember))
        await db.execute(delete(Workspace))
        await db.execute(delete(BillingWebhookEvent))
        await db.execute(delete(VaultConfig))
        await db.execute(delete(UserAccount))
        await db.commit()


def _sign_github_payload(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_session_jwt_preserves_clerk_and_github_identity():
    """Verify create_session_jwt retains clerk_id, github_username, github_user_id, and email."""
    token = create_session_jwt({
        "login": "alice",
        "name": "Alice Engineer",
        "email": "alice@example.com",
        "avatar_url": "https://example.com/a.png",
        "clerk_id": "user_clerk_123",
        "github_username": "alice-gh",
        "github_user_id": 424242,
        "auth_provider": "clerk",
        "user_token": "gho_secret_oauth_token_999",
    })
    assert "gho_secret_oauth_token_999" not in token

    decoded = verify_session_jwt(token)
    assert decoded is not None
    assert decoded["sub"] == "alice"
    assert decoded["clerk_id"] == "user_clerk_123"
    assert decoded["github_username"] == "alice-gh"
    assert decoded["github_user_id"] == 424242
    assert decoded["email"] == "alice@example.com"
    assert decoded["auth_provider"] == "clerk"
    assert decoded["user_token"] == "gho_secret_oauth_token_999"


@pytest.mark.asyncio
async def test_github_installation_webhooks_lifecycle_and_rename():
    """Verify installation created, suspend, rename, and deleted webhooks track immutable account ID with no trial."""
    await _clean_workspace_tables()
    try:
        # Seed a legacy VaultConfig under the old org login to verify automatic workspace_id linking
        async with SessionLocal() as db:
            db.add(VaultConfig(
                owner="acme-labs",
                provider="gemini",
                model="gemini-2.5-flash",
                encrypted_api_key=encrypt_secret("AIzaSyLegacyOrgKey12345"),
            ))
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def send_inst(action: str, login: str, installation_id: int = 9001):
                body = json.dumps({
                    "action": action,
                    "installation": {
                        "id": installation_id,
                        "account": {"id": 777001, "login": login, "type": "Organization"},
                    },
                }).encode("utf-8")
                return await client.post(
                    "/webhooks/github",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-GitHub-Event": "installation",
                        "X-Hub-Signature-256": _sign_github_payload(body, settings.GITHUB_WEBHOOK_SECRET),
                    },
                )

            # 1. Created -> active installation, strictly unsubscribed (no free trial)
            r1 = await send_inst("created", "Acme-Labs")
            assert r1.status_code == 200
            ws_id = workspace_id_for_account(777001)
            assert r1.json()["workspace_id"] == ws_id
            assert r1.json()["installation_status"] == "active"

            async with SessionLocal() as db:
                ws = await db.get(Workspace, ws_id)
                assert ws is not None
                assert ws.github_login == "acme-labs"
                assert ws.account_type == "Organization"
                assert ws.plan_tier == "unsubscribed"
                assert ws.subscription_status == "none"

                cfg = (await db.execute(select(VaultConfig).where(VaultConfig.owner == "acme-labs"))).scalar_one()
                assert cfg.workspace_id == ws_id

            # 2. Suspend
            r2 = await send_inst("suspend", "Acme-Labs")
            assert r2.status_code == 200
            assert r2.json()["installation_status"] == "suspended"

            # 3. Unsuspend after org rename -> immutable github_account_id keeps same workspace ID
            r3 = await send_inst("unsuspend", "Acme-Corp")
            assert r3.status_code == 200
            async with SessionLocal() as db:
                ws = await db.get(Workspace, ws_id)
                assert ws.github_login == "acme-corp"
                assert ws.installation_status == "active"

            # 4. Deleted -> uninstalled & installation_id cleared
            r4 = await send_inst("deleted", "Acme-Corp")
            assert r4.status_code == 200
            async with SessionLocal() as db:
                ws = await db.get(Workspace, ws_id)
                assert ws.installation_status == "uninstalled"
                assert ws.github_installation_id is None
    finally:
        await _clean_workspace_tables()


@pytest.mark.asyncio
async def test_sync_workspaces_and_org_rbac_isolation(monkeypatch):
    """Verify org admins can configure org vaults & billing, members are read-only, and non-members get 403."""
    await _clean_workspace_tables()
    try:
        async def fake_installations(token: str, client=None):
            if token == "gho_alice_token":
                return [
                    {"id": 1001, "account": {"id": 111, "login": "alice", "type": "User"}},
                    {"id": 2001, "account": {"id": 222, "login": "acme-org", "type": "Organization"}},
                ]
            if token == "gho_bob_token":
                return [
                    {"id": 2001, "account": {"id": 222, "login": "acme-org", "type": "Organization"}},
                ]
            return []

        async def fake_org_roles(token: str, client=None):
            if token == "gho_alice_token":
                return {"acme-org": "admin"}
            if token == "gho_bob_token":
                return {"acme-org": "member"}
            return {}

        monkeypatch.setattr("app.workspaces.fetch_user_app_installations", fake_installations)
        monkeypatch.setattr("app.workspaces.fetch_user_org_memberships", fake_org_roles)

        async with SessionLocal() as db:
            await sync_user_workspaces(
                db,
                clerk_user_id="user_alice",
                github_token="gho_alice_token",
                github_user_id=111,
                github_login="alice",
            )
            await sync_user_workspaces(
                db,
                clerk_user_id="user_bob",
                github_token="gho_bob_token",
                github_user_id=333,
                github_login="bob",
            )

        org_ws_id = workspace_id_for_account(222)

        alice_cookie = create_session_jwt({
            "login": "alice",
            "clerk_id": "user_alice",
            "github_username": "alice",
            "github_user_id": 111,
            "user_token": "gho_alice_token",
            "auth_provider": "clerk",
        })
        bob_cookie = create_session_jwt({
            "login": "bob",
            "clerk_id": "user_bob",
            "github_username": "bob",
            "github_user_id": 333,
            "user_token": "gho_bob_token",
            "auth_provider": "clerk",
        })
        eve_cookie = create_session_jwt({
            "login": "eve",
            "clerk_id": "user_eve",
            "github_username": "eve",
            "github_user_id": 999,
            "user_token": "gho_eve_token",
            "auth_provider": "clerk",
        })

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice_headers = {"Cookie": f"patchpilot_session={alice_cookie}"}
            bob_headers = {"Cookie": f"patchpilot_session={bob_cookie}"}
            eve_headers = {"Cookie": f"patchpilot_session={eve_cookie}"}

            # Alice (admin) sees both personal + org workspace
            res_ws = await client.get("/api/workspaces", headers=alice_headers)
            assert res_ws.status_code == 200
            ws_map = {w["id"]: w for w in res_ws.json()["workspaces"]}
            assert org_ws_id in ws_map
            assert ws_map[org_ws_id]["role"] == "admin"
            assert ws_map[org_ws_id]["is_admin"] is True

            # Alice (admin) can save org vault via workspace API AND legacy /vault/{owner}
            res_put = await client.put(
                f"/api/workspaces/{org_ws_id}/vault",
                headers=alice_headers,
                json={"provider": "groq", "model": "llama-3.3-70b-versatile", "api_key": "gsk_OrgVaultKey999999"},
            )
            assert res_put.status_code == 200
            assert res_put.json()["has_api_key"] is True

            res_legacy = await client.get("/vault/acme-org", headers=alice_headers)
            assert res_legacy.status_code == 200
            assert res_legacy.json()["owner"] == "acme-org"

            # Bob (member) can read org vault & billing, but gets 403 on mutations and checkout
            res_bob_read = await client.get(f"/api/workspaces/{org_ws_id}/vault", headers=bob_headers)
            assert res_bob_read.status_code == 200
            assert res_bob_read.json()["is_admin"] is False

            res_bob_write = await client.put(
                f"/api/workspaces/{org_ws_id}/vault",
                headers=bob_headers,
                json={"provider": "openai", "model": "gpt-4o", "api_key": "sk-unauthorized"},
            )
            assert res_bob_write.status_code == 403

            res_bob_checkout = await client.post(
                f"/api/workspaces/{org_ws_id}/billing/checkout",
                headers=bob_headers,
            )
            assert res_bob_checkout.status_code == 403

            # Eve (non-member) gets 403 on read & write
            res_eve_read = await client.get(f"/api/workspaces/{org_ws_id}/vault", headers=eve_headers)
            assert res_eve_read.status_code == 403
    finally:
        await _clean_workspace_tables()


@pytest.mark.asyncio
async def test_no_cross_tenant_single_vault_leak():
    """Verify _get_owner_config never leaks a lone tenant's API key to another unconfigured owner."""
    await _clean_workspace_tables()
    try:
        async with SessionLocal() as db:
            db.add(VaultConfig(
                owner="tenant-alpha",
                provider="gemini",
                model="gemini-2.5-flash",
                encrypted_api_key=encrypt_secret("AIzaSyTenantAlphaSecret1234"),
            ))
            await db.commit()

        cfg_alpha = await _get_owner_config("tenant-alpha")
        assert cfg_alpha is not None
        assert cfg_alpha.owner == "tenant-alpha"

        cfg_beta = await _get_owner_config("tenant-beta")
        assert cfg_beta is None
    finally:
        await _clean_workspace_tables()


@pytest.mark.asyncio
async def test_clerk_webhook_user_updated_and_deleted(monkeypatch):
    """Verify Clerk Svix webhook updates user profile and purges user + memberships on user.deleted."""
    await _clean_workspace_tables()
    raw_secret = "whsec_" + base64.b64encode(b"super-secret-clerk-webhook-key-32").decode("utf-8")
    monkeypatch.setattr(settings, "CLERK_WEBHOOK_SIGNING_SECRET", raw_secret)

    try:
        ws_id = workspace_id_for_account(55501)
        async with SessionLocal() as db:
            db.add(UserAccount(
                login="carol",
                clerk_id="user_carol_1",
                github_username="carol",
                email="old@example.com",
                name="Old Name",
                encrypted_github_token=encrypt_secret("gho_secret_token"),
                auth_provider="clerk",
            ))
            db.add(Workspace(id=ws_id, github_account_id=55501, github_login="carol", account_type="User"))
            db.add(WorkspaceMember(workspace_id=ws_id, clerk_user_id="user_carol_1", role="owner"))
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 1. Invalid signature rejected with 401
            bad_res = await client.post("/webhooks/clerk", content=b"{}", headers={"svix-id": "x"})
            assert bad_res.status_code == 401

            # 2. user.updated updates email and name
            upd_body = json.dumps({
                "type": "user.updated",
                "data": {
                    "id": "user_carol_1",
                    "first_name": "Carol",
                    "last_name": "Dev",
                    "primary_email_address_id": "em_1",
                    "email_addresses": [{"id": "em_1", "email_address": "carol@company.io"}],
                },
            }).encode("utf-8")
            upd_headers = sign_standard_webhook(upd_body, raw_secret, webhook_id="msg_clerk_upd_1")
            res_upd = await client.post("/webhooks/clerk", content=upd_body, headers=upd_headers)
            assert res_upd.status_code == 200
            assert res_upd.json()["status"] == "updated"

            # Duplicate delivery is ignored idempotently
            res_dup = await client.post("/webhooks/clerk", content=upd_body, headers=upd_headers)
            assert res_dup.status_code == 200
            assert res_dup.json()["status"] == "duplicate"

            async with SessionLocal() as db:
                user_rec = await db.get(UserAccount, "carol")
                assert user_rec.email == "carol@company.io"
                assert user_rec.name == "Carol Dev"

            # 3. user.deleted purges user record and workspace memberships
            del_body = json.dumps({"type": "user.deleted", "data": {"id": "user_carol_1"}}).encode("utf-8")
            del_headers = sign_standard_webhook(del_body, raw_secret, webhook_id="msg_clerk_del_1")
            res_del = await client.post("/webhooks/clerk", content=del_body, headers=del_headers)
            assert res_del.status_code == 200
            assert res_del.json()["status"] == "deleted"

            async with SessionLocal() as db:
                assert await db.get(UserAccount, "carol") is None
                members = (await db.execute(select(WorkspaceMember).where(WorkspaceMember.clerk_user_id == "user_carol_1"))).scalars().all()
                assert members == []
    finally:
        await _clean_workspace_tables()


@pytest.mark.asyncio
async def test_no_hardcoded_personal_details_in_public_ui():
    """Verify public pages never hardcode personal usernames, domains, app slugs, or admin token inputs."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/", "/dashboard", "/pricing"):
            res = await client.get(path)
            assert res.status_code == 200
            body = res.text
            assert "89Aman" not in body
            assert "noventra.org.in" not in body
            assert "patchpilot-app" not in body
            assert 'id="adminToken"' not in body
