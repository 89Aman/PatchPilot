import os
os.environ["TESTING"] = "1"
import hashlib
import hmac
import httpx
import pytest
from app.crypto import decrypt_secret, encrypt_secret
from app.github_auth import (
    build_review_summary_card,
    create_session_jwt,
    get_oauth_authorize_url,
    verify_session_jwt,
    verify_webhook_signature,
)
from app.llm import annotate_diff, extract_json_payload, get_provider_endpoint, is_noise_file


# -----------------------------------------------------------------------------
# 1. AES-256-GCM Encryption / Decryption Tests
# -----------------------------------------------------------------------------

def test_crypto_roundtrip():
    """Verify that encrypt_secret and decrypt_secret successfully round-trip secrets."""
    test_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    secrets = [
        "sk-ant-api03-test-token-123456789",
        "ghp_testgithubpersonalaccesstoken999",
        "unicode-secret-🔑-🚀-🔒",
        "a" * 5000,
        "special_chars_!@#$%^&*()_+~`-=[]{}|;':\",./<>?",
    ]

    for secret in secrets:
        token = encrypt_secret(secret, master_key_hex=test_key)
        assert isinstance(token, str)
        assert token != secret

        decrypted = decrypt_secret(token, master_key_hex=test_key)
        assert decrypted == secret


def test_crypto_nonce_randomness():
    """Verify that encrypting the same plaintext twice produces distinct ciphertexts (unique nonces)."""
    test_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    secret = "deterministic-test-key"

    token1 = encrypt_secret(secret, master_key_hex=test_key)
    token2 = encrypt_secret(secret, master_key_hex=test_key)

    assert token1 != token2
    assert decrypt_secret(token1, master_key_hex=test_key) == secret
    assert decrypt_secret(token2, master_key_hex=test_key) == secret


def test_crypto_corrupted_token():
    """Verify that tampered or invalid ciphertexts raise a ValueError."""
    test_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    token = encrypt_secret("my-secret", master_key_hex=test_key)

    # Corrupt last byte
    corrupted = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(ValueError, match="Decryption failed"):
        decrypt_secret(corrupted, master_key_hex=test_key)

    # Empty token
    with pytest.raises(ValueError, match="Token cannot be empty"):
        decrypt_secret("", master_key_hex=test_key)

    # Too short
    with pytest.raises(ValueError):
        decrypt_secret("aGVsbG8=", master_key_hex=test_key)


# -----------------------------------------------------------------------------
# 2. Diff Annotation, Line Numbering & Noise Filtering Tests
# -----------------------------------------------------------------------------

SAMPLE_UNIFIED_DIFF = """\
diff --git a/app/core.py b/app/core.py
index abc1234..def5678 100644
--- a/app/core.py
+++ b/app/core.py
@@ -10,6 +10,7 @@ def process_data(items):
     results = []
     for item in items:
-        if item.is_valid:
+        if item.is_valid and not item.is_stale:
+            logger.info("Processing item %s", item.id)
             results.append(item.value)
     return results
diff --git a/package-lock.json b/package-lock.json
index 1111111..2222222 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,5 +1,5 @@
 {
-  "name": "old-package",
+  "name": "new-package",
   "version": "1.0.0"
 }
diff --git a/yarn.lock b/yarn.lock
index 3333333..4444444 100644
--- a/yarn.lock
+++ b/yarn.lock
@@ -1,3 +1,3 @@
-# lockfile
+# updated lockfile
diff --git a/dist/bundle.min.js b/dist/bundle.min.js
index 5555555..6666666 100644
--- a/dist/bundle.min.js
+++ b/dist/bundle.min.js
@@ -1,2 +1,2 @@
-var a=1;
+var a=2;
"""


