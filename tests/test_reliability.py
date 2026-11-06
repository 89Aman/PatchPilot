import json
import time
from unittest.mock import AsyncMock
import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app import jobs
from app.db import Base, VaultConfig
from app.llm import annotate_diff, run_pr_review, budget_text
from app.github_auth import publish_pr_review

@pytest.mark.parametrize("limit", [0, 10, 100, 2500])
def test_hard_budget(limit):
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+" + "x" * 100000
    text, lines = annotate_diff(diff, max_chars=limit)
    assert len(text) <= limit
    assert not any(lines.values())
    assert len(budget_text("λ" * 1000, limit)) <= limit

@pytest.mark.asyncio
@pytest.mark.parametrize("content", ['not json', '[]', '{"findings":null,"verdict":"PASS"}'])
async def test_invalid_analysis_never_passes(content):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices":[{"message":{"content":content}}]}))) as client:
        with pytest.raises(ValueError):
            await run_pr_review(VaultConfig(provider="ollama", model="local"), "title", "", "diff", client=client)

@pytest.mark.asyncio
async def test_verifier_failure_is_not_fact_checked():
    calls = []
    def handle(r):
        calls.append(r)
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"verdict":"WARN","findings":[{"file":"a.py","line":1,"title":"bug"}]})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError):
            await run_pr_review(VaultConfig(provider="ollama",model="local"),"", "", "--- a.py ---\n[L1] +x=1",client=client)

@pytest.mark.asyncio
@pytest.mark.parametrize("code", [403,422,429,500])
async def test_inline_errors(code):
    bodies = []
    def handle(r):
        bodies.append(json.loads(r.content))
        return httpx.Response(code if r.method == "POST" else 200,json={"id":1})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        args = ("token","owner/repo",1,"sha",{"verdict":"WARN","findings":[{"file":"a.py","line":1,"title":"lost finding"}]},{"a.py":{1}})
        if code == 422:
            result = await publish_pr_review(*args,client=client,existing_review={"id":1,"kind":"review"})
            assert result["status"] == "summary_only"
            assert "lost finding" in bodies[-1]["body"]
        else:
            with pytest.raises(httpx.HTTPStatusError):
                await publish_pr_review(*args,client=client,existing_review={"id":1,"kind":"review"})

@pytest.fixture
async def queue(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(jobs,"SessionLocal",sessions)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield sessions
    await engine.dispose()

def payload():
    return {"repository":{"full_name":"owner/repo"},"pull_request":{"number":1,"head":{"sha":"abc"},"author_association":"OWNER"}}

@pytest.mark.asyncio
async def test_queue_persistence_dedup_and_completion(queue,monkeypatch):
    handler=AsyncMock()
    monkeypatch.setattr("app.main.handle_pr_event",handler)
    key=await jobs.enqueue("pull_request",payload(),"first")
    assert await jobs.enqueue("pull_request",payload(),"redelivery") == key
    async with queue() as s:
        row=await s.get(jobs.ReviewJob,key)
        assert "owner/repo" not in row.payload
    assert await jobs.process_one()
    assert not await jobs.process_one()
    handler.assert_awaited_once()
    async with queue() as s:
        row=await s.get(jobs.ReviewJob,key)
        assert row.status == "done" and row.payload == ""

@pytest.mark.asyncio
async def test_queue_retries_and_exhaustion(queue,monkeypatch):
    monkeypatch.setattr("app.main.handle_pr_event",AsyncMock(side_effect=RuntimeError("upstream")))
    key=await jobs.enqueue("pull_request",payload())
    for attempt in range(3):
        assert await jobs.process_one()
        async with queue() as s:
            row=await s.get(jobs.ReviewJob,key)
            assert row.attempts == attempt+1
            assert row.status == ("failed" if attempt==2 else "retry")
            await s.execute(update(jobs.ReviewJob).where(jobs.ReviewJob.id==key).values(available=0))
            await s.commit()
    assert not await jobs.process_one()

@pytest.mark.asyncio
async def test_expired_lease_recovered_and_live_lease_skipped(queue,monkeypatch):
    monkeypatch.setattr("app.main.handle_pr_event",AsyncMock())
    key=await jobs.enqueue("pull_request",payload())
    async with queue() as s:
        await s.execute(update(jobs.ReviewJob).values(status="running",available=time.time()+600,attempts=1))
        await s.commit()
    assert not await jobs.process_one()
    async with queue() as s:
        await s.execute(update(jobs.ReviewJob).values(available=0))
        await s.commit()
    assert await jobs.process_one()

@pytest.mark.asyncio
async def test_shared_budget(queue):
    for _ in range(25):
        assert await jobs.reserve_budget("owner/repo")
    assert not await jobs.reserve_budget("owner/repo")
    assert await jobs.reserve_budget("other/repo")

@pytest.mark.asyncio
async def test_untrusted_job_does_not_consume_budget(queue,monkeypatch):
    data=payload();data["pull_request"]["author_association"]="NONE"
    handler=AsyncMock();monkeypatch.setattr("app.main.handle_pr_event",handler)
    await jobs.enqueue("pull_request",data)
    await jobs.process_one()
    handler.assert_not_awaited()
    async with queue() as s:
        assert await s.scalar(select(jobs.ReviewBudget)) is None
@pytest.mark.asyncio
async def test_handles_survive_retry(queue,monkeypatch):
    create = AsyncMock(return_value={"id": 123})
    async def handler(data):
        assert await jobs.remembered("review",create) == {"id":123}
        raise RuntimeError("after card creation")
    monkeypatch.setattr("app.main.handle_pr_event",handler)
    monkeypatch.setattr(jobs,"finalize_failure",AsyncMock())
    key=await jobs.enqueue("pull_request",payload())
    await jobs.process_one()
    async with queue() as s:
        await s.execute(update(jobs.ReviewJob).where(jobs.ReviewJob.id==key).values(available=0))
        await s.commit()
    await jobs.process_one()
    create.assert_awaited_once()
