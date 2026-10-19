"""Unit and integration tests for PatchPilot CodeRabbit-grade Specialist AI Agent Team."""

from __future__ import annotations

import json
import pytest
from app.agents.architect import ArchitectAgent
from app.agents.base import AgentFinding, AgentResult
from app.agents.bug_hunter import BugHunterAgent
from app.agents.coordinator import AgentTeam, SynthesisCoordinator
from app.agents.performance import PerformanceAgent
from app.agents.security import SecurityAgent
from app.agents.test_coverage import TestCoverageAgent
from app.db import VaultConfig
from app.github_auth import build_review_summary_card
from app.llm import run_interactive_command
from app.triage import TriageResult


# -----------------------------------------------------------------------------
# 1. Specialist Agent Unit Tests
# -----------------------------------------------------------------------------

def test_architect_agent_parsing():
    """Verify ArchitectAgent parses summary, poem, and file walkthrough table."""
    agent = ArchitectAgent()
    mock_payload = {
        "summary": "Added wallet shield and multi-agent team.",
        "poem": "A shield for the wallet, a squad for the code,\nSafe from the bots on this open-source road!",
        "walkthrough": [
            {"file": "app/security.py", "impact": "🛡️ Security", "summary": "Added rate limiter"},
            {"file": "app/agents/coordinator.py", "impact": "✨ Feature", "summary": "Added consensus coordinator"},
        ],
        "findings": [],
    }

    result = agent.parse_response(mock_payload)
    assert result.status == "success"
    assert result.agent_name == "ArchitectAgent"
    assert "Added wallet shield" in result.metadata["summary"]
    assert "A shield for the wallet" in result.metadata["poem"]
    assert len(result.metadata["walkthrough"]) == 2
    assert result.metadata["walkthrough"][0]["impact"] == "🛡️ Security"


def test_security_agent_parsing():
    """Verify SecurityAgent parses vulnerability findings and sets severity."""
    agent = SecurityAgent()
    mock_payload = {
        "verdict": "BLOCK",
        "findings": [
            {
                "file": "app/db.py",
                "line": 42,
                "severity": "bug",
                "title": "Raw SQL Injection Vector",
                "explanation": "User parameter directly formatted into raw SQL query without parameterized binding.",
                "suggestion": "stmt = select(User).where(User.id == bind_param)",
            }
        ],
    }

    result = agent.parse_response(mock_payload)
    assert result.status == "success"
    assert result.agent_name == "SecurityAgent"
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.file == "app/db.py"
    assert finding.line == 42
    assert finding.severity == "bug"
    assert "Raw SQL Injection" in finding.title
    assert "stmt = select" in finding.suggestion


def test_bug_hunter_agent_parsing():
    """Verify BugHunterAgent parses defects and committable patches."""
    agent = BugHunterAgent()
    mock_payload = {
        "findings": [
            {
                "file": "app/core.py",
                "line": 88,
                "severity": "bug",
                "title": "Unhandled NoneType in item iteration",
                "explanation": "item may be None if fetch fails, causing AttributeError on item.id.",
                "suggestion": "if item is not None and item.is_valid:",
            }
        ]
    }

    result = agent.parse_response(mock_payload)
    assert result.status == "success"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "bug"
    assert "item is not None" in result.findings[0].suggestion


def test_performance_agent_parsing():
    """Verify PerformanceAgent parses algorithmic and I/O bottlenecks."""
    agent = PerformanceAgent()
    mock_payload = {
        "findings": [
            {
                "file": "app/services.py",
                "line": 105,
                "severity": "risk",
                "title": "O(N^2) Nested Iteration on Unindexed Collection",
                "explanation": "Nested loops over items and tags scale quadratically.",
                "suggestion": "tag_map = {t.id: t for t in tags}",
            }
        ]
    }

    result = agent.parse_response(mock_payload)
    assert result.status == "success"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "risk"
    assert "O(N^2)" in result.findings[0].title


def test_test_coverage_agent_parsing():
    """Verify TestCoverageAgent parses missing coverage and runnable test stubs."""
    agent = TestCoverageAgent()
    mock_payload = {
        "coverage_status": "warning",
        "suggested_tests": [
            {
                "target_file": "app/security.py",
                "description": "Test rate limiter boundary when window expires exactly",
                "code_snippet": "def test_boundary():\n    assert True",
            }
        ],
        "findings": [
            {
                "file": "app/security.py",
                "line": 50,
                "severity": "nit",
                "title": "Untested Rate Limiter Expiration Path",
                "explanation": "No existing test exercises the timestamp purge logic.",
                "suggestion": "",
            }
        ],
    }

    result = agent.parse_response(mock_payload)
    assert result.status == "success"
    assert result.metadata["coverage_status"] == "warning"
    assert len(result.metadata["suggested_tests"]) == 1
    assert "test_boundary" in result.metadata["suggested_tests"][0]["code_snippet"]
    assert len(result.findings) == 1


