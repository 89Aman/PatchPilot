# Contributing to PatchPilot

Thank you for your interest in contributing to **PatchPilot**! Whether you are fixing a bug, adding support for a new LLM provider, improving our pre-LLM secret scanner, or refining documentation, your contributions help teams review code safely without SaaS lock-in.

---

## 1. Local Development Prerequisites

- **Python:** `3.12` or `3.13`
- **Package Manager:** [uv](https://docs.astral.sh/uv/) (recommended) or standard `pip` + `venv`
- **Docker & Docker Compose:** Optional, for running the full PostgreSQL 16 + FastAPI stack locally
- **Git & Pre-Commit:** For secret scanning and formatting checks before pushing

---

## 2. Environment Setup

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/<your-username>/PatchPilot.git
   cd PatchPilot
   ```

2. **Create your local `.env` configuration from the template:**
   ```bash
   cp .env.example .env
   ```
   > **Note:** For local development and running tests, `TESTING=1` automatically switches SQLAlchemy to an in-memory SQLite database (`sqlite+aiosqlite:///:memory:`), so no running PostgreSQL server is required.

3. **Install dependencies via `uv`:**
   ```bash
   uv sync --dev
   ```

4. **Install pre-commit secret scanning hooks:**
   ```bash
   uv run pre-commit install
   ```

5. **Start the development server locally:**
   ```bash
   TESTING=1 uv run uvicorn app.main:app --reload --port 8080
   ```
   Open `http://localhost:8080` for the web dashboard or `http://localhost:8080/api/docs` for the interactive OpenAPI documentation.

---

## 3. Running Tests & CI Checks Locally

Before opening a Pull Request, run the exact verification suite executed by our GitHub Actions CI pipeline:

```bash
# 1. Bytecode & syntax verification across all modules
uv run python -m compileall -q app tests examples

# 2. Run the standalone quickstart verification script
uv run python examples/quickstart/demo_review.py

# 3. Execute the full async pytest suite with in-memory SQLite
TESTING=1 uv run pytest -v

# 4. Scan your staged commits for accidental secret leaks
gitleaks detect --source . --verbose
```

Our test suite covers:
- `tests/test_core.py`: AES-256-GCM encryption/decryption, webhook HMAC verification, and health endpoints.
- `tests/test_review_engine.py`: Diff line annotation, LLM payload parsing, and interactive `@patchpilot` commands.
- `tests/test_triage_secure.py`: Automated PR sizing (`size:XS`–`XL`), risk scoring, and pre-LLM secret redaction.
- `tests/test_vault_fallbacks.py`: Sequential multi-key failover (`401/402/403/429/5xx`), key masking, and promote-to-primary flows.
- `tests/test_wallet_shield.py`: Per-installation rate limiting, bot-loop prevention, and sender authorization checks.

---

## 4. Branch Architecture & Commit Conventions

### Branch Promotion Model
PatchPilot uses a structured three-tier branch promotion flow:
```text
[ Experimental / feat/* ]  --->  [ developement ]  --->  [ main ]
      (Feature Work)              (Integration)         (Production)
```
- Create feature or bugfix branches off `developement` (or `Experimental`):
  - `feat/<short-slug>` (e.g., `feat/anthropic-provider-preset`)
  - `fix/<short-slug>` (e.g., `fix/diff-hunk-line-offset`)
  - `docs/<short-slug>` (e.g., `docs/self-hosted-ollama-guide`)
  - `test/<short-slug>` (e.g., `test/secret-scanner-edge-cases`)
- Target **`developement`** as the base branch when opening a Pull Request.

### Conventional Commits
All commit messages must follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:
```text
<type>(<optional scope>): <concise imperative summary>
```
Examples:
- `feat(vault): add sequential fallback retry for HTTP 529 overloaded responses`
- `fix(security): mask Stripe live keys in pre-LLM diff sanitizer`
- `test(triage): add boundary tests for XL pull request classification`
- `docs(readme): add one-command Docker Compose quickstart`

---

## 5. Pull Request Submission Checklist

- [ ] My branch is up to date with `developement` and follows the branch naming convention.
- [ ] All existing and new tests pass locally via `TESTING=1 uv run pytest -v`.
- [ ] New features or bug fixes include corresponding unit tests in `tests/`.
- [ ] No real API keys, tokens, or `.env` files are included in the diff.
- [ ] `.env.example` and `README.md` are updated if any configuration variables changed.
