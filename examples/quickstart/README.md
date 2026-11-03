# PatchPilot Quickstart Example

Run the standalone quickstart script (no external database or API keys required):

```bash
uv run python examples/quickstart/demo_review.py
```

This 27-line script executes the core local pipeline:
1. **AES-256-GCM Vault Round-Trip**: Encrypts and verifies a mock provider API key in memory.
2. **Pre-LLM Secret Scanner**: Detects and masks a leaked `sk-proj-...` credential inside a unified diff before any external LLM request is made.
3. **Automated PR Triage**: Computes size tier (`size:XS`), risk level (`risk:critical`), and impact area (`Auth & Cryptography`).
4. **Line-Accurate Diff Annotation**: Formats the sanitized diff with right-side line numbers for inline GitHub review comments.
