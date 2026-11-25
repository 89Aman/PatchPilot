"""Tests for PatchPilot edition catalog, decided prices, seat ceilings, and comparison audit."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
os.environ["TESTING"] = "1"

import pytest
from sqlalchemy import delete, select
from app.config import settings
from app.db import (
    ReviewUsageEvent,
    SessionLocal,
    Workspace,
    WorkspaceActiveCommitter,
    init_db,
)
from app.editions import (
    EDITIONS,
    TEAM_ANNUAL_RECONCILIATION_RULE,
    TEAM_PRODUCT_SEAT_CEILING,
    get_catalog_for_view,
    get_comparison_groups,
    verify_cycle_checkout,
)
from app.entitlements import (
    evaluate_review_entitlement,
    record_completed_review,
)
from app.workspaces import workspace_id_for_account


async def _clean_billing():
    await init_db()
    async with SessionLocal() as db:
        await db.execute(delete(ReviewUsageEvent))
        await db.execute(delete(WorkspaceActiveCommitter))
        await db.execute(delete(Workspace))
        await db.commit()


def test_decided_prices_and_catalog_totals():
    """Verify all decided amounts, units, annual totals, and equivalents match the contract."""
    assert TEAM_PRODUCT_SEAT_CEILING == 5

    # Self-Hosted: $0
    sh = EDITIONS["self_hosted"]
    assert sh.monthly_price_usd == 0
    assert sh.annual_price_usd == 0
    assert sh.action_type == "link"
    assert sh.action_href == "https://github.com/patchpilot/patchpilot#quickstart"

    # Solo: $9/mo, $84/yr ($7/mo equivalent)
    solo = EDITIONS["solo"]
    assert solo.monthly_price_usd == 9
    assert solo.annual_price_usd == 84
    assert solo.annual_monthly_equivalent_usd == 7
    assert solo.annual_monthly_equivalent_usd * 12 == solo.annual_price_usd
    assert solo.seat_ceiling == 1
    assert solo.action_type == "subscribe"

    # Team: $15/dev/mo, $144/dev/yr ($12/dev/mo equivalent)
    team = EDITIONS["team"]
    assert team.monthly_price_usd == 15
    assert team.annual_price_usd == 144
    assert team.annual_monthly_equivalent_usd == 12
    assert team.annual_monthly_equivalent_usd * 12 == team.annual_price_usd
    assert team.seat_ceiling == 5
    assert team.action_type == "contact"
    assert "reconcile" in TEAM_ANNUAL_RECONCILIATION_RULE.lower()

    # Enterprise: custom
    ent = EDITIONS["enterprise"]
    assert ent.monthly_price_usd is None
    assert ent.annual_price_usd is None
    assert ent.action_type == "contact"


def test_verified_provider_price_and_availability_message():
    """Verify subscription actions show only when provider price is verified; otherwise availability message is given."""
    # Without verified provider product
    is_avail_m, msg_m = verify_cycle_checkout("solo", "monthly", configured_products={})
    assert is_avail_m is False
    assert "coming soon" in msg_m.lower()

    is_avail_a, msg_a = verify_cycle_checkout("solo", "annual", configured_products={})
    assert is_avail_a is False
    assert "annual solo plan is coming soon" in msg_a.lower()

    # With configured provider product for monthly
    is_avail_configured, _ = verify_cycle_checkout("solo", "monthly", configured_products={"solo_monthly": "prod_123"})
    assert is_avail_configured is True
    annual_available, _ = verify_cycle_checkout(
        "solo", "annual", {"solo_monthly": "monthly", "solo": "legacy", "pro": "legacy"}
    )
    assert not annual_available

    # Catalog view context generator
    catalog_view = get_catalog_for_view(cycle="monthly", configured_products={"solo_monthly": "prod_123"})
    assert catalog_view["cycle"] == "monthly"
    solo_entry = next(e for e in catalog_view["editions"] if e["id"] == "solo")
    assert solo_entry["price_display"] == "$9"
    assert solo_entry["checkout_available"] is True

    # Annual view displays equivalent and total
    catalog_annual = get_catalog_for_view(cycle="annual", configured_products={})
    solo_annual = next(e for e in catalog_annual["editions"] if e["id"] == "solo")
    assert solo_annual["price_display"] == "$84"
    assert solo_annual["equiv_display"] == "$7/mo equivalent"
    assert solo_annual["checkout_available"] is False
    assert "coming soon" in solo_annual["availability_message"]


def test_feature_matrix_audit():
    """Verify feature matrix categories, visible text, and status labeling against shipped code."""
    groups = get_comparison_groups(managed_cap_per_seat=50)
    group_names = [g.group_name for g in groups]
    assert group_names == [
        "Limits & Billing",
        "Core PR Review Engine",
        "AI Models & Vault",
        "Governance, Hosting & Support",
    ]

    all_rows = [row for g in groups for row in g.rows]
    row_names = {r.name for r in all_rows}

    # Verify key rows exist
    assert "Target Developer Scope" in row_names
    assert "Active Developer Ceiling" in row_names
    assert "Managed AI Allowance" in row_names
    assert "SAML / Enterprise SSO" in row_names
    assert "Custom Model Endpoints" in row_names
    assert "Support & SLAs" in row_names

    # Audit that unreleased/negotiated features are NOT marked as unconditionally included for Team/Solo
    sso_row = next(r for r in all_rows if r.name == "SAML / Enterprise SSO")
    assert sso_row.cells["solo"].status == "not_included"
    assert sso_row.cells["team"].status == "planned"
    assert sso_row.cells["enterprise"].status == "subject_to_agreement"

    custom_ep = next(r for r in all_rows if r.name == "Custom Model Endpoints")
    assert custom_ep.cells["solo"].status == "planned"
    assert custom_ep.cells["team"].status == "planned"
    assert custom_ep.cells["enterprise"].status == "subject_to_agreement"


@pytest.mark.asyncio
async def test_team_five_author_hard_ceiling_and_returning_authors(monkeypatch):
    """Verify Team hard ceiling of 5 authors, blocking 6th, and allowing returning authors at capacity."""
    await _clean_billing()
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_API_KEY", "AIzaSyKey")

    ws_id = workspace_id_for_account(77001)
    now = datetime.now(timezone.utc)

    try:
        async with SessionLocal() as db:
            ws = Workspace(
                id=ws_id,
                github_account_id=77001,
                github_login="team-alpha",
                account_type="Organization",
                key_mode="managed",
                plan_tier="pro",  # Team/Pro
                subscription_status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                seat_cap=None,  # Unset -> effective cap is TEAM_PRODUCT_SEAT_CEILING (5)
            )
            db.add(ws)
            await db.commit()

            # Record authors 1 through 5
            for author_id in range(1, 6):
                d = await evaluate_review_entitlement(
                    db, ws, is_private=True, github_author_id=author_id, github_author_login=f"dev_{author_id}", has_byok_key=False
                )
                assert d.allowed is True, f"Author {author_id} should be allowed under ceiling 5"

                seat = await record_completed_review(
                    db,
                    workspace=ws,
                    event_id=f"job_dev_{author_id}",
                    repo_full_name="team-alpha/repo",
                    is_private=True,
                    pr_number=author_id,
                    github_author_id=author_id,
                    github_author_login=f"dev_{author_id}",
                    key_mode="managed",
                )
                assert seat is not None

            # 6th distinct author must be BLOCKED by 5-seat ceiling
            d_6th = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=6, github_author_login="dev_6", has_byok_key=False
            )
            assert d_6th.allowed is False
            assert d_6th.reason_code == "seat_cap_reached"
            assert "ceiling" in d_6th.user_message.lower()

            # Returning author 1 (already counted) is ALLOWED even though 5 seats are filled
            d_ret = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=1, github_author_login="dev_1", has_byok_key=False
            )
            assert d_ret.allowed is True

            # Bot author is allowed and does NOT consume a seat or get blocked
            d_bot = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=999, github_author_login="renovate[bot]", author_type="Bot", has_byok_key=False
            )
            assert d_bot.allowed is True
            bot_seat = await record_completed_review(
                db,
                workspace=ws,
                event_id="job_bot",
                repo_full_name="team-alpha/repo",
                is_private=True,
                pr_number=99,
                github_author_id=999,
                github_author_login="renovate[bot]",
                author_type="Bot",
                key_mode="managed",
            )
            assert bot_seat is None
    finally:
        await _clean_billing()


@pytest.mark.asyncio
async def test_lower_optional_workspace_cap_and_enterprise_exemption(monkeypatch):
    """Verify lower workspace seat_cap (e.g. 3) takes precedence, and Enterprise plan is exempt from 5 ceiling."""
    await _clean_billing()
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_API_KEY", "AIzaSyKey")

    ws_id = workspace_id_for_account(77002)
    now = datetime.now(timezone.utc)

    try:
        async with SessionLocal() as db:
            ws = Workspace(
                id=ws_id,
                github_account_id=77002,
                github_login="capped-team",
                account_type="Organization",
                key_mode="managed",
                plan_tier="pro",
                subscription_status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                seat_cap=3,  # Lower cap of 3
            )
            db.add(ws)
            await db.commit()

            # 3 authors fill the cap
            for author_id in range(1, 4):
                await record_completed_review(
                    db,
                    workspace=ws,
                    event_id=f"job_cap_{author_id}",
                    repo_full_name="capped-team/repo",
                    is_private=True,
                    pr_number=author_id,
                    github_author_id=author_id,
                    github_author_login=f"dev_{author_id}",
                    key_mode="managed",
                )

            # 4th author is blocked by the lower cap of 3 (even though product ceiling is 5)
            d_4th = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=4, github_author_login="dev_4", has_byok_key=False
            )
            assert d_4th.allowed is False
            assert d_4th.reason_code == "seat_cap_reached"

            # Now upgrade workspace to Enterprise with no seat_cap: allows 6+ developers
            ws.plan_tier = "enterprise"
            ws.seat_cap = None
            await db.commit()

            # Author 4, 5, 6 are now all allowed!
            for author_id in range(4, 7):
                d_ent = await evaluate_review_entitlement(
                    db, ws, is_private=True, github_author_id=author_id, github_author_login=f"dev_{author_id}", has_byok_key=False
                )
                assert d_ent.allowed is True
    finally:
        await _clean_billing()


@pytest.mark.asyncio
async def test_billing_period_rollover(monkeypatch):
    """Verify seat count resets when the billing period advances to next month."""
    await _clean_billing()
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_API_KEY", "AIzaSyKey")

    ws_id = workspace_id_for_account(77003)
    period_1 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    period_2 = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)

    try:
        async with SessionLocal() as db:
            ws = Workspace(
                id=ws_id,
                github_account_id=77003,
                github_login="rollover-org",
                account_type="Organization",
                key_mode="managed",
                plan_tier="pro",
                subscription_status="active",
                current_period_start=period_1,
                current_period_end=period_1 + timedelta(days=30),
                seat_cap=2,
            )
            db.add(ws)
            await db.commit()

            # Fill 2 seats in Period 1
            for aid in (101, 102):
                await record_completed_review(
                    db,
                    workspace=ws,
                    event_id=f"job_p1_{aid}",
                    repo_full_name="rollover-org/core",
                    is_private=True,
                    pr_number=aid,
                    github_author_id=aid,
                    github_author_login=f"dev_{aid}",
                    key_mode="managed",
                    now=period_1 + timedelta(days=5),
                )

            # In Period 1, author 103 is blocked
            d_blocked = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=103, github_author_login="dev_103", has_byok_key=False, now=period_1 + timedelta(days=5)
            )
            assert d_blocked.allowed is False

            # In Period 2 (subscription renewed), seat count resets to 0!
            ws.current_period_start = period_2
            ws.current_period_end = period_2 + timedelta(days=31)
            await db.commit()

            d_rollover = await evaluate_review_entitlement(
                db, ws, is_private=True, github_author_id=103, github_author_login="dev_103", has_byok_key=False, now=period_2 + timedelta(days=2)
            )
            assert d_rollover.allowed is True
    finally:
        await _clean_billing()


@pytest.mark.asyncio
async def test_concurrent_boundary_recording(monkeypatch):
    """Verify that when 4 seats exist and 2 concurrent reviews complete, the 5-seat boundary is not exceeded."""
    await _clean_billing()
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "MANAGED_LLM_PRIMARY_API_KEY", "AIzaSyKey")

    ws_id = workspace_id_for_account(77004)
    now = datetime.now(timezone.utc)

    try:
        async with SessionLocal() as db:
            ws = Workspace(
                id=ws_id,
                github_account_id=77004,
                github_login="race-team",
                account_type="Organization",
                key_mode="managed",
                plan_tier="pro",
                subscription_status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
            db.add(ws)
            await db.commit()

            # Fill 4 seats
            for aid in range(1, 5):
                await record_completed_review(
                    db,
                    workspace=ws,
                    event_id=f"job_pre_{aid}",
                    repo_full_name="race-team/core",
                    is_private=True,
                    pr_number=aid,
                    github_author_id=aid,
                    github_author_login=f"dev_{aid}",
                    key_mode="managed",
                )

        # Now simulate two concurrent review completion tasks for author 5 and author 6
        async def record_candidate(author_id: int):
            async with SessionLocal() as s_db:
                ws_rec = await s_db.get(Workspace, ws_id)
                return await record_completed_review(
                    s_db,
                    workspace=ws_rec,
                    event_id=f"job_conc_{author_id}",
                    repo_full_name="race-team/core",
                    is_private=True,
                    pr_number=author_id,
                    github_author_id=author_id,
                    github_author_login=f"dev_{author_id}",
                    key_mode="managed",
                )

        res5, res6 = await asyncio.gather(record_candidate(5), record_candidate(6))

        # Exactly one candidate should be granted the 5th seat; the other must return None (blocked at boundary)
        successful_seats = [r for r in (res5, res6) if r is not None]
        assert len(successful_seats) == 1

        async with SessionLocal() as db:
            total_seats = (await db.execute(
                select(WorkspaceActiveCommitter).where(WorkspaceActiveCommitter.workspace_id == ws_id)
            )).scalars().all()
            assert len(total_seats) == 5, f"Expected exactly 5 seats, got {len(total_seats)}"
    finally:
        await _clean_billing()

def test_checkout_products_require_hosted_billing_and_exact_cycle(monkeypatch):
    monkeypatch.setattr(settings, "POLAR_SOLO_MONTHLY_PRODUCT_ID", "monthly")
    monkeypatch.setattr(settings, "POLAR_SOLO_ANNUAL_PRODUCT_ID", "")
    monkeypatch.setattr(settings, "BILLING_ENABLED", False)
    assert not settings.solo_checkout_products
    monkeypatch.setattr(settings, "BILLING_ENABLED", True)
    assert verify_cycle_checkout("solo", "monthly", settings.solo_checkout_products)[0]
    assert not verify_cycle_checkout("solo", "annual", settings.solo_checkout_products)[0]
