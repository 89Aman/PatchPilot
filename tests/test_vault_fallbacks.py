"""Unit tests for BYOK Vault Key Masking, Fallbacks CRUD, Sequential Multi-Provider Failover, and Free-Tier Catalog."""

from __future__ import annotations

import json
import os
os.environ["TESTING"] = "1"

import httpx
import pytest
from app.config import settings
from app.crypto import encrypt_secret
from app.db import VaultConfig, _migrate_vault_columns, engine
from app.github_auth import build_review_summary_card
from app.llm import (
    FALLBACK_TRIGGER_STATUS_CODES,
    get_provider_candidates,
    run_pr_review,
    sanitize_failure_reason,
)
from app.main import app, mask_api_key
from app.models_catalog import OFFICIAL_FREE_MODELS, get_free_models_catalog


# -----------------------------------------------------------------------------
# 1. Key Masking Tests (Short & Standard Keys)
# -----------------------------------------------------------------------------

def test_mask_api_key_empty_or_none():
    """Verify empty or None keys return safe default."""
    assert mask_api_key(None) == "Not configured"
    assert mask_api_key("") == "Not configured"
    assert mask_api_key("   ") == "Not configured"


def test_mask_api_key_short_or_dummy_keys():
    """Verify short/dummy keys (<= 10 chars) are protected against double-reveal slicing."""
    assert mask_api_key("ollama") == "ol••••••••"
    assert mask_api_key("none") == "no••••••••"
    assert mask_api_key("local123") == "lo••••••••"
    assert mask_api_key("1234567890") == "12••••••••"


def test_mask_api_key_standard_keys():
    """Verify standard keys (> 10 chars) preserve first 6 and last 4 characters."""
    masked_groq = mask_api_key("gsk_1234567890abcdef")
    assert masked_groq == "gsk_12••••••••cdef"

    masked_gemini = mask_api_key("AIzaSyD1234567890abcdef")
    assert masked_gemini == "AIzaSy••••••••cdef"


# -----------------------------------------------------------------------------
# 2. Free Models Catalog Tests
# -----------------------------------------------------------------------------

def test_free_models_catalog_structure():
    """Verify official catalog contains valid models across major free-tier providers."""
    catalog = get_free_models_catalog()
    assert len(catalog) >= 6

    providers = {m["provider"] for m in catalog}
    assert "gemini" in providers
    assert "groq" in providers
    assert "openrouter" in providers
    assert "ollama" in providers

    for item in catalog:
        assert "id" in item
        assert "name" in item
        assert "quota_summary" in item
        assert item["is_free"] is True
        assert item["key_url"].startswith("http")


# -----------------------------------------------------------------------------
# 3. SQLite ALTER TABLE Compatibility
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sqlite_alter_table_migration():
    """Verify _migrate_vault_columns runs safely without SQLite syntax error."""
    async with engine.begin() as conn:
        await conn.run_sync(_migrate_vault_columns)


# -----------------------------------------------------------------------------
# 4. Fallback Trigger Status Codes & Failure Sanitization
# -----------------------------------------------------------------------------

def test_fallback_trigger_codes():
    """Verify all critical error codes trigger failover."""
    expected = {401, 402, 403, 404, 429, 500, 502, 503, 529}
    assert expected.issubset(FALLBACK_TRIGGER_STATUS_CODES)


def test_sanitize_failure_reason():
    """Verify HTTP exceptions and codes map to safe, sanitized strings without token leaks."""
    assert sanitize_failure_reason(429) == "Rate limit exceeded (429)"
    assert sanitize_failure_reason(402) == "Daily free quota exhausted (402)"
    assert sanitize_failure_reason(401) == "Authentication failed (401)"
    assert sanitize_failure_reason(403) == "Access forbidden (403)"
    assert sanitize_failure_reason(404) == "Model not found or retired (404)"
    assert sanitize_failure_reason(529) == "Provider overloaded (529)"
    assert sanitize_failure_reason(503) == "Upstream provider outage (503)"
    assert sanitize_failure_reason(None, httpx.ReadTimeout("timed out")) == "Provider connection timeout"
    assert sanitize_failure_reason(None, httpx.ConnectError("refused")) == "Provider network connection failed"


