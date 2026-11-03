## Summary of Changes
<!-- Provide a concise description of what this PR changes and why. -->

- 
- 

## Linked Issue
<!-- Link the issue this PR resolves (e.g., Closes #12) -->
Closes #

## Type of Change
- [ ] `feat`: New feature or provider capability
- [ ] `fix`: Bug fix
- [ ] `security`: Cryptographic vault, secret scanner, or auth hardening
- [ ] `refactor`: Code improvement with no behavioral change
- [ ] `docs`: Documentation or example updates
- [ ] `test`: Additional unit or integration tests

## Documentation & Configuration Updates
- [ ] Documentation (`README.md` / `CONTRIBUTING.md` / `/docs`) updated to reflect changes
- [ ] `.env.example` updated if any environment variables were added or modified
- [ ] No documentation changes required

## Verification & Test Confirmation
- [ ] `TESTING=1 uv run pytest -v` passes locally with zero failures
- [ ] `uv run python examples/quickstart/demo_review.py` executes cleanly
- [ ] Verified zero secrets or credentials in diff (`gitleaks detect --source .`)
