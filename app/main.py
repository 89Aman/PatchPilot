from contextlib import asynccontextmanager
import hmac
import logging
import re
from typing import Any
from fastapi import (
    BackgroundTasks,
    Cookie,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from pathlib import Path
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.crypto import encrypt_secret
from app.db import DEFAULT_POLICY, Base, SessionLocal, VaultConfig, engine, get_db, init_db
from app.github_auth import (
    create_session_jwt,
    exchange_oauth_code,
    fetch_pr_diff,
    fetch_user_installations_and_repos,
    fetch_user_profile,
    get_installation_token,
    get_oauth_authorize_url,
    post_comment_reply,
    publish_pr_review,
    verify_session_jwt,
    verify_webhook_signature,
)
from app.clerk_auth import (
    derive_frontend_api,
    extract_clerk_identity,
    fetch_clerk_oauth_token,
    fetch_clerk_user,
    verify_clerk_token,
)
from app.llm import annotate_diff, run_interactive_command, run_pr_review, test_llm_connection
from app.ui import DASHBOARD_HTML

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("patchpilot")

COMMAND_RE = re.compile(r"(?:@patchpilot|@contextlens|/lens)\s+(explain|fix)\b(.*)$", re.IGNORECASE | re.DOTALL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    logger.info("Initializing PatchPilot database schema...")
    await init_db()
    logger.info("PatchPilot database initialized.")
    yield


app = FastAPI(
    title="PatchPilot",
    description="Self-Hosted BYOK GitHub App & AI PR Review Engine",
    version="0.2.0",
    docs_url="/api/docs",
    lifespan=lifespan,
)

from starlette.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://patchpilot.noventra.org.in",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_origin_regex=r"https://.*\.noventra\.org\.in",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_admin_or_session(
    owner: str,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
    patchpilot_session: str | None = Cookie(None),
    clerk_session: str | None = Cookie(None, alias="__session"),
) -> None:
    """Verify request authorization via X-Admin-Token, session cookie, or Clerk token."""
    if x_admin_token and hmac.compare_digest(x_admin_token.strip(), settings.ADMIN_API_TOKEN.strip()):
        return

    # Check Bearer authorization header (Admin token or Clerk JWT)
    if authorization and authorization.lower().startswith("bearer "):
        bearer_val = authorization[7:].strip()
        if hmac.compare_digest(bearer_val, settings.ADMIN_API_TOKEN.strip()):
            return
        clerk_payload = await verify_clerk_token(bearer_val)
        if clerk_payload:
            identity = extract_clerk_identity(clerk_payload)
            sub = clerk_payload.get("sub", "").lower()
            if identity["login"].lower() == owner.strip().lower() or sub == owner.strip().lower():
                return

    session_data = verify_session_jwt(patchpilot_session)
    if session_data and session_data.get("sub", "").lower() == owner.strip().lower():
        return

    if clerk_session:
        clerk_payload = await verify_clerk_token(clerk_session)
        if clerk_payload:
            identity = extract_clerk_identity(clerk_payload)
            sub = clerk_payload.get("sub", "").lower()
            if identity["login"].lower() == owner.strip().lower() or sub == owner.strip().lower():
                return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: Valid X-Admin-Token header or matching authenticated session required",
    )


class VaultUpsertRequest(BaseModel):
    provider: str = Field(default="gemini", description="gemini | grok | groq | openrouter | openai | ollama | custom")
    model: str = Field(default="gemini-2.5-pro", description="e.g. gemini-2.5-pro, anthropic/claude-3.7-sonnet")
    api_key: str | None = Field(default=None, description="Plaintext API key to encrypt. If empty, retains existing key.")
    base_url: str | None = Field(default=None, description="Custom base URL for Ollama or OpenAI endpoints")
    review_policy: str = Field(default=DEFAULT_POLICY, description="Custom Markdown review guidelines")


