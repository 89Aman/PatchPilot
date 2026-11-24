"""Regressions found while rechecking open issues after PR #11."""
import asyncio
from email.utils import formatdate
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import jobs, main
from app.crypto import encrypt_secret
from app.db import Base, VaultConfig
from app.llm import annotate_diff, run_pr_review
from app.github_auth import complete_check_indicator, publish_pr_review
from tests.test_reliability import queue, payload


async def test_single_unrelated_paid_vault_is_never_used(queue, monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", queue)
    async with queue() as session:
        session.add(VaultConfig(owner="unrelated", provider="openai", encrypted_api_key=encrypt_secret("synthetic")))
        await session.commit()
    assert await main._get_owner_config("missing-owner") is None


def test_many_files_keep_late_file_evidence():
    diff = "".join(f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n@@ -0,0 +1,20 @@\n" + "".join(f"+x = {j}\n" for j in range(20)) for i in range(30))
    text, lines = annotate_diff(diff, max_chars=2500)
    assert len(text) <= 2500
    assert 20 in lines.get("f29.py", set())


@pytest.mark.parametrize("finding", [
    {"file": ["a.py"], "line": 1},
    {"file": "a.py", "line": True},
    {"file": "a.py", "line": 1, "severity": []},
    {"file": "a.py", "line": 1, "title": {}},
])
async def test_invalid_finding_field_types_fail_closed(finding):
    calls = 0
    def handle(request):
        nonlocal calls
        calls += 1
        data = {"verdict": "PASS", "findings": [finding]} if calls == 1 else {"verified_findings": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError):
            await run_pr_review(VaultConfig(provider="ollama", model="local"), "", "", "--- a.py ---\n[L1] +x", client=client)


async def test_discarded_findings_do_not_turn_into_clean_pass():
    data = {"verdict": "PASS", "findings": [{"file": "missing.py", "line": 200, "title": "Unverified"}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}]}))) as client:
        result = await run_pr_review(VaultConfig(provider="ollama", model="local"), "", "", "--- a.py ---\n[L1] +x", valid_lines={"a.py": {1}}, client=client)
    assert result["verdict"] == "WARN"
    assert "unverified" in result["summary"].lower()


async def test_oversized_verifier_candidates_are_not_sent():
    sizes = []
    finding = {"file": "a.py", "line": 1, "explanation": "x" * 60000}
    def handle(request):
        sizes.append(sum(len(m["content"]) for m in json.loads(request.content)["messages"]))
        data = {"verdict": "WARN", "findings": [finding]} if len(sizes) == 1 else {"verified_findings": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError):
            await run_pr_review(VaultConfig(provider="ollama", model="local"), "", "", "--- a.py ---\n[L1] +x", client=client)
    assert len(sizes) == 1
    assert max(sizes) <= 48000


async def test_reclaimed_job_cannot_bypass_exhausted_shared_budget(queue, monkeypatch):
    handler = AsyncMock()
    monkeypatch.setattr(main, "handle_pr_event", handler)
    for _ in range(25):
        assert await jobs.reserve_budget("owner/repo")
    key = await jobs.enqueue("pull_request", payload())
    async with queue() as session:
        await session.execute(update(jobs.ReviewJob).where(jobs.ReviewJob.id == key).values(status="running", attempts=1, available=0))
        await session.commit()
    await jobs.process_one()
    handler.assert_not_awaited()
    async with queue() as session:
        assert (await session.get(jobs.ReviewJob, key)).status == "rate_limited"


async def test_permanent_publication_failure_is_not_retried(queue, monkeypatch):
    response = httpx.Response(403, request=httpx.Request("POST", "https://api.github.com/test"))
    handler = AsyncMock(side_effect=httpx.HTTPStatusError("forbidden", request=response.request, response=response))
    monkeypatch.setattr(main, "handle_pr_event", handler)
    key = await jobs.enqueue("pull_request", payload())
    await jobs.process_one()
    async with queue() as session:
        assert (await session.get(jobs.ReviewJob, key)).status == "failed"


