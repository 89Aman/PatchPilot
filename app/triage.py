"""PatchPilot Triage Engine: Automated PR sizing, complexity scoring, and risk classification."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TriageResult:
    size_tier: str
    risk_level: str
    risk_score: int
    labels: list[str]
    impact_areas: list[str]
    files_count: int
    total_lines_changed: int
    summary_text: str


SENSITIVE_PATH_KEYWORDS = {
    "Auth & Cryptography": ["auth", "crypto", "token", "jwt", "session", "key", "secret", "permission"],
    "Database & Migrations": ["migration", "schema", "alembic", "db", "sql", "models", "database"],
    "Infrastructure & Deployment": ["docker", "k8s", "kubernetes", "deploy", ".github/workflows", "ci", "nginx"],
    "Billing & Transactions": ["payment", "billing", "stripe", "invoice", "checkout"],
}


def compute_size_tier(total_lines: int) -> str:
    """Classify PR size into standard GitHub size tier labels."""
    if total_lines < 50:
        return "size:XS"
    if total_lines < 200:
        return "size:S"
    if total_lines < 500:
        return "size:M"
    if total_lines < 1000:
        return "size:L"
    return "size:XL"


def compute_pr_triage(
    files_changed: list[str],
    additions: int,
    deletions: int,
    has_secret_findings: bool = False,
) -> TriageResult:
    """Analyze changed files and diff line metrics to assign risk, size, and labels."""
    total_lines = additions + deletions
    size_tier = compute_size_tier(total_lines)

    impact_areas: list[str] = []
    risk_score = 15  # Base line risk for any code change

    # Sizing contribution to risk
    if total_lines > 1000:
        risk_score += 35
    elif total_lines > 500:
        risk_score += 25
    elif total_lines > 200:
        risk_score += 15

    # Check for sensitive impact areas
    for area, keywords in SENSITIVE_PATH_KEYWORDS.items():
        for file in files_changed:
            lower_file = file.lower()
            if any(k in lower_file for k in keywords):
                if area not in impact_areas:
                    impact_areas.append(area)
                risk_score += 20
                break

    # Deletion volatility (large deletions increase regression risk)
    if deletions > 300 and additions < 50:
        risk_score += 15

    # Pure documentation or test reductions
    if files_changed and all(f.endswith((".md", ".txt", ".rst")) for f in files_changed):
        risk_score = max(5, risk_score - 40)
    elif files_changed and all("test" in f.lower() for f in files_changed):
        risk_score = max(10, risk_score - 25)

    # Secret leakage override
    if has_secret_findings:
        risk_score = max(95, risk_score + 50)

    risk_score = min(100, max(0, risk_score))

    # Map numeric score to risk level
    if risk_score >= 80:
        risk_level = "risk:critical"
    elif risk_score >= 55:
        risk_level = "risk:high"
    elif risk_score >= 30:
        risk_level = "risk:medium"
    else:
        risk_level = "risk:low"

    labels = [size_tier, risk_level]
    if has_secret_findings:
        labels.append("security:alert")

    # Generate human-readable summary
    areas_str = ", ".join(impact_areas) if impact_areas else "Standard Application Logic"
    summary_text = (
        f"**PatchPilot Triage**: `{size_tier}` &bull; `{risk_level}` (Score: {risk_score}/100) "
        f"&bull; **Impact Areas**: {areas_str}"
    )

    return TriageResult(
        size_tier=size_tier,
        risk_level=risk_level,
        risk_score=risk_score,
        labels=labels,
        impact_areas=impact_areas,
        files_count=len(files_changed),
        total_lines_changed=total_lines,
        summary_text=summary_text,
    )
