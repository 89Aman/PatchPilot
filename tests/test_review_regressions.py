"""Regression coverage for repository review findings, using local/mock services."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.clerk_auth as clerk
import app.main as main
from app.config import settings
from app.db import Base, UserAccount, VaultConfig
from app.github_auth import verify_session_jwt


@pytest.mark.asyncio
async def test_clerk_jwks_failure_rejects_unverified_token(monkeypatch):
    def unavailable(_url):
        raise RuntimeError("JWKS unavailable")

    monkeypatch.setattr(clerk, "get_jwk_client", unavailable)
    monkeypatch.setattr(settings, "CLERK_FRONTEND_API", "clerk.example")
    token = jwt.encode({"sub": "user_external", "username": "victim", "exp": time.time() + 600},
                       "local-test-secret-never-a-real-credential", algorithm="HS256")
    assert await clerk.verify_clerk_token(token) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{}, {"iss": "https://wrong.example"}, {"exp": 1}, {"sub": None}, {"exp": None}])
async def test_clerk_signed_claim_validation(monkeypatch, changed):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, "CLERK_FRONTEND_API", "clerk.example")
    monkeypatch.setattr(settings, "CLERK_JWKS_URL", "")
    client = SimpleNamespace(get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key()))
    monkeypatch.setattr(clerk, "get_jwk_client", lambda _url: client)
    claims = {"sub": "user_local", "iss": "https://clerk.example", "exp": time.time() + 600}
    claims.update(changed)
    claims = {k: v for k, v in claims.items() if v is not None}
    result = await clerk.verify_clerk_token(jwt.encode(claims, key, algorithm="RS256"))
    assert (result is not None) is (not changed)


@pytest.mark.asyncio
async def test_clerk_session_ignores_client_owner_identity(monkeypatch):
    monkeypatch.setattr(main, "verify_clerk_token", AsyncMock(return_value={"sub": "user_safe", "username": "victim", "github_username": "victim"}))
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", "")
    monkeypatch.setattr(main, "_upsert_user_account", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/auth/clerk/session", json={
            "session_token": "mocked-verification", "user": {"login": "victim", "github_username": "victim"},
        })
        assert response.status_code == 200
        assert verify_session_jwt(response.cookies["patchpilot_session"])["sub"] == "user_safe"
        denied = await client.get("/vault/victim")
        assert denied.status_code == 401
        client.cookies.clear()
        denied = await client.get("/vault/victim", headers={"Authorization": "Bearer mocked-verification"})
        assert denied.status_code == 401


@pytest.mark.asyncio
async def test_clerk_verified_github_identity_still_works(monkeypatch):
    monkeypatch.setattr(main, "verify_clerk_token", AsyncMock(return_value={"sub": "user_safe"}))
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", "test-only")
    monkeypatch.setattr(main, "fetch_clerk_user", AsyncMock(return_value={"id": "user_safe", "github_username": "verified-owner"}))
    monkeypatch.setattr(main, "fetch_clerk_oauth_token", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "_upsert_user_account", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/auth/clerk/session", json={"session_token": "mocked-verification"})
        assert response.status_code == 200
        assert verify_session_jwt(response.cookies["patchpilot_session"])["sub"] == "verified-owner"


@pytest.mark.asyncio
async def test_owner_lookup_never_borrows_unrelated_vault(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            session.add_all([
                VaultConfig(owner="unrelated", provider="ollama", encrypted_api_key=""),
                VaultConfig(owner="empty-owner", provider="gemini", encrypted_api_key=""),
                VaultConfig(owner="user_linked", provider="ollama", encrypted_api_key=""),
                UserAccount(login="user_linked", github_username="Linked-Owner", encrypted_github_token=""),
            ])
            await session.commit()
        assert await main._get_owner_config("missing-owner") is None
        assert (await main._get_owner_config("empty-owner")).owner == "empty-owner"
        assert (await main._get_owner_config(" LINKED-OWNER ")).owner == "user_linked"
        assert (await main._get_owner_config("unrelated")).owner == "unrelated"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["issue_comment", "pull_request_review_comment"])
@pytest.mark.parametrize("association,user_type,allowed", [("NONE", "User", False), ("CONTRIBUTOR", "User", False), ("OWNER", "Bot", False), ("MEMBER", "User", True)])
async def test_comment_authorization_before_external_work(monkeypatch, event_type, association, user_type, allowed):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(main, "_get_owner_config", lookup)
    monkeypatch.setattr(main, "get_installation_token", AsyncMock(side_effect=RuntimeError("stop after authorization")))
    main.global_rate_limiter.reset()
    payload = {
        "installation": {"id": 1},
        "repository": {"full_name": "owner/repo", "owner": {"login": "owner"}, "private": False},
        "pull_request": {"number": 1}, "issue": {"number": 1, "pull_request": {}},
        "comment": {"body": "@patchpilot explain", "author_association": association, "user": {"type": user_type}},
    }
    await main.handle_comment_event(event_type, payload)
    assert bool(lookup.await_count) is allowed
    main.global_rate_limiter.reset()


@pytest.mark.asyncio
async def test_comment_shares_automatic_review_budget(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(main, "_get_owner_config", lookup)
    monkeypatch.setattr(main, "get_installation_token", AsyncMock(side_effect=RuntimeError("must not call")))
    main.global_rate_limiter.reset()
    for _ in range(25):
        main.global_rate_limiter.check_and_increment("owner/repo", max_requests=25)
    await main.handle_comment_event("issue_comment", {
        "installation": {"id": 1},
        "repository": {"full_name": "owner/repo", "owner": {"login": "owner"}},
        "issue": {"number": 1, "pull_request": {}},
        "comment": {"body": "@patchpilot review", "author_association": "OWNER", "user": {"type": "User"}},
    })
    lookup.assert_not_awaited()
    main.global_rate_limiter.reset()


@pytest.mark.asyncio
async def test_oauth_callback_requires_browser_state(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_CLIENT_ID", "test-client")
    monkeypatch.setattr(settings, "GITHUB_CLIENT_SECRET", "test-secret")
    exchange = AsyncMock(return_value="local-test-token")
    monkeypatch.setattr(main, "exchange_oauth_code", exchange)
    monkeypatch.setattr(main, "fetch_user_profile", AsyncMock(return_value={"login": "owner"}))
    monkeypatch.setattr(main, "_upsert_user_account", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        denied = await client.get("/auth/callback?code=test-code")
        assert denied.status_code == 400
        exchange.assert_not_awaited()
        login = await client.get("/auth/login")
        from urllib.parse import parse_qs, urlparse
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        denied = await client.get("/auth/callback", params={"code": "test-code", "state": "wrong"})
        assert denied.status_code == 400
        exchange.assert_not_awaited()
        accepted = await client.get("/auth/github/callback", params={"code": "test-code", "state": state})
        assert accepted.status_code == 302
        exchange.assert_awaited_once()
        assert "patchpilot_oauth_state" not in client.cookies
