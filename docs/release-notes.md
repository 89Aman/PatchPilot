# GitHub Release Announcement — `v0.2.0`

**Release Tag:** `v0.2.0`
**Release Title:** `PatchPilot v0.2.0 — Sequential Multi-Key Failover, Pre-LLM Secret Scanning & Free Models Catalog`
**Target Branch:** `main`

---

## 🚀 Overview

**PatchPilot `v0.2.0`** is our first public open-source release! PatchPilot is a self-hosted, Bring-Your-Own-Key (BYOK) GitHub App and AI Pull Request review engine built with Python 3.12 and FastAPI. It delivers line-pinned code reviews, one-click committable GitHub suggestion blocks, automated PR risk triage, and pre-LLM secret redaction using your own API keys or local Ollama models.

---

## ✨ Key Features in `v0.2.0`

- 🔄 **Sequential Multi-Key Failover Pipeline:** Chain a primary LLM with secondary and tertiary backup keys across Google Gemini, OpenAI, Groq, OpenRouter, xAI, and Ollama. If your primary key hits a `429` rate limit, `402` quota exhaustion, or `5xx` outage, PatchPilot automatically fails over and completes the review without dropping the webhook.
- 🛡️ **Pre-LLM Secret Scanner & Sanitizer:** Added diff lines are scanned in memory for exposed AWS keys, GitHub PATs, OpenAI/GCP tokens, Slack webhooks, and RSA private keys—redacting secrets *before* the diff ever leaves your server.
- 🏷️ **Automated PR Sizing & Risk Triage:** Automatically calculates PR size (`size:XS` to `size:XL`), scores regression and security risk (`0–100`), flags sensitive paths (`Auth & Cryptography`, `Database & Migrations`, `Infrastructure`), and applies GitHub labels.
- 🆓 **Official Free-Tier Models Catalog:** 1-click dashboard presets for zero-cost review models (`gemini-2.5-flash`, `llama-3.3-70b-versatile`, `qwen-2.5-coder-32b`, `deepseek/deepseek-r1:free`, and local `qwen2.5-coder:14b`).
- 🔒 **AES-256-GCM Encrypted Vault with Masked Previews:** View which keys are active via safe masked strings (`gsk_9x••••••••a81b`), test connections in one click, and promote any backup key to primary.
- 💬 **Interactive PR Commands & Wallet Shield:** Run `@patchpilot explain`, `@patchpilot fix`, or `@patchpilot review security` in any PR thread, protected by sender permission checks and per-installation rate limiting.

---

## ⚠️ Breaking Changes

- **`VaultConfig` Schema Addition (`fallbacks_json`):** A new nullable `fallbacks_json` column was added to `vault_configs`. On startup, `init_db()` in `app/db.py` automatically runs an idempotent `ALTER TABLE` migration for existing PostgreSQL and SQLite databases—no manual SQL intervention is required.

---

## ⚡ One-Line Installation & Quickstart

```bash
git clone https://github.com/89Aman/PatchPilot.git && cd PatchPilot && cp .env.example .env && docker compose up --build -d
```

Or run the zero-dependency local verification suite directly with `uv`:

```bash
uv sync --dev && TESTING=1 uv run pytest
```

---

## 🙏 Acknowledgments

A huge thank you to [@89Aman](https://github.com/89Aman) and early testers who audited the webhook security boundary, multi-key failover behavior, and prompt formatting across real-world pull requests.

**Full Changelog:** [`CHANGELOG.md`](https://github.com/89Aman/PatchPilot/blob/main/CHANGELOG.md)