# -----------------------------------------------------------------------------
# 5. Vault Fallback CRUD & Promote-to-Primary Swap Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vault_fallbacks_crud_and_primary_swap():
    """Test full CRUD lifecycle for fallbacks, key masking, and promote-to-primary swap."""
    owner = "fallback-test-org"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Setup primary vault
        put_res = await client.put(
            f"/vault/{owner}",
            headers={"X-Admin-Token": settings.ADMIN_API_TOKEN},
            json={
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "api_key": "AIzaSyD_PrimarySecretKey12345",
                "review_policy": "standard",
            },
        )
        assert put_res.status_code == 200
        assert put_res.json()["provider"] == "gemini"
        assert put_res.json()["masked_api_key"].startswith("AIzaSy")

        # 2. Add fallback
        fb_res = await client.post(
            f"/vault/{owner}/fallbacks",
            headers={"X-Admin-Token": settings.ADMIN_API_TOKEN},
            json={
                "name": "Groq Secondary",
                "provider": "groq",
                "model": "llama-3.3-70b-versatile",
                "api_key": "gsk_GroqSecretKey9876543210",
                "is_enabled": True,
            },
        )
        assert fb_res.status_code == 200
        fb_data = fb_res.json()
        assert fb_data["status"] == "ok"
        assert len(fb_data["fallbacks"]) == 1
        fb_item = fb_data["fallbacks"][0]
        assert fb_item["name"] == "Groq Secondary"
        assert fb_item["masked_api_key"].startswith("gsk_Gr")
        fallback_id = fb_item["id"]

        # 3. GET /vault/{owner} returns primary and fallbacks list with masked keys
        get_res = await client.get(
            f"/vault/{owner}",
            headers={"X-Admin-Token": settings.ADMIN_API_TOKEN},
        )
        assert get_res.status_code == 200
        vault_state = get_res.json()
        assert len(vault_state["fallbacks"]) == 1
        assert vault_state["fallbacks"][0]["id"] == fallback_id
        assert vault_state["fallbacks"][0]["masked_api_key"].startswith("gsk_Gr")
        assert len(vault_state["free_models"]) > 0

        # 4. Promote fallback to primary
        swap_res = await client.post(
            f"/vault/{owner}/fallbacks/{fallback_id}/set-primary",
            headers={"X-Admin-Token": settings.ADMIN_API_TOKEN},
        )
        assert swap_res.status_code == 200
        swap_data = swap_res.json()
        assert swap_data["provider"] == "groq"
        assert swap_data["model"] == "llama-3.3-70b-versatile"
        assert swap_data["masked_api_key"].startswith("gsk_Gr")

        # Verify old primary is now demoted into fallbacks with auto-generated uuid and label
        assert len(swap_data["fallbacks"]) == 1
        demoted = swap_data["fallbacks"][0]
        assert demoted["provider"] == "gemini"
        assert demoted["name"] == "Previous Primary (Gemini)"
        assert demoted["masked_api_key"].startswith("AIzaSy")
        demoted_id = demoted["id"]

        # 5. Delete demoted fallback
        del_res = await client.delete(
            f"/vault/{owner}/fallbacks/{demoted_id}",
            headers={"X-Admin-Token": settings.ADMIN_API_TOKEN},
        )
        assert del_res.status_code == 200
        assert del_res.json()["deleted"] is True

        # Verify fallbacks list is empty
        final_get = await client.get(
            f"/vault/{owner}",
            headers={"X-Admin-Token": settings.ADMIN_API_TOKEN},
        )
        assert len(final_get.json()["fallbacks"]) == 0


