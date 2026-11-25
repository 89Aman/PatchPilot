"""Polar.sh Merchant-of-Record billing adapter, webhook processor, and meter outbox.

Uses Polar REST API v1 directly via `httpx.AsyncClient` and verifies incoming
webhooks with zero-dependency Standard Webhooks HMAC-SHA256 validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import logging
from typing import Any, Protocol
import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import (
    BillingWebhookEvent,
    SessionLocal,
    VaultConfig,
    Workspace,
    WorkspaceActiveCommitter,
)
from app.webhooks_crypto import WebhookVerificationError, verify_standard_webhook

logger = logging.getLogger("patchpilot.billing")


class BillingProviderError(RuntimeError):
    """Raised when an upstream billing provider request fails."""


class BillingProvider(Protocol):
    """Minimal billing rail interface so Polar can be swapped without touching routes."""

    async def create_checkout_url(
        self,
        *,
        workspace: Workspace,
        customer_email: str | None,
        success_url: str,
        customer_ip: str | None = None,
    ) -> str: ...

    async def create_portal_url(self, *, workspace: Workspace) -> str: ...

    async def ingest_active_committer(
        self,
        *,
        workspace: Workspace,
        github_author_id: int,
        github_author_login: str,
        billing_period: str,
        event_id: str,
    ) -> bool: ...


class PolarBillingClient:
    """Async REST client for Polar.sh Checkout, Customer Portal, and Unique Meters."""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        base_url: str | None = None,
        pro_product_id: str | None = None,
        meter_event_name: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.access_token = (access_token if access_token is not None else settings.POLAR_ACCESS_TOKEN).strip()
        self.base_url = (base_url if base_url is not None else settings.polar_api_base_url).rstrip("/")
        self.pro_product_id = (pro_product_id if pro_product_id is not None else settings.POLAR_PRO_PRODUCT_ID).strip()
        self.meter_event_name = (meter_event_name if meter_event_name is not None else settings.POLAR_METER_EVENT_NAME).strip() or "pr_reviewed"
        self._client = client

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise BillingProviderError("POLAR_ACCESS_TOKEN is not configured")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def create_checkout_url(
        self,
        *,
        workspace: Workspace,
        customer_email: str | None,
        success_url: str,
        customer_ip: str | None = None,
    ) -> str:
        if not self.pro_product_id:
            raise BillingProviderError("POLAR_PRO_PRODUCT_ID is not configured")

        payload: dict[str, Any] = {
            "products": [self.pro_product_id],
            "external_customer_id": workspace.id,
            "success_url": success_url,
            "metadata": {
                "workspace_id": workspace.id,
                "github_account_id": str(workspace.github_account_id),
                "github_login": workspace.github_login,
                "account_type": workspace.account_type,
            },
        }
        if customer_email and customer_email.strip():
            payload["customer_email"] = customer_email.strip()
        if customer_ip and customer_ip.strip():
            payload["customer_ip_address"] = customer_ip.strip()

        close_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=20.0)
            close_client = True

        try:
            resp = await client.post(
                f"{self.base_url}/v1/checkouts/",
                headers=self._headers(),
                json=payload,
            )
            if resp.is_error:
                raise BillingProviderError(
                    f"Polar checkout creation failed ({resp.status_code}): {resp.text[:300]}"
                )
            data = resp.json()
            url = data.get("url")
            if not url:
                raise BillingProviderError("Polar checkout response did not include a URL")
            return str(url)
        finally:
            if close_client:
                await client.aclose()

    async def create_portal_url(self, *, workspace: Workspace) -> str:
        payload: dict[str, Any] = {}
        if workspace.polar_customer_id:
            payload["customer_id"] = workspace.polar_customer_id
        else:
            payload["external_customer_id"] = workspace.id

        close_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=20.0)
            close_client = True

        try:
            resp = await client.post(
                f"{self.base_url}/v1/customer-sessions/",
                headers=self._headers(),
                json=payload,
            )
            if resp.is_error:
                raise BillingProviderError(
                    f"Polar customer portal session failed ({resp.status_code}): {resp.text[:300]}"
                )
            data = resp.json()
            url = data.get("customer_portal_url") or data.get("url")
            if not url:
                raise BillingProviderError("Polar customer session response missing customer_portal_url")
            return str(url)
        finally:
            if close_client:
                await client.aclose()

    async def ingest_active_committer(
        self,
        *,
        workspace: Workspace,
        github_author_id: int,
        github_author_login: str,
        billing_period: str,
        event_id: str,
    ) -> bool:
        if not self.access_token:
            return False

        event: dict[str, Any] = {
            "name": self.meter_event_name,
            "external_id": event_id,
            "metadata": {
                "github_author_id": str(github_author_id),
                "github_author_login": github_author_login,
                "workspace_id": workspace.id,
                "billing_period": billing_period,
            },
        }
        if workspace.polar_customer_id:
            event["customer_id"] = workspace.polar_customer_id
        else:
            event["external_customer_id"] = workspace.id

        close_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(timeout=15.0)
            close_client = True

        try:
            resp = await client.post(
                f"{self.base_url}/v1/events/ingest",
                headers=self._headers(),
                json={"events": [event]},
            )
            if resp.is_error:
                logger.warning(
                    "Polar meter ingest failed for %s (%d): %s",
                    event_id,
                    resp.status_code,
                    resp.text[:250],
                )
                return False
            return True
        except Exception as exc:
            logger.warning("Polar meter ingest network error for %s: %s", event_id, exc)
            return False
        finally:
            if close_client:
                await client.aclose()


def _parse_iso_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _find_workspace_for_polar_event(
    db: AsyncSession,
    data: dict[str, Any],
) -> Workspace | None:
    metadata = data.get("metadata") or {}
    customer = data.get("customer") or {}

    candidate_ws_ids = [
        metadata.get("workspace_id"),
        customer.get("external_id"),
        data.get("external_customer_id"),
    ]
    for ws_id in candidate_ws_ids:
        if ws_id and isinstance(ws_id, str):
            ws = await db.get(Workspace, ws_id.strip())
            if ws is not None:
                return ws

    sub_id = data.get("id")
    if sub_id and isinstance(sub_id, str):
        res = await db.execute(
            select(Workspace).where(Workspace.polar_subscription_id == sub_id.strip())
        )
        ws = res.scalar_one_or_none()
        if ws is not None:
            return ws

    cust_id = data.get("customer_id") or customer.get("id")
    if cust_id and isinstance(cust_id, str):
        res = await db.execute(
            select(Workspace).where(Workspace.polar_customer_id == cust_id.strip())
        )
        ws = res.scalar_one_or_none()
        if ws is not None:
            return ws

    return None


async def _workspace_has_byok_key(db: AsyncSession, workspace: Workspace) -> bool:
    res = await db.execute(
        select(VaultConfig).where(
            (VaultConfig.workspace_id == workspace.id)
            | (VaultConfig.owner == workspace.github_login.strip().lower())
        )
    )
    cfg = res.scalar_one_or_none()
    if cfg is None:
        return False
    return bool((cfg.encrypted_api_key or "").strip()) or str(cfg.provider or "").lower() == "ollama"


async def process_polar_webhook(
    db: AsyncSession,
    raw_body: bytes,
    headers: Mapping[str, str],
    *,
    webhook_secret: str | None = None,
) -> dict[str, Any]:
    """Verify, deduplicate, and apply a Polar.sh subscription webhook event."""
    secret = webhook_secret if webhook_secret is not None else settings.POLAR_WEBHOOK_SECRET
    event_id, payload = verify_standard_webhook(raw_body, headers, secret)
    event_type = str(payload.get("type") or "").strip()
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    # Idempotency guard via billing_webhook_events
    existing = await db.get(BillingWebhookEvent, event_id)
    if existing is not None:
        return {"status": "duplicate", "event_id": event_id, "type": event_type}

    db.add(
        BillingWebhookEvent(
            event_id=event_id,
            provider="polar",
            event_type=event_type or "unknown",
            processed_at=datetime.now(timezone.utc),
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return {"status": "duplicate", "event_id": event_id, "type": event_type}

    handled_types = {
        "subscription.created",
        "subscription.updated",
        "subscription.active",
        "subscription.canceled",
        "subscription.uncanceled",
        "subscription.revoked",
    }
    if event_type not in handled_types:
        await db.commit()
        return {"status": "ignored", "event_id": event_id, "type": event_type}

    ws = await _find_workspace_for_polar_event(db, data)
    if ws is None:
        logger.warning("Polar webhook %s (%s) could not be matched to a workspace", event_id, event_type)
        await db.commit()
        return {"status": "unmatched_workspace", "event_id": event_id, "type": event_type}

    customer = data.get("customer") or {}
    polar_cust_id = data.get("customer_id") or customer.get("id")
    polar_sub_id = data.get("id")
    billing_email = customer.get("email") or data.get("customer_email")
    status_val = str(data.get("status") or "").strip().lower()
    period_start = _parse_iso_dt(data.get("current_period_start"))
    period_end = _parse_iso_dt(data.get("current_period_end"))

    if polar_cust_id:
        ws.polar_customer_id = str(polar_cust_id)
    if polar_sub_id:
        ws.polar_subscription_id = str(polar_sub_id)
    if billing_email:
        ws.billing_email = str(billing_email)
    if period_start is not None:
        ws.current_period_start = period_start
    if period_end is not None:
        ws.current_period_end = period_end

    if event_type == "subscription.revoked" or status_val in {"revoked", "unpaid", "incomplete_expired"}:
        ws.plan_tier = "unsubscribed"
        ws.subscription_status = "revoked"
        ws.cancel_at_period_end = False
        ws.key_mode = "byok"
    elif event_type == "subscription.canceled":
        ws.cancel_at_period_end = True
        if status_val == "revoked":
            ws.plan_tier = "unsubscribed"
            ws.subscription_status = "revoked"
            ws.key_mode = "byok"
        elif ws.plan_tier == "pro":
            ws.subscription_status = status_val if status_val in {"active", "past_due"} else "active"
    elif event_type == "subscription.uncanceled":
        ws.cancel_at_period_end = False
        ws.plan_tier = "pro"
        ws.subscription_status = "active"
    elif event_type in {"subscription.active", "subscription.created", "subscription.updated"}:
        if status_val in {"active", "past_due"} or event_type == "subscription.active":
            was_unsubscribed = ws.plan_tier != "pro"
            ws.plan_tier = "pro"
            ws.subscription_status = "past_due" if status_val == "past_due" else "active"
            ws.cancel_at_period_end = bool(data.get("cancel_at_period_end", False))
            # Auto-enable zero-config Managed AI upon activation if customer has not saved a BYOK key
            if was_unsubscribed and not await _workspace_has_byok_key(db, ws):
                ws.key_mode = "managed"
        elif status_val == "canceled":
            ws.cancel_at_period_end = True

    ws.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "status": "processed",
        "event_id": event_id,
        "type": event_type,
        "workspace_id": ws.id,
        "plan_tier": ws.plan_tier,
        "subscription_status": ws.subscription_status,
    }


async def flush_unreported_seat_events(
    *,
    billing_client: BillingProvider | None = None,
    limit: int = 25,
) -> int:
    """Deliver unreported active-committer seat events to Polar idempotently."""
    if not settings.BILLING_ENABLED:
        return 0
    if billing_client is None and not settings.POLAR_ACCESS_TOKEN.strip():
        return 0

    provider = billing_client or PolarBillingClient()
    reported_count = 0

    async with SessionLocal() as db:
        res = await db.execute(
            select(WorkspaceActiveCommitter, Workspace)
            .join(Workspace, WorkspaceActiveCommitter.workspace_id == Workspace.id)
            .where(
                WorkspaceActiveCommitter.meter_reported_at.is_(None),
                Workspace.plan_tier == "pro",
                Workspace.subscription_status.in_(["active", "past_due"]),
            )
            .order_by(WorkspaceActiveCommitter.first_seen_at.asc())
            .limit(limit)
        )
        rows = list(res.all())

        for seat, ws in rows:
            event_id = seat.meter_event_id or f"seat:{ws.id}:{seat.billing_period}:{seat.github_author_id}"
            seat.meter_event_id = event_id
            ok = await provider.ingest_active_committer(
                workspace=ws,
                github_author_id=seat.github_author_id,
                github_author_login=seat.github_author_login,
                billing_period=seat.billing_period,
                event_id=event_id,
            )
            if ok:
                seat.meter_reported_at = datetime.now(timezone.utc)
                reported_count += 1

        if reported_count > 0:
            await db.commit()

    return reported_count


__all__ = [
    "BillingProvider",
    "BillingProviderError",
    "PolarBillingClient",
    "WebhookVerificationError",
    "flush_unreported_seat_events",
    "process_polar_webhook",
]
