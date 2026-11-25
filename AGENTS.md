# Agent Guidelines & Operational Rules

## Git & Deployment Rules
1. **Never Push Directly**: Never run `git push` (especially to `main`) automatically or bypass branch protection rules.
2. **Review First**: Always inspect and present `git diff` to the user for explicit review before proceeding.
3. **Require User Approval**: Await explicit user review and consent before pushing any code, creating remote commits, or merging.
4. **Local Verification Only**: Run tests locally (`uv run pytest`) and verify correctness, but leave pushing to explicit user instruction.

## Karpathy-style engineering loop

For every change, state the intended behavior and a check that would disprove it. Inspect the normal entry point and reproduce the issue with the smallest relevant test or observation. Make a minimal, readable change, run the same check again, and iterate on any failure. Run related integration checks before calling the change complete. Report the evidence and identify anything that could only be verified in a different environment.
