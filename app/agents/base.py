"""Base abstractions and data contracts for PatchPilot Specialist AI Agents."""

from __future__ import annotations

import abc
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
import httpx
from app.crypto import decrypt_secret
from app.db import VaultConfig

logger = logging.getLogger("patchpilot.agents")

PROVIDER_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "grok": "https://api.x.ai/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "ollama": "http://localhost:11434/v1/chat/completions",
}


@dataclass
class AgentFinding:
    """Individual code finding identified by a specialist agent."""

    file: str
    line: int
    severity: str  # "bug" | "risk" | "nit"
    title: str
    explanation: str
    suggestion: str = ""
    agent_name: str = "SpecialistAgent"
    confidence: float = 1.0


@dataclass
class AgentResult:
    """Consolidated outcome from a single specialist agent execution."""

    agent_name: str
    status: str  # "success" | "skipped" | "error"
    findings: list[AgentFinding] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


def resolve_endpoint_and_headers(cfg: VaultConfig) -> tuple[str, dict[str, str]]:
    """Resolve endpoint URL and authorization headers for the configured provider."""
    provider = cfg.provider.lower().strip()
    if cfg.base_url:
        base = cfg.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            url = base
        elif base.endswith("/v1"):
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/v1/chat/completions"
    else:
        url = PROVIDER_URLS.get(provider, PROVIDER_URLS["openrouter"])

    api_key = ""
    if cfg.encrypted_api_key:
        api_key = decrypt_secret(cfg.encrypted_api_key) or "ollama"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/PatchPilot/PatchPilot"
        headers["X-Title"] = "PatchPilot Reviewer"

    return url, headers


def extract_json_object(content: str) -> dict[str, Any]:
    """Extract and parse a JSON dictionary from LLM markdown fenced text or raw response."""
    text = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"(\{.*\})", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(1))
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(text)
    except Exception as exc:
        logger.warning("Failed parsing agent response as JSON: %s (excerpt: %r)", exc, text[:120])
        return {}


class SpecialistAgent(abc.ABC):
    """Abstract base class for all PatchPilot specialist review agents."""

    name: str = "BaseAgent"
    role_description: str = "Specialist reviewer"

    @abc.abstractmethod
    def build_system_prompt(self, policy: str) -> str:
        """Construct role-specific system prompt."""
        raise NotImplementedError

    @abc.abstractmethod
    def build_user_prompt(self, pr_title: str, pr_body: str, annotated_diff: str) -> str:
        """Construct role-specific diff and context prompt."""
        raise NotImplementedError

    @abc.abstractmethod
    def parse_response(self, raw_data: dict[str, Any]) -> AgentResult:
        """Parse structured model dictionary into standardized AgentResult."""
        raise NotImplementedError

    async def execute(
        self,
        cfg: VaultConfig,
        pr_title: str,
        pr_body: str,
        annotated_diff: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> AgentResult:
        """Execute agent against target LLM provider asynchronously."""
        url, headers = resolve_endpoint_and_headers(cfg)
        sys_prompt = self.build_system_prompt(cfg.review_policy or "")
        usr_prompt = self.build_user_prompt(pr_title, pr_body, annotated_diff)

        payload = {
            "model": cfg.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": usr_prompt},
            ],
        }

        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=timeout)
            close_client = True

        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            parsed_json = extract_json_object(content)
            return self.parse_response(parsed_json)
        except Exception as exc:
            logger.exception("Agent %s failed: %s", self.name, exc)
            return AgentResult(
                agent_name=self.name,
                status="error",
                error_message=str(exc),
            )
        finally:
            if close_client:
                await client.aclose()
