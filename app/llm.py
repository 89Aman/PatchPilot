import json
import logging
import os
import re
from typing import Any
import httpx
from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import DEFAULT_POLICY, VaultConfig
from app.github_auth import strip_emojis
from app.security import sanitize_diff_text

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


def budget_text(text: str, limit: int) -> str:
    """Keep complete lines within a hard budget and explicitly disclose omissions."""
    limit = max(0, limit)
    if len(text) <= limit:
        return text
    notice = "[Coverage limited: remaining lines omitted]"
    if limit < len(notice):
        return ""
    out = []
    remaining = limit - len(notice) - 1
    for line in text.splitlines():
        if len(line) + 1 > remaining:
            break
        out.append(line)
        remaining -= len(line) + 1
    return "\n".join(out + [notice])


def visible_lines(text: str) -> dict[str, set[int]]:
    result = {}
    path = None
    for line in text.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            path = line[4:-4]
            result.setdefault(path, set())
        match = re.match(r"\[L(\d+)\]", line)
        if path and match:
            result[path].add(int(match.group(1)))
    return result


def _excerpt_file_lines_safely(lines: list[str], budget_chars: int) -> str:
    """Keep the file heading and complete head/tail lines within an exact budget."""
    if len("\n".join(lines)) <= budget_chars:
        return "\n".join(lines)
    notice = "[Coverage limited: lines omitted]"
    if budget_chars < len(notice):
        return ""
    selected: dict[int, str] = {}
    remaining = budget_chars - len(notice)
    # Alternate from both ends, skipping oversized lines rather than stopping.
    order = []
    for i in range((len(lines) + 1) // 2):
        order.append(i)
        if len(lines) - 1 - i != i:
            order.append(len(lines) - 1 - i)
    for i in order:
        if len(lines[i]) + 1 <= remaining:
            selected[i] = lines[i]
            remaining -= len(lines[i]) + 1
    return "\n".join([selected[i] for i in sorted(selected)] + [notice])


def annotate_diff(raw_diff: str, max_chars: int = 32_000) -> tuple[str, dict[str, set[int]]]:
    """Parse unified diff, exclude noise files, annotate right-side line numbers, and budget across all files without mid-line cuts."""
    raw_diff = sanitize_diff_text(raw_diff)
    valid_lines: dict[str, set[int]] = {}
    file_blocks: list[tuple[str | None, bool, list[str]]] = []

    current_file: str | None = None
    skip_file = False
    right_line = 0
    current_lines: list[str] = []

    diff_git_re = re.compile(r"^diff --git a/.* b/(.*)$")
    header_plus_re = re.compile(r"^\+\+\+ (?:b/)?(.*)$")

    for line in raw_diff.splitlines():
        git_match = diff_git_re.match(line)
        if git_match:
            if current_lines:
                file_blocks.append((current_file, skip_file, current_lines))
                current_lines = []
            raw_path = git_match.group(1).strip()
            current_file = raw_path
            skip_file = is_noise_file(raw_path)
            if skip_file:
                current_lines.append(f"\n[File excluded as noise/lockfile: {current_file}]")
            else:
                valid_lines.setdefault(current_file, set())
                current_lines.append(f"\n--- {current_file} ---")
            continue

        plus_match = header_plus_re.match(line)
        if plus_match and not git_match:
            cand_path = plus_match.group(1).strip()
            if cand_path != "/dev/null":
                if current_file != cand_path and current_lines and current_file is None:
                    file_blocks.append((current_file, skip_file, current_lines))
                    current_lines = []
                current_file = cand_path
                skip_file = is_noise_file(current_file)
                if skip_file:
                    current_lines.append(f"\n[File excluded as noise/lockfile: {current_file}]")
                else:
                    valid_lines.setdefault(current_file, set())
                    if not any(ln == f"\n--- {current_file} ---" for ln in current_lines):
                        current_lines.append(f"\n--- {current_file} ---")
            continue

        if skip_file:
            continue

        m = HUNK_RE.match(line)
        if m:
            right_line = int(m.group(1))
            current_lines.append(line)
            continue

        if not current_file or line.startswith(("diff --git", "index ", "--- ")):
            current_lines.append(line)
            continue

        if line.startswith("+") or line.startswith(" "):
            if current_file:
                valid_lines.setdefault(current_file, set()).add(right_line)
            current_lines.append(f"[L{right_line}] {line}")
            right_line += 1
        elif line.startswith("-"):
            current_lines.append(f"[DEL] {line}")
        else:
            current_lines.append(line)

    if current_lines:
        file_blocks.append((current_file, skip_file, current_lines))

    full_joined = "\n".join("\n".join(blk[2]) for blk in file_blocks)
    if len(full_joined) <= max_chars:
        return full_joined, valid_lines

    # Reserve separators and a coverage notice before sharing space across files.
    notice = "[Coverage limited: file lines omitted]"
    max_chars = max(0, max_chars)
    if max_chars < len(notice):
        return "", {}
    remaining = max_chars - len(notice)
    blocks = ["\n".join(block[2]).strip("\n") for block in file_blocks]
    allocations = [0] * len(blocks)
    pending = list(range(len(blocks)))
    while pending:
        share = max(0, remaining // len(pending) - 1)
        fits = [i for i in pending if len(blocks[i]) <= share]
        if not fits:
            for i in pending:
                allocations[i] = share
            break
        for i in fits:
            allocations[i] = len(blocks[i])
            remaining -= len(blocks[i]) + 1
            pending.remove(i)

    out = []
    for block, allocation in zip(blocks, allocations):
        lines = block.splitlines()
        # Retain a heading whenever any file content is included.
        heading = lines[0] if lines else ""
        if len(block) <= allocation:
            excerpt = block
        elif len(heading) + 1 <= allocation:
            body = _excerpt_file_lines_safely(lines[1:], allocation - len(heading) - 1)
            excerpt = heading + "\n" + body
        else:
            excerpt = ""
        if excerpt:
            out.append(excerpt)
    bounded = "\n".join(out + [notice])
    return bounded, visible_lines(bounded)


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
        try:
            api_key = (decrypt_secret(cfg.encrypted_api_key) or "").strip()
        except Exception:
            api_key = ""
    if not api_key:
        api_key = "ollama" if str(cfg.provider).lower() == "ollama" else "missing-api-key"

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
    if not isinstance(content, str):
        raise ValueError("Invalid model response content")
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
            "_parse_error": True,
            "summary": content_trimmed[:500] if content_trimmed else "Review completed without valid JSON payload.",
            "verdict": "WARN",
            "findings": [],
        }


FALLBACK_TRIGGER_STATUS_CODES = {400, 401, 402, 403, 404, 413, 429, 500, 502, 503, 529}


def sanitize_failure_reason(status_code: int | None, exc: Exception | None = None) -> str:
    """Produce sanitized, user-safe failure reason string without raw URLs, tokens, or tracebacks."""
    if status_code == 429:
        return "Rate limit exceeded (429)"
    elif status_code == 413:
        return "Payload exceeds provider token limit (413)"
    elif status_code == 402:
        return "Daily free quota exhausted (402)"
    elif status_code == 401:
        return "Authentication failed (401)"
    elif status_code == 400:
        return "Authentication or request rejected (400)"
    elif status_code == 403:
        return "Access forbidden (403)"
    elif status_code == 404:
        return "Model not found or retired (404)"
    elif status_code == 529:
        return "Provider overloaded (529)"
    elif status_code in (500, 502, 503):
        return f"Upstream provider outage ({status_code})"
    elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "Provider connection timeout"
    elif isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return "Provider network connection failed"
    return f"Provider error ({status_code})" if status_code else "Connection failed"


def build_managed_vault_config(
    owner: str,
    review_policy: str | None = None,
) -> VaultConfig | None:
    """Build an ephemeral in-memory VaultConfig backed by PatchPilot's server-side Managed AI pool."""
    primary_provider = settings.MANAGED_LLM_PRIMARY_PROVIDER.strip().lower() or "gemini"
    primary_model = settings.MANAGED_LLM_PRIMARY_MODEL.strip() or "gemini-2.5-flash"
    primary_key = settings.MANAGED_LLM_PRIMARY_API_KEY.strip()
    primary_base_url = settings.MANAGED_LLM_PRIMARY_BASE_URL.strip() or None

    fb_provider = settings.MANAGED_LLM_FALLBACK_PROVIDER.strip().lower()
    fb_model = settings.MANAGED_LLM_FALLBACK_MODEL.strip()
    fb_key = settings.MANAGED_LLM_FALLBACK_API_KEY.strip()
    fb_base_url = settings.MANAGED_LLM_FALLBACK_BASE_URL.strip() or None

    has_primary = bool(primary_key) or primary_provider == "ollama"
    has_fallback = bool(fb_key and fb_provider and fb_model)
    if not has_primary and not has_fallback:
        return None

    fallbacks: list[dict[str, Any]] = []
    if has_fallback:
        fallbacks.append({
            "id": "managed_fallback_1",
            "name": "Managed Fallback",
            "provider": fb_provider,
            "model": fb_model,
            "encrypted_api_key": encrypt_secret(fb_key),
            "base_url": fb_base_url,
            "is_enabled": True,
        })

    return VaultConfig(
        owner=owner.strip().lower(),
        provider=primary_provider,
        model=primary_model,
        encrypted_api_key=encrypt_secret(primary_key) if primary_key else "",
        base_url=primary_base_url,
        review_policy=(review_policy or DEFAULT_POLICY),
        fallbacks_json=json.dumps(fallbacks),
    )


def get_provider_candidates(cfg: VaultConfig, *, allow_fallbacks: bool = True) -> list[dict[str, Any]]:
    """Extract ordered list of provider candidates: Primary first, followed by enabled fallbacks."""
    candidates = [
        {
            "id": "primary",
            "name": "Primary",
            "provider": cfg.provider,
            "model": cfg.model,
            "encrypted_api_key": cfg.encrypted_api_key,
            "base_url": cfg.base_url,
            "is_primary": True,
        }
    ]

    if allow_fallbacks and getattr(cfg, "fallbacks_json", None):
        try:
            raw = json.loads(cfg.fallbacks_json)
            if isinstance(raw, list):
                for idx, fb in enumerate(raw, 1):
                    if isinstance(fb, dict) and fb.get("is_enabled", True):
                        candidates.append({
                            "id": str(fb.get("id", f"fb_{idx}")),
                            "name": str(fb.get("name", f"Fallback {idx}")),
                            "provider": str(fb.get("provider", "gemini")),
                            "model": str(fb.get("model", "gemini-2.5-flash")),
                            "encrypted_api_key": str(fb.get("encrypted_api_key", "")),
                            "base_url": fb.get("base_url"),
                            "is_primary": False,
                        })
        except Exception as exc:
            logger.warning("Failed parsing fallbacks_json for %s: %s", getattr(cfg, "owner", "unknown"), exc)

    return candidates


def _resolve_candidate_endpoint_and_headers(candidate: dict[str, Any]) -> tuple[str, dict[str, str]]:
    url = get_provider_endpoint(candidate["provider"], candidate.get("base_url"))
    api_key = ""
    enc_key = candidate.get("encrypted_api_key")
    if enc_key:
        try:
            api_key = (decrypt_secret(enc_key) or "").strip()
        except Exception:
            api_key = ""
    if not api_key:
        api_key = "ollama" if str(candidate.get("provider", "")).lower() == "ollama" else "missing-api-key"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if str(candidate["provider"]).lower() == "openrouter":
        headers["HTTP-Referer"] = settings.app_base_url or "https://github.com/PatchPilot"
        headers["X-Title"] = "PatchPilot Reviewer"
    return url, headers


def _check_response_status(resp: httpx.Response, cfg: VaultConfig | dict[str, Any]) -> None:
    """Validate HTTP response and extract upstream provider error messages."""
    if resp.is_error:
        provider = (getattr(cfg, "provider", None) or (cfg.get("provider") if isinstance(cfg, dict) else "") or "API").capitalize()
        model_name = getattr(cfg, "model", None) or (cfg.get("model") if isinstance(cfg, dict) else "")
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
                f"Verify model '{model_name}' exists and is currently active on {provider}. "
                f"Recommended active Groq models: 'llama-3.3-70b-versatile', 'llama-3.1-8b-instant'."
            )
        elif resp.status_code == 401:
            raise ValueError(f"{provider} returned 401 Unauthorized: Invalid API key.")
        elif resp.status_code == 402:
            raise ValueError(f"{provider} returned 402 Payment Required: Free-tier credit quota exhausted.")
        elif resp.status_code == 403:
            raise ValueError(f"{provider} returned 403 Forbidden: Access denied or region restricted.")
        elif resp.status_code == 429:
            raise ValueError(f"{provider} rate limit reached (429): {error_msg}")
        elif resp.status_code == 529:
            raise ValueError(f"{provider} capacity overloaded (529): {error_msg}")
        else:
            raise ValueError(f"{provider} error ({resp.status_code}): {error_msg}")


