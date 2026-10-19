"""Bug Hunter Agent: Correctness, edge cases, logic bugs, and committable fixes."""

from __future__ import annotations

from typing import Any
from app.agents.base import AgentFinding, AgentResult, SpecialistAgent


class BugHunterAgent(SpecialistAgent):
    """Specialist responsible for detecting logic defects, null pointers, and runtime exceptions."""

    name = "BugHunterAgent"
    role_description = "Defect detection, logic validation, and committable patch generation"

    def build_system_prompt(self, policy: str) -> str:
        return (
            "You are PatchPilot Bug Hunter Agent, an elite code debugger.\n"
            f"{policy}\n\n"
            "Analyze the pull request changes strictly for software bugs and logic defects:\n"
            "- Potential Null/None dereferences and missing error/boundary checks\n"
            "- Off-by-one errors in loops, ranges, or slice indexing\n"
            "- Unhandled exceptions, swallowing errors without logging or recovery\n"
            "- Race conditions, unawaited coroutines, async concurrency pitfalls\n"
            "- Resource leaks (unclosed sockets, database connections, file handles)\n"
            "- Type errors and broken assumptions between caller and callee\n\n"
            "Return strictly valid JSON with this schema:\n"
            "{\n"
            '  "findings": [\n'
            "    {\n"
            '      "file": "path/to/file.ext",\n'
            '      "line": 10,\n'
            '      "severity": "bug | risk | nit",\n'
            '      "title": "Clear concise defect title",\n'
            '      "explanation": "Root cause explanation and why this triggers a runtime defect",\n'
            '      "suggestion": "One-click committable replacement line(s) or empty string"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "1. 'line' MUST match an [L<num>] line in the file diff.\n"
            "2. Whenever possible, provide a precise, syntactically correct 'suggestion'.\n"
            "3. If no bugs exist, return 'findings': []."
        )

    def build_user_prompt(self, pr_title: str, pr_body: str, annotated_diff: str) -> str:
        return (
            f"PR Title: {pr_title}\n"
            f"PR Description: {pr_body or 'None'}\n\n"
            f"Diff Context (`[L<num>]` indicates line numbers):\n```diff\n{annotated_diff[:75_000]}\n```"
        )

    def parse_response(self, raw_data: dict[str, Any]) -> AgentResult:
        findings_raw = raw_data.get("findings") or []
        findings: list[AgentFinding] = []

        if isinstance(findings_raw, list):
            for f in findings_raw:
                if isinstance(f, dict) and f.get("file") and f.get("line") is not None:
                    findings.append(
                        AgentFinding(
                            file=str(f["file"]),
                            line=int(f["line"]),
                            severity=str(f.get("severity", "bug")).lower(),
                            title=str(f.get("title", "Logic Bug")),
                            explanation=str(f.get("explanation", "")),
                            suggestion=str(f.get("suggestion", "")),
                            agent_name=self.name,
                        )
                    )

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            metadata={"bug_count": len(findings)},
        )
