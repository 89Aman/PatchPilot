from datetime import datetime, timezone
import hashlib
import hmac
import logging
import time
from typing import Any
import httpx
import jwt
from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


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
) -> str:
    """Generate rich CodeRabbit-grade Markdown summary card with review checks, walkthrough table, and test stubs."""
    verdict_clean = verdict.upper().strip()
    verdict_emoji = {
        "PASS": "🟢",
        "WARN": "🟡",
        "BLOCK": "🔴",
    }.get(verdict_clean, "🔍")

    sha_short = commit_sha[:7] if commit_sha else "HEAD"

    # Failover Alert Notice (if review was completed via fallback provider)
    failover_notice = ""
    if fallback_used:
        cand_provider = str(fallback_used.get("provider", "")).capitalize()
        cand_model = fallback_used.get("model", "")
        primary_reason = fallback_used.get("primary_reason", "Provider rate limit or outage")
        failover_notice = (
            f"> ⚡ **Failover Alert**: Primary LLM returned *{primary_reason}*. "
            f"Review successfully completed via fallback: **{cand_provider}** (`{cand_model}`).\n\n"
        )

    # Optional CodeRabbit Poem Banner
    poem_section = ""
    if poem and poem.strip():
        poem_clean = "\n".join(f"> *{line.strip()}*" for line in poem.strip().splitlines() if line.strip())
        poem_section = f"{poem_clean}\n\n"

    triage_section = ""
    if triage_summary:
        triage_section = f"### 🏷️ Triage & Impact\n\n{triage_summary}\n\n"

    # Review Checks Matrix Badges
    checks_dict = review_checks or agent_statuses
    checks_section = ""
    if checks_dict:
        rows = [f"| **{category}** | {status} |" for category, status in checks_dict.items()]
        checks_section = (
            "### 🎯 Review Checks\n\n"
            "| Category | Status |\n"
            "| :--- | :--- |\n"
            + "\n".join(rows)
            + "\n\n"
        )


    # File Walkthrough Table
    walkthrough_section = ""
    if walkthrough:
        wt_rows = [
            f"| `{item.get('file', '')}` | {item.get('impact', '🧹 Refactor')} | {item.get('summary', '')} |"
            for item in walkthrough
            if item.get("file")
        ]
        if wt_rows:
            walkthrough_section = (
                "### 🔍 Changes Walkthrough\n\n"
                "| File | Impact | Summary |\n"
                "| :--- | :--- | :--- |\n"
                + "\n".join(wt_rows)
                + "\n\n"
            )

    security_status = (
        f"| 🚨 Secret Alerts | **{secret_alerts_count} (CRITICAL)** |\n"
        if secret_alerts_count > 0
        else "| 🛡️ Secret Scan | 🟢 Clean (0 exposed keys) |\n"
    )

    findings_section = (
        f"### 📊 Findings Breakdown\n\n"
        f"| Category | Status / Count |\n"
        f"| :--- | :--- |\n"
        f"{security_status}"
        f"| 🔴 Bugs | {bug_count} |\n"
        f"| 🟡 Risks | {risk_count} |\n"
        f"| 🔵 Nits | {nit_count} |\n\n"
    )

    # Suggested Unit Tests Section
    tests_section = ""
    if suggested_tests:
        test_blocks = []
        for idx, st in enumerate(suggested_tests, 1):
            target = st.get("target_file", "Test")
            desc = st.get("description", "Suggested edge-case test")
            code = st.get("code_snippet", "").strip()
            test_blocks.append(f"**Test {idx} ({target})**: {desc}\n```python\n{code}\n```")
        tests_section = (
            "<details>\n"
            f"<summary>🧪 <b>Suggested Unit Tests ({len(suggested_tests)})</b></summary>\n\n"
            + "\n\n".join(test_blocks)
            + "\n\n</details>\n"
        )

    return (
        f"<!-- patchpilot-ai-review -->\n"
        f"## {verdict_emoji} PatchPilot AI PR Review\n\n"
        f"**Verdict:** `{verdict_clean}` &nbsp;|&nbsp; **Commit:** `{sha_short}`\n\n"
        f"{failover_notice}"
        f"{poem_section}"
        f"{triage_section}"
        f"{summary.strip()}\n\n"
        f"{checks_section}"
        f"{walkthrough_section}"
        f"{findings_section}"
        f"{tests_section}"
    )


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
) -> dict[str, Any]:
    """Publish native GitHub review with summary card and filtered inline comments."""
    summary = review_data.get("summary", "Review completed.")
    verdict = review_data.get("verdict", "PASS")
    findings = review_data.get("findings", [])
    agent_statuses = review_data.get("agent_statuses")
    walkthrough = review_data.get("walkthrough")
    poem = review_data.get("poem")
    suggested_tests = review_data.get("suggested_tests")
    fallback_used = review_data.get("fallback_used")

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
        walkthrough=walkthrough,
        poem=poem,
        suggested_tests=suggested_tests,
        fallback_used=fallback_used,
    )


    comments: list[dict[str, Any]] = []
    for f in findings:
        file_path = f.get("file")
        line = f.get("line")
        severity = str(f.get("severity", "risk")).lower()
        title = f.get("title", "")
        explanation = f.get("explanation", "")
        suggestion = f.get("suggestion", "")

        if not file_path or line is None:
            continue

        file_valids = valid_lines.get(file_path, set())
        if line not in file_valids:
            continue

        sev_icon = {"bug": "🔴", "risk": "🟡", "nit": "🔵"}.get(severity, "ℹ️")
        comment_body = f"{sev_icon} **[{severity.upper()}] {title}**\n\n{explanation}"

        if suggestion and str(suggestion).strip():
            comment_body += f"\n\n```suggestion\n{str(suggestion).strip()}\n```"

        comments.append({
            "path": file_path,
            "line": int(line),
            "side": "RIGHT",
            "body": comment_body,
        })

    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    review_event = "APPROVE" if str(verdict).upper() in ("PASS", "APPROVE") and bug_count == 0 and secret_alerts_count == 0 else "COMMENT"
    payload: dict[str, Any] = {
        "commit_id": head_sha,
        "body": summary_card,
        "event": review_event,
    }
    if comments:
        payload["comments"] = comments

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    try:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code == 422:
            logger.warning("GitHub 422 for review event %s. Retrying with COMMENT event for %s#%s", review_event, repo_full_name, pr_number)
            fallback_payload = {
                "commit_id": head_sha,
                "body": f"{summary_card}\n\n*(Note: Some inline diff comments could not be anchored)*" if comments else summary_card,
                "event": "COMMENT",
            }
            response = await client.post(url, headers=headers, json=fallback_payload)

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
    """Post comment reply to either an inline review thread or top-level issue conversation."""
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        close_client = True

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        if review_comment_id:
            url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/comments/{review_comment_id}/replies"
            response = await client.post(url, headers=headers, json={"body": body})
        else:
            url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
            response = await client.post(url, headers=headers, json={"body": body})

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
