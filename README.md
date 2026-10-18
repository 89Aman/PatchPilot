<p align="center">
  <a href="https://patchpilot.noventra.org.in" target="_blank" rel="noopener noreferrer">
    <img src="patchpilot-brand/banners/github-social-1280x640.png" alt="PatchPilot - AI PR Reviewer" width="100%">
  </a>
</p>

<p align="center">
  <strong>Autonomous, self-hosted, Bring-Your-Own-Key (BYOK) GitHub App & AI Pull Request Review Engine.</strong>
</p>

<p align="center">
  <a href="https://patchpilot.noventra.org.in"><img src="https://img.shields.io/badge/Live_App-patchpilot.noventra.org.in-f97316?style=for-the-badge&logo=googlecloud&logoColor=white" alt="Live App"></a>
  <a href="https://patchpilot.noventra.org.in/docs"><img src="https://img.shields.io/badge/Docs-Interactive_Guide-0284c7?style=for-the-badge&logo=gitbook&logoColor=white" alt="Documentation"></a>
  <a href="#tests"><img src="https://img.shields.io/badge/Tests-20%20Passed-10b981?style=for-the-badge&logo=pytest&logoColor=white" alt="Tests"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.12%2B-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python Version"></a>
  <a href="https://fastapi.tiangolo.com"><img src="https://img.shields.io/badge/FastAPI-Modern_Async-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-8b5cf6?style=for-the-badge" alt="License"></a>
</p>

<p align="center">
  <a href="#overview">Overview</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#interactive-pr-commands">PR Commands</a> •
  <a href="#key-features">Key Features</a> •
  <a href="#supported-llm-providers">Providers</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#brand-assets">Brand Kit</a>
</p>

---

## Overview

**PatchPilot** automates GitHub pull request reviews with intelligent code analysis, line-accurate suggestions, and interactive in-PR commands. Bring your own API keys for **Google Gemini**, **OpenAI**, **xAI Grok**, **Groq Cloud**, **OpenRouter (Claude 3.7 / DeepSeek)**, or **local Ollama** — encrypted in your personal **AES-256-GCM** security vault.

Unlike rigid SaaS alternatives, PatchPilot is fully self-hostable, enforces zero-knowledge key security, respects repository scopes, and produces actionable inline GitHub review comments with one-click committable ` ```suggestion ` blocks.

---

## Architecture

```mermaid
flowchart TD
    subgraph GitHub ["GitHub Infrastructure"]
        PR["Pull Request Event / Comment"]
        App["GitHub App Webhook"]
        PR -->|Trigger| App
    end

    subgraph PatchPilot ["PatchPilot Engine (FastAPI + Async)"]
        Verify["HMAC-SHA256 Signature Validator"]
        Router["Webhook Dispatcher / Fast Route"]
        DiffParser["Smart Diff Parser & Noise Filter"]
        Vault["AES-256-GCM Security Vault"]
        PromptEngine["System Prompt & Schema Engine"]
        App -->|POST /webhook| Verify
        Verify --> Router
        Router --> DiffParser
        DiffParser --> PromptEngine
    end

    subgraph BYOK ["Multi-Provider LLM Gateway"]
        Vault -->|Decrypt In-Memory| BYOK
        PromptEngine --> BYOK
        BYOK --> Gemini["Google Gemini 1.5/2.0"]
        BYOK --> OpenAI["OpenAI GPT-4o"]
        BYOK --> Groq["Groq Llama 3.3"]
        BYOK --> xAI["xAI Grok"]
        BYOK --> OpenRouter["OpenRouter (Claude/DeepSeek)"]
        BYOK --> Ollama["Ollama (Self-Hosted)"]
    end

    subgraph GitHubOutput ["GitHub Review Delivery"]
        BYOK -->|Categorized Scorecard & Inline Diffs| OutputEngine["Review Publisher"]
        OutputEngine -->|POST /pulls/:id/reviews| GitHubPR["Inline PR Reviews + Suggestions"]
        OutputEngine -->|POST /comments/:id/replies| GitHubReplies["Interactive @patchpilot Replies"]
    end
```

---

## How It Works

1. **Sign In**: Authenticate via GitHub OAuth on the [PatchPilot Dashboard](https://patchpilot.noventra.org.in).
2. **Configure Your Vault**: Select your AI provider and enter your API key. Keys are encrypted at rest with AES-256-GCM using your master encryption key.
3. **Install the GitHub App**: Install the PatchPilot app on selected repositories or your entire organization.
4. **Get Automated Reviews**: Whenever a PR is opened or updated, PatchPilot analyzes the unified diff, filters out noise (lockfiles, vendor assets), and posts:
   - A top-level summary scorecard with counts for `🔴 Bugs`, `🟡 Risks`, and `🔵 Nits`.
   - Right-side inline comments attached to precise lines of changed code.
   - One-click committable suggestion blocks.

---

## Interactive PR Commands

Mention PatchPilot in any GitHub PR review comment or issue thread to trigger on-demand actions:

| Command | Action | Example |
| :--- | :--- | :--- |
| `@patchpilot explain` | Explains the selected code snippet, logic flow, and architectural trade-offs | `@patchpilot explain how this token refresh logic works` |
| `@patchpilot fix` | Analyzes the problem and posts a committable GitHub ` ```suggestion ` block | `@patchpilot fix handle the potential NoneType error here` |

> [!TIP]
> Custom bot aliases like `@patchpilot-bot`, `/patchpilot`, or `/lens` are also recognized automatically.

---

## Key Features

