import json
import logging
import os
import re
from typing import Any
import httpx
from app.crypto import decrypt_secret
from app.db import VaultConfig
from app.github_auth import strip_emojis

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


def _excerpt_file_lines_safely(lines: list[str], budget_chars: int) -> str:
    """Excerpt a file's annotated diff lines on complete line boundaries, preserving both head and tail hunks."""
    full_text = "\n".join(lines)
    if len(full_text) <= budget_chars:
        return full_text

    notice = "[... Middle lines of file diff omitted for context window budget; file is complete in repository ...]"
    half_budget = max(400, (budget_chars - len(notice) - 4) // 2)

    head_lines: list[str] = []
    head_len = 0
    for ln in lines:
        if head_len + len(ln) + 1 > half_budget and head_lines:
            break
        head_lines.append(ln)
        head_len += len(ln) + 1

    tail_lines: list[str] = []
    tail_len = 0
    for ln in reversed(lines[len(head_lines):]):
        if tail_len + len(ln) + 1 > half_budget and tail_lines:
            break
        tail_lines.append(ln)
        tail_len += len(ln) + 1
    tail_lines.reverse()

    if not tail_lines:
        return "\n".join(head_lines)
    return "\n".join(head_lines + [notice] + tail_lines)


def annotate_diff(raw_diff: str, max_chars: int = 48_000) -> tuple[str, dict[str, set[int]]]:
    """Parse unified diff, exclude noise files, annotate right-side line numbers, and budget across all files without mid-line cuts."""
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

    # Multi-file proportional allocation so no file is dropped or cut mid-line
    active_indices = [i for i, (_, is_skip, _) in enumerate(file_blocks) if not is_skip]
    if not active_indices:
        return full_joined[:max_chars], valid_lines

    block_texts = ["\n".join(blk[2]) for blk in file_blocks]
    remaining_budget = max_chars - sum(len(block_texts[i]) + 1 for i in range(len(file_blocks)) if i not in active_indices)
    remaining_budget = max(2000, remaining_budget)

    # First let small files take their exact size, then divide remaining budget among large files
    allocations: dict[int, int] = {}
    unallocated = list(active_indices)
    while unallocated:
        share = max(800, remaining_budget // len(unallocated))
        fit_any = False
        for idx in list(unallocated):
            f_path = (file_blocks[idx][0] or "").lower()
            is_doc = f_path.endswith((".md", ".txt", ".rst"))
            effective_len = len(block_texts[idx])
            # Cap pure markdown docs when total PR diff is huge so code files get more room
            doc_cap = max(1800, share // 2) if (is_doc and len(active_indices) > 2) else share
            if effective_len <= doc_cap:
                allocations[idx] = effective_len
                remaining_budget -= effective_len
                unallocated.remove(idx)
                fit_any = True
        if not fit_any:
            for idx in unallocated:
                f_path = (file_blocks[idx][0] or "").lower()
                is_doc = f_path.endswith((".md", ".txt", ".rst"))
                share = max(800, remaining_budget // len(unallocated))
                allocations[idx] = max(1500, share // 2) if (is_doc and len(active_indices) > 2) else share
            break

    out_blocks: list[str] = []
    for i, (_, is_skip, lines) in enumerate(file_blocks):
        if is_skip:
            out_blocks.append("\n".join(lines))
        else:
            budget = allocations.get(i, 3000)
            out_blocks.append(_excerpt_file_lines_safely(lines, budget))

    return "\n".join(out_blocks), valid_lines


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


def get_provider_candidates(cfg: VaultConfig) -> list[dict[str, Any]]:
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

    if getattr(cfg, "fallbacks_json", None):
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
        headers["HTTP-Referer"] = "https://github.com/89Aman/PatchPilot"
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
        "Your job is to verify each candidate finding against the annotated diff and eliminate any false positives.\n\n"
        "Verification Rules:\n"
        "1. Check whether the reported issue is genuinely present in the added (`+`) or modified lines of the diff.\n"
        "2. Reject any finding that claims a file or line is 'truncated' or 'incomplete' if the diff was simply excerpted.\n"
        "3. Reject speculative or hallucinated issues where the code already handles the condition properly.\n"
        "4. Do NOT use any emojis anywhere in titles or explanations.\n"
        "5. Return strictly valid JSON with schema:\n"
        "{\n"
        '  "verified_findings": [\n'
        "    {\n"
        '      "file": "path/to/file.ext",\n'
        '      "line": 10,\n'
        '      "severity": "bug|risk|nit",\n'
        '      "title": "Short verified title",\n'
        '      "explanation": "Fact-checked explanation of root cause and fix",\n'
        '      "suggestion": "Optional replacement code line(s) or empty string"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    verify_user_prompt = (
        f"Candidate Findings to Fact-Check:\n```json\n{json.dumps(candidate_findings, indent=2)}\n```\n\n"
        f"Annotated Diff Ground Truth:\n```diff\n{annotated_diff[:38000]}\n```"
    )

    payload = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": verify_system_prompt},
            {"role": "user", "content": verify_user_prompt},
        ],
    }

    try:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.is_error:
            return candidate_findings
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = extract_json_payload(content)
        verified = parsed.get("verified_findings")
        if isinstance(verified, list):
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
                    })
            return result
    except Exception as exc:
        logger.debug("Second-pass verification fallback to deterministic findings: %s", exc)

    return candidate_findings