def test_is_noise_file():
    """Verify that noise files and lockfiles are accurately identified."""
    assert is_noise_file("package-lock.json") is True
    assert is_noise_file("frontend/package-lock.json") is True
    assert is_noise_file("yarn.lock") is True
    assert is_noise_file("pnpm-lock.yaml") is True
    assert is_noise_file("extension.vsix") is True
    assert is_noise_file("static/bundle.min.js") is True
    assert is_noise_file("static/style.min.css") is True
    assert is_noise_file("uv.lock") is True
    assert is_noise_file("poetry.lock") is True
    assert is_noise_file("cargo.lock") is True
    assert is_noise_file("composer.lock") is True
    assert is_noise_file("go.sum") is True

    # Real source files must NOT be noise
    assert is_noise_file("app/main.py") is False
    assert is_noise_file("package.json") is False
    assert is_noise_file("src/bundle.js") is False


def test_annotate_diff_line_numbers_and_filtering():
    """Verify diff annotation properly numbers right-side lines and excludes noise files."""
    annotated_diff, valid_lines = annotate_diff(SAMPLE_UNIFIED_DIFF)

    # 1. Noise files must be filtered out
    assert "package-lock.json" not in valid_lines or len(valid_lines.get("package-lock.json", set())) == 0
    assert "yarn.lock" not in valid_lines or len(valid_lines.get("yarn.lock", set())) == 0
    assert "dist/bundle.min.js" not in valid_lines or len(valid_lines.get("dist/bundle.min.js", set())) == 0

    assert "File excluded as noise/lockfile: package-lock.json" in annotated_diff
    assert "File excluded as noise/lockfile: yarn.lock" in annotated_diff
    assert "File excluded as noise/lockfile: dist/bundle.min.js" in annotated_diff

    # 2. Source file must be present
    assert "app/core.py" in valid_lines
    core_lines = valid_lines["app/core.py"]

    assert 10 in core_lines
    assert 11 in core_lines
    assert 12 in core_lines
    assert 13 in core_lines
    assert 14 in core_lines
    assert 15 in core_lines

    # Check annotated text markers
    assert "[L10]      results = []" in annotated_diff
    assert "[DEL] -        if item.is_valid:" in annotated_diff
    assert "[L12] +        if item.is_valid and not item.is_stale:" in annotated_diff
    assert "[L13] +            logger.info" in annotated_diff


# -----------------------------------------------------------------------------
# 3. Webhook Signature Verification Tests
# -----------------------------------------------------------------------------

def test_webhook_signature_verification():
    """Verify HMAC-SHA256 signature verification logic for GitHub webhooks."""
    secret = "my_super_secret_webhook_key_123"
    payload = b'{"action":"opened","pull_request":{"number":42}}'

    # Compute valid signature
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    valid_header = f"sha256={digest}"

    assert verify_webhook_signature(payload, valid_header, secret) is True

    # Tampered payload
    tampered_payload = b'{"action":"opened","pull_request":{"number":43}}'
    assert verify_webhook_signature(tampered_payload, valid_header, secret) is False

    # Invalid secret
    assert verify_webhook_signature(payload, valid_header, "wrong_secret") is False

    # Missing sha256= prefix
    assert verify_webhook_signature(payload, digest, secret) is False

    # Missing or empty headers
    assert verify_webhook_signature(payload, None, secret) is False
    assert verify_webhook_signature(payload, "", secret) is False


# -----------------------------------------------------------------------------
# 4. Review Summary Card & Provider Endpoints Tests
# -----------------------------------------------------------------------------

def test_build_review_summary_card():
    """Verify the markdown format of the review summary card."""
    card = build_review_summary_card(
        verdict="WARN",
        commit_sha="a1b2c3d4e5f6",
        summary="Found 1 risk and 2 minor nits.",
        bug_count=0,
        risk_count=1,
        nit_count=2,
    )

    assert card.startswith("<!-- patchpilot-ai-review -->")
    assert "**Verdict:** `WARN`" in card
    assert "**Commit:** `a1b2c3d`" in card
    assert "Found 1 risk and 2 minor nits." in card
    assert "| 🔴 Bugs | 0 |" in card
    assert "| 🟡 Risks | 1 |" in card
    assert "| 🔵 Nits | 2 |" in card


