<p align="center">
  <img src="app/static/logo-text.png" alt="PatchPilot" width="280">
</p>

<p align="center">
  <strong>Self-hosted, multi-provider Bring-Your-Own-Key (BYOK) GitHub App & AI Pull Request review engine.</strong>
</p>

<p align="center">
  <a href="#how-it-works">How It Works</a> •
  <a href="#interactive-pr-commands">PR Commands</a> •
  <a href="#key-features">Key Features</a> •
  <a href="#quick-start">Quick Start</a>
</p>

---

**PatchPilot** automates GitHub pull request reviews with intelligent code analysis, line-accurate suggestions, and interactive chat commands. Bring your own API keys for Gemini, OpenAI, Anthropic (via OpenRouter), Groq, xAI Grok, or local Ollama — encrypted in your personal security vault.

---

## How It Works

1. **Sign In**: Authenticate using your GitHub account on the PatchPilot dashboard.
2. **Configure Your Vault**: Choose your preferred AI model provider and save your API key. Keys are encrypted at rest with AES-256-GCM.
3. **Install the GitHub App**: Grant access to your repositories.
4. **Get Automated Reviews**: When you open or update a PR, PatchPilot analyzes the git diff and leaves a categorized scorecard (`🔴 Bugs`, `🟡 Risks`, `🔵 Nits`) with one-click committable suggestions.

---

## Interactive PR Commands

Mention PatchPilot in any PR review comment thread:

| Command | Action | Example |
| :--- | :--- | :--- |
| `@patchpilot explain` | Explains the selected code lines, context, and rationale | `@patchpilot explain how this error handler works` |
| `@patchpilot fix` | Generates a committable GitHub code suggestion to resolve issues | `@patchpilot fix handle the null case here` |

> *Note: Mentions with custom bot suffixes like `@patchpilot-bot` or `@patchpilot-dev` are also supported.*

---

## Key Features

- **Bring Your Own Key (BYOK)**: Connect your own LLM accounts. Supported providers include Google Gemini, OpenAI (GPT-4o), OpenRouter (Claude 3.7, DeepSeek), Groq Cloud, xAI Grok, and local/tunneled Ollama instances.
- **Isolated AES-256-GCM Security Vault**: Your API keys and credentials are encrypted with 12-byte random nonces and isolated per GitHub account. Plaintext keys are never exposed in responses or logs.
- **Actionable PR Scorecards**: Instant reviews with summary badges and right-side line-accurate inline review comments.
- **One-Click Committable Suggestions**: Reviews include GitHub suggestion blocks (` ```suggestion `) that can be committed directly within GitHub.
- **Modern Dark-Mode Dashboard**: View repository scopes, manage encrypted provider keys, test model connections in real time, and adjust review policies.

---

## Quick Start

### 1. Configure Environment
Copy `.env.example` to `.env` and configure your credentials:
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

<p align="center">
  Made with lots of love by <a href="https://github.com/89Aman"><strong>89Aman</strong></a> for devs ❤️
</p>
