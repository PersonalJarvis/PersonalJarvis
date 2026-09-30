"""HTTP jobs must not freeze the serving loop or share authentication state."""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from conductor.core.schema import HttpJobSpec
from conductor.jobs.http import HttpHandler


async def test_tls_initialization_yields_and_concurrent_jobs_share_one_pool(monkeypatch):
    loop_thread = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    calls = []
    built = []

    class Client:
        def __init__(self, **kwargs):
            assert threading.get_ident() != loop_thread
            built.append(self)
            entered.set()
            assert release.wait(5)

        async def request(self, **kwargs):
            calls.append(kwargs)
            return httpx.Response(200, text="ok")

        async def aclose(self):
            calls.append("closed")

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    handler = HttpHandler()
    jobs = [asyncio.create_task(handler.execute(HttpJobSpec(
        url="https://example.test", timeout_s=timeout,
    ), {})) for timeout in (2, 7)]
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        # This callback runs while the trust-root loader is still blocked.
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), 1)
        assert not any(job.done() for job in jobs)
    finally:
        release.set()
    results = await asyncio.gather(*jobs)
    assert all(result.success for result in results)
    assert len(built) == 1
    assert sorted(call["timeout"] for call in calls) == [2, 7]
    await handler.aclose()
    await handler.aclose()
    assert calls.count("closed") == 1


async def test_cancelled_initialization_closes_the_late_client(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    closed = []

    class Client:
        def __init__(self, **kwargs):
            entered.set()
            assert release.wait(5)

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    handler = HttpHandler()
    job = asyncio.create_task(handler.execute(HttpJobSpec(url="https://example.test"), {}))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        job.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await job
    await handler.aclose()
    assert closed == [True]


async def test_pooled_requests_do_not_share_response_cookies_or_headers(monkeypatch):
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(200, headers={"set-cookie": "session=private; Path=/"}, text="ok")

    def client(**kwargs):
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    handler = HttpHandler()
    try:
        first = await handler.execute(HttpJobSpec(
            url="https://example.test", headers={"Authorization": "test-only"},
        ), {})
        second = await handler.execute(HttpJobSpec(url="https://example.test"), {})
        assert first.success and second.success
        assert requests[0].headers["authorization"] == "test-only"
        assert "authorization" not in requests[1].headers
        assert "cookie" not in requests[1].headers
    finally:
        await handler.aclose()


async def test_timeout_keeps_the_pool_usable(monkeypatch):
    client_type = httpx.AsyncClient
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("test timeout")
        return httpx.Response(200, text="recovered")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    handler = HttpHandler()
    spec = HttpJobSpec(url="https://example.test", timeout_s=2)
    try:
        failed = await handler.execute(spec, {})
        recovered = await handler.execute(spec, {})
        assert failed.error == "timeout after 2.0s"
        assert recovered.success
    finally:
        await handler.aclose()


async def test_scheduler_stop_cancels_inflight_requests_before_closing_pool(monkeypatch):
    from conductor.core.runner import Runner
    from conductor.core.scheduler import Scheduler

    started = asyncio.Event()
    lifecycle = []

    class Store:
        async def get_job(self, job_id):
            return {"id": job_id, "name": "test", "spec_json": HttpJobSpec(
                url="https://example.test",
            ).model_dump_json()}

        async def create_run(self, *args, **kwargs):
            return "run-1"

        async def update_run(self, *args, **kwargs):
            pass

    class Client:
        def __init__(self, **kwargs):
            pass

        async def request(self, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                lifecycle.append("request ended")

        async def aclose(self):
            lifecycle.append("pool closed")

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    store = Store()
    runner = Runner(store)
    scheduler = Scheduler(store, runner)
    await runner.trigger("job-1")
    try:
        await asyncio.wait_for(started.wait(), 5)
    finally:
        await scheduler.stop()
    assert lifecycle == ["request ended", "pool closed"]
    assert not runner._tasks


async def test_cli_run_closes_its_pool_before_leaving(tmp_path, monkeypatch):
    from argparse import Namespace

    from conductor import ConductorStore, Job, ManualSchedule, cli

    path = tmp_path / "jobs.sqlite"
    store = ConductorStore(path)
    await store.init()
    job_id = await store.upsert_job(Job(
        name="HTTP cleanup", spec=HttpJobSpec(url="https://example.test"),
        schedule=ManualSchedule(),
    ))
    await store.close()
    closed = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def request(self, **kwargs):
            return httpx.Response(200, text="ok")

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(cli, "_resolve_db_path", lambda: path)
    assert await cli.cmd_run(Namespace(id=job_id, input_json=None, timeout=10)) == 0
    assert closed == [True]
