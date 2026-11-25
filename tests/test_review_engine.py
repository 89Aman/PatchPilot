"""Unit tests for CodeRabbit-style PR review formatting, line validation, and summary card generation."""

from __future__ import annotations

import httpx
import pytest
from app.github_auth import (
    build_loading_review_card,
    build_review_summary_card,
    complete_check_indicator,
    create_pending_pr_review,
    publish_pr_review,
    start_check_indicator,
    strip_emojis,
    update_pr_review_card,
)
from app.llm import annotate_diff, extract_json_payload


def test_build_loading_review_card_stages_and_no_emojis():
    """Verify build_loading_review_card renders a 5-stage live verification pipeline without emojis."""
    card = build_loading_review_card(
        requested_by="89Aman",
        commit_sha="abcdef123456",
        command="review",
        target_ref="89Aman/AgentTrustLedger#2",
        stage_statuses={
            "diff": "COMPLETED (4 files)",
            "security": "COMPLETED (Clean)",
            "triage": "COMPLETED (size:M, risk:low)",
            "pass1": "RUNNING...",
            "pass2": "QUEUED",
        },
    )
    assert card.startswith("<!-- patchpilot-ai-review -->")
    assert "## PatchPilot AI PR Review" in card
    assert "**Status:** `IN PROGRESS`" in card
    assert "**Requested by:** @89Aman" in card
    assert "**Target:** `89Aman/AgentTrustLedger#2`" in card
    assert "### Live Verification Pipeline" in card
    assert "`COMPLETED (4 files)`" in card
    assert "`COMPLETED (Clean)`" in card
    assert "`RUNNING...`" in card
    assert "`QUEUED`" in card
    assert card == strip_emojis(card)


def test_build_review_summary_card_coderabbit_elements():
    """Verify build_review_summary_card formats checks, walkthrough, architecture diagram, agent prompts, poem, user tag, and test stubs without emojis."""
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
        architecture_diagram="flowchart LR\n    A[\"Input Stream\"] --> B[\"Memory Buffer\"]",
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
                "agent_prompt": "In `app/pipeline.py` around line 42, bound `buffer` using `deque(maxlen=1024)`.",
            }
        ],
        verification_status="2-Pass Fact-Checked",
    )

    # Check user tag and verification status
    assert "**Requested by:** @89Aman" in card
    assert "`2-Pass Fact-Checked`" in card
    # Check poem
    assert "Data streams in morning light" not in card
    # Check review checks matrix (emojis stripped)
    assert "Actionable findings" in card
    assert "Source:" in card
    assert "Findings:" in card
    assert "🛡️" not in card
    assert "⚡" not in card
    assert "🟢" not in card
    # Check collapsible walkthrough table & Mermaid architecture diagram
    assert "Architecture diagram" in card
    assert "```mermaid" in card
    assert "flowchart LR" in card
    assert "app/pipeline.py" in card
    assert "Optimized memory buffers" in card
    # Check verified findings section and Prompt for AI Agents
    assert "### Actionable findings" in card
    assert "Unbounded buffer growth" in card
    assert "Suggested agent prompt" in card
    assert "deque(maxlen=1024)" in card
    # Check suggested unit tests
    assert "Suggested tests (not executed)" in card
    assert "def test_eof_flush" in card


@pytest.mark.asyncio
async def test_pending_review_registration_and_in_place_update(monkeypatch):
    """Verify create_pending_pr_review registers bot in Reviewers via POST /reviews and publish_pr_review updates it in-place via PUT."""
    calls: list[tuple[str, str, dict]] = []

    async def mock_post(url, headers=None, json=None):
        calls.append(("POST", url, json or {}))
        if url.endswith("/reviews"):
            return httpx.Response(200, json={"id": 987654})
        if url.endswith("/check-runs"):
            return httpx.Response(201, json={"id": 555111})
        return httpx.Response(200, json={"id": 111})

    async def mock_put(url, headers=None, json=None):
        calls.append(("PUT", url, json or {}))
        return httpx.Response(200, json={"id": 987654})

    async def mock_patch(url, headers=None, json=None):
        calls.append(("PATCH", url, json or {}))
        return httpx.Response(200, json={"id": 555111})

    async with httpx.AsyncClient() as client:
        monkeypatch.setattr(client, "post", mock_post)
        monkeypatch.setattr(client, "put", mock_put)
        monkeypatch.setattr(client, "patch", mock_patch)

        loading_body = build_loading_review_card(requested_by="89Aman", commit_sha="abc1234")
        handle = await create_pending_pr_review(
            token="ghs_test",
            repo_full_name="89Aman/AgentTrustLedger",
            pr_number=2,
            head_sha="abc1234",
            body=loading_body,
            client=client,
        )
        assert handle == {"kind": "review", "id": 987654}

        check_handle = await start_check_indicator(
            token="ghs_test",
            repo_full_name="89Aman/AgentTrustLedger",
            head_sha="abc1234",
            command="review",
            client=client,
        )
        assert check_handle["head_sha"] == "abc1234"
        assert check_handle["check_run_id"] == 555111

        # Update mid-flight loading card
        updated = await update_pr_review_card(
            token="ghs_test",
            repo_full_name="89Aman/AgentTrustLedger",
            pr_number=2,
            handle=handle,
            body="Updated stage progress",
            client=client,
        )
        assert updated is True

        # Publish final review in-place
        await publish_pr_review(
            token="ghs_test",
            repo_full_name="89Aman/AgentTrustLedger",
            pr_number=2,
            head_sha="abc1234",
            review_data={
                "verdict": "PASS",
                "summary": "All checks passed.",
                "findings": [],
                "walkthrough": [{"file": "app/main.py", "impact": "Core", "summary": "Clean"}],
            },
            valid_lines={"app/main.py": {1, 2, 3}},
            client=client,
            requested_by="89Aman",
            existing_review=handle,
        )

        await complete_check_indicator(
            token="ghs_test",
            repo_full_name="89Aman/AgentTrustLedger",
            check_handle=check_handle,
            verdict="PASS",
            summary_text="All checks passed.",
            client=client,
        )

    # Verify POST /pulls/2/reviews was called once (initial loading card) and PUT /pulls/2/reviews/987654 was called twice (mid-stage + final)
    post_review_calls = [c for c in calls if c[0] == "POST" and c[1].endswith("/pulls/2/reviews")]
    put_review_calls = [c for c in calls if c[0] == "PUT" and c[1].endswith("/pulls/2/reviews/987654")]
    assert len(post_review_calls) == 1
    assert len(put_review_calls) == 2
    assert "All checks passed." in put_review_calls[-1][2]["body"]


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
