"""Durable, encrypted webhook queue with leases and bounded retries."""
import asyncio
from contextvars import ContextVar
from datetime import timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import time
import uuid
import httpx
from sqlalchemy import Integer, Float, String, Text, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base, SessionLocal
from app.crypto import encrypt_secret, decrypt_secret

log = logging.getLogger(__name__)
current_job = ContextVar("current_job", default=None)

class ReviewJob(Base):
    __tablename__ = "review_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event: Mapped[str] = mapped_column(String(60))
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available: Mapped[float] = mapped_column(Float, default=0, index=True)
    lease: Mapped[str] = mapped_column(String(64), default="")
    handles: Mapped[str] = mapped_column(Text, default="{}")

class ReviewBudget(Base):
    __tablename__ = "review_budgets"
    repo: Mapped[str] = mapped_column(String(512), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires: Mapped[float] = mapped_column(Float)

class ReviewBudgetReservation(Base):
    __tablename__ = "review_budget_reservations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo: Mapped[str] = mapped_column(String(512), index=True)
    expires: Mapped[float] = mapped_column(Float, index=True)

async def reserve_budget(repo, job=None):
    """Persist a job's reservation in the same transaction as the shared debit."""
    now = time.time()
    async with SessionLocal() as session:
        if job and json.loads(job.handles).get("budget_reserved"):
            return True
        try:
            session.add(ReviewBudget(repo=repo, count=0, expires=now + 3600))
            await session.commit()
        except IntegrityError:
            await session.rollback()
        # Serialize reservations for this repository across workers. UPDATE locks
        # the row on PostgreSQL and the write transaction on SQLite.
        await session.execute(update(ReviewBudget).where(ReviewBudget.repo == repo).values(count=ReviewBudget.count))
        budget = await session.get(ReviewBudget, repo)
        reservations = select(func.count()).select_from(ReviewBudgetReservation).where(ReviewBudgetReservation.repo == repo)
        if not await session.scalar(reservations) and budget.count and budget.expires > now:
            # Conservatively retain pre-upgrade fixed-window usage until expiry.
            session.add_all(ReviewBudgetReservation(id=uuid.uuid4().hex, repo=repo, expires=budget.expires) for _ in range(budget.count))
            await session.flush()
        await session.execute(delete(ReviewBudgetReservation).where(ReviewBudgetReservation.repo == repo, ReviewBudgetReservation.expires <= now))
        count = await session.scalar(reservations)
        if count >= 25:
            await session.commit()
            return False
        session.add(ReviewBudgetReservation(id=uuid.uuid4().hex, repo=repo, expires=now + 3600))
        budget.count = count + 1
        budget.expires = now + 3600
        if job:
            handles = json.loads(job.handles)
            handles["budget_reserved"] = True
            encoded = json.dumps(handles)
            saved = await session.execute(update(ReviewJob).where(ReviewJob.id == job.id, ReviewJob.lease == job.lease).values(handles=encoded))
            if saved.rowcount != 1:
                await session.rollback()
                raise RuntimeError("Job lease lost before budget reservation")
            job.handles = encoded
        await session.commit()
        return True


def retry_delay(exc, attempts):
    """Retry transient HTTP failures, respecting server rate-limit delays."""
    delay = min(300, 10 * 2 ** attempts)
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        code = response.status_code
        limited = code == 429 or (code == 403 and (
            "retry-after" in response.headers or response.headers.get("x-ratelimit-remaining") == "0"
        ))
        if not limited and code not in {408, 425} and code < 500:
            return None
        if limited:
            delay = max(60, delay)
        now = time.time()
        for name, offset in (("retry-after", 0), ("x-ratelimit-reset", now)):
            try:
                delay = max(delay, float(response.headers[name]) - offset)
            except (KeyError, ValueError):
                if name == "retry-after" and name in response.headers:
                    try:
                        retry_at = parsedate_to_datetime(response.headers[name])
                        if retry_at.tzinfo is None:
                            retry_at = retry_at.replace(tzinfo=timezone.utc)
                        delay = max(delay, retry_at.timestamp() - now)
                    except (TypeError, ValueError, OverflowError):
                        pass
    return delay

async def enqueue(event, payload, delivery=""):
    # Semantic identity deduplicates redeliveries even with different delivery IDs.
    repo = payload.get("repository", {}).get("full_name", "")
    pr = payload.get("pull_request", {})
    identity = [event, repo, pr.get("number"), pr.get("head", {}).get("sha")] if event == "pull_request" else [event, repo, payload.get("comment", {}).get("id") or delivery]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    async with SessionLocal() as session:
        session.add(ReviewJob(id=key, event=event, payload=encrypt_secret(json.dumps(payload))))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
    return key

async def remembered(name, call, *args, **kwargs):
    job = current_job.get()
    if not job:
        return await call(*args, **kwargs)
    handles = json.loads(job.handles)
    if name in handles:
        return handles[name]
    result = await call(*args, **kwargs)
    handles[name] = result
    job.handles = json.dumps(handles)
    async with SessionLocal() as session:
        await session.execute(update(ReviewJob).where(ReviewJob.id == job.id, ReviewJob.lease == job.lease).values(handles=job.handles))
        await session.commit()
    return result

async def process_one():
    from app.main import handle_pr_event, handle_comment_event
    now = time.time()
    async with SessionLocal() as session:
        ready = (ReviewJob.status.in_(["queued", "retry", "running"])) & (ReviewJob.available <= now)
        key = await session.scalar(select(ReviewJob.id).where(ready).order_by(ReviewJob.available).limit(1))
        if not key:
            return False
        lease = uuid.uuid4().hex
        result = await session.execute(update(ReviewJob).where(ReviewJob.id == key, ready).values(status="running", lease=lease, available=now + 600, attempts=ReviewJob.attempts + 1))
        if result.rowcount != 1:
            await session.rollback()
            return True
        await session.commit()
        job = await session.get(ReviewJob, key)
    token = current_job.set(job)
    state = "done"
    delay = 0
    cancelled = False
    try:
        if job.attempts > 3:
            raise RuntimeError("Retry budget exhausted after worker interruption")
        payload = json.loads(decrypt_secret(job.payload))
        from app.security import check_pr_authorization
        subject = payload.get("pull_request", {}) if job.event == "pull_request" else payload.get("comment", {})
        authorized, _ = check_pr_authorization(subject.get("author_association"), False, bool(payload.get("repository", {}).get("private", False)))
        if not authorized or subject.get("user", {}).get("type", "").lower() == "bot":
            state = "ignored"
        elif not await reserve_budget(payload.get("repository", {}).get("full_name", ""), job):
            state = "rate_limited"
        else:
            async with asyncio.timeout(480):
                if job.event == "pull_request":
                    await handle_pr_event(payload)
                else:
                    await handle_comment_event(job.event, payload)
    except asyncio.CancelledError:
        cancelled = True
        state = "retry" if job.attempts < 3 else "failed"
        await finalize_failure(job, state)
    except Exception as exc:
        log.exception("Review job %s failed", job.id)
        retry_after = retry_delay(exc, job.attempts)
        state = "retry" if job.attempts < 3 and retry_after is not None else "failed"
        delay = retry_after or 0
        await finalize_failure(job, state)
    finally:
        current_job.reset(token)
    async with SessionLocal() as session:
        values = dict(status=state, available=time.time() + delay)
        if state not in {"retry", "running"}:
            values["payload"] = ""  # Retain only deduplication metadata after completion.
        await session.execute(update(ReviewJob).where(ReviewJob.id == job.id, ReviewJob.lease == job.lease).values(**values))
        await session.commit()
    if cancelled:
        raise asyncio.CancelledError
    return True

async def finalize_failure(job, state):
    """Best-effort terminal status, including timeout and exhausted crash leases."""
    from app.github_auth import get_installation_token, complete_check_indicator, update_pr_review_card
    try:
        payload = json.loads(decrypt_secret(job.payload))
        handles = json.loads(job.handles)
        if not handles.get("check") and not handles.get("review"):
            return
        token = await get_installation_token(payload["installation"]["id"])
        repo = payload["repository"]["full_name"]
        number = (payload.get("pull_request") or payload.get("issue", {})).get("number")
        message = "Review interrupted or failed; retry scheduled." if state == "retry" else "Review failed and cannot be retried. Trigger a new command after resolving the provider or publication error."
        await complete_check_indicator(token, repo, handles.get("check"), verdict="ERROR", summary_text=message)
        await update_pr_review_card(token, repo, number, handles.get("review"), message)
    except Exception:
        log.exception("Could not finalize failed job %s", job.id)


async def worker():
    last_meter_flush = 0.0
    while True:
        try:
            worked = await process_one()
        except Exception:
            log.exception("Queue poll failed")
            worked = False

        now = time.time()
        if (now - last_meter_flush) >= 30.0:
            last_meter_flush = now
            try:
                from app.billing import flush_unreported_seat_events
                await flush_unreported_seat_events(limit=25)
            except Exception:
                log.exception("Billing seat meter outbox flush failed")

        await asyncio.sleep(0 if worked else 2)
