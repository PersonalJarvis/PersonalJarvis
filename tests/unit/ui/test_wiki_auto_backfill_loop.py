"""The periodic realtime wiki backfill loop (server background task).

Live 2026-07-18: realtime turns whose provider delivered no input transcript
were silently lost to live capture, and the manual ``POST /api/wiki/backfill``
was the only recovery. The server now runs the evidence-safe backfill as a
bounded periodic background task. These tests bind the loop method to a
minimal fake server so no real WebServer (sockets, config, voice) is built.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.memory.wiki.backfill import BackfillResult


def _fake_server(store: Any) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_store=store)))


def _result(**overrides: Any) -> BackfillResult:
    defaults: dict[str, Any] = {
        "dry_run": False,
        "days": 2,
        "sessions_scanned": 1,
        "sessions_eligible": 1,
        "sessions_already_reviewed": 0,
        "sessions_in_progress": 0,
        "sessions_reviewed": 1,
        "sessions_failed": 0,
        "turns_considered": 3,
        "candidates_journaled": 2,
    }
    defaults.update(overrides)
    return BackfillResult(**defaults)


async def _run_until(task: asyncio.Task, condition, *, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while not condition() and asyncio.get_event_loop().time() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_loop_runs_bounded_live_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.memory.wiki import backfill as backfill_module
    from jarvis.memory.wiki import integration as integration_module
    from jarvis.ui.web.server import WebServer

    extractor = object()
    runtime = SimpleNamespace(extractor=extractor, scheduler=object())
    monkeypatch.setattr(
        integration_module, "get_running_capture_runtime", lambda: runtime
    )
    calls: list[dict[str, Any]] = []

    async def _fake_backfill(**kwargs: Any) -> BackfillResult:
        calls.append(kwargs)
        return _result()

    monkeypatch.setattr(backfill_module, "backfill_realtime_sessions", _fake_backfill)

    store = object()
    loop = WebServer._wiki_auto_backfill_loop.__get__(_fake_server(store))
    task = asyncio.get_event_loop().create_task(
        loop(initial_delay_s=0.0, interval_s=3600.0)
    )
    await _run_until(task, lambda: len(calls) >= 1)

    assert len(calls) == 1  # one pass, then the long interval sleep
    call = calls[0]
    assert call["store"] is store
    assert call["extractor"] is extractor
    assert call["dry_run"] is False  # a safety net that actually writes
    assert call["days"] == 2 and call["max_sessions"] == 20  # bounded pass


@pytest.mark.asyncio
async def test_loop_skips_pass_when_runtime_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.memory.wiki import backfill as backfill_module
    from jarvis.memory.wiki import integration as integration_module
    from jarvis.ui.web.server import WebServer

    probes: list[bool] = []

    def _no_runtime() -> None:
        probes.append(True)
        return None

    monkeypatch.setattr(integration_module, "get_running_capture_runtime", _no_runtime)

    async def _must_not_run(**_kwargs: Any) -> BackfillResult:
        raise AssertionError("backfill must not run without a capture runtime")

    monkeypatch.setattr(backfill_module, "backfill_realtime_sessions", _must_not_run)

    loop = WebServer._wiki_auto_backfill_loop.__get__(_fake_server(object()))
    task = asyncio.get_event_loop().create_task(
        loop(initial_delay_s=0.0, interval_s=3600.0)
    )
    await _run_until(task, lambda: len(probes) >= 1)
    assert probes  # the pass ran, probed the runtime, and skipped gracefully