- **🛡️ Bring Your Own Key (BYOK)**: Zero markup. No shared token pool. Plug in keys from Google Gemini, OpenAI, Groq, xAI Grok, OpenRouter, or self-hosted Ollama.
- **🔐 Isolated AES-256-GCM Vault**: Encrypted using Python's `cryptography` library with 12-byte random nonces. Vault records are strictly isolated by GitHub user ID. Plaintext credentials are never logged or exposed.
- **🎯 Accurate Inline PR Reviews**: Comments are anchored to exact line positions in the diff rather than dumped into a generic issue comment.
- **⚡ One-Click Committable Suggestions**: Reviews include GitHub suggestion blocks (` ```suggestion `) that developers can commit directly from the GitHub UI.
- **🧹 Noise Filtering**: Automatically ignores lockfiles (`poetry.lock`, `uv.lock`, `package-lock.json`, `yarn.lock`), minified assets, and test fixtures to optimize token usage.
- **🌓 Modern Dark-Mode Dashboard**: View active repository scopes, manage encrypted provider keys, test model connections in real time, and adjust review policies.

---

## Supported LLM Providers

| Provider | Endpoint / Protocol | Recommended Models |
| :--- | :--- | :--- |
| **Google Gemini** | Google AI Studio REST API | `gemini-2.0-flash`, `gemini-1.5-pro`, `gemini-1.5-flash` |
| **OpenAI** | OpenAI API | `gpt-4o`, `gpt-4o-mini`, `o3-mini` |
| **Groq Cloud** | Groq Ultra-Fast Inference | `llama-3.3-70b-versatile`, `mixtral-8x7b-32768` |
| **xAI Grok** | xAI API | `grok-2-1212`, `grok-beta` |
| **OpenRouter** | OpenRouter Unified Gateway | `anthropic/claude-3.7-sonnet`, `deepseek/deepseek-r1` |
| **Ollama** | OpenAI-Compatible `/v1` | `deepseek-r1:14b`, `qwen2.5-coder:32b`, `llama3.3:latest` |

---

## Quick Start

### 1. Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- Docker & Docker Compose (optional for containerized deployment)

### 2. Clone & Configure Environment

```bash
git clone https://github.com/89Aman/PatchPilot.git
cd PatchPilot

cp .env.example .env
```

Edit `.env` with your GitHub App credentials and encryption secret:

```ini
# Generate with: openssl rand -hex 32
MASTER_ENCRYPTION_KEY="your-64-char-hex-master-encryption-key"
ADMIN_API_TOKEN="your-secure-admin-token"

# GitHub App Settings
GITHUB_APP_ID="123456"
GITHUB_WEBHOOK_SECRET="your_webhook_secret"
GITHUB_CLIENT_ID="Iv1..."
GITHUB_CLIENT_SECRET="your_client_secret"
GITHUB_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n..."
```

### 3. Run Locally with `uv`

```bash
# Sync dependencies
uv sync

# Run the 20-test automated test suite
uv run pytest

# Launch the development server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

The web dashboard is now live at `http://localhost:8080` (or `https://patchpilot.noventra.org.in` in production).

### 4. Run with Docker Compose

```bash
docker compose up -d --build
```

---

## Production Deployment (Google Cloud Run)

PatchPilot includes a production-ready, multistage [`Dockerfile`](Dockerfile):

```bash
# Build and deploy to Google Cloud Run
gcloud run deploy patchpilot \
  --source . \
  --region europe-west1 \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --set-env-vars MASTER_ENCRYPTION_KEY="...",GITHUB_APP_ID="..."
```

Map custom domain with Cloud Run:
- Domain: `patchpilot.noventra.org.in`
- CNAME Target: `ghs.googlehosted.com` (Cloudflare set to **DNS only**)

---

## Brand Assets

Official media, banners, and icons are available in the [`patchpilot-brand/`](patchpilot-brand/) directory:

<p align="center">
  <img src="patchpilot-brand/preview-sheet.png" alt="PatchPilot Brand Assets Preview" width="100%">
</p>

| Category | File | Description |
| :--- | :--- | :--- |
| **Banners** | [`banners/github-social-1280x640.png`](patchpilot-brand/banners/github-social-1280x640.png) | GitHub Social Preview Header (1280x640) |
| | [`banners/og-image-1200x630.png`](patchpilot-brand/banners/og-image-1200x630.png) | OpenGraph Metadata Image (1200x630) |
| | [`banners/twitter-header-1500x500.png`](patchpilot-brand/banners/twitter-header-1500x500.png) | X / Twitter Header (1500x500) |
| | [`banners/wide-1920x1080.png`](patchpilot-brand/banners/wide-1920x1080.png) | 16:9 Presentation Wallpaper (1920x1080) |
| **Transparent** | [`transparent/icon.png`](patchpilot-brand/transparent/icon.png) | Isolated Rounded App Icon (PNG) |
| | [`transparent/logo-horizontal-white.png`](patchpilot-brand/transparent/logo-horizontal-white.png) | Full Horizontal Wordmark (White text) |
| | [`transparent/wordmark-white.png`](patchpilot-brand/transparent/wordmark-white.png) | Clean Typography Wordmark (White) |
| **Icons** | [`icons/favicon-512.png`](patchpilot-brand/icons/favicon-512.png) | High-Resolution App Icon (512x512) |
| | [`icons/favicon.ico`](patchpilot-brand/icons/favicon.ico) | Multi-size Browser Favicon |

---

## Contributing

Contributions, issues, and feature requests are welcome!

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'feat: Add AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for details.

<p align="center">
  Crafted with care by <a href="https://github.com/89Aman"><strong>89Aman</strong></a> for developer communities worldwide 🚀
</p>