# -----------------------------------------------------------------------------
# Frontend Routes
# -----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def root_page(request: Request):
    """Render main PatchPilot landing page."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/dashboard", response_class=HTMLResponse, tags=["Frontend"])
async def dashboard_page():
    """Render main PatchPilot dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/settings", response_class=HTMLResponse, tags=["Frontend"])
async def settings_page():
    """Render settings page (alias to dashboard)."""
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/docs", response_class=HTMLResponse, tags=["Frontend"])
@app.get("/documentation", response_class=HTMLResponse, tags=["Frontend"])
async def docs_page(request: Request):
    """Render comprehensive PatchPilot documentation and commands page."""
    return templates.TemplateResponse(request=request, name="docs.html")


@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "app": "PatchPilot", "version": "0.2.0"}


FAVICON_SVG = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='%23f97316'/><path d='M8 20l16-8-8 16-2-6-6-2z' fill='%23ffffff'/></svg>"""

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve SVG favicon."""
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


# -----------------------------------------------------------------------------
# GitHub OAuth & User Repositories
# -----------------------------------------------------------------------------

@app.get("/auth/login", tags=["Authentication"])
async def auth_login():
    """Redirect user to GitHub OAuth authorization screen."""
    client_id = settings.GITHUB_CLIENT_ID.strip()
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GITHUB_CLIENT_ID is not configured in .env. OAuth login requires a valid GitHub OAuth App Client ID (e.g., Iv1... or Iv23...).",
        )
    url = get_oauth_authorize_url(client_id)
    return RedirectResponse(url=url)


@app.get("/auth/callback", tags=["Authentication"])
async def auth_callback(request: Request, code: str):
    """Handle OAuth callback, issue session cookie, and redirect to dashboard."""
    client_id = settings.GITHUB_CLIENT_ID.strip()
    client_secret = settings.GITHUB_CLIENT_SECRET.strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth credentials (GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET) not fully configured in .env",
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        user_token = await exchange_oauth_code(code, client_id, client_secret, client=client)
        profile = await fetch_user_profile(user_token, client=client)

    session_payload = {
        "login": profile.get("login"),
        "avatar_url": profile.get("avatar_url"),
        "name": profile.get("name"),
        "user_token": user_token,
    }
    jwt_token = create_session_jwt(session_payload)

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="patchpilot_session",
        value=jwt_token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=86400 * 7,
    )
    return response


class ClerkSessionRequest(BaseModel):
    session_token: str
    user: dict[str, Any] | None = None


@app.get("/auth/clerk/config", tags=["Authentication"])
async def clerk_config():
    """Return Clerk publishable key and configuration status for frontend client."""
    pk = settings.clerk_publishable_key
    return {
        "publishable_key": pk,
        "is_configured": bool(pk),
        "frontend_api": derive_frontend_api(pk) if pk else None,
    }


@app.post("/auth/clerk/session", tags=["Authentication"])
async def clerk_session(request: Request, body: ClerkSessionRequest):
    """Verify Clerk session token and establish PatchPilot session cookie."""
    payload = await verify_clerk_token(body.session_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Clerk session token",
        )

    sub = payload.get("sub", "")
    clerk_user_info = None
    github_token = ""
    if settings.clerk_secret_key and sub:
        clerk_user_info = await fetch_clerk_user(sub)
        github_token = await fetch_clerk_oauth_token(sub, "oauth_github") or ""

    identity = extract_clerk_identity(payload, clerk_user_info or body.user)
    jwt_token = create_session_jwt({
        "login": identity["login"],
        "avatar_url": identity["avatar_url"],
        "name": identity["name"],
        "user_token": github_token,
        "clerk_id": sub,
        "github_username": identity.get("github_username") or "",
    })

    import json
    response = Response(
        content=json.dumps({"status": "ok", "user": identity}),
        media_type="application/json",
    )
    response.set_cookie(
        key="patchpilot_session",
        value=jwt_token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=86400 * 7,
    )
    return response


@app.get("/auth/logout", tags=["Authentication"])
async def auth_logout():
    """Clear session cookie and redirect to home."""
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key="patchpilot_session")
    response.delete_cookie(key="__session")
    return response