async def test_cancelled_worker_releases_job_for_retry(queue, monkeypatch):
    monkeypatch.setattr(main, "handle_pr_event", AsyncMock(side_effect=asyncio.CancelledError))
    finalize = AsyncMock()
    monkeypatch.setattr(jobs, "finalize_failure", finalize)
    key = await jobs.enqueue("pull_request", payload())
    with pytest.raises(asyncio.CancelledError):
        await jobs.process_one()
    async with queue() as session:
        assert (await session.get(jobs.ReviewJob, key)).status == "retry"
    finalize.assert_awaited_once()


@pytest.mark.parametrize("status,headers,minimum", [
    (403, {}, None), (422, {}, None), (429, {}, 60),
    (403, {"retry-after": "120"}, 120), (503, {"retry-after": "90"}, 90),
])
def test_retry_policy_honors_http_failure_type(status, headers, minimum):
    response = httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://api.github.com/test"))
    error = httpx.HTTPStatusError("failure", request=response.request, response=response)
    delay = jobs.retry_delay(error, 1)
    assert delay is None if minimum is None else delay >= minimum


@pytest.mark.parametrize("status", [403, 429, 503])
@pytest.mark.parametrize("offset", [600, 5, -600])
def test_retry_policy_honors_http_date(monkeypatch, status, offset):
    now = 1800000000.0
    monkeypatch.setattr(jobs.time, "time", lambda: now)
    response = httpx.Response(status, headers={"retry-after": formatdate(now + offset, usegmt=True)}, request=httpx.Request("POST", "https://api.github.com/test"))
    error = httpx.HTTPStatusError("failure", request=response.request, response=response)
    assert jobs.retry_delay(error, 1) == max(60 if status in (403, 429) else 20, offset)


def test_invalid_retry_date_preserves_reset_delay(monkeypatch):
    now = 1800000000.0
    monkeypatch.setattr(jobs.time, "time", lambda: now)
    response = httpx.Response(503, headers={"retry-after": "invalid", "x-ratelimit-reset": str(now + 120)}, request=httpx.Request("POST", "https://api.github.com/test"))
    error = httpx.HTTPStatusError("failure", request=response.request, response=response)
    assert jobs.retry_delay(error, 1) == 120


async def test_retry_reserves_shared_budget_only_once(queue, monkeypatch):
    monkeypatch.setattr(main, "handle_pr_event", AsyncMock(side_effect=RuntimeError("transient")))
    key = await jobs.enqueue("pull_request", payload())
    for _ in range(2):
        await jobs.process_one()
        async with queue() as session:
            await session.execute(update(jobs.ReviewJob).where(jobs.ReviewJob.id == key).values(available=0))
            await session.commit()
    async with queue() as session:
        assert (await session.get(jobs.ReviewBudget, "owner/repo")).count == 1


async def test_shared_budget_uses_a_rolling_hour(queue, monkeypatch):
    clock = [10000.0]
    monkeypatch.setattr(jobs.time, "time", lambda: clock[0])
    assert await jobs.reserve_budget("owner/repo")
    clock[0] += 3590
    for _ in range(24):
        assert await jobs.reserve_budget("owner/repo")
    clock[0] += 11
    assert await jobs.reserve_budget("owner/repo")
    assert not await jobs.reserve_budget("owner/repo")


async def test_existing_budget_usage_survives_upgrade(queue, monkeypatch):
    async with queue() as session:
        session.add(jobs.ReviewBudget(repo="owner/repo", count=25, expires=jobs.time.time() + 3600))
        await session.commit()
    assert not await jobs.reserve_budget("owner/repo")


def test_pem_redaction_precedes_excerpting_and_preserves_line_numbers():
    body = "\n".join("+synthetic-key-material" for _ in range(100))
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,103 @@\n+-----BEGIN RSA PRIVATE KEY-----\n" + body + "\n+-----END RSA PRIVATE KEY-----\n+after = True"
    text, lines = annotate_diff(diff, max_chars=500)
    assert "synthetic-key-material" not in text
    assert "[L103] +after = True" in text
    assert 103 in lines["a.py"]


