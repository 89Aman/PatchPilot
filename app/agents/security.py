"""Security Agent: Deep scrutiny for OWASP Top 10, Auth/Authz, injection, and crypto flaws."""

from __future__ import annotations

from typing import Any
from app.agents.base import AgentFinding, AgentResult, SpecialistAgent


class SecurityAgent(SpecialistAgent):
    """Specialist responsible for identifying security vulnerabilities and compliance risks."""

    name = "SecurityAgent"
    role_description = "Application security, OWASP Top 10, and vulnerability detection"

    def build_system_prompt(self, policy: str) -> str:
        return (
            "You are PatchPilot Security Agent, a Senior Application Security & Penetration Testing Specialist.\n"
            f"{policy}\n\n"
            "Analyze the pull request changes strictly for security risks:\n"
            "- Authentication and authorization bypasses (IDOR, missing session checks)\n"
            "- Injection vulnerabilities (SQLi, Command Injection, SSRF, XSS)\n"
            "- Insecure deserialization, dangerous eval/pickle usage\n"
            "- Path traversal, insecure file operations\n"
            "- Weak cryptographic algorithms or insecure random number generation\n"
            "- Data leakage or unmasked sensitive PII\n\n"
            "Return strictly valid JSON with this schema:\n"
            "{\n"
            '  "verdict": "PASS | WARN | BLOCK",\n'
            '  "findings": [\n'
            "    {\n"
            '      "file": "path/to/file.ext",\n'
            '      "line": 10,\n'
            '      "severity": "bug | risk",\n'
            '      "title": "Short descriptive security title",\n'
            '      "explanation": "Detailed explanation of the attack vector, impact, and remediation",\n'
            '      "suggestion": "Exact replacement code line or empty string"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "1. 'line' MUST match an [L<num>] line in the file diff.\n"
            "2. Do not report deleted lines.\n"
            "3. If no security issues exist, 'findings' must be [] and 'verdict' must be 'PASS'."
        )

    def build_user_prompt(self, pr_title: str, pr_body: str, annotated_diff: str) -> str:
        return (
            f"PR Title: {pr_title}\n"
            f"PR Description: {pr_body or 'None'}\n\n"
            f"Diff Context (`[L<num>]` indicates line numbers):\n```diff\n{annotated_diff[:70_000]}\n```"
        )

    def parse_response(self, raw_data: dict[str, Any]) -> AgentResult:
        verdict = str(raw_data.get("verdict", "PASS")).upper()
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
                            title=str(f.get("title", "Security Vulnerability")),
                            explanation=str(f.get("explanation", "")),
                            suggestion=str(f.get("suggestion", "")),
                            agent_name=self.name,
                        )
                    )

        return AgentResult(
            agent_name=self.name,
            status="success",
            findings=findings,
            metadata={"verdict": verdict},
        )
