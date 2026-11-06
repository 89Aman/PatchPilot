from datetime import datetime, timezone
import hashlib
import hmac
import logging
import re
import time
from typing import Any
import httpx
import jwt
from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\u2300-\u23FF"
    "\u2B50"
    "\uFE0F"
    "\u200D"
    "\u2139"
    "]+",
    flags=re.UNICODE,
)


def strip_emojis(text: str) -> str:
    """Remove all emoji characters from text and normalize resulting spacing."""
    if not text:
        return ""
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = re.sub(r"\|\s{2,}", "| ", cleaned)
    cleaned = re.sub(r"(#+\s+)\s+", r"\1", cleaned)
    cleaned = re.sub(r"(>\s+)\s+", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^\*\n]+?)\*\*", lambda m: f"**{m.group(1).strip()}**", cleaned)
    return cleaned


def verify_webhook_signature(
    payload_bytes: bytes,
    signature_header: str | None,
    secret: str | None = None,
) -> bool:
    """Verify GitHub webhook payload against X-Hub-Signature-256 header using HMAC-SHA256."""
    target_secret = secret or get_settings().GITHUB_WEBHOOK_SECRET
    if not signature_header or not target_secret:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected_hash = hmac.new(
        target_secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    expected_header = f"sha256={expected_hash}"

    return hmac.compare_digest(expected_header, signature_header)


def create_app_jwt(app_id: str | None = None, private_key: str | None = None) -> str:
    """Generate RS256 JWT for GitHub App authentication."""
    settings = get_settings()
    target_app_id = str(app_id or settings.GITHUB_APP_ID).strip()
    target_key = private_key or settings.GITHUB_PRIVATE_KEY

    if not target_key:
        raise ValueError("GitHub App private key is not configured")

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 540,  # 9 minutes
        "iss": target_app_id,
    }

    return jwt.encode(payload, target_key, algorithm="RS256")


