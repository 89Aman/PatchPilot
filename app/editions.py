"""PatchPilot Edition Catalog, Decided Prices, and Feature Comparison Matrix.

Single server-side authority for pricing, seat boundaries, comparison table,
and checkout tier mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The Team edition hard ceiling on distinct human PR authors per workspace billing cycle.
# Workspaces needing 6 or more developers route to Enterprise.
TEAM_PRODUCT_SEAT_CEILING = 5

TEAM_ANNUAL_RECONCILIATION_RULE = (
    "Annual Team subscriptions are purchased per active developer seat ($144/developer/year, "
    "equivalent to $12/month). If active human PR authors exceed the purchased seat count during "
    "the billing cycle, seats are reconciled at renewal or via prorated true-up."
)


@dataclass(frozen=True)
class Edition:
    id: str
    name: str
    badge: str
    tagline: str
    scope_description: str
    monthly_price_usd: int | None
    annual_price_usd: int | None
    annual_monthly_equivalent_usd: int | None
    price_unit: str
    annual_price_unit: str
    action_type: str  # 'link' | 'subscribe' | 'contact'
    action_label: str
    action_href: str | None
    seat_ceiling: int | None
    highlight: bool = False


EDITIONS: dict[str, Edition] = {
    "self_hosted": Edition(
        id="self_hosted",
        name="Self-Hosted",
        badge="MIT License",
        tagline="Open-source sovereign PR reviews on your own hardware.",
        scope_description="Customer supplies infrastructure and model access.",
        monthly_price_usd=0,
        annual_price_usd=0,
        annual_monthly_equivalent_usd=0,
        price_unit="license fee",
        annual_price_unit="license fee",
        action_type="link",
        action_label="Set up PatchPilot",
        action_href="https://github.com/patchpilot/patchpilot#quickstart",
        seat_ceiling=None,
    ),
    "solo": Edition(
        id="solo",
        name="Solo",
        badge="For Individuals",
        tagline="Automated reviews for personal projects and solo maintainers.",
        scope_description="One personal GitHub account.",
        monthly_price_usd=9,
        annual_price_usd=84,
        annual_monthly_equivalent_usd=7,
        price_unit="/month",
        annual_price_unit="/year",
        action_type="subscribe",
        action_label="Subscribe to Solo",
        action_href=None,
        seat_ceiling=1,
    ),
    "team": Edition(
        id="team",
        name="Team",
        badge="Most Popular",
        tagline="Collaborative PR reviews with workspace policies and team fallback keys.",
        scope_description="Up to five distinct human PR authors per workspace billing cycle.",
        monthly_price_usd=15,
        annual_price_usd=144,
        annual_monthly_equivalent_usd=12,
        price_unit="per active developer/month",
        annual_price_unit="per active developer/year",
        action_type="contact",
        action_label="Contact Sales",
        action_href="/pricing?contact=team",
        seat_ceiling=TEAM_PRODUCT_SEAT_CEILING,
        highlight=True,
    ),
    "enterprise": Edition(
        id="enterprise",
        name="Enterprise",
        badge="Custom Scale",
        tagline="Custom active developer volume, dedicated VPCs, SLAs, and security agreements.",
        scope_description="Six or more active developers, or an explicit Enterprise inquiry at any size.",
        monthly_price_usd=None,
        annual_price_usd=None,
        annual_monthly_equivalent_usd=None,
        price_unit="custom",
        annual_price_unit="custom",
        action_type="contact",
        action_label="Contact Sales",
        action_href="/pricing?contact=enterprise",
        seat_ceiling=None,
    ),
}


@dataclass(frozen=True)
class FeatureCell:
    text: str
    status: str = "included"  # 'included' | 'planned' | 'subject_to_agreement' | 'not_included' | 'info'


@dataclass(frozen=True)
class FeatureRow:
    name: str
    description: str
    cells: dict[str, FeatureCell]


@dataclass(frozen=True)
class FeatureGroup:
    group_name: str
    rows: list[FeatureRow]


def get_comparison_groups(managed_cap_per_seat: int = 50) -> list[FeatureGroup]:
    """Return feature comparison grouped into four semantic categories audited against shipped code."""
    return [
        FeatureGroup(
            group_name="Limits & Billing",
            rows=[
                FeatureRow(
                    name="Target Developer Scope",
                    description="Permitted accounts and active developer boundary.",
                    cells={
                        "self_hosted": FeatureCell(text="Single instance or team", status="info"),
                        "solo": FeatureCell(text="1 personal GitHub account", status="info"),
                        "team": FeatureCell(text="Up to 5 active developers", status="info"),
                        "enterprise": FeatureCell(text="6+ active developers / custom", status="info"),
                    },
                ),
                FeatureRow(
                    name="Active Developer Ceiling",
                    description="Hard stop on distinct human PR authors per workspace cycle.",
                    cells={
                        "self_hosted": FeatureCell(text="Unlimited (self-managed)", status="included"),
                        "solo": FeatureCell(text="1 developer", status="included"),
                        "team": FeatureCell(text="5 developers (hard ceiling)", status="included"),
                        "enterprise": FeatureCell(text="Custom / Negotiated", status="included"),
                    },
                ),
                FeatureRow(
                    name="Managed AI Allowance",
                    description="Enforced fair-use monthly review quota for zero-config managed pool.",
                    cells={
                        "self_hosted": FeatureCell(text="Not included (customer provides models)", status="not_included"),
                        "solo": FeatureCell(text=f"{managed_cap_per_seat} reviews/month", status="included"),
                        "team": FeatureCell(text=f"{managed_cap_per_seat} reviews/developer/month", status="included"),
                        "enterprise": FeatureCell(text="Custom / Unlimited pool", status="included"),
                    },
                ),
                FeatureRow(
                    name="Pricing Model",
                    description="Monthly and annual billing rates (USD).",
                    cells={
                        "self_hosted": FeatureCell(text="$0 (Free & open source)", status="info"),
                        "solo": FeatureCell(text="$9/mo or $84/yr ($7/mo equiv)", status="info"),
                        "team": FeatureCell(text="$15/dev/mo or $144/dev/yr ($12/dev/mo equiv)", status="info"),
                        "enterprise": FeatureCell(text="Custom annual contract", status="info"),
                    },
                ),
            ],
        ),
        FeatureGroup(
            group_name="Core PR Review Engine",
            rows=[
                FeatureRow(
                    name="Automated PR Code Reviews",
                    description="Inline comments on changed lines for bugs, logic, and style.",
                    cells={
                        "self_hosted": FeatureCell(text="Included", status="included"),
                        "solo": FeatureCell(text="Included", status="included"),
                        "team": FeatureCell(text="Included", status="included"),
                        "enterprise": FeatureCell(text="Included", status="included"),
                    },
                ),
                FeatureRow(
                    name="Interactive Review Commands",
                    description="@patchpilot review, explain, fix, and test in GitHub threads.",
                    cells={
                        "self_hosted": FeatureCell(text="Included", status="included"),
                        "solo": FeatureCell(text="Included", status="included"),
                        "team": FeatureCell(text="Included", status="included"),
                        "enterprise": FeatureCell(text="Included", status="included"),
                    },
                ),
                FeatureRow(
                    name="Custom Review Policies",
                    description="Team-wide markdown rules injected into every review prompt.",
                    cells={
                        "self_hosted": FeatureCell(text="Included", status="included"),
                        "solo": FeatureCell(text="Included", status="included"),
                        "team": FeatureCell(text="Included", status="included"),
                        "enterprise": FeatureCell(text="Included", status="included"),
                    },
                ),
                FeatureRow(
                    name="Zero Data Retention",
                    description="Diffs parsed in ephemeral volatile memory; zero training on code.",
                    cells={
                        "self_hosted": FeatureCell(text="Included (100% custody)", status="included"),
                        "solo": FeatureCell(text="Included", status="included"),
                        "team": FeatureCell(text="Included", status="included"),
                        "enterprise": FeatureCell(text="Included", status="included"),
                    },
                ),
            ],
        ),
        FeatureGroup(
            group_name="AI Models & Vault",
            rows=[
                FeatureRow(
                    name="AES-256-GCM Hardware Vault",
                    description="Cryptographically sealed customer credentials at rest.",
                    cells={
                        "self_hosted": FeatureCell(text="Included", status="included"),
                        "solo": FeatureCell(text="Included", status="included"),
                        "team": FeatureCell(text="Included", status="included"),
                        "enterprise": FeatureCell(text="Included", status="included"),
                    },
                ),
                FeatureRow(
                    name="Multi-Provider Failover Chain",
                    description="Automated sequential retry across providers on rate limits or outages.",
                    cells={
                        "self_hosted": FeatureCell(text="Included", status="included"),
                        "solo": FeatureCell(text="Included", status="included"),
                        "team": FeatureCell(text="Included", status="included"),
                        "enterprise": FeatureCell(text="Included", status="included"),
                    },
                ),
                FeatureRow(
                    name="Supported BYOK Providers",
                    description="Google Gemini, Claude via OpenRouter, OpenAI, Groq, xAI, Ollama.",
                    cells={
                        "self_hosted": FeatureCell(text="All 6 providers + Ollama", status="included"),
                        "solo": FeatureCell(text="All 6 providers + Ollama", status="included"),
                        "team": FeatureCell(text="All 6 providers + Ollama", status="included"),
                        "enterprise": FeatureCell(text="All 6 providers + Ollama", status="included"),
                    },
                ),
                FeatureRow(
                    name="Custom Model Endpoints",
                    description="Dedicated custom inference gateway or private fine-tuned model URL.",
                    cells={
                        "self_hosted": FeatureCell(text="Included (via custom provider)", status="included"),
                        "solo": FeatureCell(text="Planned", status="planned"),
                        "team": FeatureCell(text="Planned", status="planned"),
                        "enterprise": FeatureCell(text="Subject to agreement", status="subject_to_agreement"),
                    },
                ),
            ],
        ),
        FeatureGroup(
            group_name="Governance, Hosting & Support",
            rows=[
                FeatureRow(
                    name="Multi-Tenant Workspace RBAC",
                    description="Role-based access control synced with GitHub organization permissions.",
                    cells={
                        "self_hosted": FeatureCell(text="Single or multi-tenant", status="included"),
                        "solo": FeatureCell(text="Personal workspace", status="included"),
                        "team": FeatureCell(text="Organization & personal", status="included"),
                        "enterprise": FeatureCell(text="Organization & enterprise", status="included"),
                    },
                ),
                FeatureRow(
                    name="Customer Portal & Invoices",
                    description="Self-serve invoice download, payment method updates, and receipts.",
                    cells={
                        "self_hosted": FeatureCell(text="Not applicable", status="not_included"),
                        "solo": FeatureCell(text="Included (via Polar)", status="included"),
                        "team": FeatureCell(text="Included (via Polar)", status="included"),
                        "enterprise": FeatureCell(text="Custom invoicing", status="included"),
                    },
                ),
                FeatureRow(
                    name="SAML / Enterprise SSO",
                    description="SAML 2.0 / OIDC identity federation for enterprise security.",
                    cells={
                        "self_hosted": FeatureCell(text="Planned", status="planned"),
                        "solo": FeatureCell(text="Not included", status="not_included"),
                        "team": FeatureCell(text="Planned", status="planned"),
                        "enterprise": FeatureCell(text="Subject to agreement", status="subject_to_agreement"),
                    },
                ),
                FeatureRow(
                    name="Deployment Model",
                    description="Where the PatchPilot runtime and database execute.",
                    cells={
                        "self_hosted": FeatureCell(text="Customer Docker / K8s", status="info"),
                        "solo": FeatureCell(text="PatchPilot Cloud", status="info"),
                        "team": FeatureCell(text="PatchPilot Cloud", status="info"),
                        "enterprise": FeatureCell(text="Dedicated VPC or Cloud Run", status="subject_to_agreement"),
                    },
                ),
                FeatureRow(
                    name="Support & SLAs",
                    description="Response guarantees and dedicated communication channels.",
                    cells={
                        "self_hosted": FeatureCell(text="Community (GitHub Issues)", status="info"),
                        "solo": FeatureCell(text="Standard email support", status="info"),
                        "team": FeatureCell(text="Priority email support", status="info"),
                        "enterprise": FeatureCell(text="Custom SLA & dedicated engineer", status="subject_to_agreement"),
                    },
                ),
            ],
        ),
    ]


def verify_cycle_checkout(
    edition_id: str,
    cycle: str,
    configured_products: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Check if the selected cycle has a verified provider product/price configured.

    Returns (is_available, message).
    """
    edition = EDITIONS.get(edition_id)
    if not edition:
        return False, "Edition not found."

    if edition.action_type == "link":
        return True, ""

    if edition.action_type == "contact":
        return True, ""

    # Self-serve subscription check (e.g. Solo)
    cfg = configured_products or {}
    key = f"{edition_id}_{cycle}"
    prod_id = cfg.get(key) or ""

    if prod_id.strip():
        return True, ""

    cycle_label = "annual" if cycle == "annual" else "monthly"
    return False, f"Self-serve checkout for the {cycle_label} {edition.name} plan is coming soon. Contact sales or switch billing cycle."


