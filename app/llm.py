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


def _check_response_status(resp: httpx.Response, cfg: VaultConfig) -> None:
    """Validate HTTP response and extract upstream provider error messages."""
    if resp.is_error:
        provider = (cfg.provider or "API").capitalize()
        error_msg = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                error_obj = body.get("error", {})
                if isinstance(error_obj, dict):
                    error_msg = error_obj.get("message", "")
                elif isinstance(error_obj, str):
                    error_msg = error_obj
                if not error_msg:
                    error_msg = str(body.get("detail", ""))
        except Exception:
            pass

        if not error_msg:
            error_msg = resp.text[:200]

        if resp.status_code == 404:
            raise ValueError(
                f"{provider} returned 404 Not Found: '{error_msg or 'Resource or model not found'}'. "
                f"Verify model '{cfg.model}' exists and is currently active on {provider}. "
                f"Recommended active Groq models: 'llama-3.3-70b-versatile', 'llama-3.1-8b-instant'."
            )
        elif resp.status_code == 401:
            raise ValueError(f"{provider} returned 401 Unauthorized: Invalid API key.")
        elif resp.status_code == 429:
            raise ValueError(f"{provider} rate limit reached (429): {error_msg}")
        else:
            raise ValueError(f"{provider} error ({resp.status_code}): {error_msg}")


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
        _check_response_status(resp, cfg)
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
    valid_lines: dict[str, set[int]] | None = None,
    triage: Any | None = None,
) -> dict[str, Any]:
    """Execute unified AI review of pull request with CodeRabbit-grade walkthrough, suggestions, and test stubs."""
    url, headers = _resolve_endpoint_and_headers(cfg)

    policy_section = cfg.review_policy or ""
    system_prompt = (
        "You are PatchPilot, an expert AI pull request reviewer modeled after CodeRabbit.\n"
        f"{policy_section}\n\n"
        "Analyze the pull request changes thoroughly across Architecture, Security (OWASP), Bug/Logic Correctness, "
        "Performance (O(N^2), DB N+1, I/O), and Test Coverage.\n\n"
        "Return strictly valid JSON matching this schema:\n"
        "{\n"
        '  "summary": "High-level summary of the PR and overall code quality.",\n'
        '  "poem": "Optional clever 2-line rhyming summary of the changes.",\n'
        '  "walkthrough": [\n'
        "    {\n"
        '      "file": "path/to/file.ext",\n'
        '      "impact": "✨ Feature | 🐛 Fix | ⚡ Perf | 📝 Docs | 🧹 Refactor | 🛡️ Security",\n'
        '      "summary": "One concise sentence describing what changed in this specific file."\n'
        "    }\n"
        "  ],\n"
        '  "verdict": "PASS|WARN|BLOCK",\n'
        '  "findings": [\n'
        "    {\n"
        '      "file": "path/to/file.ext",\n'
        '      "line": 10,\n'
        '      "severity": "bug|risk|nit",\n'
        '      "title": "Short title describing the issue",\n'
        '      "explanation": "Detailed explanation of why this is an issue and how to resolve it",\n'
        '      "suggestion": "Optional exact replacement code line(s) or empty string"\n'
        "    }\n"
        "  ],\n"
        '  "suggested_tests": [\n'
        "    {\n"
        '      "target_file": "path/to/file.ext",\n'
        '      "description": "Untested edge-case or failure mode description",\n'
        '      "code_snippet": "Runnable unit test function in project language"\n'
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
        _check_response_status(resp, cfg)
        content = resp.json()["choices"][0]["message"]["content"]
        data = extract_json_payload(content)

        raw_findings = data.get("findings", [])
        clean_findings: list[dict[str, Any]] = []

        # Filter against valid lines to prevent hallucinated line comments
        for f in raw_findings:
            if not isinstance(f, dict):
                continue
            file_path = f.get("file")
            line = f.get("line")
            if not file_path or line is None:
                continue

            if valid_lines is not None:
                valid_for_file = valid_lines.get(str(file_path), set())
                if int(line) not in valid_for_file:
                    continue

            clean_findings.append({
                "file": str(file_path),
                "line": int(line),
                "severity": str(f.get("severity", "risk")).lower(),
                "title": str(f.get("title", "")),
                "explanation": str(f.get("explanation", "")),
                "suggestion": str(f.get("suggestion", "")),
            })

        data["findings"] = clean_findings

        # Calculate review check category statuses
        bugs = sum(1 for f in clean_findings if f["severity"] == "bug")
        risks = sum(1 for f in clean_findings if f["severity"] == "risk")
        suggested_tests = data.get("suggested_tests", [])

        checks = {
            "🛡️ Security": "🟢 Clean" if not any("security" in f["title"].lower() or "secret" in f["title"].lower() for f in clean_findings) else "🔴 Issues Found",
            "🐞 Correctness": f"🟡 {bugs} Defect{'s' if bugs > 1 else ''}" if bugs > 0 else "🟢 Clean",
            "⚡ Performance": "🟡 Review Needed" if any("perf" in f["title"].lower() or "o(n" in f["title"].lower() for f in clean_findings) else "🟢 Optimal",
            "🧪 Test Coverage": f"🧪 {len(suggested_tests)} Test Case{'s' if len(suggested_tests) > 1 else ''} Suggested" if suggested_tests else "🟢 Adequate",
        }
        data["review_checks"] = checks
        data["agent_statuses"] = checks  # Backward compatibility alias

        return data
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
    """Execute interactive @patchpilot explain, fix, review, or generate tests command."""
    url, headers = _resolve_endpoint_and_headers(cfg)
    cmd_lower = command.lower().strip()

    if "explain" in cmd_lower:
        mode_instruction = "Explain the code, root cause, and architectural trade-offs concisely."
    elif "fix" in cmd_lower:
        mode_instruction = "Provide the exact fix with clear markdown code blocks and git suggestion syntax if applicable."
    elif "security" in cmd_lower:
        mode_instruction = "Perform an in-depth security and vulnerability audit (OWASP, injections, auth bypass, data leaks)."
    elif "perf" in cmd_lower:
        mode_instruction = "Analyze algorithmic complexity, database query patterns (N+1), memory bloat, and I/O efficiency."
    elif any(k in cmd_lower for k in ["test", "tests"]):
        mode_instruction = "Generate concrete, runnable unit test functions covering untested branches and edge cases."
    else:
        mode_instruction = "Review the requested code changes constructively and objectively."

    system_prompt = (
        f"{cfg.review_policy or ''}\n\n"
        f"You are PatchPilot responding to an interactive `{command}` command on a GitHub Pull Request.\n"
        f"{mode_instruction}"
    )

    context_parts = [f"User Directive: {user_comment}"]
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
        _check_response_status(resp, cfg)
        return resp.json()["choices"][0]["message"]["content"]
    finally:
        if close_client:
            await client.aclose()