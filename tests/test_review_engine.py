"""Unit tests for CodeRabbit-style PR review formatting, line validation, and summary card generation."""

from __future__ import annotations

import pytest
from app.github_auth import build_review_summary_card, strip_emojis
from app.llm import annotate_diff, extract_json_payload


def test_build_review_summary_card_coderabbit_elements():
    """Verify build_review_summary_card formats checks, walkthrough, poem, user tag, and test stubs without emojis."""
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
        requested_by="89Aman",
        findings=[
            {
                "file": "app/pipeline.py",
                "line": 42,
                "severity": "risk",
                "title": "Unbounded buffer growth",
                "explanation": "Buffer can grow without limit on slow consumers.",
                "suggestion": "buffer = deque(maxlen=1024)",
            }
        ],
        verification_status="2-Pass Fact-Checked",
    )

    # Check user tag and verification status
    assert "**Requested by:** @89Aman" in card
    assert "`2-Pass Fact-Checked`" in card
    # Check poem
    assert "Data streams in morning light" in card
    # Check review checks matrix (emojis stripped)
    assert "Review Checks" in card
    assert "**Security**" in card
    assert "**Performance**" in card
    assert "🛡️" not in card
    assert "⚡" not in card
    assert "🟢" not in card
    # Check walkthrough table
    assert "Changes Walkthrough" in card
    assert "`app/pipeline.py`" in card
    assert "Optimized memory buffers" in card
    # Check verified findings section
    assert "### Verified Findings" in card
    assert "Unbounded buffer growth" in card
    # Check suggested unit tests
    assert "Suggested Unit Tests (1)" in card
    assert "def test_eof_flush" in card


def test_strip_emojis_and_annotate_diff_no_midline_cut():
    """Verify strip_emojis removes all emojis and annotate_diff never cuts lines mid-token."""
    assert strip_emojis("### ✈️ PatchPilot 🟢 Clean") == "### PatchPilot  Clean"

    large_diff = (
        "diff --git a/file1.py b/file1.py\n"
        "--- a/file1.py\n"
        "+++ b/file1.py\n"
        "@@ -1,2 +1,100 @@\n"
        + "".join(f"+line_content_{i} = 'complete_statement_{i}'\n" for i in range(1, 101))
        + "diff --git a/file2.py b/file2.py\n"
        "--- a/file2.py\n"
        "+++ b/file2.py\n"
        "@@ -1,2 +1,20 @@\n"
        + "".join(f"+second_file_{i} = True\n" for i in range(1, 21))
    )
    annotated, valid = annotate_diff(large_diff, max_chars=2500)
    assert "file1.py" in valid
    assert "file2.py" in valid
    assert "--- file1.py ---" in annotated
    assert "--- file2.py ---" in annotated
    # Ensure last line of file1 or file2 is not cut mid-identifier
    for ln in annotated.splitlines():
        if "complete_statement_" in ln:
            assert ln.endswith("'")


def test_extract_json_payload_with_coderabbit_fields():
    """Verify extract_json_payload properly handles walkthrough and test fields."""
    raw_response = """
Here is the review:
```json
{
  "summary": "Clean refactoring with tests.",
  "poem": "Code is clean, tests are green!",
  "walkthrough": [
    {"file": "app/main.py", "impact": "Feature", "summary": "Added route"}
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
