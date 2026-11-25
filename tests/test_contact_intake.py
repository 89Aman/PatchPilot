"""Tests for PatchPilot Contact Sales intake, routing, honeypot deflection, and rate limiting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
os.environ["TESTING"] = "1"

import httpx
import pytest
from sqlalchemy import delete, func, select
from app.config import settings
from app.contact import resolve_client_ip
from app.db import (
    ContactInquiry,
    ContactRateLimit,
    SessionLocal,
    init_db,
)
from app.main import app


@pytest.fixture(autouse=True)
async def clean_contact_db():
    await init_db()
    async with SessionLocal() as db:
        await db.execute(delete(ContactInquiry))
        await db.execute(delete(ContactRateLimit))
        await db.commit()
    yield
    async with SessionLocal() as db:
        await db.execute(delete(ContactInquiry))
        await db.execute(delete(ContactRateLimit))
        await db.commit()


@pytest.mark.asyncio
async def test_team_inquiry_within_team_boundary():
    """Team inquiry for 2-5 active developers stays on Team plan."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/contact",
            json={
                "requested_plan": "team",
                "name": "Jane Doe",
                "email": "jane@example.com",
                "company_or_org": "Acme Corp",
                "team_size": "2-5",
                "billing_cycle": "monthly",
                "deployment_pref": "hosted_cloud",
                "message": "Interested in Team pilot for 4 developers.",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert data["requested_plan"] == "team"
        assert data["routed_plan"] == "team"
        assert data["upgraded_to_enterprise"] is False
        assert data["id"].startswith("inq_")

        # Verify DB persistence
        async with SessionLocal() as db:
            inq = await db.get(ContactInquiry, data["id"])
            assert inq is not None
            assert inq.requested_plan == "team"
            assert inq.routed_plan == "team"
            assert inq.name == "Jane Doe"
            assert inq.company_or_org == "Acme Corp"
            assert inq.status == "new"


@pytest.mark.asyncio
async def test_team_inquiry_exceeding_ceiling_routes_to_enterprise():
    """Team inquiry with >= 6 developers automatically recommends and routes to Enterprise."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/contact",
            json={
                "requested_plan": "team",
                "name": "Tech Lead",
                "email": "lead@bigteam.io",
                "company_or_org": "BigTeam Inc",
                "team_size": "6-15",
                "billing_cycle": "annual",
                "deployment_pref": "byok_cloud",
                "message": "We have 10 developers on the repo.",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert data["requested_plan"] == "team"
        assert data["routed_plan"] == "enterprise"
        assert data["upgraded_to_enterprise"] is True

        # Verify DB preserves initial Team interest while routing to enterprise
        async with SessionLocal() as db:
            inq = await db.get(ContactInquiry, data["id"])
            assert inq is not None
            assert inq.requested_plan == "team"
            assert inq.routed_plan == "enterprise"


@pytest.mark.asyncio
async def test_explicit_enterprise_inquiry_stays_enterprise():
    """Explicit Enterprise inquiry at any team size (even 2-5) stays Enterprise."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/contact",
            json={
                "requested_plan": "enterprise",
                "name": "SecOps Director",
                "email": "secops@fintech.com",
                "company_or_org": "Fintech Global",
                "team_size": "2-5",
                "billing_cycle": "annual",
                "deployment_pref": "dedicated_vpc",
                "message": "Need dedicated VPC and custom SLA for regulated environment.",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True
        assert data["requested_plan"] == "enterprise"
        assert data["routed_plan"] == "enterprise"
        assert data["upgraded_to_enterprise"] is False


@pytest.mark.asyncio
async def test_validation_errors():
    """Invalid payload fields return 422 Unprocessable Entity."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Invalid email
        r1 = await client.post(
            "/api/contact",
            json={
                "requested_plan": "team",
                "name": "Alex",
                "email": "not-an-email",
                "company_or_org": "Org",
                "team_size": "2-5",
                "billing_cycle": "monthly",
                "deployment_pref": "hosted_cloud",
            },
        )
        assert r1.status_code == 422

        # Blank name
        r2 = await client.post(
            "/api/contact",
            json={
                "requested_plan": "team",
                "name": "   ",
                "email": "alex@example.com",
                "company_or_org": "Org",
                "team_size": "2-5",
                "billing_cycle": "monthly",
                "deployment_pref": "hosted_cloud",
            },
        )
        assert r2.status_code == 422

        # Notes > 2000 chars
        r3 = await client.post(
            "/api/contact",
            json={
                "requested_plan": "team",
                "name": "Alex",
                "email": "alex@example.com",
                "company_or_org": "Org",
                "team_size": "2-5",
                "billing_cycle": "monthly",
                "deployment_pref": "hosted_cloud",
                "message": "x" * 2001,
            },
        )
        assert r3.status_code == 422


@pytest.mark.asyncio
async def test_honeypot_deflection():
    """Honeypot field deflection returns 200 with zero persistence or rate limit tracking."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/contact",
            json={
                "requested_plan": "team",
                "name": "Bot Spammer",
                "email": "spammer@botnet.ru",
                "company_or_org": "Spam Corp",
                "team_size": "2-5",
                "billing_cycle": "monthly",
                "deployment_pref": "hosted_cloud",
                "website": "http://spam-link.com",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["message"] == "Inquiry received"

        # Assert no DB row was stored
        async with SessionLocal() as db:
            count = await db.scalar(select(func.count()).select_from(ContactInquiry))
            assert count == 0
            rl_count = await db.scalar(select(func.count()).select_from(ContactRateLimit))
            assert rl_count == 0


@pytest.mark.asyncio
async def test_shared_rate_limiting_across_sessions():
    """Enforce 5 inquiries per 15 minutes per IP; 6th attempt returns 429 across independent sessions."""
    transport = httpx.ASGITransport(app=app)

    # First session submits 3 requests from IP 198.51.100.1
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client1:
        for i in range(3):
            r = await client1.post(
                "/api/contact",
                headers={"X-Forwarded-For": "198.51.100.1"},
                json={
                    "requested_plan": "team",
                    "name": f"User {i}",
                    "email": f"user{i}@example.com",
                    "company_or_org": "Example LLC",
                    "team_size": "2-5",
                    "billing_cycle": "monthly",
                    "deployment_pref": "hosted_cloud",
                },
            )
            assert r.status_code == 201

    # Second session submits 2 more requests from the same IP (total 5)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client2:
        for i in range(3, 5):
            r = await client2.post(
                "/api/contact",
                headers={"X-Forwarded-For": "198.51.100.1"},
                json={
                    "requested_plan": "team",
                    "name": f"User {i}",
                    "email": f"user{i}@example.com",
                    "company_or_org": "Example LLC",
                    "team_size": "2-5",
                    "billing_cycle": "monthly",
                    "deployment_pref": "hosted_cloud",
                },
            )
            assert r.status_code == 201

        # 6th request from IP 198.51.100.1 MUST be rate-limited (429)
        r6 = await client2.post(
            "/api/contact",
            headers={"X-Forwarded-For": "198.51.100.1"},
            json={
                "requested_plan": "team",
                "name": "User 6",
                "email": "user6@example.com",
                "company_or_org": "Example LLC",
                "team_size": "2-5",
                "billing_cycle": "monthly",
                "deployment_pref": "hosted_cloud",
            },
        )
        assert r6.status_code == 429
        assert "too many" in r6.json()["detail"].lower()

        # Distinct independent client IP is NOT blocked
        r_other = await client2.post(
            "/api/contact",
            headers={"X-Forwarded-For": "203.0.113.88"},
            json={
                "requested_plan": "team",
                "name": "Different User",
                "email": "other@example.com",
                "company_or_org": "Other Org",
                "team_size": "2-5",
                "billing_cycle": "monthly",
                "deployment_pref": "hosted_cloud",
            },
        )
        assert r_other.status_code == 201


@pytest.mark.asyncio
async def test_trusted_proxy_ingress_resolution():
    """Verify trusted proxy resolution and spoof protection."""
    # When peer is trusted proxy (e.g. 127.0.0.1)
    class DummyRequest:
        def __init__(self, host: str, headers: dict[str, str]):
            self.client = type("Client", (), {"host": host})()
            self.headers = headers

    # 1. Trusted proxy resolves client from X-Forwarded-For
    req_trusted = DummyRequest("127.0.0.1", {"x-forwarded-for": "198.51.100.42, 127.0.0.1"})
    assert resolve_client_ip(req_trusted, trusted_proxies={"127.0.0.1", "::1"}) == "198.51.100.42"

    # 2. CF-Connecting-IP takes precedence behind trusted Cloudflare proxy
    req_cf = DummyRequest("127.0.0.1", {"cf-connecting-ip": "203.0.113.99", "x-forwarded-for": "198.51.100.42"})
    assert resolve_client_ip(req_cf, trusted_proxies={"127.0.0.1"}) == "203.0.113.99"

    # 3. Untrusted direct client IP ignores spoofed X-Forwarded-For
    req_untrusted = DummyRequest("192.168.1.50", {"x-forwarded-for": "1.1.1.1"})
    assert resolve_client_ip(req_untrusted, trusted_proxies={"127.0.0.1"}) == "192.168.1.50"


@pytest.mark.asyncio
async def test_webhook_failure_preserves_inquiry(monkeypatch):
    """When webhook notification fails (e.g. network failure), inquiry is still preserved."""
    monkeypatch.setattr(settings, "CONTACT_WEBHOOK_URL", "http://unreachable-webhook.local:9999/notify")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/contact",
            json={
                "requested_plan": "team",
                "name": "Reliable Client",
                "email": "reliable@org.com",
                "company_or_org": "Reliable Org",
                "team_size": "2-5",
                "billing_cycle": "annual",
                "deployment_pref": "hosted_cloud",
                "message": "Preserve my inquiry even if webhook crashes.",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True

        # Inquiry MUST exist in database
        async with SessionLocal() as db:
            inq = await db.get(ContactInquiry, data["id"])
            assert inq is not None
            assert inq.email == "reliable@org.com"