def get_catalog_for_view(
    cycle: str = "monthly",
    configured_products: dict[str, str] | None = None,
    managed_cap_per_seat: int = 50,
) -> dict[str, Any]:
    """Generate view-ready context for pricing table, comparison matrix, and teaser."""
    cycle_clean = "annual" if cycle == "annual" else "monthly"
    editions_list: list[dict[str, Any]] = []

    for eid, ed in EDITIONS.items():
        is_avail, avail_msg = verify_cycle_checkout(eid, cycle_clean, configured_products)
        if cycle_clean == "annual" and ed.annual_price_usd is not None:
            price_display = f"${ed.annual_price_usd}"
            unit_display = ed.annual_price_unit
            equiv_display = f"${ed.annual_monthly_equivalent_usd}/mo equivalent" if ed.annual_monthly_equivalent_usd is not None else None
        elif ed.monthly_price_usd is not None:
            price_display = f"${ed.monthly_price_usd}"
            unit_display = ed.price_unit
            equiv_display = None
        else:
            price_display = "Custom"
            unit_display = ""
            equiv_display = None

        editions_list.append({
            "id": ed.id,
            "name": ed.name,
            "badge": ed.badge,
            "tagline": ed.tagline,
            "scope_description": ed.scope_description,
            "price_display": price_display,
            "unit_display": unit_display,
            "equiv_display": equiv_display,
            "monthly_price_usd": ed.monthly_price_usd,
            "annual_price_usd": ed.annual_price_usd,
            "annual_monthly_equivalent_usd": ed.annual_monthly_equivalent_usd,
            "action_type": ed.action_type,
            "action_label": ed.action_label,
            "action_href": ed.action_href,
            "checkout_available": is_avail,
            "availability_message": avail_msg,
            "highlight": ed.highlight,
            "seat_ceiling": ed.seat_ceiling,
        })

    return {
        "cycle": cycle_clean,
        "editions": editions_list,
        "comparison_groups": get_comparison_groups(managed_cap_per_seat=managed_cap_per_seat),
        "team_seat_ceiling": TEAM_PRODUCT_SEAT_CEILING,
        "team_reconciliation_rule": TEAM_ANNUAL_RECONCILIATION_RULE,
    }
