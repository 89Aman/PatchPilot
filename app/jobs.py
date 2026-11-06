"""Durable, encrypted webhook queue with leases and bounded retries."""
import asyncio
from contextvars import ContextVar
import hashlib
import json
import logging
import time
import uuid
from sqlalchemy import Integer, Float, String, Text, select, update
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

async def reserve_budget(repo):
    now = time.time()
    async with SessionLocal() as session:
        try:
            session.add(ReviewBudget(repo=repo, count=0, expires=now + 3600))
            await session.commit()
        except IntegrityError:
            await session.rollback()
        await session.execute(update(ReviewBudget).where(ReviewBudget.repo == repo, ReviewBudget.expires <= now).values(count=0, expires=now + 3600))
        result = await session.execute(update(ReviewBudget).where(ReviewBudget.repo == repo, ReviewBudget.count < 25).values(count=ReviewBudget.count + 1))
        await session.commit()
        return result.rowcount == 1

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
    try:
        if job.attempts > 3:
            raise RuntimeError("Retry budget exhausted after worker interruption")
        payload = json.loads(decrypt_secret(job.payload))
        from app.security import check_pr_authorization
        subject = payload.get("pull_request", {}) if job.event == "pull_request" else payload.get("comment", {})
        authorized, _ = check_pr_authorization(subject.get("author_association"), False, bool(payload.get("repository", {}).get("private", False)))
        if not authorized or subject.get("user", {}).get("type", "").lower() == "bot":
            state = "ignored"
        elif job.attempts == 1 and not await reserve_budget(payload.get("repository", {}).get("full_name", "")):
            state = "rate_limited"
        else:
            async with asyncio.timeout(480):
                if job.event == "pull_request":
                    await handle_pr_event(payload)
                else:
                    await handle_comment_event(job.event, payload)
    except asyncio.CancelledError:
        raise  # Lease expiry makes interrupted jobs recoverable by another worker.
    except Exception:
        log.exception("Review job %s failed", job.id)
        state = "retry" if job.attempts < 3 else "failed"
        await finalize_failure(job, state)
    finally:
        current_job.reset(token)
    async with SessionLocal() as session:
        values = dict(status=state, available=time.time() + min(300, 10 * 2 ** job.attempts))
        if state not in {"retry", "running"}:
            values["payload"] = ""  # Retain only deduplication metadata after completion.
        await session.execute(update(ReviewJob).where(ReviewJob.id == job.id, ReviewJob.lease == job.lease).values(**values))
        await session.commit()
    return True

async def finalize_failure(job, state):
    """Best-effort terminal status, including timeout and exhausted crash leases."""
    from app.github_auth import get_installation_token, complete_check_indicator, update_pr_review_card
    try:
        payload = json.loads(decrypt_secret(job.payload))
        handles = json.loads(job.handles)
        if not handles:
            return
        token = await get_installation_token(payload["installation"]["id"])
        repo = payload["repository"]["full_name"]
        number = (payload.get("pull_request") or payload.get("issue", {})).get("number")
        message = "Review failed; retry scheduled." if state == "retry" else "Review failed after exhausting retries. Trigger a new command after resolving the provider or publication error."
        await complete_check_indicator(token, repo, handles.get("check"), verdict="ERROR", summary_text=message)
        await update_pr_review_card(token, repo, number, handles.get("review"), message)
    except Exception:
        log.exception("Could not finalize failed job %s", job.id)


async def worker():
    while True:
        try:
            worked = await process_one()
        except Exception:
            log.exception("Queue poll failed")
            worked = False
        await asyncio.sleep(0 if worked else 2)
