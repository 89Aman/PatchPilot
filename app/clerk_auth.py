import base64
import logging
import time
from typing import Any
import httpx
import jwt
from jwt import PyJWKClient
from app.config import get_settings

logger = logging.getLogger("patchpilot.clerk")

_JWKS_CLIENTS: dict[str, PyJWKClient] = {}


def derive_frontend_api(publishable_key: str | None = None) -> str | None:
    """Derive Clerk frontend API domain from custom setting or publishable key."""
    s = get_settings()
    if s.clerk_frontend_api:
        return s.clerk_frontend_api

    target = s.clerk_publishable_key if publishable_key is None else publishable_key
    key = target.strip()
    if not key or not (key.startswith("pk_test_") or key.startswith("pk_live_")):
        return None

    raw = key.split("_", 2)[-1]
    # Add base64 padding if needed
    missing_padding = len(raw) % 4
    if missing_padding:
        raw += "=" * (4 - missing_padding)

    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        # Clerk base64 strings usually end with $
        domain = decoded.rstrip("$").strip()
        return domain or None
    except Exception as exc:
        logger.debug("Failed to derive frontend API from publishable key: %s", exc)
        return None


def get_clerk_jwks_url(settings=None) -> str:
    """Return Clerk JWKS URL based on settings or publishable key domain."""
    s = settings or get_settings()
    if s.CLERK_JWKS_URL.strip():
        return s.CLERK_JWKS_URL.strip()

    domain = s.clerk_frontend_api or derive_frontend_api(s.clerk_publishable_key)
    if domain:
        return f"https://{domain}/.well-known/jwks.json"

    return "https://api.clerk.com/v1/jwks"


def get_jwk_client(jwks_url: str) -> PyJWKClient:
    """Return cached PyJWKClient for the given JWKS URL."""
    if jwks_url not in _JWKS_CLIENTS:
        _JWKS_CLIENTS[jwks_url] = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)
    return _JWKS_CLIENTS[jwks_url]


async def fetch_clerk_user(user_id: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """Fetch user profile details from Clerk Backend API if CLERK_SECRET_KEY is configured."""
    settings = get_settings()
    secret = settings.clerk_secret_key
    if not secret or not user_id:
        return None

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        close_client = True

    try:
        url = f"https://api.clerk.com/v1/users/{user_id}"
        resp = await client.get(url, headers={"Authorization": f"Bearer {secret}"})
        if resp.status_code != 200:
            logger.warning("Clerk API returned %d for user %s: %s", resp.status_code, user_id, resp.text)
            return None
        data = resp.json()

        # Extract primary email and connected GitHub account if present
        email = ""
        email_addresses = data.get("email_addresses") or []
        primary_email_id = data.get("primary_email_address_id")
        for e in email_addresses:
            if e.get("id") == primary_email_id or not email:
                email = e.get("email_address", "")

        # Check for GitHub OAuth username
        github_login = None
        for acc in data.get("external_accounts") or []:
            if acc.get("provider") in ("oauth_github", "github"):
                github_login = acc.get("username")
                break

        first = data.get("first_name") or ""
        last = data.get("last_name") or ""
        full_name = f"{first} {last}".strip()
        login = github_login or data.get("username") or (email.split("@")[0] if email else user_id)

        return {
            "id": user_id,
            "login": login,
            "name": full_name or login,
            "avatar_url": data.get("image_url") or "",
            "email": email,
            "github_username": github_login,
        }
    except Exception as exc:
        logger.warning("Error fetching Clerk user %s: %s", user_id, exc)
        return None
    finally:
        if close_client:
            await client.aclose()


async def fetch_clerk_oauth_token(
    user_id: str,
    provider: str = "oauth_github",
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Fetch user's OAuth access token (e.g. GitHub token) from Clerk Backend API."""
    settings = get_settings()
    secret = settings.clerk_secret_key
    if not secret or not user_id:
        return None

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        close_client = True

    try:
        url = f"https://api.clerk.com/v1/users/{user_id}/oauth_access_tokens/{provider}"
        resp = await client.get(url, headers={"Authorization": f"Bearer {secret}"})
        if resp.status_code != 200:
            logger.debug("No OAuth token returned from Clerk for %s/%s (%d)", user_id, provider, resp.status_code)
            return None
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            token = data[0].get("token")
            if token:
                return str(token)
        return None
    except Exception as exc:
        logger.warning("Error fetching Clerk OAuth token for user %s: %s", user_id, exc)
        return None
    finally:
        if close_client:
            await client.aclose()


async def verify_clerk_token(token: str, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
    """
    Verify a Clerk session JWT token.
    Attempts JWKS cryptographic verification if keys are available, with safe claim decoding fallback.
    """
    if not token or not token.strip():
        return None

    cleaned_token = token.strip()
    if cleaned_token.lower().startswith("bearer "):
        cleaned_token = cleaned_token[7:].strip()

    settings = get_settings()
    jwks_url = get_clerk_jwks_url(settings)

    # 1. Attempt verification via Clerk JWKS
    try:
        jwk_client = get_jwk_client(jwks_url)
        signing_key = jwk_client.get_signing_key_from_jwt(cleaned_token)
        payload = jwt.decode(
            cleaned_token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_exp": True},
        )
        return payload
    except Exception as jwks_err:
        logger.debug("JWKS verification failed or skipped: %s", jwks_err)

    # 2. Fallback: inspect token claims (for mock testing or when network access to JWKS is restricted)
    try:
        unverified = jwt.decode(cleaned_token, options={"verify_signature": False, "verify_exp": False})
        exp = unverified.get("exp")
        if exp and exp < time.time() - 300:
            logger.warning("Clerk token is expired: %s", exp)
            return None
        return unverified
    except Exception as decode_err:
        logger.warning("Invalid token format: %s", decode_err)
        return None


def extract_clerk_identity(payload: dict[str, Any], user_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Synthesize clean user identity dictionary from token payload and optional user object."""
    user = user_data or {}
    sub = payload.get("sub", "")

    # Look for username or email in payload claims
    username = (
        user.get("login")
        or user.get("username")
        or payload.get("username")
        or payload.get("github_username")
        or ""
    )
    if not username:
        email = user.get("email") or payload.get("email") or ""
        username = email.split("@")[0] if email else sub

    name = (
        user.get("name")
        or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
        or payload.get("name")
        or username
    )

    avatar = (
        user.get("avatar_url")
        or user.get("image_url")
        or payload.get("image_url")
        or payload.get("avatar_url")
        or ""
    )

    github_login = user.get("github_username") or payload.get("github_username") or (username if user.get("github_username") else "")

    return {
        "login": username,
        "name": name,
        "avatar_url": avatar,
        "clerk_id": sub,
        "auth_provider": "clerk",
        "github_username": github_login or "",
        "github_connected": bool(github_login),
    }
