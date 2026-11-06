"""Ensure every LLM path redacts known credential formats before transmission."""

import json

import httpx
import pytest

from app.db import VaultConfig
from app.llm import run_interactive_command, run_pr_review
from app.security import sanitize_diff_text, scan_diff_for_secrets


TOKEN = "ghp_" + "a" * 36  # Synthetic pattern fixture, never a real credential.


def test_redaction_without_added_line_findings():
    diff = f"+++ b/config.py\n@@ -1,2 +1,2 @@\n TOKEN = '{TOKEN}'\n-old = 1\n+new = 2\n"
    assert scan_diff_for_secrets(diff) == []
    assert TOKEN not in sanitize_diff_text(diff, [])


def test_redaction_uses_each_patterns_own_label():
    diff = f"+++ b/config.py\n+token = '{TOKEN}'\n+aws = 'AKIA1234567890ABCDEF'\n"
    redacted = sanitize_diff_text(diff, scan_diff_for_secrets(diff))
    assert "[REDACTED_GITHUB_PERSONAL_ACCESS_TOKEN]" in redacted
    assert "[REDACTED_AWS_ACCESS_KEY_ID]" in redacted


@pytest.mark.parametrize("ending", ["-----END RSA PRIVATE KEY-----", ""])
def test_private_key_body_is_redacted(ending):
    redacted = sanitize_diff_text(f"-----BEGIN RSA PRIVATE KEY-----\nsynthetic-key-material\n{ending}", [])
    assert "synthetic-key-material" not in redacted


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["review", "interactive_pr", "interactive_hunk"])
async def test_outbound_prompts_do_not_contain_credentials(mode):
    requests = []

    async def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert TOKEN not in json.dumps(payload)
        content = json.dumps({"summary": "Reviewed", "verdict": "PASS", "findings": []}) if mode == "review" else "Explained"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    cfg = VaultConfig(owner="test", provider="ollama", model="local", encrypted_api_key="", review_policy=f"Policy {TOKEN}")
    diff = f"diff --git a/config.py b/config.py\n--- a/config.py\n+++ b/config.py\n@@ -1 +1 @@\n-{TOKEN}\n+safe = True\n"
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        if mode == "review":
            await run_pr_review(cfg, TOKEN, TOKEN, diff, client=client)
        else:
            await run_interactive_command(cfg, "explain", TOKEN, "config.py",
                                          diff if mode == "interactive_hunk" else None, diff, client=client)
    assert len(requests) == 1