def test_get_provider_endpoint():
    """Verify endpoint routing across OpenRouter, Gemini, OpenAI, and Ollama."""
    assert get_provider_endpoint("openrouter") == "https://openrouter.ai/api/v1/chat/completions"
    assert get_provider_endpoint("gemini") == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert get_provider_endpoint("openai") == "https://api.openai.com/v1/chat/completions"
    assert get_provider_endpoint("openai", "https://custom.gateway.internal") == "https://custom.gateway.internal/v1/chat/completions"
    assert get_provider_endpoint("ollama") == "http://localhost:11434/v1/chat/completions"
    assert get_provider_endpoint("ollama", "https://tunnel.example.com") == "https://tunnel.example.com/v1/chat/completions"


def test_extract_json_payload():
    """Verify robust extraction of JSON from raw text or markdown code fences."""
    raw_json = '{"summary": "Looks good", "verdict": "PASS", "findings": []}'
    assert extract_json_payload(raw_json) == {"summary": "Looks good", "verdict": "PASS", "findings": []}

    fenced_json = '```json\n{"summary": "Found bug", "verdict": "BLOCK", "findings": []}\n```'
    assert extract_json_payload(fenced_json) == {"summary": "Found bug", "verdict": "BLOCK", "findings": []}

    conversational_json = 'Here is the review result:\n```json\n{"summary": "OK", "verdict": "PASS", "findings": []}\n```\nHope that helps!'
    assert extract_json_payload(conversational_json) == {"summary": "OK", "verdict": "PASS", "findings": []}

    # Malformed non-JSON output should not crash
    malformed = "The PR looks completely fine with no bugs."
    res = extract_json_payload(malformed)
    assert res["verdict"] == "WARN"
    assert res["findings"] == []
    assert "PR looks completely fine" in res["summary"]


def test_interactive_command_regex():
    """Verify regex matching for @patchpilot, @contextlens and /lens commands."""
    from app.main import COMMAND_RE

    m1 = COMMAND_RE.search("@patchpilot explain why this retry loop exists")
    assert m1 is not None
    assert m1.group(1).lower() == "explain"
    assert m1.group(2).strip() == "why this retry loop exists"

    m2 = COMMAND_RE.search("@patchpilot fix handle the KeyError exception here")
    assert m2 is not None
    assert m2.group(1).lower() == "fix"
    assert m2.group(2).strip() == "handle the KeyError exception here"

    m3 = COMMAND_RE.search("/lens fix handle the KeyError exception here")
    assert m3 is not None
    assert m3.group(1).lower() == "fix"

    m4 = COMMAND_RE.search("@contextlens explain the caching logic")
    assert m4 is not None
    assert m4.group(1).lower() == "explain"


# -----------------------------------------------------------------------------
# 5. OAuth & Session Tokens Tests
# -----------------------------------------------------------------------------

def test_oauth_and_session_tokens():
    """Verify OAuth authorize URL builder and session token creation/verification."""
    auth_url = get_oauth_authorize_url("test_client_id_123")
    assert "https://github.com/login/oauth/authorize" in auth_url
    assert "client_id=test_client_id_123" in auth_url

    session_token = create_session_jwt({
        "login": "octocat",
        "avatar_url": "https://github.com/images/error/octocat.gif",
        "name": "Monalisa Octocat",
        "user_token": "gho_test_user_token_123",
    })
    assert isinstance(session_token, str)
    # Token must be encrypted with AES-256-GCM, not in cleartext in the raw JWT
    assert "gho_test_user_token_123" not in session_token

    payload = verify_session_jwt(session_token)
    assert payload is not None
    assert payload["sub"] == "octocat"
    assert payload["name"] == "Monalisa Octocat"
    assert payload["user_token"] == "gho_test_user_token_123"

    # Corrupted session token
    assert verify_session_jwt("corrupted.token.value") is None
    assert verify_session_jwt("") is None


# -----------------------------------------------------------------------------
# 6. FastAPI Endpoints & Security Tests
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_endpoint():
    """Verify health endpoint returns status ok."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_landing_page():
    """Verify landing page HTML route loads successfully."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "PatchPilot" in response.text
        assert "Zero Data Leakage" in response.text
        assert "/dashboard" in response.text