async def run_pr_review(
    cfg: VaultConfig,
    pr_title: str,
    pr_body: str,
    annotated_diff: str,
    client: httpx.AsyncClient | None = None,
    valid_lines: dict[str, set[int]] | None = None,
    triage: Any | None = None,
) -> dict[str, Any]:
    """Execute unified 2-pass AI review of pull request with fact-checked findings, walkthrough, and test stubs."""
    candidates = get_provider_candidates(cfg)
    policy_section = cfg.review_policy or ""
    system_prompt = (
        "You are PatchPilot, an expert pull request analysis and verification engine.\n"
        f"{policy_section}\n\n"
        "Analyze the pull request thoroughly across:\n"
        "1. State-Machine & Idempotency Correctness (missing state/status guards before mutations, double-release or duplicate timeline appends on retry).\n"
        "2. Backend & Data-Layer Consistency (accessing private/mock state like `_mock_*` that fails in production DB mode, mutating Pydantic models without validation).\n"
        "3. Frontend & UI Handler Wiring (calls to undefined JS functions, reliance on implicit `event.currentTarget`, hardcoded CSS classes ignoring state).\n"
        "4. Security & Configuration Hygiene (OWASP, exposed secrets, `.gitignore` rules excluding required scripts like `scripts/*`, Dockerfile/CI issues).\n\n"
        "Return strictly valid JSON matching this schema:\n"
        "{\n"
        '  "summary": "High-level technical summary of the PR and overall code quality.",\n'
        '  "poem": "",\n'
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
        "3. Do NOT use any emojis anywhere in your output (no emojis in summary, impact, title, or explanation).\n"
        "4. Never report '[... Middle lines of file diff omitted ...]' as truncated code; those markers only indicate prompt budgeting.\n"
        "5. If no issues exist, 'findings' must be [] and 'verdict' must be 'PASS'."
    )

    user_prompt = (
        f"PR Title: {pr_title}\n"
        f"PR Description: {pr_body or 'None'}\n\n"
        f"Annotated Diff (`[L<num>]` is line number):\n```diff\n{annotated_diff[:48000]}\n```"
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

                    title_str = strip_emojis(str(f.get("title", ""))).strip()
                    exp_str = strip_emojis(str(f.get("explanation", ""))).strip()
                    if "truncated" in title_str.lower() and "omitted" in annotated_diff:
                        continue

                    line_num = int(line)
                    if valid_lines is not None:
                        valid_for_file = valid_lines.get(str(file_path), set())
                        if not valid_for_file:
                            continue
                        if line_num not in valid_for_file:
                            # Snap to closest valid diff line in the same file if within 5 lines
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
                    })

                # Pass 2: Fact-check and verify candidate findings against the diff
                if clean_findings:
                    verified_findings = await _verify_findings_with_llm(
                        client=client,
                        url=url,
                        headers=headers,
                        model=cand["model"],
                        candidate_findings=clean_findings,
                        annotated_diff=annotated_diff,
                    )
                    # Re-validate line numbers after Pass 2
                    final_findings: list[dict[str, Any]] = []
                    for vf in verified_findings:
                        fp = vf.get("file")
                        ln = vf.get("line")
                        if not fp or ln is None:
                            continue
                        ln_int = int(ln)
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
                    clean_findings = final_findings

                data["findings"] = clean_findings
                data["summary"] = strip_emojis(str(data.get("summary", "Review completed."))).strip()
                if data.get("poem"):
                    data["poem"] = strip_emojis(str(data["poem"])).strip()
                if isinstance(data.get("walkthrough"), list):
                    for wt in data["walkthrough"]:
                        if isinstance(wt, dict):
                            wt["impact"] = strip_emojis(str(wt.get("impact", "Refactor"))).strip() or "Refactor"
                            wt["summary"] = strip_emojis(str(wt.get("summary", ""))).strip()
                data["verification_status"] = "2-Pass Fact-Checked"

                # Calculate emoji-free review check category statuses
                bugs = sum(1 for f in clean_findings if f["severity"] == "bug")
                risks = sum(1 for f in clean_findings if f["severity"] == "risk")
                suggested_tests = data.get("suggested_tests", [])

                if bugs > 0 and str(data.get("verdict", "")).upper() == "PASS":
                    data["verdict"] = "WARN"
                elif bugs == 0 and risks == 0 and not clean_findings:
                    data["verdict"] = "PASS"

                checks = {
                    "Security": "Clean" if not any("security" in f["title"].lower() or "secret" in f["title"].lower() for f in clean_findings) else "Issues Found",
                    "Correctness": f"{bugs} Defect{'s' if bugs > 1 else ''}" if bugs > 0 else "Clean",
                    "Performance": "Review Needed" if any("perf" in f["title"].lower() or "o(n" in f["title"].lower() for f in clean_findings) else "Optimal",
                    "Test Coverage": f"{len(suggested_tests)} Test Case{'s' if len(suggested_tests) > 1 else ''} Suggested" if suggested_tests else "Adequate",
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
    """Execute interactive @patchpilot explain, fix, review, or generate tests command with multi-key failover and zero emojis."""
    candidates = get_provider_candidates(cfg)
    cmd_lower = command.lower().strip()

    if "explain" in cmd_lower:
        mode_instruction = "Explain the code, root cause, and architectural trade-offs concisely."
    elif "fix" in cmd_lower:
        mode_instruction = "Provide the exact verified fix with clear markdown code blocks and git suggestion syntax if applicable."
    elif "security" in cmd_lower:
        mode_instruction = "Perform an in-depth security and vulnerability audit (OWASP, injections, auth bypass, data leaks). Fact-check every finding against the diff."
    elif "perf" in cmd_lower:
        mode_instruction = "Analyze algorithmic complexity, database query patterns (N+1), memory bloat, and I/O efficiency. Fact-check every finding against the diff."
    elif any(k in cmd_lower for k in ["test", "tests"]):
        mode_instruction = "Generate concrete, runnable unit test functions covering untested branches and edge cases."
    else:
        mode_instruction = "Review the requested code changes constructively, verifying every finding against the actual diff lines."

    system_prompt = (
        f"{cfg.review_policy or ''}\n\n"
        f"You are PatchPilot responding to an interactive `{command}` command on a GitHub Pull Request.\n"
        f"{mode_instruction}\n"
        "Rules:\n"
        "1. Do NOT use any emojis anywhere in your response.\n"
        "2. Verify every claim against the provided diff before reporting it.\n"
        "3. Never report '[... Middle lines of file diff omitted ...]' as truncated code."
    )

    context_parts = [f"User Directive: {user_comment}"]
    if file_path:
        context_parts.append(f"Target File: `{file_path}`")
    if diff_hunk:
        context_parts.append(f"Target Diff Hunk:\n```diff\n{diff_hunk}\n```")
    else:
        annotated_context, _ = annotate_diff(pr_diff, max_chars=44_000)
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
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "\n\n".join(context_parts)},
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
