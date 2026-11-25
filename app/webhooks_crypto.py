"""Zero-dependency Standard Webhooks / Svix HMAC-SHA256 verification helpers.

Used by both Polar.sh (`webhook-*` headers, `polar_whs_` / `whsec_` secrets)
and Clerk (`svix-*` headers, `whsec_` secrets).
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
import hmac
import json
import time
from typing import Any


class WebhookVerificationError(ValueError):
    """Raised when webhook signature headers or timestamp verification fails."""


def _candidate_secret_keys(secret: str) -> list[bytes]:
    """Derive valid HMAC key byte representations for Standard Webhooks secrets."""
    cleaned = (secret or "").strip()
    if not cleaned:
        return []

    candidates: list[bytes] = []

    def _try_b64(raw: str) -> bytes | None:
        padded = raw + ("=" * ((4 - len(raw) % 4) % 4))
        try:
            decoded = base64.b64decode(padded, validate=True)
            return decoded if decoded else None
        except Exception:
            return None

    if cleaned.startswith("whsec_"):
        decoded = _try_b64(cleaned[len("whsec_") :])
        if decoded:
            candidates.append(decoded)
    elif cleaned.startswith("polar_whs_"):
        # Post-Sept-2026 Polar endpoints follow Standard Webhooks base64 secrets;
        # earlier endpoints used raw UTF-8 bytes of the full secret string.
        decoded = _try_b64(cleaned[len("polar_whs_") :])
        if decoded:
            candidates.append(decoded)
        candidates.append(cleaned.encode("utf-8"))
    else:
        decoded = _try_b64(cleaned)
        if decoded:
            candidates.append(decoded)
        candidates.append(cleaned.encode("utf-8"))

    # Deduplicate while preserving order
    unique: list[bytes] = []
    for item in candidates:
        if item not in unique:
            unique.append(item)
    return unique


def _header_ci(headers: Mapping[str, str], *names: str) -> str:
    lowered = {k.lower(): v for k, v in headers.items()}
    for name in names:
        val = lowered.get(name.lower())
        if val:
            return str(val).strip()
    return ""


def sign_standard_webhook(
    raw_body: bytes,
    secret: str,
    *,
    webhook_id: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Create Standard Webhooks headers for testing or outbound signing."""
    ts = str(int(time.time()) if timestamp is None else int(timestamp))
    keys = _candidate_secret_keys(secret)
    if not keys:
        raise ValueError("Webhook signing secret cannot be empty")
    signed_content = f"{webhook_id}.{ts}.".encode("utf-8") + raw_body
    digest = hmac.new(keys[0], signed_content, hashlib.sha256).digest()
    sig = base64.b64encode(digest).decode("utf-8")
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": ts,
        "webhook-signature": f"v1,{sig}",
    }


def verify_standard_webhook(
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Verify Standard Webhooks / Svix signature and return `(event_id, payload)`."""
    if not secret or not secret.strip():
        raise WebhookVerificationError("Webhook signing secret is not configured")

    msg_id = _header_ci(headers, "webhook-id", "svix-id")
    msg_ts = _header_ci(headers, "webhook-timestamp", "svix-timestamp")
    msg_sig = _header_ci(headers, "webhook-signature", "svix-signature")

    if not msg_id or not msg_ts or not msg_sig:
        raise WebhookVerificationError("Missing required webhook signature headers")

    try:
        ts_int = int(msg_ts)
    except ValueError as exc:
        raise WebhookVerificationError("Invalid webhook timestamp header") from exc

    current_ts = time.time() if now is None else now
    if tolerance_seconds > 0 and abs(current_ts - ts_int) > tolerance_seconds:
        raise WebhookVerificationError("Webhook timestamp outside replay tolerance window")

    signed_content = f"{msg_id}.{msg_ts}.".encode("utf-8") + raw_body
    secret_keys = _candidate_secret_keys(secret)
    if not secret_keys:
        raise WebhookVerificationError("Invalid webhook signing secret")

    provided_v1_sigs: list[str] = []
    for token in msg_sig.split(" "):
        token = token.strip()
        if not token or "," not in token:
            continue
        version, sig_val = token.split(",", 1)
        if version.strip() == "v1" and sig_val.strip():
            provided_v1_sigs.append(sig_val.strip())

    if not provided_v1_sigs:
        raise WebhookVerificationError("No v1 webhook signature present")

    matched = False
    for key_bytes in secret_keys:
        expected = base64.b64encode(
            hmac.new(key_bytes, signed_content, hashlib.sha256).digest()
        ).decode("utf-8")
        for supplied in provided_v1_sigs:
            if hmac.compare_digest(expected, supplied):
                matched = True
                break
        if matched:
            break

    if not matched:
        raise WebhookVerificationError("Webhook signature verification failed")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise WebhookVerificationError("Webhook payload is not valid JSON") from exc

    if not isinstance(payload, dict):
        raise WebhookVerificationError("Webhook payload must be a JSON object")

    return msg_id, payload
