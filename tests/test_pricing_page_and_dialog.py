"""Tests for pricing page rendering, edition cards, comparison table, and teaser."""

from __future__ import annotations

import os
os.environ["TESTING"] = "1"

import httpx
import pytest
from app.config import settings
from app.editions import TEAM_ANNUAL_RECONCILIATION_RULE, TEAM_PRODUCT_SEAT_CEILING
from app.main import app


@pytest.mark.asyncio
async def test_pricing_page_rendering():
    """Verify /pricing renders all 4 editions, decided prices, units, and semantic matrix."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/pricing")
        assert resp.status_code == 200
        html = resp.text

        # 4 Edition Cards
        assert "Self-Hosted" in html
        assert "Solo" in html
        assert "Team" in html
        assert "Enterprise" in html

        # Decided Prices and Units
        assert "$0" in html
        assert "license fee" in html
        assert "$9" in html
        assert "/month" in html
        assert "$15" in html
        assert "per active developer/month" in html
        assert "Custom" in html

        # Semantic comparison table groups
        assert "Limits &amp; Billing" in html or "Limits & Billing" in html
        assert "Core PR Review Engine" in html
        assert "AI Models &amp; Vault" in html or "AI Models & Vault" in html
        assert "Governance, Hosting &amp; Support" in html or "Governance, Hosting & Support" in html

        # Contact Dialog & fields
        assert 'id="contactDialog"' in html
        assert 'name="team_size"' in html
        assert 'name="deployment_pref"' in html
        assert 'name="website"' in html  # honeypot
        assert 'id="submitBtn"' in html

        # Annual Team reconciliation rule
        assert "reconciliation" in html.lower()


@pytest.mark.asyncio
async def test_pricing_page_annual_cycle():
    """Verify /pricing?cycle=annual renders annual amounts and monthly equivalents."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/pricing?cycle=annual")
        assert resp.status_code == 200
        html = resp.text

        # Annual prices
        assert "$84" in html
        assert "$7/mo equivalent" in html
        assert "$144" in html
        assert "$12/mo equivalent" in html


@pytest.mark.asyncio
async def test_overview_editions_teaser():
    """Verify root page (/) contains the 4-edition teaser pointing to /pricing."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        html = resp.text

        assert "Editions for every team" in html
        assert 'href="/pricing"' in html
        assert "Self-Hosted" in html
        assert "Solo" in html
        assert "Team" in html
        assert "Enterprise" in html


@pytest.mark.asyncio
async def test_api_editions_endpoint():
    """Verify GET /api/editions returns valid JSON catalog and comparison groups."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/editions?cycle=monthly")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cycle"] == "monthly"
        assert len(data["editions"]) == 4
        assert len(data["comparison_groups"]) == 4
        assert data["team_seat_ceiling"] == TEAM_PRODUCT_SEAT_CEILING
        assert "reconcile" in data["team_reconciliation_rule"].lower()


@pytest.mark.asyncio
async def test_public_pages_tenant_neutrality():
    """Verify /, /pricing, /docs, /privacy, and /terms do not leak tenant/author specifics."""
    forbidden = ["89Aman", "noventra.org.in", "patchpilot-app", "adminToken"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in ["/", "/pricing", "/docs", "/privacy", "/terms"]:
            resp = await client.get(path)
            assert resp.status_code == 200, f"Failed on path {path}"
            for item in forbidden:
                assert item not in resp.text, f"Forbidden '{item}' found in rendered HTML of {path}"
