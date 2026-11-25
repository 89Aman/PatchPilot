import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import hmac
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any
import uuid
import httpx
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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.billing import (
    BillingProviderError,
    PolarBillingClient,
    WebhookVerificationError,
    flush_unreported_seat_events,
    process_polar_webhook,
)
from app.clerk_auth import (
    derive_accounts_url,
    derive_frontend_api,
    extract_clerk_identity,
    fetch_clerk_oauth_token,
    fetch_clerk_user,
    verify_clerk_token,
)
from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import (
    DEFAULT_POLICY,
    Base,
    BillingWebhookEvent,
    ReviewUsageEvent,
    SessionLocal,
    UserAccount,
    VaultConfig,
    Workspace,
    WorkspaceActiveCommitter,
    WorkspaceMember,
    engine,
    get_db,
    init_db,
    is_test,
)
from app.entitlements import (
    current_billing_period,
    evaluate_review_entitlement,
    is_workspace_subscribed,
    record_completed_review,
    workspace_billing_url,
)
from app.github_auth import (
    add_comment_reaction,
    apply_pr_labels,
    build_loading_review_card,
    complete_check_indicator,
    create_pending_pr_review,
    create_session_jwt,
    exchange_oauth_code,
    fetch_installation_repositories,
    fetch_pr_diff,
    fetch_pr_metadata,
    fetch_user_installations_and_repos,
    fetch_user_profile,
    get_installation_token,
    get_oauth_authorize_url,
    post_comment_reply,
    publish_pr_review,
    start_check_indicator,
    strip_emojis,
    update_pr_review_card,
    verify_session_jwt,
    verify_webhook_signature,
)
from app.jobs import current_job, enqueue, process_one, remembered, worker
from app.llm import (
    annotate_diff,
    build_managed_vault_config,
    get_provider_candidates,
    run_interactive_command,
    run_pr_review,
    test_llm_connection,
)
from app.models_catalog import OFFICIAL_FREE_MODELS
from app.security import (
    check_pr_authorization,
    global_rate_limiter,
    scan_diff_for_secrets,
)
from app.triage import compute_pr_triage
from app.ui import DASHBOARD_HTML
from app.webhooks_crypto import verify_standard_webhook
from app.workspaces import (
    ADMIN_ROLES,
    authorize_workspace_member,
    get_vault_for_workspace,
    list_user_workspaces,
    resolve_workspace_for_webhook,
    sync_user_workspaces,
    upsert_workspace_from_installation,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("patchpilot")

COMMAND_RE = re.compile(
    r"(?:@patchpilot[\w-]*(?:\[bot\])?|@contextlens[\w-]*(?:\[bot\])?|/lens|/patchpilot)\s+(explain|fix|review(?:\s+(?:security|perf|bugs?))?|generate\s+tests?|test|tests)\b(.*)$",
    re.IGNORECASE | re.DOTALL,
)



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    logger.info("Initializing PatchPilot database schema...")
    await init_db()
    logger.info("PatchPilot database initialized.")
    task = asyncio.create_task(worker())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="PatchPilot",
    description="Self-Hosted BYOK GitHub App & AI PR Review Engine",
    version="0.2.0",
    docs_url="/api/docs",
    lifespan=lifespan,
)

from starlette.middleware.cors import CORSMiddleware

_cors_kwargs: dict[str, Any] = {
    "allow_origins": settings.cors_origins,
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if settings.CORS_ALLOW_ORIGIN_REGEX.strip():
    _cors_kwargs["allow_origin_regex"] = settings.CORS_ALLOW_ORIGIN_REGEX.strip()

app.add_middleware(CORSMiddleware, **_cors_kwargs)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _request_origin(request: Request) -> str:
    """Resolve canonical public origin from APP_BASE_URL or incoming request URL."""
    if settings.app_base_url:
        return settings.app_base_url
    return str(request.base_url).rstrip("/")


async def _verified_clerk_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """Bind authorization to Clerk's immutable subject or a server-verified GitHub account."""
    sub = payload.get("sub") or ""
    user_info = await fetch_clerk_user(sub) if settings.clerk_secret_key and sub else None
    identity = extract_clerk_identity(payload, user_info)
    # Display usernames and email prefixes are user-editable, not GitHub ownership proof.
    github_username = (user_info or {}).get("github_username") or ""
    github_user_id = (user_info or {}).get("github_user_id")
    email = (user_info or {}).get("email") or payload.get("email") or ""
    identity.update(
        login=github_username or sub,
        github_username=github_username,
        github_user_id=github_user_id,
        email=email,
        github_connected=bool(github_username),
    )
    return identity


async def _resolve_authenticated_identity(
    patchpilot_session: str | None = None,
    authorization: str | None = None,
    clerk_session: str | None = None,
) -> dict[str, Any] | None:
    """Resolve authenticated web user from session cookie, Clerk Bearer token, or Clerk cookie."""
    session = verify_session_jwt(patchpilot_session)
    if session:
        login = (session.get("sub") or "").strip()
        gh_username = (session.get("github_username") or "").strip()
        clerk_id = (session.get("clerk_id") or "").strip()
        raw_gh_id = session.get("github_user_id")
        gh_user_id = int(raw_gh_id) if raw_gh_id is not None and str(raw_gh_id).isdigit() else None
        user_token = session.get("user_token") or ""
        provider = session.get("auth_provider") or ("clerk" if clerk_id else "github")
        # If signed in directly with GitHub OAuth (no Clerk), bind canonical user key to github:<id or login>
        canonical_user_id = clerk_id or (f"github:{gh_user_id}" if gh_user_id else login)
        return {
            "clerk_id": canonical_user_id,
            "raw_clerk_id": clerk_id,
            "login": login,
            "name": session.get("name") or login,
            "email": session.get("email") or "",
            "avatar_url": session.get("avatar_url") or "",
            "github_username": gh_username or (login if user_token else ""),
            "github_user_id": gh_user_id,
            "github_connected": bool(user_token or gh_username),
            "user_token": user_token,
            "provider": provider,
        }

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif clerk_session:
        token = clerk_session

    if token:
        clerk_payload = await verify_clerk_token(token)
        if clerk_payload:
            identity = await _verified_clerk_identity(clerk_payload)
            return {
                "clerk_id": identity.get("clerk_id") or clerk_payload.get("sub", ""),
                "raw_clerk_id": identity.get("clerk_id") or clerk_payload.get("sub", ""),
                "login": identity["login"],
                "name": identity["name"],
                "email": identity.get("email") or "",
                "avatar_url": identity["avatar_url"],
                "github_username": identity.get("github_username") or "",
                "github_user_id": identity.get("github_user_id"),
                "github_connected": bool(identity.get("github_connected")),
                "user_token": "",
                "provider": "clerk",
            }

    return None


async def _resolve_user_github_token(db: AsyncSession, identity: dict[str, Any]) -> str:
    """Resolve decrypted GitHub OAuth token from session, DB, or Clerk Backend API."""
    user_token = (identity.get("user_token") or "").strip()
    if user_token:
        return user_token

    raw_clerk_id = (identity.get("raw_clerk_id") or "").strip()
    login_clean = (identity.get("login") or "").strip().lower()

    if login_clean:
        res = await db.execute(select(UserAccount).where(UserAccount.login == login_clean))
        rec = res.scalar_one_or_none()
        if rec is None and raw_clerk_id:
            res = await db.execute(select(UserAccount).where(UserAccount.clerk_id == raw_clerk_id))
            rec = res.scalar_one_or_none()
        if rec and rec.encrypted_github_token:
            try:
                user_token = (decrypt_secret(rec.encrypted_github_token) or "").strip()
            except Exception:
                user_token = ""

    if not user_token and raw_clerk_id and settings.clerk_secret_key:
        user_token = (await fetch_clerk_oauth_token(raw_clerk_id, "oauth_github") or "").strip()

    return user_token


async def _user_is_workspace_admin_for_owner(
    db: AsyncSession,
    owner_clean: str,
    identity: dict[str, Any],
) -> bool:
    """Return True if the authenticated user is a verified owner/admin for a workspace matching owner_clean."""
    clerk_user_id = identity.get("clerk_id") or ""
    if not clerk_user_id or not owner_clean:
        return False
    res = await db.execute(
        select(Workspace).where(func.lower(Workspace.github_login) == owner_clean)
    )
    ws = res.scalar_one_or_none()
    if ws is None:
        return False
    gh_token = await _resolve_user_github_token(db, identity)
    pair = await authorize_workspace_member(
        db,
        workspace_id=ws.id,
        clerk_user_id=clerk_user_id,
        require_admin=True,
        github_token=gh_token,
        github_user_id=identity.get("github_user_id"),
        github_login=identity.get("github_username") or identity.get("login"),
    )
    return pair is not None


async def verify_admin_or_session(
    owner: str,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
    patchpilot_session: str | None = Cookie(None),
    clerk_session: str | None = Cookie(None, alias="__session"),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Verify request authorization via X-Admin-Token, personal session, or verified GitHub Org admin membership."""
    if x_admin_token and hmac.compare_digest(x_admin_token.strip(), settings.ADMIN_API_TOKEN.strip()):
        return

    owner_clean = owner.strip().lower()

    if authorization and authorization.lower().startswith("bearer "):
        bearer_val = authorization[7:].strip()
        if hmac.compare_digest(bearer_val, settings.ADMIN_API_TOKEN.strip()):
            return

    identity = await _resolve_authenticated_identity(
        patchpilot_session=patchpilot_session,
        authorization=authorization,
        clerk_session=clerk_session,
    )
    if identity:
        sub_login = (identity.get("login") or "").lower()
        clerk_sub = (identity.get("raw_clerk_id") or identity.get("clerk_id") or "").lower()
        gh_login = (identity.get("github_username") or "").lower()
        if sub_login == owner_clean or clerk_sub == owner_clean or (gh_login and gh_login == owner_clean):
            return
        if await _user_is_workspace_admin_for_owner(db, owner_clean, identity):
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: Valid X-Admin-Token header or matching authenticated workspace admin session required",
    )


class VaultUpsertRequest(BaseModel):
    provider: str = Field(default="gemini", description="gemini | grok | groq | openrouter | openai | ollama | custom")
    model: str = Field(default="gemini-2.5-pro", description="e.g. gemini-2.5-pro, anthropic/claude-3.7-sonnet")
    api_key: str | None = Field(default=None, description="Plaintext API key to encrypt. If empty, retains existing key.")
    base_url: str | None = Field(default=None, description="Custom base URL for Ollama or OpenAI endpoints")
    review_policy: str = Field(default=DEFAULT_POLICY, description="Custom Markdown review guidelines")


class FallbackUpsertRequest(BaseModel):
    id: str | None = Field(default=None, description="Optional ID when updating existing fallback")
    name: str = Field(default="Secondary Fallback", description="Label for this fallback")
    provider: str = Field(default="groq", description="gemini | groq | openrouter | openai | ollama | custom")
    model: str = Field(default="llama-3.3-70b-versatile", description="Model slug")
    api_key: str | None = Field(default=None, description="Plaintext API key. If empty, retains existing key.")
    base_url: str | None = Field(default=None, description="Custom endpoint URL")
    is_enabled: bool = Field(default=True, description="Enable or disable this candidate")


class WorkspaceBillingSettingsRequest(BaseModel):
    key_mode: str | None = Field(default=None, description="managed | byok")
    seat_cap: int | None = Field(default=None, description="Optional max billable active committers per cycle (null to clear)")
    clear_seat_cap: bool = Field(default=False, description="Set true to remove an existing seat cap")


# -----------------------------------------------------------------------------
# Frontend Routes
# -----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def root_page(request: Request):
    """Render main PatchPilot landing page."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_base_url": _request_origin(request),
            "install_url": settings.github_app_install_url,
            "billing_enabled": settings.BILLING_ENABLED,
        },
    )


