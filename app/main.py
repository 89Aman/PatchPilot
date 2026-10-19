from contextlib import asynccontextmanager
import hmac
import json
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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret
from app.db import DEFAULT_POLICY, Base, SessionLocal, UserAccount, VaultConfig, engine, get_db, init_db
from app.github_auth import (
    apply_pr_labels,
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
import uuid
from app.llm import (
    annotate_diff,
    get_provider_candidates,
    run_interactive_command,
    run_pr_review,
    test_llm_connection,
)
from app.models_catalog import OFFICIAL_FREE_MODELS
from app.security import (
    check_pr_authorization,
    global_rate_limiter,
    sanitize_diff_text,
    scan_diff_for_secrets,
)
from app.triage import compute_pr_triage
from app.ui import DASHBOARD_HTML

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

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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


class FallbackUpsertRequest(BaseModel):
    id: str | None = Field(default=None, description="Optional ID when updating existing fallback")
    name: str = Field(default="Secondary Fallback", description="Label for this fallback")
    provider: str = Field(default="groq", description="gemini | groq | openrouter | openai | ollama | custom")
    model: str = Field(default="llama-3.3-70b-versatile", description="Model slug")
    api_key: str | None = Field(default=None, description="Plaintext API key. If empty, retains existing key.")
    base_url: str | None = Field(default=None, description="Custom endpoint URL")
    is_enabled: bool = Field(default=True, description="Enable or disable this candidate")


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
async def robots_txt():
    """Serve robots.txt for search engine crawlers."""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /docs\n"
        "Disallow: /vault/\n"
        "Disallow: /api/\n"
        "Disallow: /webhooks/\n"
        "Sitemap: https://patchpilot.noventra.org.in/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    """Serve canonical sitemap.xml."""
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url>\n'
        '    <loc>https://patchpilot.noventra.org.in/</loc>\n'
        '    <changefreq>weekly</changefreq>\n'
        '    <priority>1.0</priority>\n'
        '  </url>\n'
        '  <url>\n'
        '    <loc>https://patchpilot.noventra.org.in/docs</loc>\n'
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


async def _upsert_user_account(
    db: AsyncSession,
    *,
    login: str,
    name: str | None = None,
    avatar_url: str | None = None,
    email: str | None = None,
    clerk_id: str | None = None,
    github_username: str | None = None,
    github_token: str | None = None,
    auth_provider: str = "github",
) -> None:
    """Persist or update authenticated user profile and AES-256-GCM encrypted GitHub token in PostgreSQL/Supabase."""
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
        if enc_token:
            user_rec.encrypted_github_token = enc_token
        user_rec.auth_provider = auth_provider
    else:
        user_rec = UserAccount(
            login=login_clean,
            clerk_id=clerk_id or None,
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
    """Handle OAuth callback, persist user in Supabase PostgreSQL, issue session cookie, and redirect to dashboard."""
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

    await _upsert_user_account(
        db,
        login=profile.get("login") or "",
        name=profile.get("name"),
        avatar_url=profile.get("avatar_url"),
        email=profile.get("email"),
        github_username=profile.get("login"),
        github_token=user_token,
        auth_provider="github",
    )

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
    custom_api = settings.clerk_frontend_api
    derived_api = custom_api or (derive_frontend_api(pk) if pk else None)
    return {
        "publishable_key": pk,
        "is_configured": bool(pk),
        "frontend_api": derived_api,
    }


@app.post("/auth/clerk/session", tags=["Authentication"])
async def clerk_session(request: Request, body: ClerkSessionRequest, db: AsyncSession = Depends(get_db)):
    """Verify Clerk session token, persist user in Supabase PostgreSQL, and establish PatchPilot session cookie."""
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
    await _upsert_user_account(
        db,
        login=identity["login"],
        name=identity["name"],
        avatar_url=identity["avatar_url"],
        email=(clerk_user_info or {}).get("email") or payload.get("email"),
        clerk_id=sub,
        github_username=identity.get("github_username") or None,
        github_token=github_token or None,
        auth_provider="clerk",
    )

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
    db: AsyncSession = Depends(get_db),
):
    """Store or update GitHub access token in Supabase PostgreSQL and session cookie to enable repo access."""
    session = verify_session_jwt(patchpilot_session)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required")

    clean_token = body.token.strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="Token cannot be empty")

    async with httpx.AsyncClient(timeout=10.0) as client:
        profile = await fetch_user_profile(clean_token, client=client)

    gh_login = profile.get("login") or session.get("sub") or ""
    await _upsert_user_account(
        db,
        login=gh_login,
        name=profile.get("name") or session.get("name"),
        avatar_url=profile.get("avatar_url") or session.get("avatar_url"),
        clerk_id=session.get("clerk_id") or None,
        github_username=profile.get("login"),
        github_token=clean_token,
        auth_provider="github",
    )

    jwt_token = create_session_jwt({
        "login": gh_login,
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
async def get_user_repositories(
    patchpilot_session: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Fetch repositories scoped to the authenticated user."""
    session = verify_session_jwt(patchpilot_session)
    if not session:
        raise HTTPException(status_code=401, detail="Authentication required to inspect repository scope")

    user_token = session.get("user_token")
    clerk_id = session.get("clerk_id")
    sub_login = (session.get("sub") or "").strip().lower()

    # Check PostgreSQL/Supabase users table if token not in cookie
    if not user_token and sub_login:
        stmt = select(UserAccount).where(UserAccount.login == sub_login)
        result = await db.execute(stmt)
        user_rec = result.scalar_one_or_none()
        if user_rec and user_rec.encrypted_github_token:
            try:
                user_token = decrypt_secret(user_rec.encrypted_github_token)
            except Exception:
                user_token = ""

    # If still no token, check if Clerk has user's GitHub OAuth token
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

async def _get_owner_config(owner: str) -> VaultConfig | None:
    async with SessionLocal() as session:
        result = await session.execute(select(VaultConfig).where(VaultConfig.owner == owner.strip().lower()))
        return result.scalar_one_or_none()


async def handle_pr_event(payload: dict[str, Any]) -> None:
    """Background task to fetch PR diff, scan secrets, triage risk, run review, and publish comments."""
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
        if not allowed:
            logger.warning("Rate limit reached for %s: %s", repo_full_name, rate_msg)
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

            # 4. Sanitize diff before LLM processing if secrets were detected
            clean_annotated_diff = sanitize_diff_text(annotated_diff, secret_findings) if secret_count > 0 else annotated_diff

            # 5. Run AI PR Review with clean diff
            review_data = await run_pr_review(
                cfg=cfg,
                pr_title=pr_title,
                pr_body=pr_body,
                annotated_diff=clean_annotated_diff,
                client=client,
                valid_lines=valid_lines,
                triage=triage,
            )


            # If secrets were detected, inject critical inline alerts and block verdict
            if secret_findings:
                if "findings" not in review_data or not isinstance(review_data["findings"], list):
                    review_data["findings"] = []
                for sf in secret_findings:
                    review_data["findings"].insert(0, {
                        "file": sf.file_path,
                        "line": sf.line_number,
                        "severity": "bug",
                        "title": f"🚨 Secret Leak Detected: {sf.secret_type}",
                        "comment": (
                            f"**CRITICAL SECURITY ALERT**: Potential exposed credential `{sf.matched_text_masked}` detected.\n\n"
                            f"**Remediation**:\n"
                            f"1. Immediately revoke and rotate this secret in your provider console.\n"
                            f"2. Remove the secret from code and scrub git history using `git-filter-repo`.\n"
                            f"3. Store credentials using environment variables or a secrets manager."
                        ),
                    })
                review_data["verdict"] = "BLOCK"

            # 6. Publish native GitHub review with triage summary & security badges
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
            )
            logger.info("Published review and triage for %s#%s (size: %s, risk: %s)", repo_full_name, pr_number, triage.size_tier, triage.risk_level)
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
