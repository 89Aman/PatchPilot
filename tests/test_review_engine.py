"""Unit tests for CodeRabbit-style PR review formatting, line validation, and summary card generation."""

from __future__ import annotations

import pytest
from app.github_auth import build_review_summary_card
from app.llm import extract_json_payload


def test_build_review_summary_card_coderabbit_elements():
    """Verify build_review_summary_card formats checks, walkthrough, poem, and test stubs."""
    card = build_review_summary_card(
        verdict="WARN",
        commit_sha="abcdef123456",
        summary="Refactored data pipeline and security controls.",
        bug_count=0,
        risk_count=2,
        nit_count=1,
        triage_summary="**PatchPilot Triage**: `size:M` &bull; `risk:medium`",
        secret_alerts_count=0,
        review_checks={
            "🛡️ Security": "🟢 Clean (0 vulnerabilities)",
            "🐞 Correctness": "🟢 Clean (0 bugs)",
            "⚡ Performance": "🟡 1 Bottleneck",
            "🧪 Test Coverage": "🧪 1 Test Case Suggested",
        },
        walkthrough=[
            {"file": "app/pipeline.py", "impact": "⚡ Perf", "summary": "Optimized memory buffers"},
            {"file": "app/security.py", "impact": "🛡️ Security", "summary": "Added wallet protection"},
        ],
        poem="Data streams in morning light,\nBuffers flush with all their might!",
        suggested_tests=[
            {
                "target_file": "tests/test_pipeline.py",
                "description": "Verify buffer flush on stream EOF",
                "code_snippet": "def test_eof_flush():\n    assert True",
            }
        ],
    )

    # Check poem
    assert "Data streams in morning light" in card
    # Check review checks matrix
    assert "Review Checks" in card
    assert "🛡️ Security" in card
    assert "⚡ Performance" in card
    # Check walkthrough table
    assert "Changes Walkthrough" in card
    assert "`app/pipeline.py`" in card
    assert "Optimized memory buffers" in card
    # Check suggested unit tests
    assert "Suggested Unit Tests (1)" in card
    assert "def test_eof_flush" in card


def test_extract_json_payload_with_coderabbit_fields():
    """Verify extract_json_payload properly handles walkthrough and test fields."""
    raw_response = """
Here is the review:
```json
{
  "summary": "Clean refactoring with tests.",
  "poem": "Code is clean, tests are green!",
  "walkthrough": [
    {"file": "app/main.py", "impact": "✨ Feature", "summary": "Added route"}
  ],
  "verdict": "PASS",
  "findings": [],
  "suggested_tests": [
    {"target_file": "tests/test_main.py", "description": "Verify route 200", "code_snippet": "assert True"}
  ]
}
```
"""
    parsed = extract_json_payload(raw_response)
    assert parsed["verdict"] == "PASS"
    assert parsed["poem"] == "Code is clean, tests are green!"
    assert len(parsed["walkthrough"]) == 1
    assert len(parsed["suggested_tests"]) == 1


def test_check_response_status_groq_404():
    """Verify _check_response_status extracts Groq model 404 error with recommendation."""
    import httpx
    from app.db import VaultConfig
    from app.llm import _check_response_status

    cfg = VaultConfig(owner="test", provider="groq", model="non-existent-model")
    mock_resp = httpx.Response(
        status_code=404,
        json={"error": {"message": "The model `non-existent-model` does not exist or you do not have access to it."}},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )

    with pytest.raises(ValueError, match="Groq returned 404 Not Found") as exc_info:
        _check_response_status(mock_resp, cfg)

    assert "does not exist" in str(exc_info.value)
    assert "llama-3.3-70b-versatile" in str(exc_info.value)