@app.get("/pricing", response_class=HTMLResponse, tags=["Frontend"])
async def pricing_page(request: Request):
    """Render PatchPilot pricing page."""
    return templates.TemplateResponse(
        request=request,
        name="pricing.html",
        context={
            "app_base_url": _request_origin(request),
            "install_url": settings.github_app_install_url,
            "billing_enabled": settings.BILLING_ENABLED,
        },
    )


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
    return templates.TemplateResponse(
        request=request,
        name="docs.html",
        context={
            "app_base_url": _request_origin(request),
            "install_url": settings.github_app_install_url,
        },
    )


@app.get("/privacy", response_class=HTMLResponse, tags=["Legal"])
@app.get("/privacy-policy", response_class=HTMLResponse, tags=["Legal"])
async def privacy_page(request: Request):
    """Render Privacy Policy and Zero-Leakage Guarantee page."""
    return templates.TemplateResponse(request=request, name="privacy.html")


@app.get("/terms", response_class=HTMLResponse, tags=["Legal"])
@app.get("/terms-of-service", response_class=HTMLResponse, tags=["Legal"])
@app.get("/tos", response_class=HTMLResponse, tags=["Legal"])
async def terms_page(request: Request):
    """Render Terms of Service and Acceptable Use page."""
    return templates.TemplateResponse(request=request, name="terms.html")


@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "app": "PatchPilot", "version": "0.2.0"}


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt(request: Request):
    """Serve robots.txt for search engine crawlers."""
    origin = _request_origin(request)
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /docs\n"
        "Allow: /pricing\n"
        "Disallow: /vault/\n"
        "Disallow: /api/\n"
        "Disallow: /webhooks/\n"
        f"Sitemap: {origin}/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml(request: Request):
    """Serve canonical sitemap.xml."""
    origin = _request_origin(request)
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url>\n'
        f'    <loc>{origin}/</loc>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>1.0</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{origin}/pricing</loc>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>0.9</priority>\n'
        '  </url>\n'
        '  <url>\n'
        f'    <loc>{origin}/docs</loc>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>0.8</priority>\n'
        '  </url>\n'
        '</urlset>'
    )
    return Response(content=content, media_type="application/xml")


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     width="656" height="656" viewBox="184 184 656 656">
  <defs>
    <linearGradient id="orange" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#ff6900"/>
      <stop offset="1" stop-color="#ff9b00"/>
    </linearGradient>

    <linearGradient id="rim" x1="0" y1="1" x2="0.8" y2="0">
      <stop offset="0" stop-color="#ff7910"/>
      <stop offset="1" stop-color="#ffab10"/>
    </linearGradient>
  </defs>

  <!-- Orange rounded tile -->
  <rect x="184" y="184" width="656" height="656" rx="164"
        fill="url(#rim)"/>
  <rect x="204" y="204" width="616" height="616" rx="145"
        fill="url(#orange)"/>

  <!-- Airplane -->
  <path fill="#fffefa" d="
    M 398 375
    L 420 370
    Q 434 366 445 376
    L 553 466
    L 611 450
    C 635 442 653 452 660 469
    C 667 489 653 507 633 514
    L 410 574
    Q 389 581 379 563
    L 337 490
    Q 331 479 343 474
    L 364 468
    Q 371 465 378 472
    L 410 497
    Q 418 504 430 500
    L 467 490
    L 393 392
    Q 383 380 398 375
    Z"/>

  <!-- Runway -->
  <rect x="360" y="625" width="292" height="31" rx="8"
        fill="#fffefa"/>
</svg>"""

@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
async def favicon():
    """Serve SVG favicon matching app/static/logo.svg."""
    svg_file = STATIC_DIR / "logo.svg"
    if svg_file.exists():
        return FileResponse(svg_file, media_type="image/svg+xml")
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def apple_touch_icon():
    """Serve Apple touch icon."""
    touch_file = STATIC_DIR / "favicon-180.png"
    if touch_file.exists():
        return FileResponse(touch_file, media_type="image/png")
    return FileResponse(STATIC_DIR / "logo.png", media_type="image/png")



@app.get("/logo.png", include_in_schema=False)
async def logo():
    """Serve PatchPilot brand logo."""
    logo_path = STATIC_DIR / "logo.png"
    if logo_path.exists():
        return FileResponse(logo_path, media_type="image/png")
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logo not found")


# -----------------------------------------------------------------------------
# GitHub OAuth & User Repositories
# -----------------------------------------------------------------------------

@app.get("/auth/login", tags=["Authentication"])
async def auth_login(request: Request):
    """Redirect user to GitHub OAuth authorization screen."""
    client_id = settings.GITHUB_CLIENT_ID.strip()
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GITHUB_CLIENT_ID is not configured in .env. OAuth login requires a valid GitHub OAuth App Client ID (e.g., Iv1... or Iv23...).",
        )
    oauth_state = secrets.token_urlsafe(32)
    url = get_oauth_authorize_url(client_id, state=oauth_state)
    response = RedirectResponse(url=url)
    response.set_cookie("patchpilot_oauth_state", oauth_state, httponly=True,
                        secure=request.url.scheme == "https", samesite="lax", max_age=600)
    return response


async def _upsert_user_account(
    db: AsyncSession,
    *,
    login: str,
    name: str | None = None,
    avatar_url: str | None = None,
    email: str | None = None,
    clerk_id: str | None = None,
    github_username: str | None = None,
    github_user_id: int | None = None,
    github_token: str | None = None,
    auth_provider: str = "github",
) -> None:
    """Persist or update authenticated user profile and AES-256-GCM encrypted GitHub token."""
    login_clean = (login or "").strip().lower()
    if not login_clean:
        return
    stmt = select(UserAccount).where(UserAccount.login == login_clean)
    result = await db.execute(stmt)
    user_rec = result.scalar_one_or_none()
    enc_token = encrypt_secret(github_token.strip()) if (github_token and github_token.strip()) else ""

    if user_rec:
        if name:
            user_rec.name = name
        if avatar_url:
            user_rec.avatar_url = avatar_url
        if email:
            user_rec.email = email
        if clerk_id:
            user_rec.clerk_id = clerk_id
        if github_username:
            user_rec.github_username = github_username
        if github_user_id is not None:
            user_rec.github_user_id = int(github_user_id)
        if enc_token:
            user_rec.encrypted_github_token = enc_token
        user_rec.auth_provider = auth_provider
    else:
        user_rec = UserAccount(
            login=login_clean,
            clerk_id=clerk_id or None,
            github_user_id=int(github_user_id) if github_user_id is not None else None,
            github_username=github_username or login_clean,
            email=email or None,
            name=name or login_clean,
            avatar_url=avatar_url or None,
            encrypted_github_token=enc_token,
            auth_provider=auth_provider,
        )
        db.add(user_rec)
    await db.commit()


@app.get("/auth/callback", tags=["Authentication"])
@app.get("/auth/github/callback", tags=["Authentication"])
async def auth_callback(request: Request, code: str, db: AsyncSession = Depends(get_db)):
    """Handle OAuth callback, persist user in PostgreSQL, sync workspaces, and issue session cookie."""
    client_id = settings.GITHUB_CLIENT_ID.strip()
    client_secret = settings.GITHUB_CLIENT_SECRET.strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth credentials (GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET) not fully configured in .env",
        )

    expected_state = request.cookies.get("patchpilot_oauth_state", "")
    supplied_state = request.query_params.get("state", "")
    if not expected_state or not supplied_state or not hmac.compare_digest(
        expected_state.encode(), supplied_state.encode()
    ):
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth state. Start sign-in again.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        user_token = await exchange_oauth_code(code, client_id, client_secret, client=client)
        profile = await fetch_user_profile(user_token, client=client)

    gh_user_id = int(profile["id"]) if profile.get("id") else None
    gh_login = profile.get("login") or ""
    await _upsert_user_account(
        db,
        login=gh_login,
        name=profile.get("name"),
        avatar_url=profile.get("avatar_url"),
        email=profile.get("email"),
        github_username=gh_login,
        github_user_id=gh_user_id,
        github_token=user_token,
        auth_provider="github",
    )

    canonical_id = f"github:{gh_user_id}" if gh_user_id else gh_login.lower()
    with suppress(Exception):
        await sync_user_workspaces(
            db,
            clerk_user_id=canonical_id,
            github_token=user_token,
            github_user_id=gh_user_id,
            github_login=gh_login,
        )

    session_payload = {
        "login": gh_login,
        "avatar_url": profile.get("avatar_url"),
        "name": profile.get("name"),
        "email": profile.get("email") or "",
        "github_username": gh_login,
        "github_user_id": gh_user_id,
        "auth_provider": "github",
        "user_token": user_token,
    }
    jwt_token = create_session_jwt(session_payload)

    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("patchpilot_oauth_state")
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
    """Return Clerk publishable key, dynamic portal URLs, and SaaS feature flags."""
    pk = settings.clerk_publishable_key
    custom_api = settings.clerk_frontend_api
    derived_api = custom_api or (derive_frontend_api(pk) if pk else None)
    return {
        "publishable_key": pk,
        "is_configured": bool(pk),
        "frontend_api": derived_api,
        "accounts_url": derive_accounts_url(settings),
        "install_url": settings.github_app_install_url,
        "billing_enabled": settings.BILLING_ENABLED,
        "github_oauth_configured": bool(settings.GITHUB_CLIENT_ID.strip()),
    }


@app.post("/auth/clerk/session", tags=["Authentication"])
async def clerk_session(request: Request, body: ClerkSessionRequest, db: AsyncSession = Depends(get_db)):
    """Verify Clerk session token, persist user, sync GitHub workspaces, and set session cookie."""
    payload = await verify_clerk_token(body.session_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Clerk session token",
        )

    sub = payload.get("sub", "")
    github_token = ""
    if settings.clerk_secret_key and sub:
        github_token = await fetch_clerk_oauth_token(sub, "oauth_github") or ""

    identity = await _verified_clerk_identity(payload)
    await _upsert_user_account(
        db,
        login=identity["login"],
        name=identity["name"],
        avatar_url=identity["avatar_url"],
        email=identity.get("email") or payload.get("email"),
        clerk_id=sub,
        github_username=identity.get("github_username") or None,
        github_user_id=identity.get("github_user_id"),
        github_token=github_token or None,
        auth_provider="clerk",
    )

    if sub and (github_token or identity.get("github_user_id")):
        with suppress(Exception):
            await sync_user_workspaces(
                db,
                clerk_user_id=sub,
                github_token=github_token or None,
                github_user_id=identity.get("github_user_id"),
                github_login=identity.get("github_username") or None,
            )

    jwt_token = create_session_jwt({
        "login": identity["login"],
        "avatar_url": identity["avatar_url"],
        "name": identity["name"],
        "email": identity.get("email") or payload.get("email") or "",
        "user_token": github_token,
        "clerk_id": sub,
        "github_username": identity.get("github_username") or "",
        "github_user_id": identity.get("github_user_id"),
        "auth_provider": "clerk",
    })

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
    identity = await _resolve_authenticated_identity(
        patchpilot_session=patchpilot_session,
        authorization=authorization,
        clerk_session=clerk_session,
    )
    if identity:
        return {
            "logged_in": True,
            "user": {
                "login": identity["login"],
                "avatar_url": identity["avatar_url"],
                "name": identity["name"],
                "email": identity.get("email") or "",
                "clerk_id": identity.get("raw_clerk_id") or identity.get("clerk_id") or "",
                "github_username": identity.get("github_username") or identity["login"],
                "github_user_id": identity.get("github_user_id"),
                "github_connected": bool(identity.get("github_connected")),
            },
            "provider": identity["provider"],
        }

    return {"logged_in": False, "user": None}


class GitHubTokenRequest(BaseModel):
    token: str


@app.post("/api/user/github-token", tags=["Repositories"])
async def set_user_github_token(
    body: GitHubTokenRequest,
    request: Request,
    patchpilot_session: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Store GitHub PAT only when ALLOW_MANUAL_PAT is enabled (disabled by default in production)."""
    if not (settings.ALLOW_MANUAL_PAT or is_test):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manual PAT linking is disabled. Connect GitHub via your account OAuth settings.",
        )

    session = verify_session_jwt(patchpilot_session)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")

    clean_token = body.token.strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="Token cannot be empty")

    async with httpx.AsyncClient(timeout=10.0) as client:
        profile = await fetch_user_profile(clean_token, client=client)

    gh_login = profile.get("login") or session.get("sub") or ""
    gh_user_id = int(profile["id"]) if profile.get("id") else None
    await _upsert_user_account(
        db,
        login=gh_login,
        name=profile.get("name") or session.get("name"),
        avatar_url=profile.get("avatar_url") or session.get("avatar_url"),
        clerk_id=session.get("clerk_id") or None,
        github_username=profile.get("login"),
        github_user_id=gh_user_id,
        github_token=clean_token,
        auth_provider="github",
    )

    jwt_token = create_session_jwt({
        "login": gh_login,
        "avatar_url": profile.get("avatar_url") or session.get("avatar_url"),
        "name": profile.get("name") or session.get("name"),
        "email": session.get("email", ""),
        "user_token": clean_token,
        "clerk_id": session.get("clerk_id", ""),
        "github_username": profile.get("login", ""),
        "github_user_id": gh_user_id,
        "auth_provider": "github",
    })

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
async def get_user_repositories(
    patchpilot_session: str | None = Cookie(None),
    authorization: str | None = Header(None, alias="Authorization"),
    clerk_session: str | None = Cookie(None, alias="__session"),
    db: AsyncSession = Depends(get_db),
):
    """Fetch repositories scoped to the authenticated user."""
    identity = await _resolve_authenticated_identity(
        patchpilot_session=patchpilot_session,
        authorization=authorization,
        clerk_session=clerk_session,
    )
    if not identity:
        raise HTTPException(status_code=401, detail="Authentication required to inspect repository scope")

    user_token = await _resolve_user_github_token(db, identity)
    if not user_token:
        return {
            "github_connected": False,
            "message": "GitHub account not connected. Connect GitHub via OAuth to inspect repositories.",
            "repos": [],
        }

    async with httpx.AsyncClient(timeout=30.0) as client:
        repos = await fetch_user_installations_and_repos(user_token, client=client)

    return {
        "github_connected": True,
        "repos": repos,
    }