# -----------------------------------------------------------------------------
# 2. Synthesis Coordinator Tests
# -----------------------------------------------------------------------------

def test_coordinator_filters_invalid_lines():
    """Verify coordinator removes findings with line numbers not present in diff."""
    valid_lines = {
        "app/main.py": {10, 11, 12},
    }

    findings = [
        AgentFinding(file="app/main.py", line=11, severity="bug", title="Real bug", explanation="Valid line"),
        AgentFinding(file="app/main.py", line=999, severity="bug", title="Hallucinated line", explanation="Invalid"),
        AgentFinding(file="non_existent.py", line=5, severity="risk", title="Fake file", explanation="Invalid"),
    ]

    filtered = SynthesisCoordinator.filter_and_deduplicate(findings, valid_lines)
    assert len(filtered) == 1
    assert filtered[0].file == "app/main.py"
    assert filtered[0].line == 11


def test_coordinator_deduplicates_same_line_findings():
    """Verify coordinator retains highest severity when multiple agents target the same line."""
    valid_lines = {"app/core.py": {25}}

    bug_finding = AgentFinding(
        file="app/core.py",
        line=25,
        severity="bug",
        title="Null pointer crash",
        explanation="Crashing on null",
        agent_name="BugHunterAgent",
    )
    risk_finding = AgentFinding(
        file="app/core.py",
        line=25,
        severity="risk",
        title="Unvalidated input",
        explanation="Input is unchecked",
        agent_name="SecurityAgent",
    )

    # Risk first, then Bug
    filtered = SynthesisCoordinator.filter_and_deduplicate([risk_finding, bug_finding], valid_lines)
    assert len(filtered) == 1
    assert filtered[0].severity == "bug"
    assert "Null pointer crash" in filtered[0].title
    assert "SecurityAgent" in filtered[0].explanation


def test_coordinator_agent_status_badges():
    """Verify coordinator produces correct status matrix badges based on findings."""
    results = {
        "SecurityAgent": AgentResult(
            agent_name="SecurityAgent",
            status="success",
            findings=[],
        ),
        "BugHunterAgent": AgentResult(
            agent_name="BugHunterAgent",
            status="success",
            findings=[AgentFinding(file="f", line=1, severity="bug", title="t", explanation="e")],
        ),
        "PerformanceAgent": AgentResult(
            agent_name="PerformanceAgent",
            status="skipped",
        ),
        "TestCoverageAgent": AgentResult(
            agent_name="TestCoverageAgent",
            status="success",
            metadata={"suggested_tests": [{"target_file": "f", "description": "d", "code_snippet": "c"}]},
        ),
    }

    statuses = SynthesisCoordinator.compute_agent_statuses(results)
    assert "Clean" in statuses["Security"]
    assert "1 Defect" in statuses["Bug Hunter"]
    assert "Skipped" in statuses["Performance"]
    assert "1 Test Case Suggested" in statuses["Test Coverage"]


# -----------------------------------------------------------------------------
# 3. AgentTeam Orchestration & Triage Routing Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_team_triage_routing_skips_on_docs():
    """Verify docs-only XS PR routes solely to ArchitectAgent and skips heavy agents."""
    team = AgentTeam()

    # Mock Architect execution
    async def mock_architect_exec(*args, **kwargs):
        return AgentResult(
            agent_name="ArchitectAgent",
            status="success",
            metadata={
                "summary": "Updated documentation files.",
                "poem": "Docs are clear, words sincere!",
                "walkthrough": [{"file": "README.md", "impact": "📝 Docs", "summary": "Fix typos"}],
            },
        )

    team.architect.execute = mock_architect_exec

    cfg = VaultConfig(owner="test-owner", provider="gemini", model="gemini-2.5-pro")
    triage = TriageResult(
        size_tier="size:XS",
        risk_level="risk:low",
        risk_score=5,
        labels=["size:XS", "risk:low"],
        impact_areas=[],
        files_count=1,
        total_lines_changed=5,
        summary_text="Docs only",
    )

    review_data = await team.run_review(
        cfg=cfg,
        pr_title="Docs update",
        pr_body="",
        annotated_diff="[L1] +Documentation update",
        valid_lines={"README.md": {1}},
        triage=triage,
    )

    assert review_data["verdict"] == "PASS"
    assert review_data["summary"] == "Updated documentation files."
    assert review_data["poem"] == "Docs are clear, words sincere!"
    assert review_data["agent_statuses"]["Security"] == "⚪ Skipped (Low Risk)"
    assert review_data["agent_statuses"]["Bug Hunter"] == "⚪ Skipped (Docs PR)"


