"""Performance Agent: Algorithmic complexity, database N+1 queries, and resource efficiency."""

from __future__ import annotations

from typing import Any
from app.agents.base import AgentFinding, AgentResult, SpecialistAgent


class PerformanceAgent(SpecialistAgent):
    """Specialist responsible for detecting performance regressions and scalability bottlenecks."""

    name = "PerformanceAgent"
    role_description = "Performance engineering, algorithmic optimization, and I/O efficiency"

    def build_system_prompt(self, policy: str) -> str:
        return (
            "You are PatchPilot Performance Agent, a Staff Performance Engineer.\n"
            f"{policy}\n\n"
            "Analyze the pull request changes strictly for performance and efficiency bottlenecks:\n"
            "- Algorithmic time/space complexity (e.g. O(N²) loops, redundant array scans)\n"
            "- Database N+1 queries or missing eager loading / batch fetching\n"
            "- Unbuffered or repeated disk/network I/O in loops\n"
            "- Unnecessary memory allocations, huge list copies, or missing generators\n"
            "- Inefficient regular expressions (ReDoS or uncompiled patterns in hot paths)\n\n"
            "Return strictly valid JSON with this schema:\n"
            "{\n"
            '  "findings": [\n'
            "    {\n"
            '      "file": "path/to/file.ext",\n'
            '      "line": 10,\n'
            '      "severity": "risk | nit",\n'
            '      "title": "Short performance bottleneck title",\n'
            '      "explanation": "Explanation of the inefficiency, algorithmic order, and suggested optimization",\n'
            '      "suggestion": "Optimized replacement code or empty string"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "1. 'line' MUST match an [L<num>] line in the file diff.\n"
            "2. Focus only on real bottlenecks, avoiding premature micro-optimizations.\n"
            "3. If no performance concerns exist, return 'findings': []."
        )

    def build_user_prompt(self, pr_title: str, pr_body: str, annotated_diff: str) -> str:
        return (
            f"PR Title: {pr_title}\n"
            f"PR Description: {pr_body or 'None'}\n\n"
            f"Diff Context (`[L<num>]` indicates line numbers):\n```diff\n{annotated_diff[:70_000]}\n```"
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
                            severity=str(f.get("severity", "risk")).lower(),
                            title=str(f.get("title", "Performance Bottleneck")),
                            explanation=str(f.get("explanation", "")),
                            suggestion=str(f.get("suggestion", "")),
                            agent_name=self.name,
                        )
                    )

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            metadata={"perf_issues_count": len(findings)},
        )
