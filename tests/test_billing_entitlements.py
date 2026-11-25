"""Tests for Polar.sh per-active-committer billing, Standard Webhooks, entitlements, and outbox retries."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
os.environ["TESTING"] = "1"
import time

import httpx
import pytest
from sqlalchemy import delete, select
from app.billing import (
    PolarBillingClient,
    flush_unreported_seat_events,
    process_polar_webhook,
)
from app.config import settings
from app.db import (
    BillingWebhookEvent,
    ReviewUsageEvent,
    SessionLocal,
    VaultConfig,
    Workspace,
    WorkspaceActiveCommitter,
    init_db,
)
from app.entitlements import (
    evaluate_review_entitlement,
    is_workspace_subscribed,
    record_completed_review,
)
from app.llm import build_managed_vault_config
from app.main import app
from app.webhooks_crypto import (
    WebhookVerificationError,
    sign_standard_webhook,
    verify_standard_webhook,
)
from app.workspaces import workspace_id_for_account


async def _clean_billing_tables() -> None:
    await init_db()
    async with SessionLocal() as db:
        await db.execute(delete(ReviewUsageEvent))
        await db.execute(delete(WorkspaceActiveCommitter))
        await db.execute(delete(BillingWebhookEvent))
        await db.execute(delete(VaultConfig))
        await db.execute(delete(Workspace))
        await db.commit()


def test_standard_webhook_verification_and_replay_protection():
    """Verify Standard Webhooks HMAC-SHA256 validation for whsec_ and polar_whs_ secrets, rotation, and replay window."""
    payload = b'{"type":"subscription.active","data":{"id":"sub_1"}}'
    whsec_secret = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode("utf-8")
    polar_secret = "polar_whs_" + base64.b64encode(b"fedcba9876543210fedcba9876543210").decode("utf-8")

    # Valid whsec_ signature
    headers = sign_standard_webhook(payload, whsec_secret, webhook_id="evt_1")
    evt_id, parsed = verify_standard_webhook(payload, headers, whsec_secret)
    assert evt_id == "evt_1"
    assert parsed["type"] == "subscription.active"

    # Valid polar_whs_ signature + multi-signature rotation header
    p_headers = sign_standard_webhook(payload, polar_secret, webhook_id="evt_2")
    p_headers["webhook-signature"] = f"v1,invalidSig== {p_headers['webhook-signature']}"
    evt_id_2, _ = verify_standard_webhook(payload, p_headers, polar_secret)
    assert evt_id_2 == "evt_2"

    # Tampered payload fails
    with pytest.raises(WebhookVerificationError):
        verify_standard_webhook(payload + b" ", headers, whsec_secret)

    # Expired timestamp (> 5 min) fails
    old_headers = sign_standard_webhook(payload, whsec_secret, webhook_id="evt_old", timestamp=int(time.time()) - 600)
    with pytest.raises(WebhookVerificationError, match="replay"):
        verify_standard_webhook(payload, old_headers, whsec_secret)


@pytest.mark.asyncio
async def test_polar_webhook_subscription_lifecycle_and_idempotency(monkeypatch):
    """Verify subscription.active -> canceled -> revoked transitions and webhook deduplication."""
    await _clean_billing_tables()
    secret = "polar_whs_" + base64.b64encode(b"polar-test-signing-key-32-bytes!").decode("utf-8")
    monkeypatch.setattr(settings, "POLAR_WEBHOOK_SECRET", secret)

    ws_id = workspace_id_for_account(88001)
    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=30)

    try:
        async with SessionLocal() as db:
            db.add(Workspace(
                id=ws_id,
                github_account_id=88001,
                github_login="rocket-org",
                account_type="Organization",
                github_installation_id=44001,
                installation_status="active",
                key_mode="byok",
                plan_tier="unsubscribed",
                subscription_status="none",
            ))
            await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 1. subscription.active activates Pro and auto-switches to Managed AI when no BYOK key exists
            active_body = json.dumps({
                "type": "subscription.active",
                "data": {
                    "id": "sub_polar_123",
                    "status": "active",
                    "customer_id": "cus_polar_456",
                    "current_period_start": now.isoformat(),
                    "current_period_end": period_end.isoformat(),
                    "cancel_at_period_end": False,
                    "customer": {"id": "cus_polar_456", "external_id": ws_id, "email": "cto@rocket.dev"},
                    "metadata": {"workspace_id": ws_id},
                },
            }).encode("utf-8")
            h1 = sign_standard_webhook(active_body, secret, webhook_id="wh_polar_1")
            r1 = await client.post("/webhooks/polar", content=active_body, headers=h1)
            assert r1.status_code == 200
            assert r1.json()["status"] == "processed"
            assert r1.json()["plan_tier"] == "pro"

            # Replay of exact same webhook-id is deduplicated
            r1_dup = await client.post("/webhooks/polar", content=active_body, headers=h1)
            assert r1_dup.status_code == 200
            assert r1_dup.json()["status"] == "duplicate"

            async with SessionLocal() as db:
                ws = await db.get(Workspace, ws_id)
                assert ws.plan_tier == "pro"
                assert ws.subscription_status == "active"
                assert ws.key_mode == "managed"
                assert ws.polar_customer_id == "cus_polar_456"
                assert ws.billing_email == "cto@rocket.dev"
                assert is_workspace_subscribed(ws) is True

            # 2. subscription.canceled marks cancel_at_period_end=True while remaining active until period end
            cancel_body = json.dumps({
                "type": "subscription.canceled",
                "data": {
                    "id": "sub_polar_123",
                    "status": "active",
                    "customer_id": "cus_polar_456",
                    "cancel_at_period_end": True,
                },
            }).encode("utf-8")
            h2 = sign_standard_webhook(cancel_body, secret, webhook_id="wh_polar_2")
            r2 = await client.post("/webhooks/polar", content=cancel_body, headers=h2)
            assert r2.status_code == 200

            async with SessionLocal() as db:
                ws = await db.get(Workspace, ws_id)
                assert ws.cancel_at_period_end is True
                assert is_workspace_subscribed(ws) is True

            # 3. subscription.revoked strips Pro access and reverts key_mode to byok
            revoke_body = json.dumps({
                "type": "subscription.revoked",
                "data": {
                    "id": "sub_polar_123",
                    "status": "revoked",
                    "customer_id": "cus_polar_456",
                },
            }).encode("utf-8")
            h3 = sign_standard_webhook(revoke_body, secret, webhook_id="wh_polar_3")
            r3 = await client.post("/webhooks/polar", content=revoke_body, headers=h3)
            assert r3.status_code == 200

            async with SessionLocal() as db:
                ws = await db.get(Workspace, ws_id)
                assert ws.plan_tier == "unsubscribed"
                assert ws.subscription_status == "revoked"
                assert ws.key_mode == "byok"
                assert is_workspace_subscribed(ws) is False
    finally:
        await _clean_billing_tables()


@pytest.mark.asyncio
async def test_entitlements_paid_only_no_free_tier_no_trial(monkeypatch):
    """Verify BILLING_ENABLED=True strictly requires an active subscription (no free tier, no trial)."""
    await _clean_billing_tables()
    ws_id = workspace_id_for_account(88002)

    try:
        async with SessionLocal() as db:
            ws = Workspace(
                id=ws_id,
                github_account_id=88002,
                github_login="indie-dev",
                account_type="User",
                github_installation_id=44002,
                installation_status="active",
                key_mode="byok",
                plan_tier="unsubscribed",
                subscription_status="none",
            )
            db.add(ws)
            await db.commit()

            # 1. Self-hosted mode (BILLING_ENABLED=False): allowed without subscription
            monkeypatch.setattr(settings, "BILLING_ENABLED", False)
            d_self = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=101, github_author_login="alice", has_byok_key=True
            )
            assert d_self.allowed is True
            assert d_self.reason_code == "self_hosted"

            # 2. Hosted cloud (BILLING_ENABLED=True): NO free tier on public OR private repos, NO free trial
            monkeypatch.setattr(settings, "BILLING_ENABLED", True)
            monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_API_KEY", "AIzaSyManagedPoolKey12345")

            d_pub = await evaluate_review_entitlement(
                db, ws, is_private=False, github_author_id=101, github_author_login="alice", has_byok_key=True
            )
            assert d_pub.allowed is False
            assert d_pub.reason_code == "subscription_required"
            assert "Subscribe to Pro" in d_pub.user_message

            d_priv = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=101, github_author_login="alice", has_byok_key=True
            )
            assert d_priv.allowed is False
            assert d_priv.reason_code == "subscription_required"

            # 3. Once subscribed to Pro, Managed AI works without any BYOK key
            ws.plan_tier = "pro"
            ws.subscription_status = "active"
            ws.key_mode = "managed"
            await db.commit()

            d_pro_managed = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=101, github_author_login="alice", has_byok_key=False
            )
            assert d_pro_managed.allowed is True
            assert d_pro_managed.effective_key_mode == "managed"
            assert d_pro_managed.allow_fallbacks is True

            # 4. Pro workspace switched to BYOK uses customer keys
            ws.key_mode = "byok"
            await db.commit()
            d_pro_byok = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=101, github_author_login="alice", has_byok_key=True
            )
            assert d_pro_byok.allowed is True
            assert d_pro_byok.effective_key_mode == "byok"
    finally:
        await _clean_billing_tables()


@pytest.mark.asyncio
async def test_per_active_committer_metering_bots_and_seat_cap(monkeypatch):
    """Verify unique human PR authors count as 1 seat, bots are excluded, and seat_cap blocks new seat N+1."""
    await _clean_billing_tables()
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_API_KEY", "AIzaSyManagedPoolKey12345")

    ws_id = workspace_id_for_account(88003)
    now = datetime.now(timezone.utc)

    try:
        async with SessionLocal() as db:
            ws = Workspace(
                id=ws_id,
                github_account_id=88003,
                github_login="scale-team",
                account_type="Organization",
                github_installation_id=44003,
                installation_status="active",
                key_mode="managed",
                plan_tier="pro",
                subscription_status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                seat_cap=2,
            )
            db.add(ws)
            await db.commit()

            # Author A opens 3 PRs -> counts as 1 unique active committer with review_count=3
            for i in range(1, 4):
                seat = await record_completed_review(
                    db,
                    workspace=ws,
                    event_id=f"job_a_{i}",
                    repo_full_name="scale-team/api",
                    is_private=True,
                    pr_number=i,
                    github_author_id=101,
                    github_author_login="alice",
                    key_mode="managed",
                    provider="gemini",
                    model="gemini-2.5-flash",
                )
                assert seat is not None
                assert seat.review_count == i
                assert seat.managed_review_count == i

            # Bot author opens PR -> recorded in usage events, NEVER billed as a seat
            bot_seat = await record_completed_review(
                db,
                workspace=ws,
                event_id="job_bot_1",
                repo_full_name="scale-team/api",
                is_private=True,
                pr_number=10,
                github_author_id=9999,
                github_author_login="dependabot[bot]",
                author_type="Bot",
                key_mode="managed",
            )
            assert bot_seat is None

            # Author B opens 1 PR -> 2nd unique seat (now at seat_cap=2)
            seat_b = await record_completed_review(
                db,
                workspace=ws,
                event_id="job_b_1",
                repo_full_name="scale-team/api",
                is_private=True,
                pr_number=11,
                github_author_id=102,
                github_author_login="bob",
                key_mode="managed",
            )
            assert seat_b is not None

            # Author A (already counted this cycle) is STILL allowed under seat_cap=2
            d_a_again = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=101, github_author_login="alice", has_byok_key=False
            )
            assert d_a_again.allowed is True

            # Author C (3rd new developer) is BLOCKED by seat_cap=2
            d_c_blocked = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=103, github_author_login="charlie", has_byok_key=False
            )
            assert d_c_blocked.allowed is False
            assert d_c_blocked.reason_code == "seat_cap_reached"
    finally:
        await _clean_billing_tables()


@pytest.mark.asyncio
async def test_polar_checkout_portal_and_outbox_retry(monkeypatch):
    """Verify Polar Checkout, Customer Portal, and idempotent outbox retry after transient 503."""
    await _clean_billing_tables()
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "POLAR_ACCESS_TOKEN", "polar_oat_test_token")
    monkeypatch.setattr(settings, "POLAR_PRO_PRODUCT_ID", "prod_patchpilot_pro")

    ws_id = workspace_id_for_account(88004)
    now = datetime.now(timezone.utc)
    captured_requests: list[tuple[str, dict]] = []
    ingest_attempts = 0

    def mock_polar_transport(request: httpx.Request) -> httpx.Response:
        nonlocal ingest_attempts
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append((request.url.path, body))
        if request.url.path == "/v1/checkouts/":
            return httpx.Response(201, json={"url": "https://sandbox.polar.sh/checkout/chk_123"})
        if request.url.path == "/v1/customer-sessions/":
            return httpx.Response(201, json={"customer_portal_url": "https://sandbox.polar.sh/portal/ses_123"})
        if request.url.path == "/v1/events/ingest":
            ingest_attempts += 1
            if ingest_attempts == 1:
                return httpx.Response(503, json={"detail": "Temporary Polar outage"})
            return httpx.Response(200, json={"inserted": 1})
        return httpx.Response(404)

    try:
        async with SessionLocal() as db:
            ws = Workspace(
                id=ws_id,
                github_account_id=88004,
                github_login="polar-org",
                account_type="Organization",
                github_installation_id=44004,
                installation_status="active",
                key_mode="managed",
                plan_tier="pro",
                subscription_status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                polar_customer_id="cus_polar_999",
            )
            db.add(ws)
            await db.commit()

            await record_completed_review(
                db,
                workspace=ws,
                event_id="job_polar_1",
                repo_full_name="polar-org/core",
                is_private=True,
                pr_number=5,
                github_author_id=501,
                github_author_login="dev-one",
                key_mode="managed",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(mock_polar_transport)) as mock_http:
            polar_client = PolarBillingClient(
                access_token="polar_oat_test_token",
                base_url="https://sandbox-api.polar.sh",
                pro_product_id="prod_patchpilot_pro",
                client=mock_http,
            )

            async with SessionLocal() as db:
                ws = await db.get(Workspace, ws_id)
                chk_url = await polar_client.create_checkout_url(
                    workspace=ws,
                    customer_email="founder@polar-org.dev",
                    success_url="https://app.example.com/dashboard?checkout=success",
                )
                assert chk_url == "https://sandbox.polar.sh/checkout/chk_123"

                portal_url = await polar_client.create_portal_url(workspace=ws)
                assert portal_url == "https://sandbox.polar.sh/portal/ses_123"

            # First outbox flush hits 503 -> 0 reported, seat remains queued
            n1 = await flush_unreported_seat_events(billing_client=polar_client)
            assert n1 == 0

            # Second outbox flush succeeds -> 1 reported, meter_reported_at populated
            n2 = await flush_unreported_seat_events(billing_client=polar_client)
            assert n2 == 1

            # Third outbox flush sends nothing (idempotent)
            n3 = await flush_unreported_seat_events(billing_client=polar_client)
            assert n3 == 0

            ingest_payloads = [body for path, body in captured_requests if path == "/v1/events/ingest"]
            assert len(ingest_payloads) == 2
            event_sent = ingest_payloads[-1]["events"][0]
            assert event_sent["name"] == settings.POLAR_METER_EVENT_NAME
            assert event_sent["customer_id"] == "cus_polar_999"
            assert event_sent["metadata"]["github_author_id"] == "501"
            assert event_sent["external_id"].startswith(f"seat:{ws_id}:")
    finally:
        await _clean_billing_tables()


def test_build_managed_vault_config(monkeypatch):
    """Verify build_managed_vault_config constructs encrypted primary + fallback pool from settings."""
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_API_KEY", "AIzaSyManagedPrimary12345")
    monkeypatch.setattr(settings, "MANAGED_LLM_FALLBACK_PROVIDER", "groq")
    monkeypatch.setattr(settings, "MANAGED_LLM_FALLBACK_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setattr(settings, "MANAGED_LLM_FALLBACK_API_KEY", "gsk_ManagedFallback67890")

    cfg = build_managed_vault_config("acme-org", review_policy="# Custom Policy")
    assert cfg is not None
    assert cfg.provider == "gemini"
    assert cfg.model == "gemini-2.5-flash"
    assert cfg.review_policy == "# Custom Policy"
    assert "AIzaSyManagedPrimary12345" not in cfg.encrypted_api_key
    fallbacks = json.loads(cfg.fallbacks_json)
    assert len(fallbacks) == 1
    assert fallbacks[0]["provider"] == "groq"
    assert "gsk_ManagedFallback67890" not in fallbacks[0]["encrypted_api_key"]


@pytest.mark.asyncio
async def test_handle_pr_event_blocks_unsubscribed_and_meters_pro_managed(monkeypatch):
    """End-to-end check: unsubscribed PR never calls LLM; subscribed Pro PR runs Managed AI and records active committer."""
    from unittest.mock import AsyncMock
    from app.main import handle_pr_event
    from app.security import global_rate_limiter

    await _clean_billing_tables()
    global_rate_limiter.reset()
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_API_KEY", "AIzaSyManagedE2EKey12345")

    ws_id = workspace_id_for_account(88005)
    pr_payload = {
        "installation": {"id": 44005},
        "repository": {
            "full_name": "acme-cloud/backend",
            "private": True,
            "owner": {"id": 88005, "login": "acme-cloud", "type": "Organization"},
        },
        "pull_request": {
            "number": 7,
            "title": "Add billing guard",
            "body": "Enforces active committer billing",
            "additions": 3,
            "deletions": 1,
            "author_association": "MEMBER",
            "head": {"sha": "deadbeef123", "repo": {"fork": False}},
            "user": {"id": 9001, "login": "maya", "type": "User"},
        },
    }

    notice_mock = AsyncMock()
    llm_mock = AsyncMock(return_value={
        "summary": "Looks solid.",
        "verdict": "PASS",
        "findings": [],
        "_provider": "gemini",
        "_model": "gemini-2.5-flash",
        "_usage": {"prompt_tokens": 120, "completion_tokens": 45},
    })
    monkeypatch.setattr("app.main._post_entitlement_blocked_notice", notice_mock)
    monkeypatch.setattr("app.main.run_pr_review", llm_mock)
    monkeypatch.setattr("app.main.get_installation_token", AsyncMock(return_value="ghs_inst_token"))
    monkeypatch.setattr("app.main.fetch_pr_metadata", AsyncMock(return_value={"head": {"sha": "deadbeef123"}, "state": "open"}))
    monkeypatch.setattr("app.main.create_pending_pr_review", AsyncMock(return_value={"kind": "review", "id": 11}))
    monkeypatch.setattr("app.main.start_check_indicator", AsyncMock(return_value={"head_sha": "deadbeef123"}))
    monkeypatch.setattr("app.main.fetch_pr_diff", AsyncMock(return_value="diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n x = 1\n+y = 2\n"))
    monkeypatch.setattr("app.main.apply_pr_labels", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.main.update_pr_review_card", AsyncMock(return_value=True))
    monkeypatch.setattr("app.main.publish_pr_review", AsyncMock(return_value={"status": "updated_in_place"}))
    monkeypatch.setattr("app.main.complete_check_indicator", AsyncMock())
    monkeypatch.setattr("app.main.flush_unreported_seat_events", AsyncMock(return_value=1))

    try:
        # 1. Unsubscribed org PR -> blocked with notice, LLM is NEVER invoked
        await handle_pr_event(pr_payload)
        notice_mock.assert_awaited_once()
        llm_mock.assert_not_awaited()

        # 2. Activate Pro subscription in Managed AI mode (no customer BYOK key!)
        async with SessionLocal() as db:
            ws = await db.get(Workspace, ws_id)
            assert ws is not None
            ws.plan_tier = "pro"
            ws.subscription_status = "active"
            ws.key_mode = "managed"
            await db.commit()

        await handle_pr_event(pr_payload)
        llm_mock.assert_awaited_once()

        async with SessionLocal() as db:
            seats = (await db.execute(
                select(WorkspaceActiveCommitter).where(WorkspaceActiveCommitter.workspace_id == ws_id)
            )).scalars().all()
            assert len(seats) == 1
            assert seats[0].github_author_id == 9001
            assert seats[0].github_author_login == "maya"
            assert seats[0].managed_review_count == 1

            usage_rows = (await db.execute(
                select(ReviewUsageEvent).where(ReviewUsageEvent.workspace_id == ws_id)
            )).scalars().all()
            assert len(usage_rows) == 1
            assert usage_rows[0].key_mode == "managed"
            assert usage_rows[0].prompt_tokens == 120
            assert usage_rows[0].completion_tokens == 45
    finally:
        await _clean_billing_tables()