@pytest.mark.asyncio
async def test_agent_team_full_squad_parallel_dispatch():
    """Verify standard code PR triggers full 5-agent squad concurrently."""
    team = AgentTeam()

    async def mock_arch(*args, **kwargs):
        return AgentResult(
            agent_name="ArchitectAgent",
            status="success",
            metadata={
                "summary": "Architectural refactoring of auth tokens.",
                "poem": "Keys securely locked away,\nTokens shine without delay!",
                "walkthrough": [{"file": "app/auth.py", "impact": "🛡️ Security", "summary": "Rotated tokens"}],
            },
        )

    async def mock_sec(*args, **kwargs):
        return AgentResult(agent_name="SecurityAgent", status="success", findings=[])

    async def mock_bug(*args, **kwargs):
        return AgentResult(
            agent_name="BugHunterAgent",
            status="success",
            findings=[
                AgentFinding(
                    file="app/auth.py",
                    line=10,
                    severity="bug",
                    title="Token expiry check missing",
                    explanation="Expired tokens accepted",
                    suggestion="if token.is_expired: raise ValueError('Expired')",
                    agent_name="BugHunterAgent",
                )
            ],
        )

    async def mock_perf(*args, **kwargs):
        return AgentResult(agent_name="PerformanceAgent", status="success", findings=[])

    async def mock_test(*args, **kwargs):
        return AgentResult(
            agent_name="TestCoverageAgent",
            status="success",
            metadata={
                "suggested_tests": [
                    {
                        "target_file": "tests/test_auth.py",
                        "description": "Test expired token rejection",
                        "code_snippet": "def test_expired(): pass",
                    }
                ]
            },
        )

    team.architect.execute = mock_arch
    team.security.execute = mock_sec
    team.bug_hunter.execute = mock_bug
    team.performance.execute = mock_perf
    team.test_coverage.execute = mock_test

    cfg = VaultConfig(owner="test-owner", provider="gemini", model="gemini-2.5-pro")
    triage = TriageResult(
        size_tier="size:M",
        risk_level="risk:medium",
        risk_score=45,
        labels=["size:M", "risk:medium"],
        impact_areas=["Auth & Cryptography"],
        files_count=2,
        total_lines_changed=150,
        summary_text="Auth changes",
    )

    review_data = await team.run_review(
        cfg=cfg,
        pr_title="Refactor auth",
        pr_body="",
        annotated_diff="[L10] +token check",
        valid_lines={"app/auth.py": {10}},
        triage=triage,
    )

    # 1 bug -> verdict BLOCK
    assert review_data["verdict"] == "BLOCK"
    assert len(review_data["findings"]) == 1
    assert "Token expiry check" in review_data["findings"][0]["title"]
    assert len(review_data["walkthrough"]) == 1
    assert len(review_data["suggested_tests"]) == 1
    assert "Clean" in review_data["agent_statuses"]["Security"]
    assert "1 Defect" in review_data["agent_statuses"]["Bug Hunter"]


# -----------------------------------------------------------------------------
# 4. CodeRabbit Summary Card Markdown Verification
# -----------------------------------------------------------------------------

def test_build_review_summary_card_coderabbit_elements():
    """Verify build_review_summary_card formats badges, walkthrough, poem, and test stubs."""
    card = build_review_summary_card(
        verdict="WARN",
        commit_sha="abcdef123456",
        summary="Refactored data pipeline and security controls.",
        bug_count=0,
        risk_count=2,
        nit_count=1,
        triage_summary="**PatchPilot Triage**: `size:M` &bull; `risk:medium`",
        secret_alerts_count=0,
        agent_statuses={
            "🛡️ Security": "🟢 Clean (0 vulnerabilities)",
            "🐞 Bug Hunter": "🟢 Clean (0 bugs)",
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

    # Check CodeRabbit poem
    assert "Data streams in morning light" in card
    # Check squad matrix
    assert "Specialist Agent Squad" in card
    assert "🛡️ Security" in card
    assert "⚡ Performance" in card
    # Check walkthrough table
    assert "Changes Walkthrough" in card
    assert "`app/pipeline.py`" in card
    assert "Optimized memory buffers" in card
    # Check suggested unit tests
    assert "Suggested Unit Tests (1)" in card
    assert "def test_eof_flush" in card