# -----------------------------------------------------------------------------
# Multi-Tenant Workspace & Per-Active-Committer Billing Endpoints
# -----------------------------------------------------------------------------

async def _require_identity(
    patchpilot_session: str | None = Cookie(None),
    authorization: str | None = Header(None, alias="Authorization"),
    clerk_session: str | None = Cookie(None, alias="__session"),
) -> dict[str, Any]:
    identity = await _resolve_authenticated_identity(
        patchpilot_session=patchpilot_session,
        authorization=authorization,
        clerk_session=clerk_session,
    )
    if not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return identity


async def _serialize_workspace(db: AsyncSession, ws: Workspace, role: str) -> dict[str, Any]:
    period = current_billing_period(ws)
    active_count = await db.scalar(
        select(func.count()).select_from(WorkspaceActiveCommitter).where(
            WorkspaceActiveCommitter.workspace_id == ws.id,
            WorkspaceActiveCommitter.billing_period == period,
        )
    ) or 0
    return {
        "id": ws.id,
        "github_account_id": ws.github_account_id,
        "github_login": ws.github_login,
        "account_type": ws.account_type,
        "github_installation_id": ws.github_installation_id,
        "installation_status": ws.installation_status,
        "role": role,
        "is_admin": role.lower() in ADMIN_ROLES,
        "key_mode": ws.key_mode,
        "plan_tier": ws.plan_tier,
        "subscription_status": ws.subscription_status,
        "is_subscribed": is_workspace_subscribed(ws),
        "billing_enabled": settings.BILLING_ENABLED,
        "current_period_start": ws.current_period_start.isoformat() if ws.current_period_start else None,
        "current_period_end": ws.current_period_end.isoformat() if ws.current_period_end else None,
        "cancel_at_period_end": bool(ws.cancel_at_period_end),
        "seat_cap": ws.seat_cap,
        "billing_period": period,
        "active_committers_count": int(active_count),
    }