@app.get("/auth/me", tags=["Authentication"])
async def auth_me(
    patchpilot_session: str | None = Cookie(None),
    authorization: str | None = Header(None, alias="Authorization"),
    clerk_session: str | None = Cookie(None, alias="__session"),
):
    """Return currently authenticated user profile from session cookie or Clerk."""
    session = verify_session_jwt(patchpilot_session)
    if session:
        has_gh = bool(session.get("user_token") or session.get("github_username"))
        return {
            "logged_in": True,
            "user": {
                "login": session.get("sub"),
                "avatar_url": session.get("avatar_url"),
                "name": session.get("name"),
                "github_username": session.get("github_username") or session.get("sub"),
                "github_connected": has_gh,
            },
            "provider": "github" if session.get("user_token") else "clerk",
        }

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif clerk_session:
        token = clerk_session

    if token:
        clerk_payload = await verify_clerk_token(token)
        if clerk_payload:
            clerk_user_info = None
            sub = clerk_payload.get("sub", "")
            if settings.clerk_secret_key and sub:
                clerk_user_info = await fetch_clerk_user(sub)
            identity = extract_clerk_identity(clerk_payload, clerk_user_info)
            return {
                "logged_in": True,
                "user": identity,
                "provider": "clerk",
            }

    return {"logged_in": False, "user": None}


class GitHubTokenRequest(BaseModel):
    token: str


@app.post("/api/user/github-token", tags=["Repositories"])
async def set_user_github_token(
    body: GitHubTokenRequest,
    request: Request,
    patchpilot_session: str | None = Cookie(None),
):
    """Store or update GitHub access token for the authenticated session to enable repo access."""
    session = verify_session_jwt(patchpilot_session)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")

    clean_token = body.token.strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="Token cannot be empty")

    async with httpx.AsyncClient(timeout=10.0) as client:
        profile = await fetch_user_profile(clean_token, client=client)

    jwt_token = create_session_jwt({
        "login": profile.get("login") or session.get("sub"),
        "avatar_url": profile.get("avatar_url") or session.get("avatar_url"),
        "name": profile.get("name") or session.get("name"),
        "user_token": clean_token,
        "clerk_id": session.get("clerk_id", ""),
        "github_username": profile.get("login", ""),
    })

    import json
    response = Response(
        content=json.dumps({"status": "ok", "github_login": profile.get("login")}),
        media_type="application/json",
    )
    response.set_cookie(
        key="patchpilot_session",
        value=jwt_token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=86400 * 7,
    )
    return response


@app.get("/api/user/repos", tags=["Repositories"])
async def get_user_repositories(patchpilot_session: str | None = Cookie(None)):
    """Fetch repositories scoped to the authenticated user."""
    session = verify_session_jwt(patchpilot_session)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required to inspect repository scope")

    user_token = session.get("user_token")
    clerk_id = session.get("clerk_id")

    # If no token in session, check if Clerk has user's GitHub OAuth token
    if not user_token and clerk_id and settings.clerk_secret_key:
        user_token = await fetch_clerk_oauth_token(clerk_id, "oauth_github")

    if not user_token:
        return {
            "github_connected": False,
            "message": "GitHub account not connected. Connect GitHub to allow repository scope.",
            "repos": [],
        }

    async with httpx.AsyncClient(timeout=30.0) as client:
        repos = await fetch_user_installations_and_repos(user_token, client=client)

    return {
        "github_connected": True,
        "repos": repos,
    }


# -----------------------------------------------------------------------------
# BYOK Vault Endpoints
# -----------------------------------------------------------------------------

