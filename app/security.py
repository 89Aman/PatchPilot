"""PatchPilot Security Engine: Pre-LLM secret scanning, entropy analysis, and SAST detection."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretFinding:
    secret_type: str
    file_path: str
    line_number: int | None
    matched_text_masked: str
    raw_snippet: str
    severity: str = "CRITICAL"


# Common high-risk secret patterns
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key ID", re.compile(r"\b(?:AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b")),
    ("GitHub Personal Access Token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,255}\b")),
    ("GitHub Fine-Grained Token", re.compile(r"\bgithub_pat_[a-zA-Z0-9]{22}_[a-zA-Z0-9]{59}\b")),
    ("OpenAI API Key", re.compile(r"\bsk-(?:proj-|admin-|svcacct-)?[a-zA-Z0-9_\-]{32,128}\b")),
    ("Google Cloud API Key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("Slack API Token", re.compile(r"\bxox[baprs]-[0-9a-zA-Z]{10,48}\b")),
    ("Private Encryption Key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
]


def mask_secret(secret: str) -> str:
    """Mask sensitive string leaving only edge characters visible."""
    s = secret.strip()
    if len(s) <= 8:
        return "*" * len(s)
    prefix_len = min(4, len(s) // 4)
    suffix_len = min(2, len(s) // 6)
    masked_middle = "*" * (len(s) - prefix_len - suffix_len)
    return f"{s[:prefix_len]}{masked_middle}{s[-suffix_len:]}"


def scan_diff_for_secrets(raw_diff: str) -> list[SecretFinding]:
    """Scan newly added diff lines in memory for exposed secrets and tokens."""
    findings: list[SecretFinding] = []
    current_file = "unknown"
    current_line = 0

    hunk_regex = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for line in raw_diff.splitlines():
        # Track active target file
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            continue
        elif line.startswith("+++ "):
            current_file = line[4:].strip()
            continue

        # Track diff line numbers
        hunk_match = hunk_regex.match(line)
        if hunk_match:
            try:
                current_line = int(hunk_match.group(1)) - 1
            except ValueError:
                current_line = 0
            continue

        # Only inspect added lines
        if line.startswith("+") and not line.startswith("+++"):
            current_line += 1
            code_line = line[1:]

            # Exclude obvious documentation / dummy tests / examples
            lower_line = code_line.lower()
            if any(dummy in lower_line for dummy in ["example", "dummy", "fake", "placeholder", "your_api_key_here"]):
                continue

            for secret_name, pattern in SECRET_PATTERNS:
                match = pattern.search(code_line)
                if match:
                    raw_secret = match.group(0)
                    masked = mask_secret(raw_secret)
                    findings.append(
                        SecretFinding(
                            secret_type=secret_name,
                            file_path=current_file,
                            line_number=current_line,
                            matched_text_masked=masked,
                            raw_snippet=code_line.strip(),
                        )
                    )
        elif not line.startswith("-"):
            current_line += 1

    return findings


def sanitize_diff_text(diff_text: str, findings: list[SecretFinding]) -> str:
    """Sanitize secrets in the diff text before sending to LLM."""
    sanitized = diff_text
    for f in findings:
        # Match any raw secret that produced this masked pattern
        # Replace occurrences in the diff with the masked token
        for _, pattern in SECRET_PATTERNS:
            sanitized = pattern.sub(f"[REDACTED_{f.secret_type.upper().replace(' ', '_')}]", sanitized)
    return sanitized


# -----------------------------------------------------------------------------
# PatchPilot Wallet Shield & Anti-Abuse Controls
# -----------------------------------------------------------------------------

MAX_DIFF_CHARS = 80_000
TRUSTED_AUTHOR_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def check_pr_authorization(
    author_association: str | None,
    is_fork: bool,
    is_private: bool = False,
    allow_forks: bool = False,
) -> tuple[bool, str]:
    """Verify if a PR is authorized for automatic LLM review to protect user API token balances."""
    # Private repositories are internal and safe
    if is_private:
        return True, "Authorized (Private Repository)"

    # If repo explicitly enables external forks
    if allow_forks:
        return True, "Authorized (allow_forks enabled)"

    assoc_clean = (author_association or "NONE").upper().strip()
    if assoc_clean in TRUSTED_AUTHOR_ASSOCIATIONS:
        return True, f"Authorized (Trusted Association: {assoc_clean})"

    # External contributor on public repository / fork
    if is_fork:
        return (
            False,
            "Wallet Shield blocked automatic review: PR from external fork on public repository. "
            "A maintainer must trigger review manually via `@patchpilot review` to protect API credit.",
        )

    return (
        False,
        f"Wallet Shield blocked automatic review: Author association '{assoc_clean}' is not trusted for automatic reviews on public repository. "
        "Maintainer review command required.",
    )


class RateLimiter:
    """Simple in-memory rolling-window rate limiter for webhook PR review requests."""

    def __init__(self) -> None:
        import time
        from collections import defaultdict

        self._time = time
        self._history: dict[str, list[float]] = defaultdict(list)

    def check_and_increment(
        self,
        key: str,
        max_requests: int = 20,
        window_seconds: int = 3600,
    ) -> tuple[bool, str]:
        """Check if request count for key within window is below threshold and record event."""
        now = self._time.time()
        cutoff = now - window_seconds
        records = [ts for ts in self._history[key] if ts > cutoff]

        if len(records) >= max_requests:
            self._history[key] = records
            return False, f"Rate limit reached ({len(records)}/{max_requests} reviews per {window_seconds // 60}m). Skipping to conserve token budget."

        records.append(now)
        self._history[key] = records
        return True, "OK"

    def reset(self, key: str | None = None) -> None:
        """Reset rate limiter records."""
        if key:
            self._history.pop(key, None)
        else:
            self._history.clear()


global_rate_limiter = RateLimiter()

