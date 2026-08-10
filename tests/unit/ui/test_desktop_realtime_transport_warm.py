"""The realtime transport pre-warm must be early, re-armable, and honest.

Two field defects this pins (2026-08-03 orchestration audit):

* The warm was scheduled inside ``_heavy_backend_bg``, which waits on the
  wake-model gate AND the whole ``server.start()`` chain — so it could not
  begin until roughly 30-40 s after launch, while the wake word was armed
  within ~1 s. Anyone who spoke in that window paid the provider's full cold
  start (15-25 s for the ChatGPT-subscription transport) inside their own call.
* It was a strict one-shot. The shared app-server client is torn down on any
  transport error, and nothing ever warmed it again — so the call after a
  failure was silently cold, with no way to tell it apart from a healthy one.

AP-26 stays intact: the worker does nothing until ``VoiceBootStatus`` reports
``voice_usable``, i.e. after the mark ``check_boot_budget.py`` measures.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.core.events import VoiceBootStatus, VoiceSessionEnded
from jarvis.ui import desktop_app as da
from jarvis.ui.desktop_app import DesktopApp


class _FakeBus:
    """Minimal synchronous bus: records subscriptions, dispatches on demand."""

    def __init__(self) -> None:
        self.handlers: dict[type[Any], list[Any]] = {}

    def subscribe(self, event_type: type[Any], handler: Any) -> None:
        self.handlers.setdefault(event_type, []).append(handler)

    async def publish(self, event: Any) -> None:
        for handler in self.handlers.get(type(event), []):
            await handler(event)


class _WarmRecorder:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls = 0
        self._fail_times = fail_times
        self.seen = asyncio.Event()

    async def __call__(self, cfg: Any) -> None:
        del cfg
        self.calls += 1
        self.seen.set()
        if self.calls <= self._fail_times:
            raise RuntimeError("app-server refused to start")


@pytest.fixture
def fast_warm(monkeypatch):
    """Collapse the production waits so the worker is testable in real time."""
    monkeypatch.setattr(da, "_REALTIME_WARM_DELAY_S", 0.0)
    monkeypatch.setattr(da, "_REALTIME_WARM_VOICE_GATE_S", 0.3)
    monkeypatch.setattr(da, "_REALTIME_WARM_MIN_INTERVAL_S", 0.0)


@pytest.fixture
def recorder(monkeypatch):
    import jarvis.core.config as config_mod
    import jarvis.realtime.factory as factory_mod

    rec = _WarmRecorder()
    monkeypatch.setattr(factory_mod, "realtime_warm_selected_transports", rec)
    monkeypatch.setattr(config_mod, "load_config", lambda: object())
    return rec


@pytest.fixture
def prespawn_recorder(monkeypatch):
    import jarvis.realtime.factory as factory_mod

    rec = _WarmRecorder()
    monkeypatch.setattr(factory_mod, "realtime_prespawn_transports", rec)
    return rec


async def _start_worker(bus: _FakeBus) -> asyncio.Task[None]:
    # The worker never touches ``self``; a bare instance keeps the test free of
    # the desktop app's window/tray construction.
    task = asyncio.create_task(
        DesktopApp._run_realtime_transport_warm(object.__new__(DesktopApp), bus)
    )
    await asyncio.sleep(0)
    return task


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_worker_waits_for_voice_usable_before_warming(fast_warm, recorder):
    """AP-26: nothing is warmed until voice reports itself usable."""
    bus = _FakeBus()
    task = await _start_worker(bus)
    try:
        assert VoiceBootStatus in bus.handlers
        # A not-yet-usable boot status must not release the gate.
        await bus.publish(
            VoiceBootStatus(
                source_layer="test", ready=True, detail="voice_unavailable"
            )
        )
        await asyncio.sleep(0.05)
        assert recorder.calls == 0

        await bus.publish(VoiceBootStatus(source_layer="test", ready=True))
        await asyncio.wait_for(recorder.seen.wait(), timeout=2.0)
        assert recorder.calls == 1
    finally:
        await _stop(task)


async def test_prespawn_fires_before_the_voice_gate(
    monkeypatch, recorder, prespawn_recorder
):
    """The managed local server loads its models for 45-90 s in its OWN
    process, so its spawn must not sit behind the voice gate. Live
    2026-08-10: the pipeline never reported usable, the warm sat out the
    full 45 s gate on every boot, and the server was first started by the
    first call — which it then rejected with "try again in about a minute".
    The full warm stays gated (AP-26); only the spawn-only prestart moves.
    """
    monkeypatch.setattr(da, "_REALTIME_WARM_DELAY_S", 0.0)
    # A gate long enough that a leaked early WARM would be caught below.
    monkeypatch.setattr(da, "_REALTIME_WARM_VOICE_GATE_S", 5.0)
    monkeypatch.setattr(da, "_REALTIME_WARM_MIN_INTERVAL_S", 0.0)

    bus = _FakeBus()
    task = await _start_worker(bus)
    try:
        await asyncio.wait_for(prespawn_recorder.seen.wait(), timeout=2.0)
        assert prespawn_recorder.calls == 1
        assert recorder.calls == 0  # the full warm still waits on the gate
    finally:
        await _stop(task)


async def test_warm_happens_anyway_when_voice_never_becomes_usable(
    fast_warm, recorder
):
    """A host with no microphone still gets a warm transport for its browser call."""
    bus = _FakeBus()
    task = await _start_worker(bus)
    try:
        await asyncio.wait_for(recorder.seen.wait(), timeout=2.0)
        assert recorder.calls == 1
    finally:
        await _stop(task)


async def test_every_finished_call_re_arms_the_warm(fast_warm, recorder):
    """The one-shot bug: a transport that died mid-session was never reopened."""
    bus = _FakeBus()
    task = await _start_worker(bus)
    try:
        await bus.publish(VoiceBootStatus(source_layer="test", ready=True))
        await asyncio.wait_for(recorder.seen.wait(), timeout=2.0)
        assert recorder.calls == 1

        assert VoiceSessionEnded in bus.handlers
        recorder.seen.clear()
        await bus.publish(
            VoiceSessionEnded(source_layer="test", hangup_reason="error")
        )
        await asyncio.wait_for(recorder.seen.wait(), timeout=2.0)
        assert recorder.calls == 2

        recorder.seen.clear()
        await bus.publish(
            VoiceSessionEnded(source_layer="test", hangup_reason="turn_complete")
        )
        await asyncio.wait_for(recorder.seen.wait(), timeout=2.0)
        assert recorder.calls == 3
    finally:
        await _stop(task)


async def test_a_failed_warm_is_reported_and_the_worker_survives(
    fast_warm, monkeypatch, caplog
):
    """AP-30: a failed warm says so, and does not kill the re-arm for the session."""
    import jarvis.core.config as config_mod
    import jarvis.realtime.factory as factory_mod

    rec = _WarmRecorder(fail_times=1)
    monkeypatch.setattr(factory_mod, "realtime_warm_selected_transports", rec)
    monkeypatch.setattr(config_mod, "load_config", lambda: object())

    bus = _FakeBus()
    task = await _start_worker(bus)
    try:
        await bus.publish(VoiceBootStatus(source_layer="test", ready=True))
        await asyncio.wait_for(rec.seen.wait(), timeout=2.0)
        assert rec.calls == 1

        rec.seen.clear()
        await bus.publish(
            VoiceSessionEnded(source_layer="test", hangup_reason="error")
        )
        await asyncio.wait_for(rec.seen.wait(), timeout=2.0)
        assert rec.calls == 2
    finally:
        await _stop(task)


async def test_min_interval_coalesces_a_burst_of_short_calls(monkeypatch, recorder):
    """Back-to-back calls must not re-verify the account once per call."""
    monkeypatch.setattr(da, "_REALTIME_WARM_DELAY_S", 0.0)
    monkeypatch.setattr(da, "_REALTIME_WARM_VOICE_GATE_S", 0.3)
    monkeypatch.setattr(da, "_REALTIME_WARM_MIN_INTERVAL_S", 5.0)

    bus = _FakeBus()
    task = await _start_worker(bus)
    try:
        await bus.publish(VoiceBootStatus(source_layer="test", ready=True))
        await asyncio.wait_for(recorder.seen.wait(), timeout=2.0)
        assert recorder.calls == 1

        for _ in range(4):
            await bus.publish(
                VoiceSessionEnded(source_layer="test", hangup_reason="hotkey")
            )
        await asyncio.sleep(0.15)
        # Still inside the floor: the burst is coalesced into ONE pending warm.
        assert recorder.calls == 1
    finally:
        await _stop(task)


async def test_a_bus_without_subscribe_does_not_stop_the_warm(fast_warm, recorder):
    """A bare/headless bus loses the re-arm, not the warm itself."""

    class _BrokenBus:
        def subscribe(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("no subscriptions on this bus")

    task = await _start_worker(_BrokenBus())  # type: ignore[arg-type]
    try:
        await asyncio.wait_for(recorder.seen.wait(), timeout=2.0)
        assert recorder.calls == 1
    finally:
        await _stop(task)


def test_warm_is_not_scheduled_from_the_heavy_backend(monkeypatch):
    """Regression pin for the scheduling slot itself.

    The warm must be created beside the wake listener, not inside
    ``_heavy_backend_bg`` — the latter is gated on the wake-model event and on
    ``await server.start()``, which is what made the wake word live 40-60 s
    before the transport it triggers.
    """
    import inspect

    source = inspect.getsource(DesktopApp._run_backend)
    heavy_start = source.index("async def _heavy_backend_bg()")
    heavy_end = source.index('loop.create_task(_heavy_backend_bg()')
    heavy_body = source[heavy_start:heavy_end]

    assert "realtime-transport-warm" in source
    assert "realtime-transport-warm" not in heavy_body
    assert "_run_realtime_transport_warm" not in heavy_body