@pytest.mark.asyncio
async def test_dashboard_page():
    """Verify dashboard HTML route loads successfully."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard")
        assert response.status_code == 200
        assert "PatchPilot" in response.text
        assert "BYOK Encryption Vault" in response.text


@pytest.mark.asyncio
async def test_vault_admin_token_protection():
    """Verify that vault endpoints reject requests without a valid X-Admin-Token or session."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Missing token
        res_missing = await client.get("/vault/test-owner")
        assert res_missing.status_code == 401

        # Invalid token
        res_invalid = await client.get("/vault/test-owner", headers={"X-Admin-Token": "bad-token"})
        assert res_invalid.status_code == 401

        # Valid token
        from app.config import settings
        res_valid = await client.get("/vault/test-owner", headers={"X-Admin-Token": settings.ADMIN_API_TOKEN})
        assert res_valid.status_code == 200
        assert res_valid.json()["owner"] == "test-owner"


@pytest.mark.asyncio
async def test_webhook_unauthorized_rejection():
    """Verify that github webhook endpoint rejects requests with invalid signature."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/github",
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=invalidhash000000000000000000000000000000000000000000000000000000",
            },
            json={"action": "opened"},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_docs_and_favicon_endpoints():
    """Verify separate documentation page and favicon routes."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # /docs
        res_docs = await client.get("/docs")
        assert res_docs.status_code == 200
        assert "Documentation" in res_docs.text
        assert "/review" in res_docs.text
        assert "/explain" in res_docs.text
        assert "/fix" in res_docs.text

        # /documentation alias
        res_alias = await client.get("/documentation")
        assert res_alias.status_code == 200

        # /favicon.ico
        res_fav = await client.get("/favicon.ico")
        assert res_fav.status_code == 200
        assert res_fav.headers["content-type"] == "image/svg+xml"
        assert "<svg" in res_fav.text

        # /dashboard has toast and no Models in navbar
        res_dash = await client.get("/dashboard")
        assert res_dash.status_code == 200
        assert "toastContainer" in res_dash.text
        assert "showToast" in res_dash.text
        assert 'href="#models"' not in res_dash.text


def test_clerk_auth_helpers():
    """Verify Clerk publishable key parser and identity extraction."""
    import base64
    from app.clerk_auth import derive_frontend_api, extract_clerk_identity

    # Valid publishable key with base64 domain
    domain = "clean-hound-12.clerk.accounts.dev$"
    encoded = base64.b64encode(domain.encode()).decode().rstrip("=")
    pk = f"pk_test_{encoded}"
    assert derive_frontend_api(pk) == "clean-hound-12.clerk.accounts.dev"

    # Invalid / empty keys
    assert derive_frontend_api("") is None
    assert derive_frontend_api("invalid_key_format") is None

    # Identity extraction from claims
    payload = {
        "sub": "user_2abc123",
        "username": "shasank",
        "name": "Shasank Shekhar",
        "image_url": "https://img.clerk.com/avatar.png",
    }
    identity = extract_clerk_identity(payload)
    assert identity["login"] == "shasank"
    assert identity["name"] == "Shasank Shekhar"
    assert identity["avatar_url"] == "https://img.clerk.com/avatar.png"
    assert identity["auth_provider"] == "clerk"


