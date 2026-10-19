"""Synthesis Coordinator & Agent Team: Parallel dispatch, deduplication, and consensus review."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
import httpx
from app.agents.architect import ArchitectAgent
from app.agents.base import AgentFinding, AgentResult, SpecialistAgent
from app.agents.bug_hunter import BugHunterAgent
from app.agents.performance import PerformanceAgent
from app.agents.security import SecurityAgent
from app.agents.test_coverage import TestCoverageAgent
from app.db import VaultConfig
from app.triage import TriageResult

logger = logging.getLogger("patchpilot.agents.coordinator")


class SynthesisCoordinator:
    """Coordinates specialist agents, eliminates hallucinations, and deduplicates findings."""

    @staticmethod
    def filter_and_deduplicate(
        findings: list[AgentFinding],
        valid_lines: dict[str, set[int]],
    ) -> list[AgentFinding]:
        """Enforce strict diff line validation and deduplicate findings targeting the same line."""
        seen: dict[tuple[str, int], AgentFinding] = {}
        severity_rank = {"bug": 3, "risk": 2, "nit": 1}

        for f in findings:
            # 1. Enforce strict right-side line existence
            file_lines = valid_lines.get(f.file)
            if not file_lines or f.line not in file_lines:
                logger.debug("Discarding invalid diff line finding: %s#L%s", f.file, f.line)
                continue

            key = (f.file, f.line)
            existing = seen.get(key)
            if not existing:
                seen[key] = f
            else:
                # Retain higher severity finding, append unique insight
                existing_rank = severity_rank.get(existing.severity.lower(), 1)
                new_rank = severity_rank.get(f.severity.lower(), 1)

                if new_rank > existing_rank:
                    f.explanation = f"{f.explanation}\n\n*Note from {existing.agent_name}:* {existing.title}"
                    seen[key] = f
                else:
                    existing.explanation = f"{existing.explanation}\n\n*Note from {f.agent_name}:* {f.title}"

        return list(seen.values())

    @staticmethod
    def compute_agent_statuses(results: dict[str, AgentResult]) -> dict[str, str]:
        """Compute status indicator badge for each specialist agent in the squad."""
        statuses: dict[str, str] = {}

        # Security Status
        sec_res = results.get("SecurityAgent")
        if sec_res and sec_res.status == "success":
            sec_bugs = sum(1 for f in sec_res.findings if f.severity == "bug")
            sec_risks = sum(1 for f in sec_res.findings if f.severity == "risk")
            if sec_bugs > 0:
                statuses["Security"] = f"🔴 {sec_bugs} Vulnerabilit{'y' if sec_bugs == 1 else 'ies'}"
            elif sec_risks > 0:
                statuses["Security"] = f"🟡 {sec_risks} Concern{'s' if sec_risks > 1 else ''}"
            else:
                statuses["Security"] = "🟢 Clean (0 vulnerabilities)"
        elif sec_res and sec_res.status == "skipped":
            statuses["Security"] = "⚪ Skipped (Low Risk)"
        else:
            statuses["Security"] = "⚪ Offline"

        # Bug Hunter Status
        bug_res = results.get("BugHunterAgent")
        if bug_res and bug_res.status == "success":
            bug_count = len(bug_res.findings)
            if bug_count > 0:
                statuses["Bug Hunter"] = f"🟡 {bug_count} Defect{'s' if bug_count > 1 else ''} Found"
            else:
                statuses["Bug Hunter"] = "🟢 Clean (0 bugs)"
        elif bug_res and bug_res.status == "skipped":
            statuses["Bug Hunter"] = "⚪ Skipped (Docs PR)"
        else:
            statuses["Bug Hunter"] = "⚪ Offline"

        # Performance Status
        perf_res = results.get("PerformanceAgent")
        if perf_res and perf_res.status == "success":
            perf_count = len(perf_res.findings)
            if perf_count > 0:
                statuses["Performance"] = f"🟡 {perf_count} Bottleneck{'s' if perf_count > 1 else ''}"
            else:
                statuses["Performance"] = "🟢 Optimal"
        elif perf_res and perf_res.status == "skipped":
            statuses["Performance"] = "⚪ Skipped (Trivial)"
        else:
            statuses["Performance"] = "⚪ Offline"

        # Test Coverage Status
        test_res = results.get("TestCoverageAgent")
        if test_res and test_res.status == "success":
            suggested = test_res.metadata.get("suggested_tests", [])
            if suggested:
                statuses["Test Coverage"] = f"🧪 {len(suggested)} Test Case{'s' if len(suggested) > 1 else ''} Suggested"
            else:
                statuses["Test Coverage"] = "🟢 Adequate"
        elif test_res and test_res.status == "skipped":
            statuses["Test Coverage"] = "⚪ Skipped"
        else:
            statuses["Test Coverage"] = "⚪ Offline"

        return statuses


class AgentTeam:
    """High-efficiency multi-agent review team for PatchPilot."""

    def __init__(self) -> None:
        self.architect = ArchitectAgent()
        self.security = SecurityAgent()
        self.bug_hunter = BugHunterAgent()
        self.performance = PerformanceAgent()
        self.test_coverage = TestCoverageAgent()
        self.coordinator = SynthesisCoordinator()

    async def run_review(
        self,
        cfg: VaultConfig,
        pr_title: str,
        pr_body: str,
        annotated_diff: str,
        valid_lines: dict[str, set[int]],
        triage: TriageResult | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """Execute parallel triage-routed review across the specialist agent squad."""
        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=120.0)
            close_client = True

        try:
            # 1. Smart Triage-Aware Agent Routing
            is_docs_or_xs = False
            if triage:
                is_docs_or_xs = (
                    triage.size_tier == "size:XS"
                    and not triage.impact_areas
                    and triage.risk_level == "risk:low"
                )

            agent_tasks: list[tuple[str, Any]] = [
                ("ArchitectAgent", self.architect.execute(cfg, pr_title, pr_body, annotated_diff, client=client)),
            ]

            if not is_docs_or_xs:
                agent_tasks.extend([
                    ("SecurityAgent", self.security.execute(cfg, pr_title, pr_body, annotated_diff, client=client)),
                    ("BugHunterAgent", self.bug_hunter.execute(cfg, pr_title, pr_body, annotated_diff, client=client)),
                    ("PerformanceAgent", self.performance.execute(cfg, pr_title, pr_body, annotated_diff, client=client)),
                    ("TestCoverageAgent", self.test_coverage.execute(cfg, pr_title, pr_body, annotated_diff, client=client)),
                ])

            # 2. Parallel Concurrency Execution
            names = [name for name, _ in agent_tasks]
            coroutines = [coro for _, coro in agent_tasks]
            raw_results = await asyncio.gather(*coroutines, return_exceptions=True)

            results_map: dict[str, AgentResult] = {}
            for name, res in zip(names, raw_results):
                if isinstance(res, AgentResult):
                    results_map[name] = res
                elif isinstance(res, Exception):
                    logger.error("Agent %s encountered unhandled exception: %s", name, res)
                    results_map[name] = AgentResult(agent_name=name, status="error", error_message=str(res))
                else:
                    results_map[name] = AgentResult(agent_name=name, status="error", error_message="Unknown error")

            # Record skipped agents for reporting
            if is_docs_or_xs:
                for skipped_name in ["SecurityAgent", "BugHunterAgent", "PerformanceAgent", "TestCoverageAgent"]:
                    results_map[skipped_name] = AgentResult(agent_name=skipped_name, status="skipped")

            # 3. Consolidate & Deduplicate Findings
            all_findings: list[AgentFinding] = []
            for agent_name, agent_res in results_map.items():
                if agent_res.status == "success":
                    all_findings.extend(agent_res.findings)

            clean_findings = self.coordinator.filter_and_deduplicate(all_findings, valid_lines)

            # 4. Extract High-Level Metadata
            architect_res = results_map.get("ArchitectAgent")
            summary = (
                architect_res.metadata.get("summary", "Review completed successfully.")
                if architect_res and architect_res.status == "success"
                else "Review completed."
            )
            poem = (
                architect_res.metadata.get("poem", "")
                if architect_res and architect_res.status == "success"
                else ""
            )
            walkthrough = (
                architect_res.metadata.get("walkthrough", [])
                if architect_res and architect_res.status == "success"
                else []
            )

            test_res = results_map.get("TestCoverageAgent")
            suggested_tests = (
                test_res.metadata.get("suggested_tests", [])
                if test_res and test_res.status == "success"
                else []
            )

            # 5. Compute Consolidated Verdict
            bug_count = sum(1 for f in clean_findings if f.severity == "bug")
            risk_count = sum(1 for f in clean_findings if f.severity == "risk")

            if bug_count > 0:
                verdict = "BLOCK"
            elif risk_count >= 2:
                verdict = "WARN"
            else:
                verdict = "PASS"

            # 6. Compute Agent Squad Statuses
            agent_statuses = self.coordinator.compute_agent_statuses(results_map)

            # Standardized serializable payload
            findings_dicts = [
                {
                    "file": f.file,
                    "line": f.line,
                    "severity": f.severity,
                    "title": f"[{f.agent_name.replace('Agent', '')}] {f.title}",
                    "explanation": f.explanation,
                    "suggestion": f.suggestion,
                }
                for f in clean_findings
            ]

            return {
                "summary": summary,
                "verdict": verdict,
                "findings": findings_dicts,
                "walkthrough": walkthrough,
                "poem": poem,
                "suggested_tests": suggested_tests,
                "agent_statuses": agent_statuses,
            }
        finally:
            if close_client:
                await client.aclose()


global_agent_team = AgentTeam()
