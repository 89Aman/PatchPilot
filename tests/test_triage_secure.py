"""Unit tests for PatchPilot Triage and PatchPilot Secure engines."""

from __future__ import annotations

import httpx
import pytest
from app.github_auth import apply_pr_labels, build_review_summary_card
from app.security import mask_secret, sanitize_diff_text, scan_diff_for_secrets
from app.triage import compute_pr_triage, compute_size_tier


# -----------------------------------------------------------------------------
# 1. PatchPilot Secure (Secret Scanner & Diff Sanitizer) Tests
# -----------------------------------------------------------------------------

def test_mask_secret():
    """Verify masking utility conceals secret bodies while retaining small hints."""
    raw = "AKIAIOSFODNN7EXAMPLE"
    masked = mask_secret(raw)
    assert "AKIA" in masked
    assert "LE" in masked
    assert "*" in masked
    assert len(masked) == len(raw)

    short_secret = "12345"
    assert mask_secret(short_secret) == "*****"


def test_secret_scanner_detection():
    """Verify scanner flags AWS, GitHub, OpenAI, and Private Keys in diff additions."""
    diff_with_secrets = """diff --git a/app/config.py b/app/config.py
--- a/app/config.py
+++ b/app/config.py
@@ -10,3 +10,8 @@
+AWS_KEY = "AKIA1234567890ABCDEF"
+GITHUB_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
+OPENAI_KEY = "sk-proj-1234567890abcdefghijklmnopqrstuvwxyz12"
+KEY = "-----BEGIN RSA PRIVATE KEY-----"
-OLD_KEY = "none"
"""
    findings = scan_diff_for_secrets(diff_with_secrets)
    assert len(findings) == 4

    types = {f.secret_type for f in findings}
    assert "AWS Access Key ID" in types
    assert "GitHub Personal Access Token" in types
    assert "OpenAI API Key" in types
    assert "Private Encryption Key" in types

    for f in findings:
        assert f.file_path == "app/config.py"
        assert f.severity == "CRITICAL"
        assert "*" in f.matched_text_masked


def test_secret_scanner_ignores_placeholders():
    """Verify scanner ignores common placeholder/dummy strings."""
    diff_with_dummy = """diff --git a/docs/README.md b/docs/README.md
--- a/docs/README.md
+++ b/docs/README.md
@@ -1,3 +1,3 @@
+# Example config
+export OPENAI_API_KEY="your_api_key_here"
+export AWS_KEY="dummy_key_example"
"""
    findings = scan_diff_for_secrets(diff_with_dummy)
    assert len(findings) == 0


def test_diff_sanitizer():
    """Verify that sensitive secrets are sanitized before external model transmission."""
    diff_text = 'KEY = "AKIA1234567890ABCDEF"'
    findings = scan_diff_for_secrets("+++ b/file.py\n+KEY = \"AKIA1234567890ABCDEF\"")
    assert len(findings) == 1

    sanitized = sanitize_diff_text(diff_text, findings)
    assert "AKIA1234567890ABCDEF" not in sanitized
    assert "[REDACTED_AWS_ACCESS_KEY_ID]" in sanitized


# -----------------------------------------------------------------------------
# 2. PatchPilot Triage Tests
# -----------------------------------------------------------------------------

def test_triage_size_tiers():
    """Verify standard GitHub PR sizing tiers."""
    assert compute_size_tier(25) == "size:XS"
    assert compute_size_tier(150) == "size:S"
    assert compute_size_tier(350) == "size:M"
    assert compute_size_tier(800) == "size:L"
    assert compute_size_tier(1500) == "size:XL"


def test_triage_risk_low_docs():
    """Verify documentation-only changes receive low risk assessment."""
    triage = compute_pr_triage(
        files_changed=["README.md", "docs/architecture.md"],
        additions=20,
        deletions=5,
        has_secret_findings=False,
    )
    assert triage.size_tier == "size:XS"
    assert triage.risk_level == "risk:low"
    assert triage.risk_score <= 20
    assert "size:XS" in triage.labels
    assert "risk:low" in triage.labels


def test_triage_risk_high_security():
    """Verify changes to auth/crypto and database paths elevate risk level."""
    triage = compute_pr_triage(
        files_changed=["app/crypto.py", "app/auth.py", "alembic/versions/001_init.py"],
        additions=180,
        deletions=40,
        has_secret_findings=False,
    )
    assert triage.size_tier == "size:M"
    assert triage.risk_level in ("risk:high", "risk:critical")
    assert "Auth & Cryptography" in triage.impact_areas
    assert "Database & Migrations" in triage.impact_areas


def test_triage_secret_leak_escalation():
    """Verify secret findings immediately escalate triage to critical with alert label."""
    triage = compute_pr_triage(
        files_changed=["app/main.py"],
        additions=10,
        deletions=2,
        has_secret_findings=True,
    )
    assert triage.risk_level == "risk:critical"
    assert triage.risk_score >= 95
    assert "security:alert" in triage.labels


# -----------------------------------------------------------------------------
# 3. Label Dispatcher & Summary Card Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_pr_labels_api(monkeypatch):
    """Verify apply_pr_labels formats payload and handles GitHub API response."""
    captured = {}

    async def mock_post(url, headers=None, json=None):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, json=[{"name": "size:S"}, {"name": "risk:low"}])

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(client, "post", mock_post)
        labels = await apply_pr_labels(
            token="ghp_test",
            repo_full_name="89Aman/PatchPilot",
            pr_number=42,
            labels=["size:S", "risk:low"],
            client=client,
        )
        assert labels == ["size:S", "risk:low"]
        assert captured["url"] == "https://api.github.com/repos/89Aman/PatchPilot/issues/42/labels"
        assert captured["json"] == {"labels": ["size:S", "risk:low"]}


def test_build_review_summary_card_with_triage_and_security():
    """Verify review summary card renders triage metrics and secret alerts."""
    card = build_review_summary_card(
        verdict="PASS",
        commit_sha="abcdef123456",
        summary="Clean refactoring.",
        bug_count=0,
        risk_count=1,
        nit_count=2,
        triage_summary="**PatchPilot Triage**: `size:S` • `risk:low`",
        secret_alerts_count=0,
    )
    assert "PatchPilot AI PR Review" in card
    assert "### 🏷️ Triage & Impact" in card
    assert "`size:S`" in card
    assert "🛡️ Secret Scan" in card
    assert "🟢 Clean" in card

    # Test alert status when secrets detected
    card_alert = build_review_summary_card(
        verdict="BLOCK",
        commit_sha="abcdef123456",
        summary="Blocked due to credentials.",
        bug_count=1,
        risk_count=0,
        nit_count=0,
        secret_alerts_count=1,
    )
    assert "🚨 Secret Alerts" in card_alert
    assert "**1 (CRITICAL)**" in card_alert
