"""Minimal runnable quickstart demonstrating PatchPilot PR triage and pre-LLM secret redaction."""

from app.security import sanitize_diff_text, scan_diff_for_secrets
from app.triage import compute_pr_triage

SAMPLE_DIFF = """--- a/app/auth.py
+++ b/app/auth.py
@@ -10,2 +10,4 @@
 def verify_token(token: str) -> bool:
+    backup_key = "sk-proj-1234567890abcdef1234567890abcdef12345678"
+    return token is not None
"""

if __name__ == "__main__":
    secrets_found = scan_diff_for_secrets(SAMPLE_DIFF)
    triage = compute_pr_triage(
        files_changed=["app/auth.py"],
        additions=2,
        deletions=0,
        has_secret_findings=bool(secrets_found),
    )
    safe_diff = sanitize_diff_text(SAMPLE_DIFF, secrets_found)

    print(f"PR Triage Classification : {triage.size_tier} | {triage.risk_level} (Score: {triage.risk_score}/100)")
    print(f"Sensitive Impact Areas   : {', '.join(triage.impact_areas)}")
    print(f"Pre-LLM Secret Scanner   : {len(secrets_found)} leak blocked ({secrets_found[0].secret_type})")
    print(f"Sanitized Diff Preview   :\n{safe_diff.strip()}")