async def test_fresh_review_reports_summary_only_when_inline_rejected():
    bodies = []
    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(422 if len(bodies) == 1 else 200, json={"id": 1})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await publish_pr_review("token", "owner/repo", 1, "sha", {"verdict": "WARN", "findings": [{"file": "a.py", "line": 1, "title": "Retained finding"}]}, {"a.py": {1}}, client=client)
    assert result["status"] == "summary_only"
    assert "Retained finding" in bodies[-1]["body"]


@pytest.mark.parametrize("handle", [{"head_sha": "sha", "check_run_id": 1}, {"head_sha": "sha", "status_active": True}])
async def test_check_completion_errors_are_propagated(handle):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await complete_check_indicator("token", "owner/repo", handle, client=client)


async def test_clean_review_is_single_pass():
    bodies = []
    def handle(request):
        bodies.append(json.loads(request.content))
        data = {"verdict": "PASS", "findings": [], "summary": "Clean"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await run_pr_review(VaultConfig(provider="ollama", model="local"), "", "", "--- a.py ---\n[L1] +x", client=client)
    assert result["verdict"] == "PASS"
    assert result["verification_status"].startswith("Single-pass")
    assert len(bodies) == 1


async def test_two_passes_share_bounded_evidence():
    bodies = []
    finding = {"file": "a.py", "line": 1, "severity": "risk", "title": "Verify this"}
    def handle(request):
        bodies.append(json.loads(request.content))
        data = {"verdict": "WARN", "findings": [finding]} if len(bodies) == 1 else {"verified_findings": [finding]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}]})
    diff = "--- a.py ---\n[L1] +x = 1\n" + "\n".join(f"[L{i}] +{'x' * 80}" for i in range(2, 1000))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await run_pr_review(VaultConfig(provider="ollama", model="local"), "", "", diff, valid_lines={"a.py": set(range(1, 1000))}, client=client)
    evidence = [body["messages"][1]["content"].split("```diff\n", 1)[1].rsplit("\n```", 1)[0] for body in bodies]
    assert len(evidence) == 2 and evidence[0] == evidence[1]
    assert all(sum(len(m["content"]) for m in b["messages"]) <= 48000 for b in bodies)
    assert "Coverage limited:" in evidence[0]
    assert "[L999]" not in evidence[0]
    assert "coverage is limited" in result["summary"]


async def test_webhook_acknowledges_only_persisted_jobs(queue, monkeypatch):
    monkeypatch.setattr(main, "verify_webhook_signature", lambda *args: True)
    data = payload()
    data["action"] = "opened"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        response = await client.post("/webhooks/github", json=data, headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery"})
    assert response.status_code == 200
    async with queue() as session:
        job = await session.scalar(select(jobs.ReviewJob))
        assert job.status == "queued"
        assert job.payload and "owner/repo" not in job.payload


async def test_concurrent_budget_reservations_enforce_global_cap(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}")
    monkeypatch.setattr(jobs, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        results = await asyncio.gather(*(jobs.reserve_budget("owner/repo") for _ in range(30)))
        assert sum(results) == 25
    finally:
        await engine.dispose()


async def test_queue_survives_database_reopen_and_redelivery(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'restart.db'}"
    engine = create_async_engine(url)
    monkeypatch.setattr(jobs, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    key = await jobs.enqueue("pull_request", payload(), "first-delivery")
    await engine.dispose()

    restarted = create_async_engine(url)
    monkeypatch.setattr(jobs, "SessionLocal", async_sessionmaker(restarted, expire_on_commit=False))
    handler = AsyncMock()
    monkeypatch.setattr(main, "handle_pr_event", handler)
    try:
        assert await jobs.enqueue("pull_request", payload(), "redelivery") == key
        assert await jobs.process_one()
        assert not await jobs.process_one()
        handler.assert_awaited_once()
    finally:
        await restarted.dispose()