# -----------------------------------------------------------------------------
# 6. Sequential Multi-Provider Failover in run_pr_review
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_pr_review_sequential_failover_429():
    """Verify run_pr_review fails over to secondary on HTTP 429 and records sanitized fallback_used."""
    raw_primary_key = "AIzaSyD_Primary_12345"
    raw_fallback_key = "gsk_Fallback_67890"

    cfg = VaultConfig(
        owner="test-failover",
        provider="gemini",
        model="gemini-2.5-flash",
        encrypted_api_key=encrypt_secret(raw_primary_key),
        fallbacks_json=json.dumps([
            {
                "id": "fb-groq-1",
                "name": "Groq Backup",
                "provider": "groq",
                "model": "llama-3.3-70b-versatile",
                "encrypted_api_key": encrypt_secret(raw_fallback_key),
                "is_enabled": True,
            }
        ]),
    )

    valid_review_json = json.dumps({
        "summary": "Review completed via fallback provider.",
        "findings": [],
        "verdict": "PASS",
        "walkthrough": [],
    })

    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Primary returns 429 Rate Limit
            return httpx.Response(
                429,
                json={"error": {"message": "Resource has been exhausted"}},
                request=request,
            )
        # Secondary returns 200 Success
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": valid_review_json}}]},
            request=request,
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
-def foo(): pass
+def foo(): return 42
"""
    result = await run_pr_review(cfg, "Test PR", "Test Description", diff, client=mock_client)

    assert call_count == 2
    assert result["verdict"] == "PASS"
    assert "fallback_used" in result
    assert result["fallback_used"]["provider"] == "groq"
    assert result["fallback_used"]["model"] == "llama-3.3-70b-versatile"
    assert result["fallback_used"]["primary_reason"] == "Rate limit exceeded (429)"


@pytest.mark.asyncio
async def test_run_pr_review_sequential_failover_timeout():
    """Verify run_pr_review fails over on network timeout and records sanitized primary reason."""
    raw_primary_key = "AIzaSyD_Primary_12345"
    raw_fallback_key = "gsk_Fallback_67890"

    cfg = VaultConfig(
        owner="test-timeout-org",
        provider="gemini",
        model="gemini-2.5-flash",
        encrypted_api_key=encrypt_secret(raw_primary_key),
        fallbacks_json=json.dumps([
            {
                "id": "fb-groq-2",
                "name": "Groq Backup",
                "provider": "groq",
                "model": "llama-3.3-70b-versatile",
                "encrypted_api_key": encrypt_secret(raw_fallback_key),
                "is_enabled": True,
            }
        ]),
    )

    valid_review_json = json.dumps({
        "summary": "Review completed after primary timeout.",
        "findings": [],
        "verdict": "PASS",
        "walkthrough": [],
    })

    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ReadTimeout("Request timed out", request=request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": valid_review_json}}]},
            request=request,
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))

    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
-x = 1
+x = 2
"""
    result = await run_pr_review(cfg, "Test PR", "Test Description", diff, client=mock_client)

    assert call_count == 2
    assert result["verdict"] == "PASS"
    assert "fallback_used" in result
    assert result["fallback_used"]["provider"] == "groq"
    assert result["fallback_used"]["primary_reason"] == "Provider connection timeout"


# -----------------------------------------------------------------------------
# 7. PR Review Summary Card Failover Alert Notice
# -----------------------------------------------------------------------------

def test_summary_card_failover_notice_sanitized():
    """Verify build_review_summary_card renders sanitized failover badge."""
    card = build_review_summary_card(
        verdict="PASS",
        commit_sha="c0ffee1",
        summary="Clean PR with fallback review.",
        bug_count=0,
        risk_count=0,
        nit_count=0,
        fallback_used={
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "primary_reason": "Rate limit exceeded (429)",
        },
    )

    assert "⚡ **Failover Alert**" in card
    assert "Primary LLM returned *Rate limit exceeded (429)*" in card
    assert "Review successfully completed via fallback: **Groq** (`llama-3.3-70b-versatile`)" in card
    # Ensure no raw URLs, auth tokens or secrets leak into card
    assert "http" not in card
    assert "Authorization" not in card
    assert "gsk_" not in card
