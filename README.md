<p align="center">
  <a href="https://patchpilot.noventra.org.in" target="_blank" rel="noopener noreferrer">
    <img src="patchpilot-brand/banners/linkedin-banner-1584x396.png" alt="PatchPilot - AI PR Reviewer" width="100%">
  </a>
</p>

<h1 align="center">PatchPilot</h1>

<p align="center">
  <strong>Automate line-accurate AI pull request reviews on GitHub using your own LLM keys or local Ollama.</strong>
</p>

<p align="center">
  <a href="https://github.com/89Aman/PatchPilot/actions/workflows/ci.yml"><img src="https://github.com/89Aman/PatchPilot/actions/workflows/ci.yml/badge.svg" alt="CI Status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-f59e0b.svg" alt="SPDX License: MIT"></a>
  <a href="https://codespaces.new/89Aman/PatchPilot"><img src="https://img.shields.io/badge/Codespaces-Open_Sandbox-24292f?logo=github" alt="Open in GitHub Codespaces"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.12%20%7C%203.13-3776ab?logo=python&logoColor=white" alt="Python 3.12 | 3.13"></a>
</p>

<p align="center">
  <a href="https://patchpilot.noventra.org.in"><img src="https://img.shields.io/badge/Launch_App-patchpilot.noventra.org.in-f97316?style=for-the-badge&logo=googlecloud&logoColor=white" alt="Launch App"></a>
  <a href="https://patchpilot.noventra.org.in/docs"><img src="https://img.shields.io/badge/User_Guide-Documentation-0284c7?style=for-the-badge&logo=gitbook&logoColor=white" alt="User Guide"></a>
  <a href="#supported-ai-providers"><img src="https://img.shields.io/badge/BYOK-Multi--Provider-10b981?style=for-the-badge&logo=openai&logoColor=white" alt="Multi-Provider"></a>
  <a href="#privacy--security"><img src="https://img.shields.io/badge/Vault-AES--256--GCM-8b5cf6?style=for-the-badge&logo=lock&logoColor=white" alt="Security Vault"></a>
</p>

<p align="center">
  <a href="#why-patchpilot">Why PatchPilot</a> •
  <a href="#quickstart">Quickstart</a> •
  <a href="#getting-started-in-3-steps">Cloud Setup</a> •
  <a href="#how-it-works-in-your-prs">In Your PRs</a> •
  <a href="#interactive-chat-commands">PR Commands</a> •
  <a href="#supported-ai-providers">Supported Models</a> •
  <a href="#official-free-tier-models-catalog">Free Models</a> •
  <a href="#sequential-multi-key-failover-pipeline">Multi-Key Failover</a> •
  <a href="#privacy--security">Security & Privacy</a>
</p>

