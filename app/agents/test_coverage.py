"""Test Coverage & QA Agent: Identify untested paths and generate ready-to-run test stubs."""

from __future__ import annotations

from typing import Any
from app.agents.base import AgentFinding, AgentResult, SpecialistAgent


class TestCoverageAgent(SpecialistAgent):
    """Specialist responsible for evaluating test coverage adequacy and generating test code."""

    name = "TestCoverageAgent"
    role_description = "Test coverage assessment, QA edge-case detection, and test stub synthesis"

    def build_system_prompt(self, policy: str) -> str:
        return (
            "You are PatchPilot Test Coverage Agent, a Senior QA Automation Engineer.\n"
            f"{policy}\n\n"
            "Analyze the pull request changes strictly for test adequacy:\n"
            "- Did the developer add or update tests corresponding to new business logic?\n"
            "- What edge cases, failure states, or error handlers remain completely untested?\n"
            "- Generate concrete, runnable unit test functions for uncovered code.\n\n"
            "Return strictly valid JSON with this schema:\n"
            "{\n"
            '  "coverage_status": "adequate | warning | missing",\n'
            '  "suggested_tests": [\n'
            "    {\n"
            '      "target_file": "path/to/target.py",\n'
            '      "description": "Description of the edge case or boundary test",\n'
            '      "code_snippet": "Runnable test function code in Python/JS/Go matching project language"\n'
            "    }\n"
            "  ],\n"
            '  "findings": [\n'
            "    {\n"
            '      "file": "path/to/file.ext",\n'
            '      "line": 10,\n'
            '      "severity": "nit | risk",\n'
            '      "title": "Untested Logic or Missing Edge Case Test",\n'
            '      "explanation": "Explain what branch or failure mode is untested",\n'
            '      "suggestion": ""\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "1. 'line' MUST match an [L<num>] line in the file diff.\n"
            "2. If tests are adequate or PR is docs-only, 'coverage_status' is 'adequate' and 'findings' is []."
        )

    def build_user_prompt(self, pr_title: str, pr_body: str, annotated_diff: str) -> str:
        return (
            f"PR Title: {pr_title}\n"
            f"PR Description: {pr_body or 'None'}\n\n"
            f"Diff Context (`[L<num>]` indicates line numbers):\n```diff\n{annotated_diff[:70_000]}\n```"
        )

    def parse_response(self, raw_data: dict[str, Any]) -> AgentResult:
        coverage_status = str(raw_data.get("coverage_status", "adequate")).lower()
        suggested_tests_raw = raw_data.get("suggested_tests") or []

        suggested_tests: list[dict[str, str]] = []
        if isinstance(suggested_tests_raw, list):
            for st in suggested_tests_raw:
                if isinstance(st, dict):
                    suggested_tests.append({
                        "target_file": str(st.get("target_file", "")),
                        "description": str(st.get("description", "")),
                        "code_snippet": str(st.get("code_snippet", "")),
                    })

        findings_raw = raw_data.get("findings") or []
        findings: list[AgentFinding] = []

        if isinstance(findings_raw, list):
            for f in findings_raw:
                if isinstance(f, dict) and f.get("file") and f.get("line") is not None:
                    findings.append(
                        AgentFinding(
                            file=str(f["file"]),
                            line=int(f["line"]),
                            severity=str(f.get("severity", "nit")).lower(),
                            title=str(f.get("title", "Missing Test Coverage")),
                            explanation=str(f.get("explanation", "")),
                            suggestion=str(f.get("suggestion", "")),
                            agent_name=self.name,
                        )
                    )

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            metadata={
                "coverage_status": coverage_status,
                "suggested_tests": suggested_tests,
            },
        )