async def get_installation_token(
    installation_id: int | str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Exchange GitHub App JWT for a repository installation access token."""
    jwt_token = create_app_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    try:
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        token = data.get("token")
        if not token:
            raise ValueError(f"GitHub access token missing from response: {data}")
        return str(token)
    finally:
        if close_client:
            await client.aclose()


async def fetch_pr_metadata(
    token: str,
    repo_full_name: str,
    pr_number: int,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch pull request metadata JSON (title, body, head SHA, author, line counts)."""
    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    finally:
        if close_client:
            await client.aclose()


async def fetch_pr_diff(
    token: str,
    repo_full_name: str,
    pr_number: int,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch raw unified diff for a pull request."""
    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.text
    finally:
        if close_client:
            await client.aclose()


async def get_pr_diff(
    repo_full_name: str,
    pr_number: int,
    installation_token: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Legacy positional wrapper for fetch_pr_diff."""
    return await fetch_pr_diff(installation_token, repo_full_name, pr_number, client=client)


async def add_comment_reaction(
    token: str,
    repo_full_name: str,
    comment_id: int | str,
    reaction: str = "eyes",
    is_review_comment: bool = False,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Add an immediate reaction (e.g. 'eyes') to acknowledge a user's command comment."""
    if not comment_id:
        return False

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        close_client = True

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    endpoint = (
        f"https://api.github.com/repos/{repo_full_name}/pulls/comments/{comment_id}/reactions"
        if is_review_comment
        else f"https://api.github.com/repos/{repo_full_name}/issues/comments/{comment_id}/reactions"
    )
    try:
        resp = await client.post(endpoint, headers=headers, json={"content": reaction})
        return resp.status_code in (200, 201)
    except Exception as exc:
        logger.debug("Could not add comment reaction on %s: %s", repo_full_name, exc)
        return False
    finally:
        if close_client:
            await client.aclose()


def build_loading_review_card(
    requested_by: str | None = None,
    commit_sha: str | None = None,
    command: str = "review",
    stage_statuses: dict[str, str] | None = None,
    target_ref: str | None = None,
) -> str:
    """Build an emoji-free, GitHub Actions-style live progress card shown while PatchPilot executes checks."""
    sha_short = (commit_sha or "HEAD")[:7]
    cmd_clean = strip_emojis(command).strip() or "review"

    meta_parts: list[str] = []
    if requested_by and requested_by.strip():
        clean_user = requested_by.strip().lstrip("@")
        meta_parts.append(f"**Requested by:** @{clean_user}")
    if target_ref and target_ref.strip():
        meta_parts.append(f"**Target:** `{target_ref.strip()}`")
    meta_parts.append("**Status:** `IN PROGRESS`")
    meta_parts.append(f"**Command:** `{cmd_clean}`")
    meta_parts.append(f"**Commit:** `{sha_short}`")
    meta_line = " &nbsp;|&nbsp; ".join(meta_parts)

    defaults = {
        "diff": "RUNNING...",
        "security": "QUEUED",
        "triage": "QUEUED",
        "pass1": "QUEUED",
        "pass2": "QUEUED",
    }
    if stage_statuses:
        defaults.update(stage_statuses)

    pipeline_rows = [
        f"| **1. Diff Ingestion** | Fetching unified diff and filtering lockfiles | `{defaults['diff']}` |",
        f"| **2. Security Gate** | Pre-LLM scan for exposed credentials and API keys | `{defaults['security']}` |",
        f"| **3. PR Triage** | Computing size tier, blast radius, and risk score | `{defaults['triage']}` |",
        f"| **4. Pass 1 Analysis** | Auditing architecture, state transitions, and edge cases | `{defaults['pass1']}` |",
        f"| **5. Pass 2 Verification** | Fact-checking candidate findings against ground-truth diff | `{defaults['pass2']}` |",
    ]

    card = (
        "<!-- patchpilot-ai-review -->\n"
        "## PatchPilot AI PR Review\n\n"
        f"{meta_line}\n\n"
        "> **PatchPilot Engine is actively analyzing this pull request.**\n"
        "> `patchpilot-app[bot]` has been registered as a reviewer. This card will automatically update in-place with the final report once all security and verification checks complete.\n\n"
        "### Live Verification Pipeline\n\n"
        "| Stage | Check Description | Status |\n"
        "| :--- | :--- | :--- |\n"
        + "\n".join(pipeline_rows)
        + "\n"
    )
    return strip_emojis(card)


async def create_pending_pr_review(
    token: str,
    repo_full_name: str,
    pr_number: int,
    head_sha: str | None,
    body: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Immediately submit a COMMENT review so PatchPilot appears in the PR Reviewers sidebar and displays the loading screen."""
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)
        close_client = True

    clean_body = strip_emojis(body)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    review_url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/reviews"
    payload: dict[str, Any] = {
        "body": clean_body,
        "event": "COMMENT",
    }
    if head_sha:
        payload["commit_id"] = head_sha

    try:
        resp = await client.post(review_url, headers=headers, json=payload)
        if resp.status_code in (200, 201):
            data = resp.json()
            rev_id = data.get("id")
            if rev_id:
                return {"kind": "review", "id": rev_id}

        # Fallback to issue comment (e.g. if PR is closed/merged or review creation returned 422)
        comment_url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
        c_resp = await client.post(comment_url, headers=headers, json={"body": clean_body})
        if c_resp.status_code in (200, 201):
            c_data = c_resp.json()
            c_id = c_data.get("id")
            if c_id:
                return {"kind": "comment", "id": c_id}
    except Exception as exc:
        logger.warning("Could not create initial loading review card on %s#%s: %s", repo_full_name, pr_number, exc)
    finally:
        if close_client:
            await client.aclose()
    return None


async def update_pr_review_card(
    token: str,
    repo_full_name: str,
    pr_number: int,
    handle: dict[str, Any] | None,
    body: str,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Update an existing Pull Request Review or Issue Comment in-place with updated stage progress or final results."""
    if not handle or not handle.get("id"):
        return False

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)
        close_client = True

    clean_body = strip_emojis(body)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    item_id = handle["id"]
    kind = handle.get("kind", "review")

    try:
        if kind == "review":
            url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/reviews/{item_id}"
            resp = await client.put(url, headers=headers, json={"body": clean_body})
        else:
            url = f"https://api.github.com/repos/{repo_full_name}/issues/comments/{item_id}"
            resp = await client.patch(url, headers=headers, json={"body": clean_body})
        return resp.status_code in (200, 201)
    except Exception as exc:
        logger.warning("Failed updating review card in-place on %s#%s: %s", repo_full_name, pr_number, exc)
        return False
    finally:
        if close_client:
            await client.aclose()


async def start_check_indicator(
    token: str,
    repo_full_name: str,
    head_sha: str | None,
    command: str = "review",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Start a native GitHub Check Run and/or Commit Status ('pending') so a GitHub Actions-style spinner is visible on the PR."""
    if not head_sha:
        return {}

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        close_client = True

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    handle: dict[str, Any] = {"head_sha": head_sha}

    try:
        # 1. Try Check Runs API
        check_url = f"https://api.github.com/repos/{repo_full_name}/check-runs"
        check_payload = {
            "name": f"PatchPilot / {command.upper()}",
            "head_sha": head_sha,
            "status": "in_progress",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "output": {
                "title": "Running security scan, PR triage, and 2-pass verification...",
                "summary": "PatchPilot is analyzing the pull request diff and fact-checking candidate findings.",
            },
        }
        c_resp = await client.post(check_url, headers=headers, json=check_payload)
        if c_resp.status_code in (200, 201):
            handle["check_run_id"] = c_resp.json().get("id")

        # 2. Also attempt Commit Status API (pending spinner)
        status_url = f"https://api.github.com/repos/{repo_full_name}/statuses/{head_sha}"
        status_payload = {
            "state": "pending",
            "context": "PatchPilot / Security & Code Review",
            "description": f"Running PatchPilot {command} analysis and verification...",
        }
        s_resp = await client.post(status_url, headers=headers, json=status_payload)
        if s_resp.status_code in (200, 201):
            handle["status_active"] = True
    except Exception as exc:
        logger.debug("Check indicator start skipped on %s: %s", repo_full_name, exc)
    finally:
        if close_client:
            await client.aclose()

    return handle


async def complete_check_indicator(
    token: str,
    repo_full_name: str,
    check_handle: dict[str, Any] | None,
    verdict: str = "PASS",
    summary_text: str = "PatchPilot verification completed.",
    client: httpx.AsyncClient | None = None,
) -> None:
    """Complete the GitHub Check Run and/or Commit Status spinner once analysis finishes."""
    if not check_handle or not check_handle.get("head_sha"):
        return

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        close_client = True

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    head_sha = check_handle["head_sha"]
    verdict_upper = verdict.upper().strip()
    conclusion = "success" if verdict_upper in {"PASS", "APPROVE"} else "failure"
    status_state = "success" if verdict_upper in {"PASS", "APPROVE"} else "failure"
    clean_title = strip_emojis(f"Verdict: {verdict_upper} — {summary_text[:110]}").strip()

    try:
        check_run_id = check_handle.get("check_run_id")
        if check_run_id:
            check_url = f"https://api.github.com/repos/{repo_full_name}/check-runs/{check_run_id}"
            await client.patch(
                check_url,
                headers=headers,
                json={
                    "status": "completed",
                    "conclusion": conclusion,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "output": {
                        "title": clean_title[:250],
                        "summary": strip_emojis(summary_text)[:60000],
                    },
                },
            )

        if check_handle.get("status_active"):
            status_url = f"https://api.github.com/repos/{repo_full_name}/statuses/{head_sha}"
            await client.post(
                status_url,
                headers=headers,
                json={
                    "state": status_state,
                    "context": "PatchPilot / Security & Code Review",
                    "description": clean_title[:135],
                },
            )
    except Exception as exc:
        logger.debug("Check indicator completion skipped on %s: %s", repo_full_name, exc)
    finally:
        if close_client:
            await client.aclose()


def _build_agent_prompt_for_finding(f: dict[str, Any]) -> str:
    """Construct a concise, copy-ready prompt for AI coding agents to resolve a specific finding."""
    explicit = strip_emojis(str(f.get("agent_prompt", ""))).strip()
    if explicit:
        return explicit
    f_file = f.get("file", "target file")
    f_line = f.get("line", "")
    f_title = strip_emojis(str(f.get("title", "the reported issue"))).strip()
    f_exp = strip_emojis(str(f.get("explanation") or f.get("comment") or "")).strip()
    loc = f"around line {f_line}" if f_line else "in the modified section"
    return f"In `{f_file}` {loc}, fix '{f_title}': {f_exp}"


def build_review_summary_card(
    verdict: str,
    commit_sha: str,
    summary: str,
    bug_count: int,
    risk_count: int,
    nit_count: int,
    triage_summary: str | None = None,
    secret_alerts_count: int = 0,
    agent_statuses: dict[str, str] | None = None,
    review_checks: dict[str, str] | None = None,
    walkthrough: list[dict[str, str]] | None = None,
    poem: str | None = None,
    suggested_tests: list[dict[str, str]] | None = None,
    fallback_used: dict[str, Any] | None = None,
    requested_by: str | None = None,
    findings: list[dict[str, Any]] | None = None,
    verification_status: str | None = None,
    architecture_diagram: str | None = None,
) -> str:
    """Generate clean, emoji-free CodeRabbit-grade PatchPilot Markdown summary card."""
    verdict_clean = strip_emojis(verdict).upper().strip() or "PASS"
    sha_short = commit_sha[:7] if commit_sha else "HEAD"

    user_tag_part = ""
    if requested_by and requested_by.strip():
        clean_user = requested_by.strip().lstrip("@")
        user_tag_part = f"**Requested by:** @{clean_user} &nbsp;|&nbsp; "

    verif_part = ""
    if verification_status and verification_status.strip():
        verif_part = f" &nbsp;|&nbsp; **Verification:** `{strip_emojis(verification_status).strip()}`"

    # Failover Alert Notice (if review was completed via fallback provider)
    failover_notice = ""
    if fallback_used:
        cand_provider = str(fallback_used.get("provider", "")).capitalize()
        cand_model = fallback_used.get("model", "")
        primary_reason = fallback_used.get("primary_reason", "Provider rate limit or outage")
        failover_notice = (
            f"> **Failover Alert**: Primary LLM returned *{primary_reason}*. "
            f"Review successfully completed via fallback: **{cand_provider}** (`{cand_model}`).\n\n"
        )

    # Optional Poem Banner
    poem_section = ""
    if poem and poem.strip():
        poem_clean = "\n".join(f"> *{strip_emojis(line).strip()}*" for line in poem.strip().splitlines() if strip_emojis(line).strip())
        if poem_clean:
            poem_section = f"{poem_clean}\n\n"

    triage_section = ""
    if triage_summary:
        triage_section = f"### Triage & Impact\n\n{strip_emojis(triage_summary).strip()}\n\n"

    # Review Checks Matrix Badges
    checks_dict = review_checks or agent_statuses
    checks_section = ""
    if checks_dict:
        rows = [
            f"| **{strip_emojis(str(category)).strip()}** | {strip_emojis(str(status)).strip()} |"
            for category, status in checks_dict.items()
        ]
        checks_section = (
            "### Review Checks\n\n"
            "| Category | Status |\n"
            "| :--- | :--- |\n"
            + "\n".join(rows)
            + "\n\n"
        )

    # File Walkthrough & Architecture Diagram Section
    walkthrough_section = ""
    diagram_block = ""
    if architecture_diagram and architecture_diagram.strip():
        raw_diag = strip_emojis(architecture_diagram).strip()
        if raw_diag.startswith("```"):
            diagram_block = f"{raw_diag}\n\n"
        else:
            diagram_block = f"```mermaid\n{raw_diag}\n```\n\n"

    wt_table = ""
    if walkthrough:
        wt_rows = [
            f"| `{item.get('file', '')}` | {strip_emojis(str(item.get('impact', 'Refactor'))).strip() or 'Refactor'} | {strip_emojis(str(item.get('summary', ''))).strip()} |"
            for item in walkthrough
            if item.get("file")
        ]
        if wt_rows:
            wt_table = (
                "| File | Impact | Summary |\n"
                "| :--- | :--- | :--- |\n"
                + "\n".join(wt_rows)
                + "\n\n"
            )

    if wt_table or diagram_block:
        walkthrough_section = (
            "### Changes Walkthrough\n\n"
            "<details open>\n"
            "<summary><b>File Walkthrough &amp; Architecture Flow</b></summary>\n\n"
            f"{diagram_block}"
            f"{wt_table}"
            "</details>\n\n"
        )

    # Verified Findings Detail Section with Prompt for AI Agents
    verified_findings_section = ""
    if findings:
        finding_blocks: list[str] = []
        for idx, f in enumerate(findings, 1):
            if not isinstance(f, dict):
                continue
            f_file = f.get("file", "unknown")
            f_line = f.get("line", "")
            f_sev = strip_emojis(str(f.get("severity", "risk"))).upper().strip() or "RISK"
            f_title = strip_emojis(str(f.get("title", "Issue Detected"))).strip()
            f_exp = strip_emojis(str(f.get("explanation") or f.get("comment") or "")).strip()
            f_sug = str(f.get("suggestion", "")).strip()
            line_ref = f" (Line {f_line})" if f_line else ""
            block = f"#### {idx}. [{f_sev}] `{f_file}`{line_ref} — {f_title}\n\n{f_exp}"
            if f_sug:
                block += f"\n\n```suggestion\n{f_sug}\n```"
            agent_prompt = _build_agent_prompt_for_finding(f)
            block += (
                "\n\n<details>\n"
                "<summary><b>Prompt for AI Agents</b></summary>\n\n"
                f"```text\n{agent_prompt}\n```\n\n"
                "</details>"
            )
            finding_blocks.append(block)
        if finding_blocks:
            verified_findings_section = (
                "### Verified Findings\n\n"
                + "\n\n---\n\n".join(finding_blocks)
                + "\n\n"
            )

    security_status = (
        f"| Secret Alerts | **{secret_alerts_count} (CRITICAL)** |\n"
        if secret_alerts_count > 0
        else "| Secret Scan | Clean (0 exposed keys) |\n"
    )

    findings_section = (
        f"### Findings Breakdown\n\n"
        f"| Category | Status / Count |\n"
        f"| :--- | :--- |\n"
        f"{security_status}"
        f"| Bugs | {bug_count} |\n"
        f"| Risks | {risk_count} |\n"
        f"| Nits | {nit_count} |\n\n"
    )

    # Suggested Unit Tests Section
    tests_section = ""
    if suggested_tests:
        test_blocks = []
        for idx, st in enumerate(suggested_tests, 1):
            target = strip_emojis(str(st.get("target_file", "Test"))).strip()
            desc = strip_emojis(str(st.get("description", "Suggested edge-case test"))).strip()
            code = str(st.get("code_snippet", "")).strip()
            test_blocks.append(f"**Test {idx} ({target})**: {desc}\n```python\n{code}\n```")
        tests_section = (
            "<details>\n"
            f"<summary><b>Suggested Unit Tests ({len(suggested_tests)})</b></summary>\n\n"
            + "\n\n".join(test_blocks)
            + "\n\n</details>\n"
        )

    card_body = (
        f"<!-- patchpilot-ai-review -->\n"
        f"## PatchPilot AI PR Review\n\n"
        f"{user_tag_part}**Verdict:** `{verdict_clean}` &nbsp;|&nbsp; **Commit:** `{sha_short}`{verif_part}\n\n"
        f"{failover_notice}"
        f"{poem_section}"
        f"{triage_section}"
        f"### Executive Summary\n\n"
        f"{strip_emojis(summary).strip()}\n\n"
        f"{checks_section}"
        f"{walkthrough_section}"
        f"{verified_findings_section}"
        f"{findings_section}"
        f"{tests_section}"
    )
    return strip_emojis(card_body)


async def publish_pr_review(
    token: str,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    review_data: dict[str, Any],
    valid_lines: dict[str, set[int]],
    client: httpx.AsyncClient | None = None,
    triage_summary: str | None = None,
    secret_alerts_count: int = 0,
    requested_by: str | None = None,
    existing_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish native GitHub review with emoji-free summary card, in-place loading card replacement, and verified inline comments."""
    summary = review_data.get("summary", "Review completed.")
    verdict = review_data.get("verdict", "PASS")
    findings = review_data.get("findings", [])
    agent_statuses = review_data.get("agent_statuses")
    review_checks = review_data.get("review_checks")
    walkthrough = review_data.get("walkthrough")
    poem = review_data.get("poem")
    suggested_tests = review_data.get("suggested_tests")
    fallback_used = review_data.get("fallback_used")
    verification_status = review_data.get("verification_status")
    architecture_diagram = review_data.get("architecture_diagram")

    bug_count = sum(1 for f in findings if str(f.get("severity", "")).lower() == "bug")
    risk_count = sum(1 for f in findings if str(f.get("severity", "")).lower() == "risk")
    nit_count = sum(1 for f in findings if str(f.get("severity", "")).lower() == "nit")

    summary_card = build_review_summary_card(
        verdict=verdict,
        commit_sha=head_sha,
        summary=summary,
        bug_count=bug_count,
        risk_count=risk_count,
        nit_count=nit_count,
        triage_summary=triage_summary,
        secret_alerts_count=secret_alerts_count,
        agent_statuses=agent_statuses,
        review_checks=review_checks,
        walkthrough=walkthrough,
        poem=poem,
        suggested_tests=suggested_tests,
        fallback_used=fallback_used,
        requested_by=requested_by,
        findings=findings,
        verification_status=verification_status,
        architecture_diagram=architecture_diagram,
    )

    comments: list[dict[str, Any]] = []
    for f in findings:
        file_path = f.get("file")
        line = f.get("line")
        severity = strip_emojis(str(f.get("severity", "risk"))).lower().strip() or "risk"
        title = strip_emojis(str(f.get("title", ""))).strip()
        explanation = strip_emojis(str(f.get("explanation") or f.get("comment") or "")).strip()
        suggestion = f.get("suggestion", "")

        if not file_path or line is None:
            continue

        file_valids = valid_lines.get(file_path, set())
        if line not in file_valids:
            continue

        comment_body = strip_emojis(f"**[{severity.upper()}] {title}**\n\n{explanation}")

        if suggestion and str(suggestion).strip():
            comment_body += f"\n\n```suggestion\n{str(suggestion).strip()}\n```"

        agent_prompt = _build_agent_prompt_for_finding(f)
        comment_body += (
            "\n\n<details>\n"
            "<summary><b>Prompt for AI Agents</b></summary>\n\n"
            f"```text\n{agent_prompt}\n```\n\n"
            "</details>"
        )

        comments.append({
            "path": file_path,
            "line": int(line),
            "side": "RIGHT",
            "body": strip_emojis(comment_body),
        })

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    review_event = "APPROVE" if str(verdict).upper() in ("PASS", "APPROVE") and bug_count == 0 and secret_alerts_count == 0 else "COMMENT"

    try:
        # If we already created an initial loading review card, update it in-place with the final summary card!
        if existing_review and existing_review.get("id"):
            updated_ok = await update_pr_review_card(
                token=token,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                handle=existing_review,
                body=summary_card,
                client=client,
            )
            if updated_ok:
                # If there are inline diff comments, attach them via a compact review submission
                if comments:
                    inline_payload: dict[str, Any] = {
                        "body": f"PatchPilot inline review findings ({len(comments)} comment{'s' if len(comments) != 1 else ''}).",
                        "event": "COMMENT",
                        "comments": comments,
                    }
                    if head_sha:
                        inline_payload["commit_id"] = head_sha
                    inline_response = await client.post(url, headers=headers, json=inline_payload)
                    if inline_response.status_code == 422:
                        fallback = summary_card + "\n\nInline placement failed; findings are preserved below:\n\n" + "\n\n".join(c["path"] + ":" + str(c["line"]) + "\n" + c["body"] for c in comments)
                        if not await update_pr_review_card(token, repo_full_name, pr_number, existing_review, fallback, client=client):
                            raise RuntimeError("Could not preserve rejected inline findings")
                        return {"status": "summary_only", "id": existing_review["id"]}
                    inline_response.raise_for_status()
                return {"status": "updated_in_place", "id": existing_review["id"]}

        payload: dict[str, Any] = {
            "commit_id": head_sha,
            "body": summary_card,
            "event": review_event,
        }
        if comments:
            payload["comments"] = comments

        response = await client.post(url, headers=headers, json=payload)
        if response.status_code == 422:
            logger.warning("GitHub 422 for review event %s. Retrying with COMMENT event for %s#%s", review_event, repo_full_name, pr_number)
            fallback_payload: dict[str, Any] = {
                "body": summary_card,
                "event": "COMMENT",
            }
            if head_sha:
                fallback_payload["commit_id"] = head_sha
            response = await client.post(url, headers=headers, json=fallback_payload)

        if response.status_code == 422:
            logger.warning("GitHub 422 on pulls/reviews (e.g. closed/merged PR). Falling back to issue comment for %s#%s", repo_full_name, pr_number)
            return await post_comment_reply(
                token=token,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                body=summary_card,
                review_comment_id=None,
                client=client,
            )

        response.raise_for_status()
        return response.json()
    finally:
        if close_client:
            await client.aclose()


async def post_comment_reply(
    token: str,
    repo_full_name: str,
    pr_number: int,
    body: str,
    review_comment_id: int | str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Post emoji-free comment reply to either an inline review thread or top-level issue conversation."""
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    clean_body = strip_emojis(body)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        if review_comment_id:
            url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/comments/{review_comment_id}/replies"
            response = await client.post(url, headers=headers, json={"body": clean_body})
        else:
            url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
            response = await client.post(url, headers=headers, json={"body": clean_body})

        response.raise_for_status()
        return response.json()
    finally:
        if close_client:
            await client.aclose()


async def apply_pr_labels(
    token: str,
    repo_full_name: str,
    pr_number: int,
    labels: list[str],
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Add triage, size, and security labels to GitHub Pull Request / Issue."""
    if not labels:
        return []

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/labels"
    try:
        response = await client.post(url, headers=headers, json={"labels": labels})
        if response.status_code in (200, 201):
            data = response.json()
            if isinstance(data, list):
                return [l.get("name", "") for l in data if isinstance(l, dict)]
            return labels
        logger.warning("GitHub label API returned status %s for %s#%s", response.status_code, repo_full_name, pr_number)
        return []
    except Exception as exc:
        logger.warning("Failed to apply PR labels to %s#%s: %s", repo_full_name, pr_number, exc)
        return []
    finally:
        if close_client:
            await client.aclose()


# -----------------------------------------------------------------------------
# GitHub OAuth & Repository Scope Inspection
# -----------------------------------------------------------------------------

def get_oauth_authorize_url(client_id: str, redirect_uri: str | None = None, state: str | None = None) -> str:
    """Generate GitHub OAuth authorization URL."""
    base = f"https://github.com/login/oauth/authorize?client_id={client_id}&scope=read:user,repo"
    if redirect_uri:
        base += f"&redirect_uri={redirect_uri}"
    if state:
        base += f"&state={state}"
    return base


async def exchange_oauth_code(
    code: str,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Exchange OAuth code for user access token."""
    url = "https://github.com/login/oauth/access_token"
    headers = {"Accept": "application/json"}
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    try:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise ValueError(f"Failed to obtain OAuth access token: {data}")
        return str(token)
    finally:
        if close_client:
            await client.aclose()


async def fetch_user_profile(
    user_token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch GitHub authenticated user profile."""
    url = "https://api.github.com/user"
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    finally:
        if close_client:
            await client.aclose()


async def fetch_user_installations_and_repos(
    user_token: str,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch repositories scoped to the user or where the GitHub App is installed."""
    url = "https://api.github.com/user/repos?per_page=50&sort=updated"
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    try:
        response = await client.get(url, headers=headers)
        if response.status_code != 200:
            logger.warning("Could not list user repos: %s", response.text)
            return []
        repos = response.json()
        return [
            {
                "full_name": r.get("full_name"),
                "name": r.get("name"),
                "owner": r.get("owner", {}).get("login"),
                "private": r.get("private", False),
                "html_url": r.get("html_url"),
                "description": r.get("description"),
            }
            for r in repos
        ]
    except Exception as exc:
        logger.warning("Error fetching user repos: %s", exc)
        return []
    finally:
        if close_client:
            await client.aclose()


def create_session_jwt(user_data: dict[str, Any]) -> str:
    """Create signed session JWT for user cookie with AES-256 encrypted user_token."""
    settings = get_settings()
    now = int(time.time())
    raw_token = user_data.get("user_token", "")
    encrypted_token = encrypt_secret(raw_token) if raw_token else ""
    payload = {
        "sub": user_data.get("login", ""),
        "avatar_url": user_data.get("avatar_url", ""),
        "name": user_data.get("name", ""),
        "user_token": encrypted_token,
        "iat": now,
        "exp": now + 86400 * 7,  # 7 days
    }
    return jwt.encode(payload, settings.session_secret, algorithm="HS256")


def verify_session_jwt(session_token: str | None) -> dict[str, Any] | None:
    """Verify and decode session JWT from cookie, decrypting the user_token."""
    if not session_token:
        return None
    settings = get_settings()
    try:
        data = jwt.decode(session_token, settings.session_secret, algorithms=["HS256"])
        if data.get("user_token"):
            try:
                data["user_token"] = decrypt_secret(data["user_token"])
            except Exception:
                data["user_token"] = ""
        return data
    except Exception:
        return None
