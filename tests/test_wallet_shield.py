"""Unit tests for PatchPilot Wallet Shield and Anti-Abuse Rate Limiting."""

from __future__ import annotations

import time
import pytest
from app.security import RateLimiter, check_pr_authorization


def test_wallet_shield_private_repo_always_authorized():
    """Verify private repositories always authorize reviews regardless of author."""
    authorized, reason = check_pr_authorization(
        author_association="NONE",
        is_fork=True,
        is_private=True,
    )
    assert authorized is True
    assert "Private Repository" in reason


def test_wallet_shield_trusted_associations():
    """Verify OWNER, MEMBER, and COLLABORATOR are automatically authorized on public repos."""
    for assoc in ["OWNER", "MEMBER", "COLLABORATOR", "owner", "member", "collaborator"]:
        authorized, reason = check_pr_authorization(
            author_association=assoc,
            is_fork=False,
            is_private=False,
        )
        assert authorized is True
        assert "Trusted Association" in reason


def test_wallet_shield_untrusted_forks_blocked():
    """Verify external contributors and forks on public repos are blocked by default."""
    # Untrusted author on fork
    auth_fork, reason_fork = check_pr_authorization(
        author_association="FIRST_TIME_CONTRIBUTOR",
        is_fork=True,
        is_private=False,
    )
    assert auth_fork is False
    assert "external fork" in reason_fork

    # Untrusted author on same repo
    auth_none, reason_none = check_pr_authorization(
        author_association="NONE",
        is_fork=False,
        is_private=False,
    )
    assert auth_none is False
    assert "not trusted" in reason_none


def test_wallet_shield_allow_forks_flag():
    """Verify allow_forks overrides fork restriction when explicitly configured."""
    authorized, reason = check_pr_authorization(
        author_association="FIRST_TIMER",
        is_fork=True,
        is_private=False,
        allow_forks=True,
    )
    assert authorized is True
    assert "allow_forks enabled" in reason


def test_rate_limiter_window_and_limit():
    """Verify RateLimiter blocks requests exceeding threshold within the rolling window."""
    limiter = RateLimiter()
    key = "octocat/test-repo"

    # Allow up to 3 requests
    for i in range(3):
        allowed, msg = limiter.check_and_increment(key, max_requests=3, window_seconds=60)
        assert allowed is True
        assert msg == "OK"

    # 4th request must be rejected
    blocked, msg = limiter.check_and_increment(key, max_requests=3, window_seconds=60)
    assert blocked is False
    assert "Rate limit reached" in msg

    # Reset allows again
    limiter.reset(key)
    allowed_again, _ = limiter.check_and_increment(key, max_requests=3, window_seconds=60)
    assert allowed_again is True
