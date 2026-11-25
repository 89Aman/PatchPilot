"""Contact sales and inquiry intake service.

Handles validation, rate limiting, enterprise routing, honeypot deflection,
transactional persistence, and post-commit webhook notification.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any, Literal
import uuid
from fastapi import HTTPException, Request, status
import httpx
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import ContactInquiry, ContactRateLimit

logger = logging.getLogger("patchpilot.contact")

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContactInquiryRequest(BaseModel):
    requested_plan: Literal["team", "enterprise"]
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=320)
    company_or_org: str = Field(..., min_length=1, max_length=255)
    team_size: Literal["2-5", "6-15", "16-50", "51+"]
    billing_cycle: Literal["monthly", "annual"]
    deployment_pref: Literal["hosted_cloud", "byok_cloud", "dedicated_vpc", "self_hosted_support"]
    message: str = Field(default="", max_length=2000)
    website: str = Field(default="", max_length=200)  # Honeypot field

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Name cannot be blank.")
        return s

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        s = v.strip().lower()
        if not EMAIL_REGEX.match(s):
            raise ValueError("Please provide a valid email address.")
        return s

    @field_validator("company_or_org")
    @classmethod
    def validate_company(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Company or organization name cannot be blank.")
        return s

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        s = (v or "").strip()
        if len(s) > 2000:
            raise ValueError("Notes cannot exceed 2,000 characters.")
        return s


def resolve_client_ip(request: Request, trusted_proxies: set[str] | None = None) -> str:
    """Resolve normalized client IP safely using trusted ingress configuration."""
    proxies = trusted_proxies if trusted_proxies is not None else settings.trusted_proxies_list
    client_host = request.client.host if request.client else "127.0.0.1"

    # Only inspect forwarded headers if direct peer is in TRUSTED_PROXIES
    if client_host in proxies:
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip and cf_ip.strip():
            return cf_ip.strip()

        xff = request.headers.get("x-forwarded-for")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            for p in reversed(parts):
                if p not in proxies:
                    return p
            if parts:
                return parts[0]

    return client_host


async def submit_contact_inquiry(
    payload: ContactInquiryRequest,
    request: Request,
    db: AsyncSession,
) -> dict[str, Any]:
    """Process a contact sales inquiry with honeypot deflection, rate limiting, and routing."""
    # 1. Honeypot check: return 200 without saving or webhook dispatch
    if payload.website.strip():
        logger.info("Deflected bot inquiry via website honeypot field.")
        return {
            "ok": True,
            "message": "Inquiry received",
        }

    # 2. Rate limit check: 5 requests per 15 minutes per client IP
    client_ip = resolve_client_ip(request)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=15)

    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                         {"key": f"contact:{client_ip}"})
    else:
        # SQLite serializes writers. Acquire its write lock before counting.
        await db.execute(delete(ContactRateLimit).where(ContactRateLimit.created_at < window_start))

    recent_count = await db.scalar(
        select(func.count()).select_from(ContactRateLimit).where(
            ContactRateLimit.client_ip == client_ip,
            ContactRateLimit.created_at >= window_start,
        )
    ) or 0

    if recent_count >= 5:
        logger.warning("Rate limit exceeded for contact inquiry from IP: %s (count=%s)", client_ip, recent_count)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many contact inquiries. Please try again later.",
        )

    # 3. Routing logic: Team with >= 6 active devs routes to Enterprise
    requested = payload.requested_plan.lower()
    upgraded_to_enterprise = False

    if requested == "team":
        if payload.team_size in {"6-15", "16-50", "51+"}:
            routed_plan = "enterprise"
            upgraded_to_enterprise = True
        else:
            routed_plan = "team"
    else:
        routed_plan = "enterprise"

    inquiry_id = f"inq_{uuid.uuid4().hex[:16]}"
    inquiry = ContactInquiry(
        id=inquiry_id,
        requested_plan=requested,
        routed_plan=routed_plan,
        name=payload.name,
        email=payload.email,
        company_or_org=payload.company_or_org,
        team_size=payload.team_size,
        billing_cycle=payload.billing_cycle,
        deployment_pref=payload.deployment_pref,
        message=payload.message,
        status="new",
        created_at=now,
    )
    db.add(inquiry)
    db.add(ContactRateLimit(client_ip=client_ip, created_at=now))
    await db.commit()
    logger.info("Contact inquiry created: id=%s, requested=%s, routed=%s", inquiry.id, requested, routed_plan)

    # 4. Optional post-commit webhook notification (failure must preserve inquiry)
    webhook_url = settings.CONTACT_WEBHOOK_URL.strip()
    if webhook_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                webhook_payload = {
                    "id": inquiry.id,
                    "requested_plan": inquiry.requested_plan,
                    "routed_plan": inquiry.routed_plan,
                    "upgraded_to_enterprise": upgraded_to_enterprise,
                    "name": inquiry.name,
                    "email": inquiry.email,
                    "company_or_org": inquiry.company_or_org,
                    "team_size": inquiry.team_size,
                    "billing_cycle": inquiry.billing_cycle,
                    "deployment_pref": inquiry.deployment_pref,
                    "message": inquiry.message,
                    "created_at": inquiry.created_at.isoformat(),
                }
                resp = await client.post(webhook_url, json=webhook_payload)
                if resp.status_code >= 400:
                    logger.warning("Contact webhook returned status %s for inquiry %s", resp.status_code, inquiry.id)
        except Exception as exc:
            logger.warning("Contact webhook notification failed for %s: %s", inquiry.id, exc)

    return {
        "ok": True,
        "id": inquiry.id,
        "requested_plan": inquiry.requested_plan,
        "routed_plan": inquiry.routed_plan,
        "upgraded_to_enterprise": upgraded_to_enterprise,
        "contact_email": settings.CONTACT_SALES_EMAIL.strip() or None,
    }
