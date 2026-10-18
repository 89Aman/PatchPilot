import json
import logging
import os
import re
from typing import Any
import httpx
from app.crypto import decrypt_secret
from app.db import VaultConfig

logger = logging.getLogger(__name__)

PROVIDER_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "grok": "https://api.x.ai/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "ollama": "http://localhost:11434/v1/chat/completions",
}

EXCLUDED_SUFFIXES = (
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "uv.lock",
    "poetry.lock",
    "pipfile.lock",
    "cargo.lock",
    "composer.lock",
    "go.sum",
    ".vsix",
    ".min.js",
    ".min.css",
    ".map",
)

HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")


def is_noise_file(filepath: str) -> bool:
    """Determine if a file should be excluded from review analysis."""
    basename = os.path.basename(filepath).lower()
    return any(basename.endswith(suffix) or filepath.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def annotate_diff(raw_diff: str, max_chars: int = 120_000) -> tuple[str, dict[str, set[int]]]:
    """Parse unified diff, exclude noise files, and annotate right-side line numbers."""
    valid_lines: dict[str, set[int]] = {}
    out: list[str] = []
    current_file: str | None = None
    skip_file = False
    right_line = 0

    diff_git_re = re.compile(r"^diff --git a/.* b/(.*)$")
    header_plus_re = re.compile(r"^\+\+\+ (?:b/)?(.*)$")

    for line in raw_diff.splitlines():
        git_match = diff_git_re.match(line)
        if git_match:
            raw_path = git_match.group(1).strip()
            current_file = raw_path
            skip_file = is_noise_file(raw_path)
            if skip_file:
                out.append(f"\n[File excluded as noise/lockfile: {current_file}]")
            else:
                valid_lines.setdefault(current_file, set())
                out.append(f"\n--- {current_file} ---")
            continue

        plus_match = header_plus_re.match(line)
        if plus_match and not git_match:
            cand_path = plus_match.group(1).strip()
            if cand_path != "/dev/null":
                current_file = cand_path
                skip_file = is_noise_file(current_file)
                if skip_file:
                    out.append(f"\n[File excluded as noise/lockfile: {current_file}]")
                else:
                    valid_lines.setdefault(current_file, set())
                    out.append(f"\n--- {current_file} ---")
            continue

        if skip_file:
            continue

        m = HUNK_RE.match(line)
        if m:
            right_line = int(m.group(1))
            out.append(line)
            continue

        if not current_file or line.startswith(("diff --git", "index ", "--- ")):
            out.append(line)
            continue

        if line.startswith("+") or line.startswith(" "):
            if current_file:
                valid_lines.setdefault(current_file, set()).add(right_line)
            out.append(f"[L{right_line}] {line}")
            right_line += 1
        elif line.startswith("-"):
            out.append(f"[DEL] {line}")
        else:
            out.append(line)

    return "\n".join(out)[:max_chars], valid_lines


def get_provider_endpoint(provider: str, base_url: str | None = None) -> str:
    """Resolve API endpoint URL based on model provider."""
    p = provider.lower().strip()
    if base_url:
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        elif base.endswith("/v1"):
            return f"{base}/chat/completions"
        else:
            return f"{base}/v1/chat/completions"
    return PROVIDER_URLS.get(p, PROVIDER_URLS["openrouter"])


def _resolve_endpoint_and_headers(cfg: VaultConfig) -> tuple[str, dict[str, str]]:
    url = get_provider_endpoint(cfg.provider, cfg.base_url)
    api_key = ""
    if cfg.encrypted_api_key:
        api_key = decrypt_secret(cfg.encrypted_api_key) or "ollama"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if cfg.provider.lower() == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/PatchPilot/PatchPilot"
        headers["X-Title"] = "PatchPilot Reviewer"
    return url, headers


def extract_json_payload(content: str) -> dict[str, Any]:
    """Extract and parse JSON object from LLM response text."""
    content_trimmed = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content_trimmed, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"(\{.*\})", content_trimmed, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(1))
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(content_trimmed)
    except Exception as exc:
        logger.warning("Failed parsing LLM output as JSON: %s (first 100 chars: %r)", exc, content_trimmed[:100])
        return {
            "summary": content_trimmed[:500] if content_trimmed else "Review completed without valid JSON payload.",
            "verdict": "WARN",
            "findings": [],
        }


