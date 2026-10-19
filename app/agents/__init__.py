"""PatchPilot AI Agents Package."""

from app.agents.architect import ArchitectAgent
from app.agents.base import AgentFinding, AgentResult, SpecialistAgent
from app.agents.bug_hunter import BugHunterAgent
from app.agents.coordinator import AgentTeam, SynthesisCoordinator, global_agent_team
from app.agents.performance import PerformanceAgent
from app.agents.security import SecurityAgent
from app.agents.test_coverage import TestCoverageAgent

__all__ = [
    "AgentFinding",
    "AgentResult",
    "SpecialistAgent",
    "ArchitectAgent",
    "SecurityAgent",
    "BugHunterAgent",
    "PerformanceAgent",
    "TestCoverageAgent",
    "SynthesisCoordinator",
    "AgentTeam",
    "global_agent_team",
]
