# Agent Guidelines & Operational Rules

## Git & Deployment Rules
1. **Never Push Directly**: Never run `git push` (especially to `main`) automatically or bypass branch protection rules.
2. **Review First**: Always inspect and present `git diff` to the user for explicit review before proceeding.
3. **Require User Approval**: Await explicit user review and consent before pushing any code, creating remote commits, or merging.
4. **Local Verification Only**: Run tests locally (`uv run pytest`) and verify correctness, but leave pushing to explicit user instruction.