async def test_llm_connection(cfg: VaultConfig, client: httpx.AsyncClient | None = None) -> str:
    """Test connection to the configured LLM provider and return response."""
    url, headers = _resolve_endpoint_and_headers(cfg)
    payload = {
        "model": cfg.model,
        "temperature": 0.0,
        "max_tokens": 30,
        "messages": [{"role": "user", "content": "Reply with the single word READY."}],
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=25.0)
        close_client = True

    try:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    finally:
        if close_client:
            await client.aclose()


async def run_pr_review(
    cfg: VaultConfig,
    pr_title: str,
    pr_body: str,
    annotated_diff: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Execute AI review of pull request and return structured findings."""
    url, headers = _resolve_endpoint_and_headers(cfg)

    policy_section = cfg.review_policy or ""
    system_prompt = (
        "You are PatchPilot AI, an expert code reviewer.\n"
        f"{policy_section}\n\n"
        "Return strictly valid JSON matching this schema:\n"
        "{\n"
        '  "summary": "High-level summary of the PR and overall code quality.",\n'
        '  "verdict": "PASS|WARN|BLOCK",\n'
        '  "findings": [\n'
        "    {\n"
        '      "file": "path/to/file.ext",\n'
        '      "line": 10,\n'
        '      "severity": "bug|risk|nit",\n'
        '      "title": "Short title describing the issue",\n'
        '      "explanation": "Detailed explanation of why this is an issue and how to resolve it",\n'
        '      "suggestion": "Optional replacement code line or empty string"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "1. 'line' MUST match an [L<num>] line in the file diff.\n"
        "2. Do not comment on deleted lines.\n"
        "3. If no issues exist, 'findings' must be [] and 'verdict' must be 'PASS'."
    )

    user_prompt = (
        f"PR Title: {pr_title}\n"
        f"PR Description: {pr_body or 'None'}\n\n"
        f"Annotated Diff (`[L<num>]` is line number):\n```diff\n{annotated_diff[:80000]}\n```"
    )

    payload = {
        "model": cfg.model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=120.0)
        close_client = True

    try:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return extract_json_payload(content)
    finally:
        if close_client:
            await client.aclose()


async def run_interactive_command(
    cfg: VaultConfig,
    command: str,
    user_comment: str,
    file_path: str | None,
    diff_hunk: str | None,
    pr_diff: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Execute interactive @patchpilot explain or @patchpilot fix command."""
    url, headers = _resolve_endpoint_and_headers(cfg)

    mode_instruction = (
        "Explain the code, root cause, and architectural trade-offs concisely."
        if command == "explain"
        else "Provide the exact fix with clear markdown code blocks."
    )

    system_prompt = (
        f"{cfg.review_policy or ''}\n\n"
        f"You are replying to an interactive `@patchpilot {command}` command on a GitHub Pull Request.\n"
        f"{mode_instruction}"
    )

    context_parts = [f"User Request: {user_comment}"]
    if file_path:
        context_parts.append(f"Target File: `{file_path}`")
    if diff_hunk:
        context_parts.append(f"Target Diff Hunk:\n```diff\n{diff_hunk}\n```")
    else:
        context_parts.append(f"PR Diff Context:\n```diff\n{pr_diff[:40_000]}\n```")

    payload = {
        "model": cfg.model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n\n".join(context_parts)},
        ],
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=90.0)
        close_client = True

    try:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    finally:
        if close_client:
            await client.aclose()