> [!TIP]
> **Evaluate Without Installing Locally:** Try the hosted web dashboard immediately at **[patchpilot.noventra.org.in](https://patchpilot.noventra.org.in)** to connect your GitHub repositories in under two minutes. Prefer an isolated cloud container? Click **[Open in GitHub Codespaces](https://codespaces.new/89Aman/PatchPilot)** to launch a pre-configured development sandbox with the in-memory test suite ready to run.

---

## Why PatchPilot?

Most AI code review tools charge expensive monthly subscriptions with strict token limits and markups. **PatchPilot** changes that:

- 💸 **Bring Your Own Key (BYOK)**: Use your existing API keys from Google Gemini, OpenAI, Groq, xAI, OpenRouter, or your own local Ollama instance. Pay raw inference cost—often pennies per month.
- 🔄 **Sequential Multi-Key Failover**: Automatically failover to secondary or tertiary backup models on rate limits (`429`), quota exhaustion (`401/402/403`), or provider outages (`5xx`).
- 👁️ **Persistent Masked Key Visibility**: Always see which keys and models are configured in your vault with secure masked previews (`gsk_9x••••••••a81b`).
- 🎯 **Line-Accurate Feedback**: Inline comments placed directly on the exact lines of changed code, not buried in generic pull request threads.
- ⚡ **One-Click Committable Fixes**: Review suggestions format directly as GitHub ```` ```suggestion ```` blocks. Accept code fixes with a single click.
- 💬 **Interactive Bot in PR Comments**: Ask `@patchpilot explain` or `@patchpilot fix` directly inside any review comment thread.
- 🛡️ **Zero-Knowledge Security Vault & Pre-LLM Secret Scanner**: Your API keys are encrypted at rest with **AES-256-GCM**, and added diff lines are scanned and redacted for leaked secrets in memory before calling any external LLM.

---

## Quickstart

Clone, configure, and launch the full **PatchPilot** stack (FastAPI + PostgreSQL 16) in a single command:

```bash
git clone https://github.com/89Aman/PatchPilot.git && cd PatchPilot && cp .env.example .env && docker compose up --build -d
```

Expected output from a healthy first run (or when running `uv run python examples/quickstart/demo_review.py`):

```text
✔ Network patchpilot_default  Created
✔ Container contextlens-db    Healthy
✔ Container contextlens-api   Started
INFO:     Initializing PatchPilot database schema...
INFO:     PatchPilot database initialized.
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
Vault AES-256-GCM Status : Verified (92 bytes encrypted)
PR Triage Classification : size:XS | risk:critical (Score: 95/100)
Pre-LLM Secret Scanner   : 1 leak blocked (OpenAI API Key)
```

<details>
<summary><strong>Manual / Source Installation (uv & In-Memory SQLite)</strong></summary>

For local development or evaluation without Docker or PostgreSQL, use [uv](https://docs.astral.sh/uv/) with `TESTING=1` (in-memory SQLite mode):

```bash
# 1. Clone the repository and copy environment defaults
git clone https://github.com/89Aman/PatchPilot.git
cd PatchPilot
cp .env.example .env

# 2. Install Python 3.12+ dependencies via uv
uv sync --dev

# 3. Run the standalone 27-line quickstart pipeline (Vault + Secret Scan + Triage)
uv run python examples/quickstart/demo_review.py

# 4. Run the automated test suite (in-memory SQLite)
TESTING=1 uv run pytest -v

# 5. Start the FastAPI server with hot reload on http://localhost:8080
TESTING=1 uv run uvicorn app.main:app --reload --port 8080
```

</details>

---

## Getting Started in 3 Steps

You can set up the hosted PatchPilot app on your repositories in under 2 minutes:

### 1. Sign In to the Dashboard
Visit **[patchpilot.noventra.org.in](https://patchpilot.noventra.org.in)** and click **Sign in with GitHub**.

### 2. Configure Your AI Provider Vault
- Select your preferred provider (e.g. Google Gemini, OpenAI, Groq, OpenRouter).
- Paste your API key and choose your target model (e.g. `gemini-2.5-flash`, `gpt-4o`, `llama-3.3-70b-versatile`).
- Click **Save Key to Vault** & test your connection with a single click.

### 3. Install the GitHub App on Your Repos
- Click **Install GitHub App** on your dashboard.
- Choose whether to grant access to all repositories or only specific projects.

🎉 **You are all set!** Whenever you open or update a Pull Request, PatchPilot will review your changes automatically.

---

## How It Works in Your PRs

```mermaid
flowchart LR
    Dev["Developer Opens PR"] --> Webhook["PatchPilot GitHub App"]
    Webhook --> Scanner["Pre-LLM Secret Scanner & Triage"]
    Scanner --> Review["AI Code Review Engine"]
    Review --> Scorecard["Top Scorecard Summary\n(🔴 Bugs, 🟡 Risks, 🔵 Nits)"]
    Review --> Inline["Line-by-Line Annotations"]
    Inline --> Suggestion["One-Click Committable Fix"]
```

When you open a PR, PatchPilot provides:

1. **Top-Level Scorecard**: A clear summary showing review status, PR size/risk triage labels, and defect breakdown:
   - 🔴 **Bugs**: Potential null pointers, uncaught exceptions, and logical defects.
   - 🟡 **Risks**: Security gaps, performance bottlenecks, and concurrency concerns.
   - 🔵 **Nits**: Style improvements, naming clarity, and idiomatic practices.
2. **Inline Annotations**: Detailed feedback attached directly to relevant lines in the unified diff.
3. **Commit Suggestion Blocks**: Ready-to-merge patches formatted in standard GitHub suggestion blocks.

---

## Interactive Chat Commands

Have questions about a piece of code or want an alternative implementation? Mention PatchPilot in any PR review comment thread:

| Command | What it does | Example |
| :--- | :--- | :--- |
| `@patchpilot explain` | Breaks down logic, context, potential pitfalls, and edge cases | `@patchpilot explain how this token refresh logic handles expiration` |
| `@patchpilot fix` | Analyzes requested change and replies with a committable code suggestion | `@patchpilot fix handle the None case gracefully` |
| `@patchpilot review security` | Runs a targeted security audit on the PR diff | `@patchpilot review security` |
| `@patchpilot test` | Generates unit tests covering the modified functions | `@patchpilot generate tests for the fallback loop` |

> [!TIP]
> You can also use `@patchpilot-bot`, `/patchpilot`, or `/lens` as interchangeable aliases.

---

## Supported AI Providers

Plug in any of the following providers using your personal API key:

| Provider | Why Choose It | Recommended Models |
| :--- | :--- | :--- |
| **Google Gemini** | Generous free tier, ultra-fast latency, massive context window | `gemini-2.5-flash`, `gemini-2.5-pro` |
| **OpenAI** | Industry-standard reasoning and code synthesis | `gpt-4o`, `gpt-4o-mini`, `o3-mini` |
| **Groq Cloud** | Near-instant inference speed (~500+ tokens/sec) | `llama-3.3-70b-versatile`, `qwen-2.5-coder-32b` |
| **OpenRouter** | Access Claude 3.7 Sonnet, DeepSeek R1, and 100+ top models | `deepseek/deepseek-r1:free`, `meta-llama/llama-3.3-70b-instruct:free` |
| **xAI Grok** | Cutting-edge code comprehension and reasoning | `grok-2-1212`, `grok-beta` |
| **Ollama (Self-Hosted)** | 100% private and offline on your own GPU/workstation | `qwen2.5-coder:14b`, `deepseek-r1:14b` |

---

### Official Free-Tier Models Catalog

Zero-cost automated PR reviews with generous free quotas and 1-click dashboard presets:

| Provider | Model Identifier | Free Quota / Rate Limits | Get Free API Key |
| :--- | :--- | :--- | :--- |
| **Google AI Studio** | `gemini-2.5-flash` | 15 RPM, 1,000,000 TPM, 1,500 RPD | [Google AI Studio](https://aistudio.google.com/app/apikey) |
| **Google AI Studio** | `gemini-2.5-pro` | Deep reasoning, 2M context window | [Google AI Studio](https://aistudio.google.com/app/apikey) |
| **Groq Cloud** | `llama-3.3-70b-versatile` | 30 RPM, 6,000 TPM, ~300+ tok/s | [Groq Console](https://console.groq.com/keys) |
| **Groq Cloud** | `qwen-2.5-coder-32b` | Specialist coding architecture | [Groq Console](https://console.groq.com/keys) |
| **OpenRouter** | `deepseek/deepseek-r1:free` | 100% free community reasoning | [OpenRouter](https://openrouter.ai/keys) |
| **OpenRouter** | `meta-llama/llama-3.3-70b-instruct:free` | Flagship open weights | [OpenRouter](https://openrouter.ai/keys) |
| **Ollama** | `qwen2.5-coder:14b` | Unlimited local self-hosted | [Ollama Download](https://ollama.com) |

---

### Sequential Multi-Key Failover Pipeline

Never let a rate limit or upstream outage block your PR reviews:

1. **Persistent Key Visibility**: View saved keys at any time via safe masked previews (`gsk_9x••••••••a81b` or `ol••••••••` for local endpoints). Plaintext secrets are never stored unencrypted.
2. **Automatic Sequential Failover**: When your primary provider hits HTTP `401`, `402` (free quota exhausted), `403`, `404`, `429` (rate limit), `500/502/503/529`, or a network timeout, PatchPilot immediately tries your secondary and tertiary fallbacks in order.
3. **Sanitized PR Alerts**: If a fallback is used, the PR summary card alerts the team with a sanitized badge (e.g. `⚡ Failover Alert: Primary LLM returned Rate limit exceeded (429). Review successfully completed via fallback: Groq (llama-3.3-70b-versatile)`) without exposing raw request URLs or credentials.
4. **1-Click Promote-to-Primary**: Instantly swap any fallback to primary with a single click. The previous primary is automatically demoted into your fallback inventory.

---

## Privacy & Security

Your code and API credentials deserve complete privacy:

- 🔒 **AES-256-GCM Encryption**: Credentials are encrypted at rest using individual 12-byte initialization vectors (nonces) and a 256-bit master key.
- 🚫 **Zero Plaintext Exposure**: Plaintext API keys are never stored in databases, never rendered back to user browsers, and never output in server logs.
- 👥 **Strict Per-User Isolation**: Vault configurations are strictly bound to your authenticated GitHub account. No other user or repository can use or see your credentials.
- 📦 **No Data Harvesting**: Your source code is never used to train machine learning models. Diffs are processed strictly in ephemeral memory during review generation.

---

## Help, Brand Assets & Documentation

- 🌐 **Live Web Application**: [https://patchpilot.noventra.org.in](https://patchpilot.noventra.org.in)
- 📖 **Interactive User Guide**: [https://patchpilot.noventra.org.in/docs](https://patchpilot.noventra.org.in/docs)
- 🎨 **Brand Assets**: [`patchpilot-brand/`](patchpilot-brand/)
- 🤝 **Contributing Guide**: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- 📋 **Changelog**: [`CHANGELOG.md`](CHANGELOG.md)
- 🐛 **Issue Tracker**: [GitHub Issues](https://github.com/89Aman/PatchPilot/issues)

---

## License

Licensed under the **[MIT License](LICENSE)** — you are free to use, modify, distribute, and self-host PatchPilot for personal or commercial use provided the original copyright and license notice are retained.

<p align="center">
  Crafted with care by <a href="https://github.com/89Aman"><strong>89Aman</strong></a> for developers and open-source teams ❤️
</p>
