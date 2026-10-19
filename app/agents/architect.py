"""Architect & Walkthrough Agent: High-level change summaries, file tables, and poetic overview."""

from __future__ import annotations

from typing import Any
from app.agents.base import AgentFinding, AgentResult, SpecialistAgent


class ArchitectAgent(SpecialistAgent):
    """Specialist responsible for overall PR architecture, file walkthroughs, and executive summary."""

    name = "ArchitectAgent"
    role_description = "Architecture overview and file-level walkthrough synthesis"

    def build_system_prompt(self, policy: str) -> str:
        return (
            "You are PatchPilot Architect Agent, an expert software architect and technical lead.\n"
            f"{policy}\n\n"
            "Analyze the pull request changes, intent, and architectural impact.\n"
            "Return strictly valid JSON with this schema:\n"
            "{\n"
            '  "summary": "High-level architectural summary of the pull request.",\n'
            '  "poem": "A short, clever 2-4 line rhyming poem summarizing the changes in CodeRabbit style.",\n'
            '  "walkthrough": [\n'
            "    {\n"
            '      "file": "path/to/file.ext",\n'
            '      "impact": "✨ Feature | 🐛 Fix | ⚡ Perf | 📝 Docs | 🧹 Refactor | 🛡️ Security",\n'
            '      "summary": "One concise sentence describing what changed in this specific file."\n'
            "    }\n"
            "  ],\n"
            '  "findings": []\n'
            "}\n"
        )

    def build_user_prompt(self, pr_title: str, pr_body: str, annotated_diff: str) -> str:
        return (
            f"PR Title: {pr_title}\n"
            f"PR Description: {pr_body or 'None'}\n\n"
            f"Diff Context (`[L<num>]` indicates line numbers):\n```diff\n{annotated_diff[:70_000]}\n```"
        )

    def parse_response(self, raw_data: dict[str, Any]) -> AgentResult:
        summary = str(raw_data.get("summary") or "Architecture review completed.").strip()
        poem = str(raw_data.get("poem") or "").strip()
        walkthrough_raw = raw_data.get("walkthrough") or []

        walkthrough: list[dict[str, str]] = []
        if isinstance(walkthrough_raw, list):
            for item in walkthrough_raw:
                if isinstance(item, dict) and "file" in item:
                    walkthrough.append({
                        "file": str(item.get("file", "")),
                        "impact": str(item.get("impact", "🧹 Refactor")),
                        "summary": str(item.get("summary", "")),
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
                            severity=str(f.get("severity", "risk")).lower(),
                            title=str(f.get("title", "Architecture Notice")),
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
                "summary": summary,
                "poem": poem,
                "walkthrough": walkthrough,
            },
        )