@app.get("/vault/{owner}", tags=["Vault"])
async def get_vault(
    owner: str,
    _: None = Depends(verify_admin_or_session),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve owner vault metadata without exposing raw API keys."""
    owner_clean = owner.strip().lower()
    stmt = select(VaultConfig).where(VaultConfig.owner == owner_clean)
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()

    if not cfg:
        return {
            "owner": owner_clean,
            "provider": "gemini",
            "model": "gemini-2.5-pro",
            "base_url": "",
            "has_api_key": False,
            "review_policy": DEFAULT_POLICY,
        }

    return {
        "owner": cfg.owner,
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url or "",
        "has_api_key": bool(cfg.encrypted_api_key),
        "review_policy": cfg.review_policy,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@app.put("/vault/{owner}", tags=["Vault"])
async def upsert_vault(
    owner: str,
    body: VaultUpsertRequest,
    _: None = Depends(verify_admin_or_session),
    db: AsyncSession = Depends(get_db),
):
    """Upsert BYOK vault configuration for an owner."""
    owner_clean = owner.strip().lower()
    if not owner_clean:
        raise HTTPException(status_code=400, detail="Owner cannot be empty")

    stmt = select(VaultConfig).where(VaultConfig.owner == owner_clean)
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()

    encrypted_key = ""
    if body.api_key and body.api_key.strip():
        encrypted_key = encrypt_secret(body.api_key.strip())
    elif cfg and cfg.encrypted_api_key:
        encrypted_key = cfg.encrypted_api_key

    if cfg:
        cfg.provider = body.provider.lower()
        cfg.model = body.model.strip()
        cfg.encrypted_api_key = encrypted_key
        cfg.base_url = body.base_url.strip() if body.base_url else None
        cfg.review_policy = body.review_policy.strip()
    else:
        cfg = VaultConfig(
            owner=owner_clean,
            provider=body.provider.lower(),
            model=body.model.strip(),
            encrypted_api_key=encrypted_key,
            base_url=body.base_url.strip() if body.base_url else None,
            review_policy=body.review_policy.strip(),
        )
        db.add(cfg)

    await db.commit()
    await db.refresh(cfg)

    return {
        "owner": cfg.owner,
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "has_api_key": bool(cfg.encrypted_api_key),
        "review_policy": cfg.review_policy,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@app.post("/vault/{owner}/test", tags=["Vault"])
async def test_vault_endpoint(
    owner: str,
    _: None = Depends(verify_admin_or_session),
    db: AsyncSession = Depends(get_db),
):
    """Test live LLM connection with configured credentials."""
    owner_clean = owner.strip().lower()
    stmt = select(VaultConfig).where(VaultConfig.owner == owner_clean)
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()

    if not cfg:
        raise HTTPException(status_code=404, detail=f"No vault configuration found for owner '{owner_clean}'. Save first.")

    try:
        reply = await test_llm_connection(cfg)
        return {"status": "ok", "provider": cfg.provider, "model": cfg.model, "reply": reply}
    except Exception as exc:
        logger.error("Connection test error for %s: %s", owner_clean, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# -----------------------------------------------------------------------------
# Background Handlers
# -----------------------------------------------------------------------------

async def _get_owner_config(owner: str) -> VaultConfig | None:
    async with SessionLocal() as session:
        result = await session.execute(select(VaultConfig).where(VaultConfig.owner == owner.strip().lower()))
        return result.scalar_one_or_none()


async def handle_pr_event(payload: dict[str, Any]) -> None:
    """Background task to fetch PR diff, run review, and publish comments."""
    try:
        installation_id = payload.get("installation", {}).get("id")
        repository = payload.get("repository", {})
        repo_full_name = repository.get("full_name", "")
        owner = repository.get("owner", {}).get("login", "")
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number")
        head_sha = pr.get("head", {}).get("sha", "")
        pr_title = pr.get("title", "")
        pr_body = pr.get("body") or ""

        if not installation_id or not repo_full_name or not pr_number:
            return

        cfg = await _get_owner_config(owner)
        if not cfg:
            logger.warning("No vault configuration found for %s. Skipping review.", owner)
            return

        async with httpx.AsyncClient(timeout=60.0) as client:
            token = await get_installation_token(installation_id, client=client)
            raw_diff = await fetch_pr_diff(token, repo_full_name, pr_number, client=client)
            annotated_diff, valid_lines = annotate_diff(raw_diff)

            if not annotated_diff.strip():
                return

            review_data = await run_pr_review(cfg, pr_title, pr_body, annotated_diff, client=client)
            await publish_pr_review(token, repo_full_name, pr_number, head_sha, review_data, valid_lines, client=client)
            logger.info("Published review for %s#%s", repo_full_name, pr_number)
    except Exception as exc:
        logger.exception("Failed processing PR review webhook: %s", exc)


async def handle_comment_event(event_type: str, payload: dict[str, Any]) -> None:
    """Background task to answer @patchpilot explain or @patchpilot fix commands."""
    try:
        comment = payload.get("comment", {})
        comment_body = comment.get("body", "")
        match = COMMAND_RE.search(comment_body)
        if not match:
            return

        command = match.group(1).lower()
        extra_prompt = match.group(2).strip() or comment_body
        installation_id = payload.get("installation", {}).get("id")
        repository = payload.get("repository", {})
        repo_full_name = repository.get("full_name", "")
        owner = repository.get("owner", {}).get("login", "")

        if event_type == "pull_request_review_comment":
            pr_number = payload.get("pull_request", {}).get("number")
            review_comment_id = comment.get("id")
            file_path = comment.get("path")
            diff_hunk = comment.get("diff_hunk")
        else:
            issue = payload.get("issue", {})
            if "pull_request" not in issue:
                return
            pr_number = issue.get("number")
            review_comment_id = None
            file_path = None
            diff_hunk = None

        if not installation_id or not repo_full_name or not pr_number:
            return

        cfg = await _get_owner_config(owner)
        if not cfg:
            return

        async with httpx.AsyncClient(timeout=60.0) as client:
            token = await get_installation_token(installation_id, client=client)
            pr_diff = "" if diff_hunk else await fetch_pr_diff(token, repo_full_name, pr_number, client=client)

            reply_md = await run_interactive_command(
                cfg=cfg,
                command=command,
                user_comment=extra_prompt,
                file_path=file_path,
                diff_hunk=diff_hunk,
                pr_diff=pr_diff,
                client=client,
            )

            await post_comment_reply(
                token=token,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                body=f"### ✈️ PatchPilot (`{command}`)\n\n{reply_md}",
                review_comment_id=review_comment_id,
                client=client,
            )
            logger.info("Replied to %s command on %s#%s", command, repo_full_name, pr_number)
    except Exception as exc:
        logger.exception("Failed processing interactive comment: %s", exc)


# -----------------------------------------------------------------------------
# GitHub Webhook Endpoint
# -----------------------------------------------------------------------------

@app.post("/webhooks/github", tags=["Webhook"])
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str = Header(default="", alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
):
    """Receive and dispatch GitHub webhook events."""
    raw_body = await request.body()
    if not verify_webhook_signature(raw_body, x_hub_signature_256, settings.GITHUB_WEBHOOK_SECRET):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Hub-Signature-256 signature",
        )

    payload = await request.json()
    action = payload.get("action", "")

    # 1. Pull Request Events
    if x_github_event == "pull_request" and action in ("opened", "synchronize", "reopened"):
        if not payload.get("pull_request", {}).get("draft", False):
            background_tasks.add_task(handle_pr_event, payload)
            return {"status": "queued_pr_review"}
        return {"status": "ignored_draft"}

    # 2. Interactive Comments
    if x_github_event in ("pull_request_review_comment", "issue_comment") and action == "created":
        sender_type = str(payload.get("comment", {}).get("user", {}).get("type") or payload.get("sender", {}).get("type", "")).lower()
        if sender_type != "bot":
            body_text = payload.get("comment", {}).get("body", "")
            if COMMAND_RE.search(body_text):
                background_tasks.add_task(handle_comment_event, x_github_event, payload)
                return {"status": "queued_comment_reply"}

        return {"status": "ignored_comment"}

    return {"status": "ignored_event"}