@pytest.mark.asyncio
async def test_clerk_endpoints():
    """Verify Clerk config, session exchange, and bearer token authorization."""
    import time
    import jwt
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Config endpoint
        res_cfg = await client.get("/auth/clerk/config")
        assert res_cfg.status_code == 200
        assert "publishable_key" in res_cfg.json()
        assert "is_configured" in res_cfg.json()

        # Generate a valid mock Clerk session JWT
        now = int(time.time())
        token_payload = {
            "sub": "user_testclerk",
            "username": "clerkuser",
            "name": "Clerk Tester",
            "image_url": "https://img.clerk.com/test.png",
            "exp": now + 3600,
            "iat": now,
        }
        mock_token = jwt.encode(token_payload, "mock-clerk-secret-32-bytes-long!", algorithm="HS256")

        # Session exchange endpoint
        res_sess = await client.post(
            "/auth/clerk/session",
            json={"session_token": mock_token, "user": {"login": "clerkuser"}},
        )
        assert res_sess.status_code == 200
        assert res_sess.json()["status"] == "ok"
        assert res_sess.json()["user"]["login"] == "clerkuser"
        assert "patchpilot_session" in res_sess.cookies

        # Auth me with Clerk Bearer token
        res_me = await client.get("/auth/me", headers={"Authorization": f"Bearer {mock_token}"})
        assert res_me.status_code == 200
        data_me = res_me.json()
        assert data_me["logged_in"] is True
        assert data_me["user"]["login"] == "clerkuser"
        assert data_me["provider"] == "clerk"

        # Access vault with Clerk Bearer token matching owner
        res_vault = await client.get(
            "/vault/clerkuser",
            headers={"Authorization": f"Bearer {mock_token}"},
        )
        assert res_vault.status_code == 200
        assert res_vault.json()["owner"] == "clerkuser"

        # Access vault with Clerk Bearer token mismatching owner
        res_vault_mismatch = await client.get(
            "/vault/otheruser",
            headers={"Authorization": f"Bearer {mock_token}"},
        )
        assert res_vault_mismatch.status_code == 401


@pytest.mark.asyncio
async def test_clerk_then_github_connect_flow(monkeypatch):
    """Verify flow: user logs in via Clerk first, then connects GitHub to allow repo scope."""
    import time
    import jwt
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Step 1: User logs in with Clerk
        now = int(time.time())
        token_payload = {
            "sub": "user_clerk_developer",
            "username": "clerkdev",
            "name": "Clerk Developer",
            "exp": now + 3600,
            "iat": now,
        }
        mock_token = jwt.encode(token_payload, "mock-clerk-secret-32-bytes-long!", algorithm="HS256")

        res_sess = await client.post(
            "/auth/clerk/session",
            json={"session_token": mock_token, "user": {"login": "clerkdev"}},
        )
        assert res_sess.status_code == 200
        session_cookie = res_sess.cookies["patchpilot_session"]
        client.cookies.set("patchpilot_session", session_cookie)

        # Step 2: User checks repos before connecting GitHub -> not connected
        res_repos_before = await client.get("/api/user/repos")
        assert res_repos_before.status_code == 200
        assert res_repos_before.json()["github_connected"] is False

        # Step 3: Mock GitHub profile & repo API calls
        async def mock_fetch_profile(token, client=None):
            assert token == "ghp_valid_test_token_123"
            return {"login": "octocat-dev", "name": "Octo Dev", "avatar_url": "https://github.com/avatar.png"}

        async def mock_fetch_repos(token, client=None):
            return [{"full_name": "octocat-dev/super-repo", "name": "super-repo", "private": False}]

        monkeypatch.setattr("app.main.fetch_user_profile", mock_fetch_profile)
        monkeypatch.setattr("app.main.fetch_user_installations_and_repos", mock_fetch_repos)

        # Step 4: User connects GitHub token / PAT with repo scope
        res_connect = await client.post(
            "/api/user/github-token",
            json={"token": "ghp_valid_test_token_123"},
        )
        assert res_connect.status_code == 200
        assert res_connect.json()["status"] == "ok"
        assert res_connect.json()["github_login"] == "octocat-dev"

        new_session_cookie = res_connect.cookies["patchpilot_session"]
        client.cookies.set("patchpilot_session", new_session_cookie)

        # Step 5: Check repos again -> connected with scoped repositories
        res_repos_after = await client.get("/api/user/repos")
        assert res_repos_after.status_code == 200
        assert res_repos_after.json()["github_connected"] is True
        assert len(res_repos_after.json()["repos"]) == 1
        assert res_repos_after.json()["repos"][0]["full_name"] == "octocat-dev/super-repo"