async def _require_workspace_member(
    db: AsyncSession,
    workspace_id: str,
    identity: dict[str, Any],
    *,
    require_admin: bool = False,
) -> tuple[Workspace, WorkspaceMember]:
    gh_token = await _resolve_user_github_token(db, identity)
    pair = await authorize_workspace_member(
        db,
        workspace_id=workspace_id,
        clerk_user_id=identity["clerk_id"],
        require_admin=require_admin,
        github_token=gh_token,
        github_user_id=identity.get("github_user_id"),
        github_login=identity.get("github_username") or identity.get("login"),
    )
    if pair is None:
        detail = (
            "Workspace owner or GitHub organization admin role required"
            if require_admin
            else "You do not have access to this workspace"
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return pair


@app.get("/api/workspaces", tags=["Workspaces"])
async def get_workspaces(
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """List personal and GitHub Organization workspaces accessible to the signed-in user."""
    pairs = await list_user_workspaces(db, identity["clerk_id"])
    if not pairs:
        gh_token = await _resolve_user_github_token(db, identity)
        if gh_token or identity.get("github_user_id"):
            pairs = await sync_user_workspaces(
                db,
                clerk_user_id=identity["clerk_id"],
                github_token=gh_token or None,
                github_user_id=identity.get("github_user_id"),
                github_login=identity.get("github_username") or identity.get("login"),
            )

    items = [await _serialize_workspace(db, ws, member.role) for ws, member in pairs]
    return {
        "workspaces": items,
        "billing_enabled": settings.BILLING_ENABLED,
        "install_url": settings.github_app_install_url,
    }


@app.post("/api/workspaces/sync", tags=["Workspaces"])
async def sync_workspaces_endpoint(
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Force refresh of GitHub App installations and Organization roles for the current user."""
    gh_token = await _resolve_user_github_token(db, identity)
    if not gh_token and not identity.get("github_user_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect your GitHub account via Clerk OAuth before syncing workspaces.",
        )

    pairs = await sync_user_workspaces(
        db,
        clerk_user_id=identity["clerk_id"],
        github_token=gh_token or None,
        github_user_id=identity.get("github_user_id"),
        github_login=identity.get("github_username") or identity.get("login"),
    )
    items = [await _serialize_workspace(db, ws, member.role) for ws, member in pairs]
    return {"status": "ok", "workspaces": items}


@app.get("/api/workspaces/{workspace_id}/repos", tags=["Workspaces"])
async def get_workspace_repos(
    workspace_id: str,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """List repositories scoped to this workspace's GitHub App installation."""
    ws, _ = await _require_workspace_member(db, workspace_id, identity, require_admin=False)
    if not ws.github_installation_id or ws.installation_status != "active":
        return {"installed": False, "repos": [], "install_url": settings.github_app_install_url}
    if not settings.GITHUB_PRIVATE_KEY.strip():
        return {"installed": True, "repos": [], "install_url": settings.github_app_install_url}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            inst_token = await get_installation_token(ws.github_installation_id, client=client)
            repos = await fetch_installation_repositories(inst_token, client=client)
        return {"installed": True, "repos": repos, "install_url": settings.github_app_install_url}
    except Exception as exc:
        logger.warning("Failed fetching installation repos for workspace %s: %s", ws.id, exc)
        return {"installed": True, "repos": [], "install_url": settings.github_app_install_url}


@app.get("/api/workspaces/{workspace_id}/vault", tags=["Workspaces"])
async def get_workspace_vault(
    workspace_id: str,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve workspace BYOK vault metadata and key mode (member read access)."""
    ws, member = await _require_workspace_member(db, workspace_id, identity, require_admin=False)
    cfg = await get_vault_for_workspace(db, ws)
    if not cfg:
        return {
            "workspace_id": ws.id,
            "owner": ws.github_login,
            "key_mode": ws.key_mode,
            "is_admin": member.role.lower() in ADMIN_ROLES,
            "provider": "gemini",
            "model": "gemini-2.5-pro",
            "base_url": "",
            "has_api_key": False,
            "masked_api_key": "",
            "review_policy": DEFAULT_POLICY,
            "fallbacks": [],
            "free_models": OFFICIAL_FREE_MODELS,
        }
    return {
        "workspace_id": ws.id,
        "owner": cfg.owner,
        "key_mode": ws.key_mode,
        "is_admin": member.role.lower() in ADMIN_ROLES,
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url or "",
        "has_api_key": bool(cfg.encrypted_api_key),
        "masked_api_key": _mask_api_key(cfg.encrypted_api_key),
        "review_policy": cfg.review_policy,
        "fallbacks": _serialize_fallbacks(cfg.fallbacks_json),
        "free_models": OFFICIAL_FREE_MODELS,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@app.put("/api/workspaces/{workspace_id}/vault", tags=["Workspaces"])
async def upsert_workspace_vault(
    workspace_id: str,
    body: VaultUpsertRequest,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Create or update BYOK vault credentials for a workspace (owner/admin only)."""
    ws, _ = await _require_workspace_member(db, workspace_id, identity, require_admin=True)
    cfg = await get_vault_for_workspace(db, ws)

    encrypted_key = ""
    if body.api_key and body.api_key.strip():
        encrypted_key = encrypt_secret(body.api_key.strip())
    elif cfg and cfg.encrypted_api_key:
        encrypted_key = cfg.encrypted_api_key

    if cfg:
        cfg.workspace_id = ws.id
        cfg.provider = body.provider.lower()
        cfg.model = body.model.strip()
        cfg.encrypted_api_key = encrypted_key
        cfg.base_url = body.base_url.strip() if body.base_url else None
        cfg.review_policy = body.review_policy.strip()
    else:
        cfg = VaultConfig(
            owner=ws.github_login.strip().lower(),
            workspace_id=ws.id,
            provider=body.provider.lower(),
            model=body.model.strip(),
            encrypted_api_key=encrypted_key,
            base_url=body.base_url.strip() if body.base_url else None,
            review_policy=body.review_policy.strip(),
            fallbacks_json="[]",
        )
        db.add(cfg)

    await db.commit()
    await db.refresh(cfg)

    return {
        "workspace_id": ws.id,
        "owner": cfg.owner,
        "key_mode": ws.key_mode,
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url or "",
        "has_api_key": bool(cfg.encrypted_api_key),
        "masked_api_key": _mask_api_key(cfg.encrypted_api_key),
        "review_policy": cfg.review_policy,
        "fallbacks": _serialize_fallbacks(cfg.fallbacks_json),
        "free_models": OFFICIAL_FREE_MODELS,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@app.post("/api/workspaces/{workspace_id}/vault/fallbacks", tags=["Workspaces"])
async def add_workspace_fallback(
    workspace_id: str,
    body: FallbackUpsertRequest,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Add or update a fallback LLM key for a workspace (owner/admin only)."""
    ws, _ = await _require_workspace_member(db, workspace_id, identity, require_admin=True)
    return await add_or_update_fallback(ws.github_login, body, None, db)


@app.delete("/api/workspaces/{workspace_id}/vault/fallbacks/{fallback_id}", tags=["Workspaces"])
async def delete_workspace_fallback(
    workspace_id: str,
    fallback_id: str,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Delete a fallback LLM key for a workspace (owner/admin only)."""
    ws, _ = await _require_workspace_member(db, workspace_id, identity, require_admin=True)
    return await delete_fallback(ws.github_login, fallback_id, None, db)


@app.post("/api/workspaces/{workspace_id}/vault/fallbacks/{fallback_id}/set-primary", tags=["Workspaces"])
async def promote_workspace_fallback(
    workspace_id: str,
    fallback_id: str,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Promote a fallback key to primary for a workspace (owner/admin only)."""
    ws, _ = await _require_workspace_member(db, workspace_id, identity, require_admin=True)
    return await promote_fallback_to_primary(ws.github_login, fallback_id, None, db)


@app.post("/api/workspaces/{workspace_id}/vault/test", tags=["Workspaces"])
async def test_workspace_vault(
    workspace_id: str,
    fallback_id: str | None = None,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Test primary or fallback LLM connectivity for a workspace (owner/admin only)."""
    ws, _ = await _require_workspace_member(db, workspace_id, identity, require_admin=True)
    return await test_vault_endpoint(ws.github_login, fallback_id, None, db)


@app.get("/api/workspaces/{workspace_id}/billing", tags=["Billing"])
async def get_workspace_billing(
    workspace_id: str,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return workspace subscription state, active committers for current cycle, and recent review usage."""
    ws, member = await _require_workspace_member(db, workspace_id, identity, require_admin=False)
    period = current_billing_period(ws)

    seats_res = await db.execute(
        select(WorkspaceActiveCommitter)
        .where(
            WorkspaceActiveCommitter.workspace_id == ws.id,
            WorkspaceActiveCommitter.billing_period == period,
        )
        .order_by(WorkspaceActiveCommitter.last_seen_at.desc())
    )
    seats = list(seats_res.scalars().all())

    usage_res = await db.execute(
        select(ReviewUsageEvent)
        .where(ReviewUsageEvent.workspace_id == ws.id)
        .order_by(ReviewUsageEvent.created_at.desc())
        .limit(20)
    )
    recent_usage = list(usage_res.scalars().all())

    return {
        "workspace": await _serialize_workspace(db, ws, member.role),
        "billing_period": period,
        "managed_monthly_review_cap_per_seat": settings.MANAGED_MONTHLY_REVIEW_CAP_PER_SEAT,
        "active_committers": [
            {
                "github_author_id": s.github_author_id,
                "github_author_login": s.github_author_login,
                "review_count": s.review_count,
                "managed_review_count": s.managed_review_count,
                "meter_reported": s.meter_reported_at is not None,
                "first_seen_at": s.first_seen_at.isoformat() if s.first_seen_at else None,
                "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            }
            for s in seats
        ],
        "recent_reviews": [
            {
                "id": u.id,
                "repo_full_name": u.repo_full_name,
                "is_private": u.is_private,
                "pr_number": u.pr_number,
                "github_author_login": u.github_author_login,
                "trigger_type": u.trigger_type,
                "key_mode": u.key_mode,
                "provider": u.provider,
                "model": u.model,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in recent_usage
        ],
    }


@app.patch("/api/workspaces/{workspace_id}/billing/settings", tags=["Billing"])
async def update_workspace_billing_settings(
    workspace_id: str,
    body: WorkspaceBillingSettingsRequest,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Update workspace key mode ('managed' | 'byok') and optional monthly active-committer cap (admin only)."""
    ws, member = await _require_workspace_member(db, workspace_id, identity, require_admin=True)

    if body.key_mode is not None:
        mode_clean = body.key_mode.strip().lower()
        if mode_clean not in {"managed", "byok"}:
            raise HTTPException(status_code=400, detail="key_mode must be 'managed' or 'byok'")
        if mode_clean == "managed" and settings.BILLING_ENABLED and not is_workspace_subscribed(ws):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="An active PatchPilot Pro subscription is required for Managed AI mode.",
            )
        ws.key_mode = mode_clean

    if body.clear_seat_cap:
        ws.seat_cap = None
    elif body.seat_cap is not None:
        if body.seat_cap < 1:
            raise HTTPException(status_code=400, detail="seat_cap must be at least 1")
        ws.seat_cap = int(body.seat_cap)

    ws.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(ws)
    return {"status": "ok", "workspace": await _serialize_workspace(db, ws, member.role)}


@app.post("/api/workspaces/{workspace_id}/billing/checkout", tags=["Billing"])
async def create_workspace_checkout(
    workspace_id: str,
    request: Request,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Create a hosted Polar.sh checkout session for PatchPilot Pro (workspace owner/admin only)."""
    ws, _ = await _require_workspace_member(db, workspace_id, identity, require_admin=True)
    origin = _request_origin(request)
    success_url = f"{origin}/dashboard?workspace={ws.id}&tab=billing&checkout=success"
    forwarded_for = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    customer_ip = forwarded_for or (request.client.host if request.client else None)

    try:
        billing_client = PolarBillingClient()
        checkout_url = await billing_client.create_checkout_url(
            workspace=ws,
            customer_email=ws.billing_email or identity.get("email") or None,
            success_url=success_url,
            customer_ip=customer_ip,
        )
        return {"checkout_url": checkout_url}
    except BillingProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/billing/portal", tags=["Billing"])
async def create_workspace_portal(
    workspace_id: str,
    identity: dict[str, Any] = Depends(_require_identity),
    db: AsyncSession = Depends(get_db),
):
    """Create a hosted Polar.sh customer portal session for managing payment methods and invoices (admin only)."""
    ws, _ = await _require_workspace_member(db, workspace_id, identity, require_admin=True)
    if not ws.polar_customer_id and not is_workspace_subscribed(ws):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No billing profile found for this workspace yet. Subscribe to Pro first.",
        )

    try:
        billing_client = PolarBillingClient()
        portal_url = await billing_client.create_portal_url(workspace=ws)
        return {"portal_url": portal_url}
    except BillingProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# -----------------------------------------------------------------------------
# BYOK Vault Endpoints
# -----------------------------------------------------------------------------

def mask_api_key(key: str | None) -> str:
    """Mask key preserving prefix/suffix, with length guard for short dummy keys."""
    if not key or not key.strip():
        return "Not configured"
    k = key.strip()
    if len(k) <= 10:
        return f"{k[:2]}••••••••"
    return f"{k[:6]}••••••••{k[-4:]}"


def _mask_api_key(encrypted_key: str | None) -> str:
    """Safely decrypt and produce a masked string for UI display."""
    if not encrypted_key:
        return ""
    try:
        raw = decrypt_secret(encrypted_key)
        if not raw:
            return ""
        return mask_api_key(raw)
    except Exception:
        return "••••••••"


def _serialize_fallbacks(fallbacks_json: str | None) -> list[dict[str, Any]]:
    """Parse and sanitize fallbacks JSON for client response without raw secret leakage."""
    if not fallbacks_json:
        return []
    try:
        raw = json.loads(fallbacks_json)
        if isinstance(raw, list):
            res = []
            for item in raw:
                if isinstance(item, dict):
                    res.append({
                        "id": str(item.get("id", "")),
                        "name": str(item.get("name", "Fallback")),
                        "provider": str(item.get("provider", "gemini")),
                        "model": str(item.get("model", "")),
                        "base_url": item.get("base_url") or "",
                        "has_api_key": bool(item.get("encrypted_api_key")),
                        "masked_api_key": _mask_api_key(item.get("encrypted_api_key")),
                        "is_enabled": bool(item.get("is_enabled", True)),
                    })
            return res
    except Exception as exc:
        logger.warning("Error parsing fallbacks_json: %s", exc)
    return []


@app.get("/vault/{owner}", tags=["Vault"])
async def get_vault(
    owner: str,
    _: None = Depends(verify_admin_or_session),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve owner vault metadata, configured fallbacks, and official free models catalog."""
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
            "masked_api_key": "",
            "review_policy": DEFAULT_POLICY,
            "fallbacks": [],
            "free_models": OFFICIAL_FREE_MODELS,
        }

    return {
        "owner": cfg.owner,
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url or "",
        "has_api_key": bool(cfg.encrypted_api_key),
        "masked_api_key": _mask_api_key(cfg.encrypted_api_key),
        "review_policy": cfg.review_policy,
        "fallbacks": _serialize_fallbacks(cfg.fallbacks_json),
        "free_models": OFFICIAL_FREE_MODELS,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@app.put("/vault/{owner}", tags=["Vault"])
async def upsert_vault(
    owner: str,
    body: VaultUpsertRequest,
    _: None = Depends(verify_admin_or_session),
    db: AsyncSession = Depends(get_db),
):
    """Upsert BYOK primary vault configuration for an owner."""
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
            fallbacks_json="[]",
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
        "masked_api_key": _mask_api_key(cfg.encrypted_api_key),
        "review_policy": cfg.review_policy,
        "fallbacks": _serialize_fallbacks(cfg.fallbacks_json),
        "free_models": OFFICIAL_FREE_MODELS,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@app.post("/vault/{owner}/fallbacks", tags=["Vault"])
async def add_or_update_fallback(
    owner: str,
    body: FallbackUpsertRequest,
    _: None = Depends(verify_admin_or_session),
    db: AsyncSession = Depends(get_db),
):
    """Add or update a secondary fallback LLM credential."""
    owner_clean = owner.strip().lower()
    stmt = select(VaultConfig).where(VaultConfig.owner == owner_clean)
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail=f"No vault configuration found for owner '{owner_clean}'. Save primary vault first.")

    raw_fbs: list[dict[str, Any]] = []
    if cfg.fallbacks_json:
        try:
            parsed = json.loads(cfg.fallbacks_json)
            if isinstance(parsed, list):
                raw_fbs = parsed
        except Exception:
            raw_fbs = []

    target_id = body.id
    existing_idx = -1
    if target_id:
        for i, fb in enumerate(raw_fbs):
            if str(fb.get("id")) == str(target_id):
                existing_idx = i
                break

    if existing_idx >= 0:
        fb_item = raw_fbs[existing_idx]
        fb_item["name"] = body.name.strip()
        fb_item["provider"] = body.provider.lower().strip()
        fb_item["model"] = body.model.strip()
        fb_item["base_url"] = body.base_url.strip() if body.base_url else None
        fb_item["is_enabled"] = body.is_enabled
        if body.api_key and body.api_key.strip():
            fb_item["encrypted_api_key"] = encrypt_secret(body.api_key.strip())
    else:
        new_id = uuid.uuid4().hex[:8]
        enc_key = encrypt_secret(body.api_key.strip()) if (body.api_key and body.api_key.strip()) else ""
        fb_item = {
            "id": new_id,
            "name": body.name.strip() or f"Fallback {len(raw_fbs) + 1}",
            "provider": body.provider.lower().strip(),
            "model": body.model.strip(),
            "encrypted_api_key": enc_key,
            "base_url": body.base_url.strip() if body.base_url else None,
            "is_enabled": body.is_enabled,
        }
        raw_fbs.append(fb_item)

    cfg.fallbacks_json = json.dumps(raw_fbs)
    await db.commit()
    await db.refresh(cfg)

    return {
        "status": "ok",
        "fallbacks": _serialize_fallbacks(cfg.fallbacks_json),
    }


@app.delete("/vault/{owner}/fallbacks/{fallback_id}", tags=["Vault"])
async def delete_fallback(
    owner: str,
    fallback_id: str,
    _: None = Depends(verify_admin_or_session),
    db: AsyncSession = Depends(get_db),
):
    """Remove a fallback credential from vault."""
    owner_clean = owner.strip().lower()
    stmt = select(VaultConfig).where(VaultConfig.owner == owner_clean)
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Vault not found")

    raw_fbs = []
    if cfg.fallbacks_json:
        try:
            parsed = json.loads(cfg.fallbacks_json)
            if isinstance(parsed, list):
                raw_fbs = [fb for fb in parsed if str(fb.get("id")) != str(fallback_id)]
        except Exception:
            raw_fbs = []

    cfg.fallbacks_json = json.dumps(raw_fbs)
    await db.commit()
    await db.refresh(cfg)

    return {
        "status": "ok",
        "deleted": True,
        "fallbacks": _serialize_fallbacks(cfg.fallbacks_json),
    }


@app.post("/vault/{owner}/fallbacks/{fallback_id}/set-primary", tags=["Vault"])
@app.post("/vault/{owner}/fallback/{fallback_id}/set-primary", tags=["Vault"])
async def promote_fallback_to_primary(
    owner: str,
    fallback_id: str,
    _: None = Depends(verify_admin_or_session),
    db: AsyncSession = Depends(get_db),
):
    """Promote selected fallback to primary and demote current primary into fallbacks list with auto-generated UUID and descriptive label."""
    owner_clean = owner.strip().lower()
    stmt = select(VaultConfig).where(VaultConfig.owner == owner_clean)
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Vault not found")

    raw_fbs: list[dict[str, Any]] = []
    if cfg.fallbacks_json:
        try:
            parsed = json.loads(cfg.fallbacks_json)
            if isinstance(parsed, list):
                raw_fbs = parsed
        except Exception:
            raw_fbs = []

    target_idx = -1
    for i, fb in enumerate(raw_fbs):
        if str(fb.get("id")) == str(fallback_id):
            target_idx = i
            break

    if target_idx < 0:
        raise HTTPException(status_code=404, detail=f"Fallback '{fallback_id}' not found")

    target_fb = raw_fbs.pop(target_idx)

    # Demote old primary into a fallback entry with generated uuid4 and label
    old_provider_title = cfg.provider.capitalize()
    demoted_primary = {
        "id": uuid.uuid4().hex[:8],
        "name": f"Previous Primary ({old_provider_title})",
        "provider": cfg.provider,
        "model": cfg.model,
        "encrypted_api_key": cfg.encrypted_api_key,
        "base_url": cfg.base_url,
        "is_enabled": True,
    }

    # Promote target fallback to primary
    cfg.provider = target_fb["provider"]
    cfg.model = target_fb["model"]
    cfg.encrypted_api_key = target_fb.get("encrypted_api_key", "")
    cfg.base_url = target_fb.get("base_url")

    # Insert demoted primary at the top of fallbacks
    raw_fbs.insert(0, demoted_primary)
    cfg.fallbacks_json = json.dumps(raw_fbs)

    await db.commit()
    await db.refresh(cfg)

    return {
        "status": "ok",
        "provider": cfg.provider,
        "model": cfg.model,
        "masked_api_key": _mask_api_key(cfg.encrypted_api_key),
        "fallbacks": _serialize_fallbacks(cfg.fallbacks_json),
    }


@app.post("/vault/{owner}/test", tags=["Vault"])
async def test_vault_endpoint(
    owner: str,
    fallback_id: str | None = None,
    _: None = Depends(verify_admin_or_session),
    db: AsyncSession = Depends(get_db),
):
    """Test live LLM connection for primary or specified fallback credential."""
    owner_clean = owner.strip().lower()
    stmt = select(VaultConfig).where(VaultConfig.owner == owner_clean)
    result = await db.execute(stmt)
    cfg = result.scalar_one_or_none()

    if not cfg:
        raise HTTPException(status_code=404, detail=f"No vault configuration found for owner '{owner_clean}'. Save first.")

    candidates = get_provider_candidates(cfg)
    target_cand = None
    if fallback_id and fallback_id != "primary":
        for c in candidates:
            if str(c.get("id")) == str(fallback_id):
                target_cand = c
                break
        if not target_cand:
            raise HTTPException(status_code=404, detail=f"Fallback ID '{fallback_id}' not found")
    else:
        target_cand = candidates[0] if candidates else None

    try:
        reply = await test_llm_connection(cfg, candidate=target_cand)
        cand_name = target_cand["name"] if target_cand else "Primary"
        cand_provider = target_cand["provider"] if target_cand else cfg.provider
        cand_model = target_cand["model"] if target_cand else cfg.model
        return {
            "status": "ok",
            "candidate": cand_name,
            "provider": cand_provider,
            "model": cand_model,
            "reply": reply,
        }
    except Exception as exc:
        logger.error("Connection test error for %s (%s): %s", owner_clean, fallback_id or "primary", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# -----------------------------------------------------------------------------
# Background Handlers
# -----------------------------------------------------------------------------

def _vault_has_usable_key(cfg: VaultConfig | None) -> bool:
    if not cfg:
        return False
    if bool((cfg.encrypted_api_key or "").strip()) or str(cfg.provider or "").lower() == "ollama":
        return True
    if getattr(cfg, "fallbacks_json", None):
        try:
            raw = json.loads(cfg.fallbacks_json)
            if isinstance(raw, list):
                for fb in raw:
                    if isinstance(fb, dict) and fb.get("is_enabled", True):
                        if bool((fb.get("encrypted_api_key") or "").strip()) or str(fb.get("provider", "")).lower() == "ollama":
                            return True
        except Exception:
            pass
    return False


async def _get_owner_config(owner: str, workspace: Workspace | None = None) -> VaultConfig | None:
    """Resolve BYOK VaultConfig strictly scoped to the workspace or owner (zero cross-tenant fallback)."""
    owner_clean = owner.strip().lower()
    async with SessionLocal() as session:
        if workspace is not None:
            res_ws = await session.execute(
                select(VaultConfig).where(VaultConfig.workspace_id == workspace.id)
            )
            ws_cfg = res_ws.scalar_one_or_none()
            if _vault_has_usable_key(ws_cfg):
                return ws_cfg

        result = await session.execute(select(VaultConfig).where(VaultConfig.owner == owner_clean))
        cfg = result.scalar_one_or_none()
        if _vault_has_usable_key(cfg):
            return cfg

        # Resolve only explicit account links in SQL, never other tenants' keys.
        linked = await session.execute(
            select(VaultConfig)
            .join(UserAccount, VaultConfig.owner == UserAccount.login)
            .where(func.lower(UserAccount.github_username) == owner_clean)
            .order_by(VaultConfig.owner)
        )
        for row in linked.scalars():
            if _vault_has_usable_key(row):
                return row

        return cfg


async def _post_entitlement_blocked_notice(
    *,
    installation_id: int | str,
    repo_full_name: str,
    pr_number: int,
    head_sha: str | None,
    command: str,
    message: str,
    review_comment_id: int | str | None = None,
) -> None:
    """Post a clean, emoji-free billing/setup action notice on GitHub when entitlement blocks execution."""
    if not settings.GITHUB_PRIVATE_KEY.strip():
        return
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            token = await get_installation_token(installation_id, client=client)
            if head_sha:
                check_handle = await start_check_indicator(
                    token, repo_full_name, head_sha, command=command, client=client
                )
                await complete_check_indicator(
                    token,
                    repo_full_name,
                    check_handle,
                    verdict="WARN",
                    summary_text=message[:120],
                    client=client,
                )
            await post_comment_reply(
                token=token,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                body=message,
                review_comment_id=review_comment_id,
                client=client,
            )
    except Exception as exc:
        logger.debug("Could not post entitlement notice on %s#%s: %s", repo_full_name, pr_number, exc)


async def handle_pr_event(payload: dict[str, Any]) -> None:
    """Background task to fetch PR diff, enforce entitlements, scan secrets, triage risk, run review, and meter seats."""
    pending_review = check_handle = None
    token = repo_full_name = ""
    pr_number = 0
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
        additions = pr.get("additions", 0)
        deletions = pr.get("deletions", 0)

        if not installation_id or not repo_full_name or not pr_number:
            return

        # 0. Wallet Shield Anti-Abuse & Rate-Limiting Protection
        is_fork = bool(pr.get("head", {}).get("repo", {}).get("fork", False) or repository.get("fork", False))
        is_private = bool(repository.get("private", False))
        author_association = pr.get("author_association", "NONE")

        authorized, auth_reason = check_pr_authorization(
            author_association=author_association,
            is_fork=is_fork,
            is_private=is_private,
        )
        if not authorized:
            logger.info("Wallet Shield blocked automatic review for %s#%s: %s", repo_full_name, pr_number, auth_reason)
            return

        allowed, rate_msg = global_rate_limiter.check_and_increment(repo_full_name, max_requests=25, window_seconds=3600)
        if not allowed and current_job.get() is None:
            logger.warning("Rate limit reached for %s: %s", repo_full_name, rate_msg)
            return

        pr_user = pr.get("user") or {}
        pr_author = pr_user.get("login") or owner
        pr_author_id = int(pr_user["id"]) if pr_user.get("id") is not None else None
        pr_author_type = pr_user.get("type")

        async with SessionLocal() as ent_db:
            workspace = await resolve_workspace_for_webhook(ent_db, payload)
            byok_cfg = await _get_owner_config(owner, workspace=workspace)
            decision = await evaluate_review_entitlement(
                ent_db,
                workspace,
                is_private=is_private,
                github_author_id=pr_author_id,
                github_author_login=pr_author,
                author_type=pr_author_type,
                has_byok_key=_vault_has_usable_key(byok_cfg),
            )

        if not decision.allowed:
            logger.info(
                "Entitlement blocked PR review for %s#%s: %s",
                repo_full_name,
                pr_number,
                decision.reason_code,
            )
            if decision.user_message:
                await _post_entitlement_blocked_notice(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    head_sha=head_sha,
                    command="review",
                    message=decision.user_message,
                )
            return

        if decision.effective_key_mode == "managed":
            custom_policy = byok_cfg.review_policy if byok_cfg else DEFAULT_POLICY
            cfg = build_managed_vault_config(owner, review_policy=custom_policy)
        else:
            cfg = byok_cfg

        if not cfg:
            logger.warning("No vault configuration found for %s. Skipping review.", owner)
            return

        async with httpx.AsyncClient(timeout=60.0) as client:
            token = await get_installation_token(installation_id, client=client)

            latest = await fetch_pr_metadata(token, repo_full_name, pr_number, client=client)
            if latest.get("head", {}).get("sha") != head_sha or latest.get("state") == "closed":
                job = current_job.get()
                if job:
                    handles = json.loads(job.handles)
                    await complete_check_indicator(token, repo_full_name, handles.get("check"), verdict="ERROR", summary_text="Superseded by a newer commit or closed PR.", client=client)
                    await update_pr_review_card(token, repo_full_name, pr_number, handles.get("review"), "Review superseded by a newer commit or closed PR.", client=client)
                return

            # Immediately register PatchPilot in Reviewers sidebar and post live loading card
            loading_card = build_loading_review_card(
                requested_by=pr_author,
                commit_sha=head_sha,
                command="review",
                target_ref=f"{repo_full_name}#{pr_number}",
            )
            pending_review = await remembered("review", create_pending_pr_review,
                token,
                repo_full_name,
                pr_number,
                head_sha,
                loading_card,
                client=client,
            )
            check_handle = await remembered("check", start_check_indicator,
                token,
                repo_full_name,
                head_sha,
                command="review",
                client=client,
            )

            raw_diff = await fetch_pr_diff(token, repo_full_name, pr_number, client=client)
            annotated_diff, valid_lines = annotate_diff(raw_diff)

            if not annotated_diff.strip():
                empty_msg = strip_emojis(
                    f"## PatchPilot AI PR Review\n\n"
                    f"**Requested by:** @{pr_author} &nbsp;|&nbsp; **Status:** `Skipped (No reviewable code changes)`\n\n"
                    f"Only lockfiles, binary assets, or empty diffs were detected in `{repo_full_name}#{pr_number}`."
                )
                await update_pr_review_card(
                    token,
                    repo_full_name,
                    pr_number,
                    pending_review,
                    empty_msg,
                    client=client,
                )
                await complete_check_indicator(
                    token,
                    repo_full_name,
                    check_handle,
                    verdict="PASS",
                    summary_text="Skipped: no reviewable source changes.",
                    client=client,
                )
                return

            # 1. Pre-LLM Secret Detection (PatchPilot Secure)
            secret_findings = scan_diff_for_secrets(raw_diff)
            secret_count = len(secret_findings)

            # 2. PR Complexity & Risk Assessment (PatchPilot Triage)
            files_list = list(valid_lines.keys())
            triage = compute_pr_triage(
                files_changed=files_list,
                additions=additions,
                deletions=deletions,
                has_secret_findings=secret_count > 0,
            )

            # 3. Apply GitHub Triage & Security Labels asynchronously
            await apply_pr_labels(token, repo_full_name, pr_number, triage.labels, client=client)

            # Update loading card stages before 2-pass LLM analysis
            mid_loading_card = build_loading_review_card(
                requested_by=pr_author,
                commit_sha=head_sha,
                command="review",
                target_ref=f"{repo_full_name}#{pr_number}",
                stage_statuses={
                    "diff": f"COMPLETED ({len(files_list)} files)",
                    "security": "COMPLETED (Clean)" if secret_count == 0 else f"ALERT ({secret_count} found)",
                    "triage": f"COMPLETED ({triage.size_tier}, {triage.risk_level})",
                    "pass1": "RUNNING...",
                    "pass2": "RUNNING...",
                },
            )
            await update_pr_review_card(
                token,
                repo_full_name,
                pr_number,
                pending_review,
                mid_loading_card,
                client=client,
            )

            # Redaction is enforced by run_pr_review for every outbound prompt.
            review_data = await run_pr_review(
                cfg=cfg,
                pr_title=pr_title,
                pr_body=pr_body,
                annotated_diff=annotated_diff,
                client=client,
                valid_lines=valid_lines,
                triage=triage,
            )


            # If secrets were detected, inject critical inline alerts and block verdict
            if secret_findings:
                if "findings" not in review_data or not isinstance(review_data["findings"], list):
                    review_data["findings"] = []
                for sf in secret_findings:
                    remediation_msg = (
                        f"**CRITICAL SECURITY ALERT**: Potential exposed credential `{sf.matched_text_masked}` detected.\n\n"
                        f"**Remediation**:\n"
                        f"1. Immediately revoke and rotate this secret in your provider console.\n"
                        f"2. Remove the secret from code and scrub git history using `git-filter-repo`.\n"
                        f"3. Store credentials using environment variables or a secrets manager."
                    )
                    review_data["findings"].insert(0, {
                        "file": sf.file_path,
                        "line": sf.line_number,
                        "severity": "bug",
                        "title": f"Secret Leak Detected: {sf.secret_type}",
                                "source": "scanner",
                                "confidence": sf.severity,
                        "explanation": remediation_msg,
                        "comment": remediation_msg,
                    })
                review_data["verdict"] = "BLOCK"

            # 6. Publish native GitHub review in-place & complete check indicator
            await publish_pr_review(
                token,
                repo_full_name,
                pr_number,
                head_sha,
                review_data,
                valid_lines,
                client=client,
                triage_summary=triage.summary_text,
                secret_alerts_count=secret_count,
                requested_by=pr_author,
                existing_review=pending_review,
            )
            await complete_check_indicator(
                token,
                repo_full_name,
                check_handle,
                verdict=str(review_data.get("verdict", "PASS")),
                summary_text=str(review_data.get("summary", "PatchPilot verification completed.")),
                client=client,
            )
            if workspace is not None:
                job_obj = current_job.get()
                usage_meta = review_data.get("_usage") or {}
                try:
                    async with SessionLocal() as usage_db:
                        ws_live = await usage_db.get(Workspace, workspace.id)
                        if ws_live is not None:
                            await record_completed_review(
                                usage_db,
                                workspace=ws_live,
                                event_id=job_obj.id if job_obj else uuid.uuid4().hex,
                                repo_full_name=repo_full_name,
                                is_private=is_private,
                                pr_number=int(pr_number),
                                github_author_id=pr_author_id,
                                github_author_login=pr_author,
                                author_type=pr_author_type,
                                trigger_type="pr_review",
                                key_mode=decision.effective_key_mode,
                                provider=str(review_data.get("_provider") or cfg.provider),
                                model=str(review_data.get("_model") or cfg.model),
                                prompt_tokens=usage_meta.get("prompt_tokens"),
                                completion_tokens=usage_meta.get("completion_tokens"),
                            )
                    await flush_unreported_seat_events(limit=5)
                except Exception as meter_exc:
                    logger.warning("Usage/meter recording warning for %s#%s: %s", repo_full_name, pr_number, meter_exc)
            logger.info("Published review and triage for %s#%s (size: %s, risk: %s)", repo_full_name, pr_number, triage.size_tier, triage.risk_level)
    except Exception as exc:
        logger.exception("Failed processing PR review webhook: %s", exc)
        if current_job.get() is not None:
            raise
        if token and repo_full_name:
            await complete_check_indicator(token, repo_full_name, check_handle, verdict="ERROR", summary_text="Review failed; queued jobs retry automatically.")
            if pending_review:
                await update_pr_review_card(token, repo_full_name, pr_number, pending_review, "PatchPilot could not complete this review. The job will retry within its retry budget.")
        if current_job.get() is not None:
            raise


async def handle_comment_event(event_type: str, payload: dict[str, Any]) -> None:
    """Background task to run full verified PR review or answer interactive @patchpilot commands."""
    pending_review = check_handle = None
    token = repo_full_name = ""
    pr_number = 0
    try:
        comment = payload.get("comment", {})
        comment_body = comment.get("body", "")
        match = COMMAND_RE.search(comment_body)
        if not match:
            return

        command = match.group(1).lower().strip()
        extra_prompt = match.group(2).strip() or comment_body
        installation_id = payload.get("installation", {}).get("id")
        repository = payload.get("repository", {})
        repo_full_name = repository.get("full_name", "")
        owner = repository.get("owner", {}).get("login", "")
        comment_user = comment.get("user", {}).get("login") or payload.get("sender", {}).get("login") or owner

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

        sender_type = str(comment.get("user", {}).get("type") or payload.get("sender", {}).get("type", "")).lower()
        if sender_type == "bot":
            return
        authorized, auth_reason = check_pr_authorization(
            author_association=comment.get("author_association"),
            is_fork=False,
            is_private=bool(repository.get("private", False)),
        )
        if not authorized:
            logger.info("Wallet Shield blocked comment for %s#%s: %s", repo_full_name, pr_number, auth_reason)
            return
        allowed, rate_msg = global_rate_limiter.check_and_increment(repo_full_name, max_requests=25, window_seconds=3600)
        if not allowed and current_job.get() is None:
            logger.warning("Rate limit reached for %s: %s", repo_full_name, rate_msg)
            return

        comment_user_obj = comment.get("user") or payload.get("sender") or {}
        comment_user_id = int(comment_user_obj["id"]) if comment_user_obj.get("id") is not None else None
        is_private = bool(repository.get("private", False))

        async with SessionLocal() as ent_db:
            workspace = await resolve_workspace_for_webhook(ent_db, payload)
            byok_cfg = await _get_owner_config(owner, workspace=workspace)
            decision = await evaluate_review_entitlement(
                ent_db,
                workspace,
                is_private=is_private,
                github_author_id=comment_user_id,
                github_author_login=comment_user,
                author_type=sender_type,
                has_byok_key=_vault_has_usable_key(byok_cfg),
            )

        if not decision.allowed:
            logger.info(
                "Entitlement blocked interactive command '%s' on %s#%s: %s",
                command,
                repo_full_name,
                pr_number,
                decision.reason_code,
            )
            if decision.user_message:
                await _post_entitlement_blocked_notice(
                    installation_id=installation_id,
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    head_sha=None,
                    command=command,
                    message=decision.user_message,
                    review_comment_id=review_comment_id,
                )
            return

        if decision.effective_key_mode == "managed":
            custom_policy = byok_cfg.review_policy if byok_cfg else DEFAULT_POLICY
            cfg = build_managed_vault_config(owner, review_policy=custom_policy)
        else:
            cfg = byok_cfg

        if not cfg:
            cfg = VaultConfig(
                owner=owner.strip().lower(),
                provider="gemini",
                model="gemini-2.5-flash",
                encrypted_api_key="",
                review_policy=DEFAULT_POLICY,
            )

        async with httpx.AsyncClient(timeout=90.0) as client:
            token = await get_installation_token(installation_id, client=client)

            # 1. Immediately add 'eyes' reaction to the triggering comment
            comment_id = comment.get("id")
            if comment_id:
                await add_comment_reaction(
                    token=token,
                    repo_full_name=repo_full_name,
                    comment_id=int(comment_id),
                    reaction="eyes",
                    is_review_comment=(event_type == "pull_request_review_comment"),
                    client=client,
                )

            # 2. For top-level PR comments, fetch PR metadata and immediately register PatchPilot as Reviewer with a live loading card
            pr_meta: dict[str, Any] = {}
            head_sha = ""
            pending_review: dict[str, Any] | None = None
            check_handle: dict[str, Any] | None = None

            if event_type == "issue_comment" and not diff_hunk:
                try:
                    pr_meta = await fetch_pr_metadata(token, repo_full_name, pr_number, client=client)
                    head_sha = pr_meta.get("head", {}).get("sha", "")
                except Exception as meta_exc:
                    logger.warning("Could not fetch PR metadata for %s#%s: %s", repo_full_name, pr_number, meta_exc)

                loading_card = build_loading_review_card(
                    requested_by=comment_user,
                    commit_sha=head_sha,
                    command=command,
                    target_ref=f"{repo_full_name}#{pr_number}",
                )
                pending_review = await remembered("review", create_pending_pr_review,
                    token=token,
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    head_sha=head_sha,
                    body=loading_card,
                    client=client,
                )
                check_handle = await remembered("check", start_check_indicator,
                    token=token,
                    repo_full_name=repo_full_name,
                    head_sha=head_sha,
                    command=command,
                    client=client,
                )

            pr_diff = "" if diff_hunk else await fetch_pr_diff(token, repo_full_name, pr_number, client=client)

            try:
                # Full structured PatchPilot PR review when triggered via top-level 'review' command
                if event_type == "issue_comment" and command == "review" and not diff_hunk:
                    pr_title = pr_meta.get("title") or payload.get("issue", {}).get("title", f"Pull Request #{pr_number}")
                    pr_body = pr_meta.get("body") or payload.get("issue", {}).get("body") or ""
                    additions = pr_meta.get("additions") or sum(
                        1 for ln in pr_diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")
                    )
                    deletions = pr_meta.get("deletions") or sum(
                        1 for ln in pr_diff.splitlines() if ln.startswith("-") and not ln.startswith("---")
                    )

                    annotated_diff, valid_lines = annotate_diff(pr_diff)
                    secret_findings = scan_diff_for_secrets(pr_diff)
                    secret_count = len(secret_findings)

                    files_list = list(valid_lines.keys())
                    triage = compute_pr_triage(
                        files_changed=files_list,
                        additions=additions,
                        deletions=deletions,
                        has_secret_findings=secret_count > 0,
                    )
                    await apply_pr_labels(token, repo_full_name, pr_number, triage.labels, client=client)

                    # Update live loading card stages before 2-pass LLM verification
                    mid_loading_card = build_loading_review_card(
                        requested_by=comment_user,
                        commit_sha=head_sha,
                        command="review",
                        target_ref=f"{repo_full_name}#{pr_number}",
                        stage_statuses={
                            "diff": f"COMPLETED ({len(files_list)} files)",
                            "security": "COMPLETED (Clean)" if secret_count == 0 else f"ALERT ({secret_count} found)",
                            "triage": f"COMPLETED ({triage.size_tier}, {triage.risk_level})",
                            "pass1": "RUNNING...",
                            "pass2": "RUNNING...",
                        },
                    )
                    await update_pr_review_card(
                        token=token,
                        repo_full_name=repo_full_name,
                        pr_number=pr_number,
                        handle=pending_review,
                        body=mid_loading_card,
                        client=client,
                    )

                    review_data = await run_pr_review(
                        cfg=cfg,
                        pr_title=pr_title,
                        pr_body=pr_body,
                        annotated_diff=annotated_diff,
                        client=client,
                        valid_lines=valid_lines,
                        triage=triage,
                    )

                    if secret_findings:
                        if "findings" not in review_data or not isinstance(review_data["findings"], list):
                            review_data["findings"] = []
                        for sf in secret_findings:
                            remediation_msg = (
                                f"**CRITICAL SECURITY ALERT**: Potential exposed credential `{sf.matched_text_masked}` detected.\n\n"
                                f"**Remediation**:\n"
                                f"1. Immediately revoke and rotate this secret in your provider console.\n"
                                f"2. Remove the secret from code and scrub git history using `git-filter-repo`.\n"
                                f"3. Store credentials using environment variables or a secrets manager."
                            )
                            review_data["findings"].insert(0, {
                                "file": sf.file_path,
                                "line": sf.line_number,
                                "severity": "bug",
                                "title": f"Secret Leak Detected: {sf.secret_type}",
                                "source": "scanner",
                                "confidence": sf.severity,
                                "explanation": remediation_msg,
                                "comment": remediation_msg,
                            })
                        review_data["verdict"] = "BLOCK"

                    await publish_pr_review(
                        token=token,
                        repo_full_name=repo_full_name,
                        pr_number=pr_number,
                        head_sha=head_sha,
                        review_data=review_data,
                        valid_lines=valid_lines,
                        client=client,
                        triage_summary=triage.summary_text,
                        secret_alerts_count=secret_count,
                        requested_by=comment_user,
                        existing_review=pending_review,
                    )
                    await complete_check_indicator(
                        token=token,
                        repo_full_name=repo_full_name,
                        check_handle=check_handle,
                        verdict=str(review_data.get("verdict", "PASS")),
                        summary_text=str(review_data.get("summary", "PatchPilot verification completed.")),
                        client=client,
                    )
                    if workspace is not None:
                        job_obj = current_job.get()
                        usage_meta = review_data.get("_usage") or {}
                        try:
                            async with SessionLocal() as usage_db:
                                ws_live = await usage_db.get(Workspace, workspace.id)
                                if ws_live is not None:
                                    await record_completed_review(
                                        usage_db,
                                        workspace=ws_live,
                                        event_id=job_obj.id if job_obj else uuid.uuid4().hex,
                                        repo_full_name=repo_full_name,
                                        is_private=is_private,
                                        pr_number=int(pr_number),
                                        github_author_id=comment_user_id,
                                        github_author_login=comment_user,
                                        author_type=sender_type,
                                        trigger_type="command",
                                        key_mode=decision.effective_key_mode,
                                        provider=str(review_data.get("_provider") or cfg.provider),
                                        model=str(review_data.get("_model") or cfg.model),
                                        prompt_tokens=usage_meta.get("prompt_tokens"),
                                        completion_tokens=usage_meta.get("completion_tokens"),
                                    )
                            await flush_unreported_seat_events(limit=5)
                        except Exception as meter_exc:
                            logger.warning("Usage/meter recording warning for command review on %s#%s: %s", repo_full_name, pr_number, meter_exc)
                    logger.info("Published full verified PR review for @%s on %s#%s", comment_user, repo_full_name, pr_number)
                    return

                reply_md = await run_interactive_command(
                    cfg=cfg,
                    command=command,
                    user_comment=extra_prompt,
                    file_path=file_path,
                    diff_hunk=diff_hunk,
                    pr_diff=pr_diff,
                    client=client,
                )
                body_text = strip_emojis(
                    f"## PatchPilot Command Response (`{command}`)\n\n"
                    f"**Requested by:** @{comment_user} &nbsp;|&nbsp; **Target:** `{repo_full_name}#{pr_number}`\n\n"
                    f"{reply_md}"
                )
            except Exception as llm_exc:
                logger.warning("Interactive LLM call failed on %s#%s: %s", repo_full_name, pr_number, llm_exc)
                if current_job.get() is not None:
                    raise
                has_key = _vault_has_usable_key(cfg)
                active_cands = [
                    c for c in get_provider_candidates(cfg)
                    if c.get("encrypted_api_key") or str(c.get("provider", "")).lower() == "ollama"
                ]
                active_cand = active_cands[-1] if active_cands else {"provider": cfg.provider, "model": cfg.model}
                status_detail = (
                    "No API key is currently stored in your BYOK Vault."
                    if not has_key
                    else f"Configured provider (`{active_cand['provider']}` / `{active_cand['model']}`) rejected the request or key."
                )
                dashboard_url = workspace_billing_url(workspace) if workspace else (f"{settings.app_base_url}/dashboard" if settings.app_base_url else "/dashboard")
                body_text = strip_emojis(
                    f"## PatchPilot Command Response (`{command}`)\n\n"
                    f"**Requested by:** @{comment_user} &nbsp;|&nbsp; **Status:** `Action Required`\n\n"
                    f"**AI Provider Key Missing or Invalid**\n\n"
                    f"PatchPilot received your `{command}` command on `{repo_full_name}#{pr_number}` "
                    f"(diff fetched: `{len(pr_diff):,}` chars), but could not complete the AI analysis: {status_detail}\n\n"
                    f"**How to fix in 30 seconds:**\n"
                    f"1. Open the PatchPilot Dashboard (`{dashboard_url}`).\n"
                    f"2. Select workspace `{owner.strip().lower()}` and save a valid API key or enable Managed AI.\n"
                    f"3. Comment `@patchpilot {command}` again on this PR."
                )
                if check_handle:
                    await complete_check_indicator(
                        token=token,
                        repo_full_name=repo_full_name,
                        check_handle=check_handle,
                        verdict="WARN",
                        summary_text="Action Required: Configure or verify BYOK Vault API key.",
                        client=client,
                    )
                    check_handle = None

            if check_handle:
                await complete_check_indicator(
                    token=token,
                    repo_full_name=repo_full_name,
                    check_handle=check_handle,
                    verdict="PASS",
                    summary_text=f"Completed PatchPilot `{command}` command.",
                    client=client,
                )

            updated_in_place = False
            if pending_review and not review_comment_id:
                updated_in_place = await update_pr_review_card(
                    token=token,
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    handle=pending_review,
                    body=body_text,
                    client=client,
                )

            if not updated_in_place:
                await post_comment_reply(
                    token=token,
                    repo_full_name=repo_full_name,
                    pr_number=pr_number,
                    body=body_text,
                    review_comment_id=review_comment_id,
                    client=client,
                )
            if workspace is not None and "Action Required" not in body_text:
                job_obj = current_job.get()
                try:
                    async with SessionLocal() as usage_db:
                        ws_live = await usage_db.get(Workspace, workspace.id)
                        if ws_live is not None:
                            await record_completed_review(
                                usage_db,
                                workspace=ws_live,
                                event_id=job_obj.id if job_obj else uuid.uuid4().hex,
                                repo_full_name=repo_full_name,
                                is_private=is_private,
                                pr_number=int(pr_number),
                                github_author_id=comment_user_id,
                                github_author_login=comment_user,
                                author_type=sender_type,
                                trigger_type="command",
                                key_mode=decision.effective_key_mode,
                                provider=str(cfg.provider),
                                model=str(cfg.model),
                            )
                    await flush_unreported_seat_events(limit=5)
                except Exception as meter_exc:
                    logger.warning("Usage/meter recording warning for command reply on %s#%s: %s", repo_full_name, pr_number, meter_exc)
            logger.info("Replied to %s command on %s#%s", command, repo_full_name, pr_number)
    except Exception as exc:
        logger.exception("Failed processing interactive comment: %s", exc)
        if current_job.get() is not None:
            raise
        if token and repo_full_name:
            await complete_check_indicator(token, repo_full_name, check_handle, verdict="ERROR", summary_text="Review failed; queued jobs retry automatically.")
            if pending_review:
                await update_pr_review_card(token, repo_full_name, pr_number, pending_review, "PatchPilot could not complete this review. The job will retry within its retry budget.")


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

    # 0. GitHub App Installation Lifecycle Events (multi-tenant workspace sync)
    if x_github_event == "installation" and action in (
        "created",
        "deleted",
        "suspend",
        "unsuspend",
        "new_permissions_accepted",
    ):
        installation_obj = payload.get("installation") or {}
        async with SessionLocal() as ws_db:
            ws = await upsert_workspace_from_installation(ws_db, installation_obj, action=action)
        return {
            "status": "installation_synced",
            "workspace_id": ws.id if ws else None,
            "installation_status": ws.installation_status if ws else None,
        }

    if x_github_event == "installation_repositories":
        installation_obj = payload.get("installation") or {}
        async with SessionLocal() as ws_db:
            ws = await upsert_workspace_from_installation(ws_db, installation_obj, action="sync")
        return {
            "status": "installation_repos_synced",
            "workspace_id": ws.id if ws else None,
        }

    # 1. Pull Request Events
    if x_github_event == "pull_request" and action in ("opened", "synchronize", "reopened"):
        if not payload.get("pull_request", {}).get("draft", False):
            await enqueue(x_github_event, payload, request.headers.get("X-GitHub-Delivery", ""))
            if os.environ.get("TESTING") != "1":
                task = asyncio.create_task(process_one())
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=8.5)
                except asyncio.TimeoutError:
                    pass
            return {"status": "queued_pr_review"}
        return {"status": "ignored_draft"}

    # 2. Interactive Comments
    if x_github_event in ("pull_request_review_comment", "issue_comment") and action == "created":
        sender_type = str(payload.get("comment", {}).get("user", {}).get("type") or payload.get("sender", {}).get("type", "")).lower()
        if sender_type != "bot":
            body_text = payload.get("comment", {}).get("body", "")
            if COMMAND_RE.search(body_text):
                await enqueue(x_github_event, payload, request.headers.get("X-GitHub-Delivery", ""))
                if os.environ.get("TESTING") != "1":
                    task = asyncio.create_task(process_one())
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=8.5)
                    except asyncio.TimeoutError:
                        pass
                return {"status": "queued_comment_reply"}

        return {"status": "ignored_comment"}

    return {"status": "ignored_event"}


# -----------------------------------------------------------------------------
# Polar.sh Billing & Clerk Auth Webhooks (Public, Signature-Verified Only)
# -----------------------------------------------------------------------------

@app.post("/webhooks/polar", tags=["Webhook"])
async def polar_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive and verify Polar.sh subscription lifecycle webhooks (Standard Webhooks)."""
    raw_body = await request.body()
    try:
        result = await process_polar_webhook(db, raw_body, request.headers)
        return result
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@app.post("/webhooks/clerk", tags=["Webhook"])
async def clerk_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Receive and verify Clerk user lifecycle webhooks (`user.updated`, `user.deleted`)."""
    raw_body = await request.body()
    try:
        event_id, payload = verify_standard_webhook(
            raw_body,
            request.headers,
            settings.CLERK_WEBHOOK_SIGNING_SECRET,
        )
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    existing = await db.get(BillingWebhookEvent, event_id)
    if existing is not None:
        return {"status": "duplicate", "event_id": event_id}

    evt_type = str(payload.get("type") or "").strip()
    data = payload.get("data") or {}

    db.add(
        BillingWebhookEvent(
            event_id=event_id,
            provider="clerk",
            event_type=evt_type or "unknown",
            processed_at=datetime.now(timezone.utc),
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return {"status": "duplicate", "event_id": event_id}

    clerk_user_id = str(data.get("id") or "").strip()
    if evt_type == "user.deleted" and clerk_user_id:
        await db.execute(delete(WorkspaceMember).where(WorkspaceMember.clerk_user_id == clerk_user_id))
        await db.execute(delete(UserAccount).where(UserAccount.clerk_id == clerk_user_id))
        await db.commit()
        return {"status": "deleted", "event_id": event_id, "clerk_id": clerk_user_id}

    if evt_type == "user.updated" and clerk_user_id:
        res = await db.execute(select(UserAccount).where(UserAccount.clerk_id == clerk_user_id))
        user_rec = res.scalar_one_or_none()
        if user_rec is not None:
            emails = data.get("email_addresses") or []
            primary_id = data.get("primary_email_address_id")
            for item in emails:
                if isinstance(item, dict) and (item.get("id") == primary_id or not user_rec.email):
                    if item.get("email_address"):
                        user_rec.email = str(item["email_address"])
            first = data.get("first_name") or ""
            last = data.get("last_name") or ""
            full_name = f"{first} {last}".strip()
            if full_name:
                user_rec.name = full_name
            if data.get("image_url"):
                user_rec.avatar_url = str(data["image_url"])
        await db.commit()
        return {"status": "updated", "event_id": event_id, "clerk_id": clerk_user_id}

    await db.commit()
    return {"status": "ignored", "event_id": event_id, "type": evt_type}
