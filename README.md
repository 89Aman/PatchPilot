# ✈️ PatchPilot

**PatchPilot** is a self-hosted, multi-provider, Bring-Your-Own-Key (BYOK) GitHub App and AI Pull Request review engine. Authenticate via GitHub OAuth, scope your repository installations, and securely store encrypted LLM credentials (Gemini, xAI Grok, Groq, OpenRouter, OpenAI, Ollama) in an isolated AES-256-GCM database vault.

---

## Key Features

- **Database & Security Vault (`app/db.py`, `app/crypto.py`)**:
  - PostgreSQL / Supabase storage using async SQLAlchemy 2.0 with `asyncpg`.
  - AES-256-GCM encryption with 12-byte random nonces and 32-byte master key.
  - Strict per-user isolation keyed by GitHub username.
- **GitHub OAuth & Repo Scoping (`app/github_auth.py`)**:
  - GitHub OAuth authentication with signed `HttpOnly` JWT session cookies (`pyjwt`).
  - Live inspection of repository scopes granted to PatchPilot.
  - RS256 JWT App authentication and `X-Hub-Signature-256` HMAC webhook verification.
- **Multi-Provider LLM Router (`app/llm.py`)**:
  - Native endpoints for Gemini 2.5, xAI Grok, Groq Cloud, OpenRouter (Claude 3.7 / DeepSeek), OpenAI (GPT-4o), and local/tunnel Ollama instances.
  - Unified git diff parsing with automatic lockfile noise filtering and exact right-side (`HEAD`) line numbering.
- **Automated PR Reviews & Interactive Commands (`app/main.py`)**:
  - Automated PR reviews with summary scorecards (`🔴 Bugs`, `🟡 Risks`, `🔵 Nits`) and line-accurate inline review comments.
  - One-click committable GitHub ` ```suggestion ` blocks.
  - Responds to `@patchpilot explain` and `@patchpilot fix` (as well as `/lens` and `@contextlens`) in review comment threads.
- **Modern Dashboard & Settings (`app/ui.py`)**:
  - Dark-mode Tailwind CSS interface with GitHub profile card, repository scope pill tags, provider dropdowns, encrypted key status badges, and model connection tester.

---

## Quick Start

### 1. Configure Environment
Copy `.env.example` to `.env` and populate your secrets:
```bash
cp .env.example .env
```

### 2. Install Dependencies & Run Locally
```bash
# Sync dependencies via uv
uv sync

# Run tests
uv run pytest

# Start development server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

### 3. Run with Docker Compose
```bash
docker compose up -d
```

---

## API Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/` or `/dashboard` | `GET` | Main PatchPilot dashboard with OAuth identity card and vault settings |
| `/health` | `GET` | Health check endpoint returning JSON `{"status": "ok"}` |
| `/auth/login` | `GET` | Initiates GitHub OAuth authentication handshake |
| `/auth/callback` | `GET` | Exchanges code for token, sets secure `patchpilot_session` cookie |
| `/auth/logout` | `GET` | Clears user session cookie |
| `/auth/me` | `GET` | Returns profile info for authenticated user |
| `/api/user/repos` | `GET` | Lists repositories scoped to the authenticated user |
| `/vault/{owner}` | `GET` | Retrieves vault metadata without exposing plaintext API keys |
| `/vault/{owner}` | `PUT` | Upserts provider, model, encrypted API key, and review policy |
| `/vault/{owner}/test` | `POST` | Executes live test ping against configured LLM model |
| `/webhooks/github` | `POST` | GitHub App webhook receiver (HMAC-SHA256 verified) |
