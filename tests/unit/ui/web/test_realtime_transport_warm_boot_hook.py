"""The web/headless boot path must warm the selected realtime transports.

``realtime_warm_selected_transports`` pre-opens the primary transport (plus any
fallback that explicitly declares eager warming safe) so its cold start (for a
subscription transport: a spawned app-server plus a live account check) is not
paid inside the user's first call. Its only caller used to be the desktop shell, which left
``run.bat --headless`` and every browser-only install — headless Linux included
— paying that cost on every first call.

These tests pin the three properties the boot hook must have: it schedules the
warm, it schedules it exactly once (the desktop shell warms under the same task
name on the same loop), and a warm that raises never breaks startup.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from loguru import logger

from jarvis.realtime import factory as realtime_factory
from jarvis.ui.web import server as web_server


class _StubServer:
    """Only the two attributes the boot hook touches.

    Constructing a real ``WebServer`` builds the whole FastAPI app; the hook
    under test needs nothing but ``cfg`` and its own task slot, so the methods
    are borrowed onto a stub instead.
    """

    _schedule_realtime_transport_warm = web_server.WebServer._schedule_realtime_transport_warm
    _warm_realtime_transports = web_server.WebServer._warm_realtime_transports

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._realtime_warm_task: asyncio.Task[None] | None = None


@pytest.fixture(autouse=True)
def _no_boot_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the boot deferral so the tests assert behavior, not wall clock."""
    monkeypatch.setattr(web_server, "REALTIME_WARM_BOOT_DELAY_S", 0.0)


def _record_warm(monkeypatch: pytest.MonkeyPatch, calls: list[Any]) -> None:
    async def _fake_warm(cfg: Any) -> None:
        calls.append(cfg)

    monkeypatch.setattr(realtime_factory, "realtime_warm_selected_transports", _fake_warm)


async def test_boot_hook_schedules_the_warm_with_the_server_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    _record_warm(monkeypatch, calls)
    cfg = object()
    srv = _StubServer(cfg)

    srv._schedule_realtime_transport_warm()

    assert srv._realtime_warm_task is not None
    await srv._realtime_warm_task
    assert calls == [cfg]


async def test_the_prespawn_runs_before_the_boot_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The managed local server loads its models for 45-90 s in its OWN
    process; the spawn-only prestart must not sit behind the warm's boot
    delay — every second of that delay was a second the local voice could
    not answer, for nothing."""
    monkeypatch.setattr(web_server, "REALTIME_WARM_BOOT_DELAY_S", 30.0)
    warm_calls: list[Any] = []
    _record_warm(monkeypatch, warm_calls)
    prespawned = asyncio.Event()
    prespawn_calls: list[Any] = []

    async def _fake_prespawn(cfg: Any) -> None:
        prespawn_calls.append(cfg)
        prespawned.set()

    monkeypatch.setattr(
        realtime_factory, "realtime_prespawn_transports", _fake_prespawn
    )
    cfg = object()
    srv = _StubServer(cfg)
    srv._schedule_realtime_transport_warm()
    task = srv._realtime_warm_task
    assert task is not None
    try:
        await asyncio.wait_for(prespawned.wait(), timeout=2.0)
        assert prespawn_calls == [cfg]
        assert warm_calls == []  # the full warm still waits out the delay
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_repeated_scheduling_warms_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    _record_warm(monkeypatch, calls)
    srv = _StubServer(object())

    srv._schedule_realtime_transport_warm()
    first = srv._realtime_warm_task
    srv._schedule_realtime_transport_warm()

    assert srv._realtime_warm_task is first
    assert first is not None
    await first
    assert len(calls) == 1


async def test_a_live_warm_task_on_the_loop_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The desktop shell warms under the same task name on the same loop."""
    calls: list[Any] = []
    _record_warm(monkeypatch, calls)
    release = asyncio.Event()

    async def _desktop_warm_worker() -> None:
        await release.wait()

    foreign = asyncio.create_task(_desktop_warm_worker(), name=web_server.REALTIME_WARM_TASK_NAME)
    await asyncio.sleep(0)
    srv = _StubServer(object())
    try:
        srv._schedule_realtime_transport_warm()
        assert srv._realtime_warm_task is None
        assert calls == []
    finally:
        release.set()
        await foreign


async def test_a_raising_warm_never_breaks_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(cfg: Any) -> None:
        raise RuntimeError("app-server spawn refused")

    monkeypatch.setattr(realtime_factory, "realtime_warm_selected_transports", _boom)
    records: list[str] = []
    sink_id = logger.add(
        lambda message: records.append(message.record["level"].name), level="WARNING"
    )
    srv = _StubServer(object())
    try:
        srv._schedule_realtime_transport_warm()
        task = srv._realtime_warm_task
        assert task is not None
        await task
        assert task.exception() is None
    finally:
        logger.remove(sink_id)

    # AP-30: advisory, but never silent.
    assert "WARNING" in records


def test_scheduling_without_a_running_loop_is_a_quiet_no_op() -> None:
    """A synchronous start (test harness, embedding host) must not raise."""
    srv = _StubServer(object())

    srv._schedule_realtime_transport_warm()

    assert srv._realtime_warm_task is None
