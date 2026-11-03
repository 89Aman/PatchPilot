# Changelog

All notable changes to **PatchPilot** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] - 2026-11-03

### Added
- **Sequential Multi-Key Failover Pipeline (`app/llm.py`, `app/db.py`):** Configure primary, secondary, and tertiary fallback AI providers per account. Automatically fails over on HTTP `401`, `402`, `403`, `404`, `429`, `500/502/503/529`, and network timeouts, appending a sanitized failover notice to the PR review scorecard.
- **Persistent Masked Key Visibility & 1-Click Promotion (`app/main.py`, `app/templates/settings.html`):** Display safe masked previews (`gsk_9x••••••••a81b`) of saved vault keys and allow 1-click promotion of any fallback model to primary.
- **Official Free-Tier Models Catalog (`app/models_catalog.py`):** Built-in 1-click presets for zero-cost code review models across Google AI Studio (`gemini-2.5-flash`, `gemini-2.5-pro`), Groq (`llama-3.3-70b-versatile`, `qwen-2.5-coder-32b`), OpenRouter (`deepseek/deepseek-r1:free`, `meta-llama/llama-3.3-70b-instruct:free`), and local Ollama (`qwen2.5-coder:14b`).
- **Pre-LLM Secret Scanner & Diff Sanitizer (`app/security.py`):** In-memory regex scanner that detects and masks AWS, GitHub PAT, OpenAI, Google Cloud, Slack, and PEM private keys in added diff lines before sending code to external LLMs.
- **Automated PR Triage Engine (`app/triage.py`):** Classifies PRs by size (`size:XS` through `size:XL`), computes risk scores (`0–100` across `risk:low` to `risk:critical`), detects sensitive impact areas (Auth, Database, Infra, Billing), and applies GitHub labels automatically.
- **Wallet Shield & Sender Authorization (`app/security.py`, `tests/test_wallet_shield.py`):** Per-installation sliding-window rate limiter, bot-loop prevention, and GitHub `author_association` verification for interactive PR commands.
- **In-Memory SQLite Test Mode (`app/db.py`, `tests/conftest.py`):** Zero-dependency test execution via `TESTING=1 uv run pytest` using `sqlite+aiosqlite:///:memory:`.
- **Cloud Run Deployment & Release Scaffolding (`cloudbuild.yaml`, `.devcontainer/`, `examples/quickstart/`):** Automated Google Cloud Build pipeline, GitHub Codespaces sandbox configuration, and standalone runnable quickstart example.

### Changed
- **Clean PR Approval Flow (`app/main.py`):** Automatically submits a GitHub `APPROVE` review verdict when a PR passes with zero critical bugs or security findings.
- **Bot Mention Regex (`app/main.py`):** Updated interactive command parser to match `@patchpilot[bot]` and `@contextlens[bot]` webhook payloads accurately.

### Fixed
- **CI Database Initialization (`app/db.py`):** Resolved PostgreSQL driver requirement during automated CI runs by switching to `StaticPool` in-memory SQLite when `TESTING=1` or `pytest` is active.

---

## [0.1.0] - 2026-10-15

### Added
- **Initial FastAPI GitHub App & Webhook Engine (`app/main.py`, `app/github_auth.py`):** HMAC-SHA256 webhook verification (`X-Hub-Signature-256`), RS256 GitHub App JWT signing, installation token minting, and unified diff fetching.
- **AES-256-GCM Encrypted BYOK Vault (`app/crypto.py`, `app/db.py`):** Per-owner encrypted credential storage with unique 96-bit nonces and custom markdown `review_policy` support.
- **Multi-Provider LLM Review Engine (`app/llm.py`):** Support for Google Gemini, OpenAI, Groq, OpenRouter, xAI Grok, and self-hosted Ollama endpoints with line-accurate diff annotations and committable ```` ```suggestion ```` blocks.
- **Interactive PR Chat Commands:** Support for `@patchpilot explain` and `@patchpilot fix` inside pull request review comment threads.
- **Web Dashboard & Auth Flows (`app/ui.py`, `app/clerk_auth.py`):** Self-service web UI with GitHub OAuth and Clerk authentication, interactive `/docs` guide, Privacy Policy, and Terms of Service pages.