async def test_llm_connection(
    cfg: VaultConfig,
    candidate: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Test connection to the configured primary or fallback LLM provider and return response."""
    cand = candidate or {
        "id": "primary",
        "name": "Primary",
        "provider": cfg.provider,
        "model": cfg.model,
        "encrypted_api_key": cfg.encrypted_api_key,
        "base_url": cfg.base_url,
        "is_primary": True,
    }
    url, headers = _resolve_candidate_endpoint_and_headers(cand)
    payload = {
        "model": cand["model"],
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
        _check_response_status(resp, cand)
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    finally:
        if close_client:
            await client.aclose()


MAX_PROMPT_CHARS = 48_000


def validate_findings(findings: Any) -> list[dict[str, Any]]:
    if not isinstance(findings, list):
        raise ValueError("Invalid findings list")
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("Invalid review finding")
        line = finding.get("line")
        if isinstance(line, str) and line.strip().isdigit():
            line = int(line.strip())
        if type(line) is not int or line <= 0 or not isinstance(finding.get("file"), str) or not finding["file"].strip():
            raise ValueError("Invalid finding location")
        finding["line"] = line
        for field in ("severity", "title", "explanation", "suggestion", "agent_prompt"):
            if field in finding and not isinstance(finding[field], str):
                raise ValueError("Invalid finding field type")
        if finding.get("severity", "risk").lower().strip() not in {"bug", "risk", "nit"}:
            raise ValueError("Invalid finding severity")
    return findings


def validate_review(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("_parse_error") or data.get("verdict") not in ("PASS", "WARN", "BLOCK"):
        raise ValueError("Invalid review response schema")
    validate_findings(data.get("findings"))
    for field in ("summary", "poem", "architecture_diagram"):
        if field in data and not isinstance(data[field], str):
            raise ValueError("Invalid review text field")
    for field in ("walkthrough", "suggested_tests"):
        if field in data and (not isinstance(data[field], list) or any(not isinstance(row, dict) or any(not isinstance(value, str) for value in row.values()) for row in data[field])):
            raise ValueError("Invalid review detail field")
    return data


async def _verify_findings_with_llm(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    model: str,
    candidate_findings: list[dict[str, Any]],
    annotated_diff: str,
) -> list[dict[str, Any]]:
    """Second-pass truthfulness verifier: fact-check candidate findings against the annotated diff to eliminate false positives."""
    if not candidate_findings:
        return []

    verify_system_prompt = (
        "You are PatchPilot Verification Engine, a strict second-pass code review fact-checker.\n"
        "Redaction placeholders are transport substitutions, never original source. Do not infer bugs from them.\n"
        "Your job is to verify each candidate finding against the annotated diff and eliminate any false positives.\n\n"
        "Verification Rules:\n"
        "1. Check whether the reported issue is genuinely present in the added (`+`) or modified lines of the diff.\n"
        "2. Reject any finding that claims a file or line is 'truncated' or 'incomplete' if the diff was simply excerpted.\n"
        "3. Reject speculative or hallucinated issues where the code already handles the condition properly.\n"
        "4. Do NOT use any emojis anywhere in titles, explanations, or agent_prompt.\n"
        "5. Return strictly valid JSON with schema:\n"
        "{\n"
        '  "verified_findings": [\n'
        "    {\n"
        '      "file": "path/to/file.ext",\n'
        '      "line": 10,\n'
        '      "severity": "bug|risk|nit",\n'
        '      "title": "Short verified title",\n'
        '      "explanation": "Fact-checked explanation of root cause and fix",\n'
        '      "suggestion": "Optional replacement code line(s) or empty string",\n'
        '      "agent_prompt": "Concise 1-2 sentence instruction for an AI coding assistant to apply the fix"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    verify_user_prompt = (
        f"Candidate Findings to Fact-Check:\n```json\n{json.dumps(candidate_findings, indent=2)}\n```\n\n"
        f"Annotated Diff Ground Truth:\n```diff\n{annotated_diff}\n```"
    )

    payload = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": verify_system_prompt},
            {"role": "user", "content": sanitize_diff_text(verify_user_prompt)},
        ],
    }

    if sum(len(message["content"]) for message in payload["messages"]) > MAX_PROMPT_CHARS:
        raise ValueError("Verifier prompt exceeds budget; review is inconclusive")

    try:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = extract_json_payload(content)
        verified = validate_findings(parsed.get("verified_findings"))
        if isinstance(verified, list):
            if any(not isinstance(f, dict) or not f.get("file") or not isinstance(f.get("line"), int) for f in verified):
                raise ValueError("Invalid verified finding")
            result: list[dict[str, Any]] = []
            for vf in verified:
                if isinstance(vf, dict) and vf.get("file") and vf.get("line") is not None:
                    result.append({
                        "file": str(vf["file"]),
                        "line": int(vf["line"]),
                        "severity": strip_emojis(str(vf.get("severity", "risk"))).lower().strip() or "risk",
                        "title": strip_emojis(str(vf.get("title", ""))).strip(),
                        "explanation": strip_emojis(str(vf.get("explanation", ""))).strip(),
                        "suggestion": str(vf.get("suggestion", "")),
                        "agent_prompt": strip_emojis(str(vf.get("agent_prompt", ""))).strip(),
                    })
            return result
    except Exception as exc:
        raise ValueError("Second-pass verification failed") from exc

    raise ValueError("Invalid verifier response")


async def run_pr_review(
    cfg: VaultConfig,
    pr_title: str,
    pr_body: str,
    annotated_diff: str,
    client: httpx.AsyncClient | None = None,
    valid_lines: dict[str, set[int]] | None = None,
    triage: Any | None = None,
) -> dict[str, Any]:
    """Execute unified 2-pass AI review of pull request with fact-checked findings, architecture diagram, walkthrough, and test stubs."""
    # Apply the privacy boundary to all prompt fields, including context/deletions.
    redacted_locations = set()
    active_file = None
    for raw_line in annotated_diff.splitlines():
        if raw_line.startswith("--- ") and raw_line.endswith(" ---"):
            active_file = raw_line[4:-4]
        match = re.match(r"\[L(\d+)\]", raw_line)
        if match and sanitize_diff_text(raw_line) != raw_line:
            redacted_locations.add((active_file, int(match.group(1))))
    annotated_diff = budget_text(sanitize_diff_text(annotated_diff), 32000)
    if valid_lines is not None:
        shown = visible_lines(annotated_diff)
        valid_lines = {path: lines & shown.get(path, set()) for path, lines in valid_lines.items()}
    pr_title = budget_text(sanitize_diff_text(pr_title), 1000)
    pr_body = budget_text(sanitize_diff_text(pr_body), 4000)
    candidates = get_provider_candidates(cfg)
    policy_section = budget_text(sanitize_diff_text(cfg.review_policy or ""), 4000)
    system_prompt = (
        "You are PatchPilot, an expert pull request analysis and verification engine modeled after CodeRabbit.\n"
        f"{policy_section}\n\n"
        "Redaction placeholders are transport substitutions, never original source. Do not infer bugs from them.\n"
        "Analyze the pull request thoroughly across:\n"
        "1. State-Machine & Idempotency Correctness (missing state/status guards before mutations, double-release or duplicate timeline appends on retry).\n"
        "2. Backend & Data-Layer Consistency (accessing private/mock state like `_mock_*` that fails in production DB mode, mutating Pydantic models without validation).\n"
        "3. Frontend & UI Handler Wiring (calls to undefined JS functions, reliance on implicit `event.currentTarget`, hardcoded CSS classes ignoring state).\n"
        "4. Security & Configuration Hygiene (OWASP, exposed secrets, `.gitignore` rules excluding required scripts like `scripts/*`, Dockerfile/CI issues).\n\n"
        "Return strictly valid JSON matching this schema:\n"
        "{\n"
        '  "summary": "High-level technical summary of the PR and overall code quality.",\n'
        '  "poem": "",\n'
        '  "architecture_diagram": "Optional valid Mermaid flowchart LR or sequenceDiagram illustrating how the modified modules interact (quote labels with special characters).",\n'
        '  "walkthrough": [\n'
        "    {\n"
        '      "file": "path/to/file.ext",\n'
        '      "impact": "Feature | Fix | Performance | Docs | Refactor | Security",\n'
        '      "summary": "One concise sentence describing what changed in this specific file."\n'
        "    }\n"
        "  ],\n"
        '  "verdict": "PASS|WARN|BLOCK",\n'
        '  "findings": [\n'
        "    {\n"
        '      "file": "path/to/file.ext",\n'
        '      "line": 10,\n'
        '      "severity": "bug|risk|nit",\n'
        '      "title": "Short title describing the verified issue",\n'
        '      "explanation": "Detailed explanation of why this is an issue and how to resolve it",\n'
        '      "suggestion": "Optional exact replacement code line(s) or empty string",\n'
        '      "agent_prompt": "Concise 1-2 sentence copy-ready instruction telling an AI coding agent how to fix this exact line"\n'
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
        "3. Do NOT use any emojis anywhere in your output (no emojis in summary, impact, title, explanation, or agent_prompt).\n"
        "4. Never report '[... Middle lines of file diff omitted ...]' as truncated code; those markers only indicate prompt budgeting.\n"
        "5. If no issues exist, 'findings' must be [] and 'verdict' must be 'PASS'."
    )

    user_prompt = (
        f"PR Title: {pr_title}\n"
        f"PR Description: {pr_body or 'None'}\n\n"
        f"Annotated Diff (`[L<num>]` is line number):\n```diff\n{annotated_diff}\n```"
    )

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=120.0)
        close_client = True

    last_exc: Exception | None = None
    sanitized_primary_reason: str = ""

    try:
        for idx, cand in enumerate(candidates):
            has_next = (idx + 1) < len(candidates)
            if has_next and not cand.get("encrypted_api_key") and str(cand.get("provider", "")).lower() != "ollama":
                if cand.get("is_primary"):
                    sanitized_primary_reason = "Primary API key not configured"
                logger.warning(
                    "Provider candidate '%s' (%s - %s) has no API key configured. Failing over to next candidate...",
                    cand["name"], cand["provider"], cand["model"]
                )
                continue
            url, headers = _resolve_candidate_endpoint_and_headers(cand)
            payload = {
                "model": cand["model"],
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            try:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.is_error:
                    if resp.status_code in FALLBACK_TRIGGER_STATUS_CODES and has_next:
                        reason = sanitize_failure_reason(resp.status_code)
                        if cand.get("is_primary"):
                            sanitized_primary_reason = reason
                        logger.warning(
                            "Provider candidate '%s' (%s - %s) returned %d (%s). Failing over to next candidate...",
                            cand["name"], cand["provider"], cand["model"], resp.status_code, reason
                        )
                        continue
                    _check_response_status(resp, cand)

                resp_json = resp.json()
                content = resp_json["choices"][0]["message"]["content"]
                usage_raw = resp_json.get("usage") if isinstance(resp_json, dict) else None
                data = extract_json_payload(content)

                validate_review(data)
                raw_findings = data["findings"]
                unverified_findings = False
                clean_findings: list[dict[str, Any]] = []

                # Filter against valid lines to prevent hallucinated line comments
                for f in raw_findings:
                    if not isinstance(f, dict):
                        continue
                    file_path = f.get("file")
                    line = f.get("line")
                    if not file_path or line is None:
                        continue

                    if (str(file_path), int(line)) in redacted_locations:
                        continue
                    title_str = strip_emojis(str(f.get("title", ""))).strip()
                    exp_str = strip_emojis(str(f.get("explanation", ""))).strip()
                    if "[REDACTED_" in exp_str or "[REDACTED_" in str(f.get("suggestion", "")):
                        continue
                    if "truncated" in title_str.lower() and "omitted" in annotated_diff:
                        continue

                    line_num = int(line)
                    if valid_lines is not None:
                        valid_for_file = valid_lines.get(str(file_path), set())
                        if not valid_for_file:
                            continue
                        if line_num not in valid_for_file:
                            closest = min(valid_for_file, key=lambda vl: abs(vl - line_num))
                            if abs(closest - line_num) <= 5:
                                line_num = closest
                            else:
                                continue

                    clean_findings.append({
                        "file": str(file_path),
                        "line": line_num,
                        "severity": strip_emojis(str(f.get("severity", "risk"))).lower().strip() or "risk",
                        "title": title_str,
                        "explanation": exp_str,
                        "suggestion": str(f.get("suggestion", "")),
                        "agent_prompt": strip_emojis(str(f.get("agent_prompt", ""))).strip(),
                    })

                # Pass 2: Fact-check and verify candidate findings against the diff
                unverified_findings = len(clean_findings) != len(raw_findings)
                verification_performed = False
                if clean_findings:
                    verified_findings = await _verify_findings_with_llm(
                        client=client,
                        url=url,
                        headers=headers,
                        model=cand["model"],
                        candidate_findings=clean_findings,
                        annotated_diff=annotated_diff,
                    )
                    verification_performed = True
                    final_findings: list[dict[str, Any]] = []
                    for vf in verified_findings:
                        fp = vf.get("file")
                        ln = vf.get("line")
                        if not fp or ln is None:
                            continue
                        ln_int = int(ln)
                        if (str(fp), ln_int) in redacted_locations:
                            continue
                        if valid_lines is not None:
                            v_set = valid_lines.get(str(fp), set())
                            if not v_set:
                                continue
                            if ln_int not in v_set:
                                closest = min(v_set, key=lambda vl: abs(vl - ln_int))
                                if abs(closest - ln_int) <= 5:
                                    ln_int = closest
                                else:
                                    continue
                        vf["line"] = ln_int
                        final_findings.append(vf)
                    unverified_findings |= len(final_findings) != len(verified_findings)
                    clean_findings = final_findings

                for finding in clean_findings:
                    finding["source"] = "llm"
                data["findings"] = clean_findings
                data["summary"] = strip_emojis(str(data.get("summary", "Review completed."))).strip()
                if redacted_locations:
                    data["summary"] += " Redacted locations were excluded from source-dependent analysis."
                if data.get("poem"):
                    data["poem"] = strip_emojis(str(data["poem"])).strip()
                if data.get("architecture_diagram"):
                    data["architecture_diagram"] = strip_emojis(str(data["architecture_diagram"])).strip()
                if isinstance(data.get("walkthrough"), list):
                    for wt in data["walkthrough"]:
                        if isinstance(wt, dict):
                            wt["impact"] = strip_emojis(str(wt.get("impact", "Refactor"))).strip() or "Refactor"
                            wt["summary"] = strip_emojis(str(wt.get("summary", ""))).strip()
                data["verification_status"] = "2-Pass Fact-Checked" if verification_performed else "Single-pass: no verifiable candidate findings"
                if unverified_findings:
                    data["verification_status"] = "Incomplete: unverified finding locations"
                    data["summary"] += " Some findings remain unverified because their locations were not in the reviewed evidence."
                    if data["verdict"] == "PASS":
                        data["verdict"] = "WARN"
                if "Coverage limited:" in annotated_diff or "Middle lines of file diff omitted" in annotated_diff:
                    data["summary"] += " Review coverage is limited; omitted code was not analyzed."
                    if data.get("verdict") == "PASS":
                        data["verdict"] = "WARN"

                # Calculate emoji-free review check category statuses
                bugs = sum(1 for f in clean_findings if f["severity"] == "bug")
                risks = sum(1 for f in clean_findings if f["severity"] == "risk")
                suggested_tests = data.get("suggested_tests", [])

                if (bugs > 0 or risks > 0) and str(data.get("verdict", "")).upper() == "PASS":
                    data["verdict"] = "WARN"


                checks = {
                    "Security": "Clean" if not any("security" in f["title"].lower() or "secret" in f["title"].lower() for f in clean_findings) else "Issues Found",
                    "Correctness": f"{bugs} Defect{'s' if bugs > 1 else ''}" if bugs > 0 else "Clean",
                    "Performance": "Review Needed" if any("perf" in f["title"].lower() or "o(n" in f["title"].lower() for f in clean_findings) else "No performance finding reported",
                    "Test Coverage": f"{len(suggested_tests)} Test Case{'s' if len(suggested_tests) > 1 else ''} Suggested" if suggested_tests else "Coverage not measured",
                }
                data["review_checks"] = checks
                data["agent_statuses"] = checks  # Backward compatibility alias

                if not cand.get("is_primary"):
                    data["fallback_used"] = {
                        "candidate_id": cand.get("id"),
                        "name": cand.get("name"),
                        "provider": cand.get("provider"),
                        "model": cand.get("model"),
                        "primary_reason": sanitized_primary_reason or "Primary provider unavailable",
                    }

                data["_provider"] = cand.get("provider", cfg.provider)
                data["_model"] = cand.get("model", cfg.model)
                if isinstance(usage_raw, dict):
                    data["_usage"] = {
                        "prompt_tokens": usage_raw.get("prompt_tokens"),
                        "completion_tokens": usage_raw.get("completion_tokens"),
                    }

                return data

            except Exception as exc:
                last_exc = exc
                if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, httpx.HTTPError, ValueError)) and has_next:
                    reason = sanitize_failure_reason(None, exc)
                    if cand.get("is_primary"):
                        sanitized_primary_reason = reason
                    logger.warning(
                        "Provider candidate '%s' (%s - %s) encountered error: %s. Failing over...",
                        cand["name"], cand["provider"], cand["model"], reason
                    )
                    continue
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError("No LLM candidates available to run review.")
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
    """Execute interactive @patchpilot explain, fix, review, or test command with CodeRabbit-grade structured layout and zero emojis."""
    candidates = get_provider_candidates(cfg)
    cmd_lower = command.lower().strip()

    annotated_context = ""
    changed_files: list[str] = []
    existing_test_files: list[str] = []
    if not diff_hunk and pr_diff:
        annotated_context, valid_map = annotate_diff(pr_diff, max_chars=44_000)
        changed_files = list(valid_map.keys())
        existing_test_files = [f for f in changed_files if "test" in f.lower()]

    if "explain" in cmd_lower:
        mode_instruction = (
            "Provide a CodeRabbit-grade architectural explanation using this exact Markdown structure:\n"
            "### Executive Summary\n"
            "(Concise overview of the purpose and behavior of the changes)\n\n"
            "### Architecture & Control-Flow Diagram\n"
            "(A valid ```mermaid flowchart LR or sequenceDiagram block showing how the components interact; quote node labels with parentheses)\n\n"
            "### Component-by-Component Breakdown\n"
            "| File / Module | Key Symbol | Architectural Role & Change Summary |\n"
            "| :--- | :--- | :--- |\n\n"
            "### Key Design Trade-offs & Edge Cases\n"
            "(Bullet points analyzing invariants, failure modes, and operational considerations)"
        )
    elif "fix" in cmd_lower:
        mode_instruction = (
            "Provide a CodeRabbit-grade verified remediation using this exact Markdown structure:\n"
            "### Root-Cause Diagnosis\n"
            "| File | Line(s) | Defect Category | Root Cause |\n"
            "| :--- | :--- | :--- | :--- |\n\n"
            "### Committable Fix\n"
            "(Provide exact replacement code using ```suggestion or ```diff blocks per affected file)\n\n"
            "<details>\n"
            "<summary><b>Prompt for AI Agents</b></summary>\n\n"
            "```text\n"
            "(Copy-ready 1-2 sentence prompt for an IDE coding agent to apply the fix)\n"
            "```\n\n"
            "</details>\n\n"
            "### Verification Steps\n"
            "(Concrete steps or test commands to verify the fix)"
        )
    elif "security" in cmd_lower:
        mode_instruction = (
            "Perform a rigorous security and vulnerability audit (OWASP Top 10, injections, auth bypass, secret exposure) using this exact Markdown structure:\n"
            "### Security Audit Summary\n\n"
            "### Threat & Vulnerability Matrix\n"
            "| Severity | File & Line | CWE / Category | Summary |\n"
            "| :--- | :--- | :--- | :--- |\n\n"
            "### Verified Security Findings & Remediation\n"
            "(For each confirmed issue, include root cause, a ```suggestion fix block, and a collapsible `<details><summary><b>Prompt for AI Agents</b></summary>` block)"
        )
    elif "perf" in cmd_lower:
        mode_instruction = (
            "Perform an algorithmic and runtime performance audit (O(N^2) loops, DB N+1 queries, memory allocations, blocking I/O) using this exact Markdown structure:\n"
            "### Performance Audit Summary\n\n"
            "### Complexity & Bottleneck Matrix\n"
            "| Impact | File & Line | Bottleneck Type | Current vs Target Complexity |\n"
            "| :--- | :--- | :--- | :--- |\n\n"
            "### Optimization Recommendations\n"
            "(Include exact code replacements and a collapsible `<details><summary><b>Prompt for AI Agents</b></summary>` block)"
        )
    elif any(k in cmd_lower for k in ["test", "tests"]):
        existing_note = (
            f"Existing test files already modified in this PR: {', '.join(f'`{t}`' for t in existing_test_files)}. "
            "Do NOT duplicate test cases already present in those files; generate only missing edge cases, failure modes, and negative paths."
            if existing_test_files
            else "Match the repository's existing test framework (`pytest` or `unittest`)."
        )
        mode_instruction = (
            "Generate a comprehensive, CodeRabbit-grade unit test suite using this exact Markdown structure:\n"
            f"Note: {existing_note}\n\n"
            "### Coverage Gap & Edge-Case Matrix\n"
            "| Source File | Target Symbol | Untested Branch / Edge Case | Priority | Target Test File |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n\n"
            "### Generated Test Suites (Modular Per File)\n"
            "Organize tests into separate collapsible `<details open>` blocks—one per target test file (never combine unrelated modules into one giant file):\n"
            "<details open>\n"
            "<summary><b>Test Suite: <code>tests/test_<module>.py</code></b></summary>\n\n"
            "```python\n"
            "# Complete, self-contained, runnable test module for <module>\n"
            "```\n\n"
            "</details>\n\n"
            "### Mocking & Fixture Notes\n"
            "(Brief bullet points explaining mocked dependencies, isolated state, or environment flags)\n\n"
            "### Verification Commands\n"
            "```bash\n"
            "# Exact CLI command to run the generated test suites\n"
            "```"
        )
    else:
        mode_instruction = (
            "Review the requested code changes constructively and objectively, verifying every finding against the actual diff lines."
        )

    system_prompt = (
        f"{cfg.review_policy or ''}\n\n"
        f"You are PatchPilot responding to an interactive `{command}` command on a GitHub Pull Request.\n"
        f"{mode_instruction}\n\n"
        "Strict Rules:\n"
        "1. Do NOT use any emojis anywhere in your response.\n"
        "2. Verify every symbol name, route path, and signature against the provided diff before writing code or findings.\n"
        "3. Never report '[... Middle lines of file diff omitted ...]' as truncated code."
    )

    context_parts = [f"User Directive: {user_comment}"]
    if file_path:
        context_parts.append(f"Target File: `{file_path}`")
    if diff_hunk:
        context_parts.append(f"Target Diff Hunk:\n```diff\n{diff_hunk}\n```")
    else:
        context_parts.append(f"Annotated PR Diff Context:\n```diff\n{annotated_context}\n```")

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=90.0)
        close_client = True

    last_exc: Exception | None = None
    try:
        for idx, cand in enumerate(candidates):
            has_next = (idx + 1) < len(candidates)
            if has_next and not cand.get("encrypted_api_key") and str(cand.get("provider", "")).lower() != "ollama":
                logger.warning(
                    "Interactive candidate '%s' has no API key configured. Failing over...",
                    cand["name"]
                )
                continue
            url, headers = _resolve_candidate_endpoint_and_headers(cand)
            payload = {
                "model": cand["model"],
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": budget_text(sanitize_diff_text(system_prompt), 8000)},
                    {"role": "user", "content": budget_text(sanitize_diff_text("\n\n".join(context_parts)), 40000)},
                ],
            }
            try:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.is_error:
                    if resp.status_code in FALLBACK_TRIGGER_STATUS_CODES and has_next:
                        reason = sanitize_failure_reason(resp.status_code)
                        logger.warning(
                            "Interactive candidate '%s' returned %d (%s). Failing over...",
                            cand["name"], resp.status_code, reason
                        )
                        continue
                    _check_response_status(resp, cand)

                return strip_emojis(resp.json()["choices"][0]["message"]["content"]).strip()
            except Exception as exc:
                last_exc = exc
                if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, httpx.HTTPError, ValueError)) and has_next:
                    reason = sanitize_failure_reason(None, exc)
                    logger.warning(
                        "Interactive candidate '%s' error: %s. Failing over...",
                        cand["name"], reason
                    )
                    continue
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError("No LLM candidates available to run interactive command.")
    finally:
        if close_client:
            await client.aclose()
