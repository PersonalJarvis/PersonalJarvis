"""Computer-Use pause-and-resume on an OS elevation prompt (UAC Secure Desktop).

When a launched app raises a UAC prompt, Windows hoists the Secure Desktop. A
non-elevated process can neither screenshot it nor click it (UIPI), so the loop
used to abort blind with the misleading "couldn't see the screen" (exit 1).

These pin the new behavior: detect the privileged prompt, ask the user for the
one unavoidable confirmation click, poll until it clears, then RESUME — or, if
no confirmation comes within the wait budget, stop with the honest
elevation-specific message (exit 9).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.harness import screenshot_only_loop as loop
from jarvis.voice.action_phrases import action_phrase


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class _FakeToken:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self._cancelled


@pytest.fixture(autouse=True)
def _fast_elevation_timings(monkeypatch):
    # Keep the poll loop sub-second so the timeout test does not sleep 60s.
    monkeypatch.setattr(loop, "_ELEVATION_WAIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(loop, "_ELEVATION_POLL_S", 0.005)


def _set_probe(monkeypatch, fn) -> None:
    monkeypatch.setattr(
        "jarvis.platform.privileged_prompt.privileged_prompt_active", fn
    )


@pytest.mark.asyncio
async def test_no_prompt_returns_no_prompt_without_announcing(monkeypatch):
    _set_probe(monkeypatch, lambda: False)
    bus = _RecordingBus()
    ctx = SimpleNamespace(bus=bus)

    result = await loop._await_privileged_prompt_clearance(
        ctx, "open OBS and record", 1, None
    )

    assert result == "no_prompt"
    assert bus.events == []  # a normal step must never speak the elevation ask


@pytest.mark.asyncio
async def test_cleared_when_prompt_disappears(monkeypatch):
    calls = {"n": 0}

    def _probe() -> bool:
        calls["n"] += 1
        return calls["n"] < 2  # up on the first check, gone afterwards

    _set_probe(monkeypatch, _probe)
    ctx = SimpleNamespace(bus=_RecordingBus())

    result = await loop._await_privileged_prompt_clearance(
        ctx, "open OBS", 3, None
    )

    assert result == "cleared"


@pytest.mark.asyncio
async def test_timeout_when_prompt_persists(monkeypatch):
    _set_probe(monkeypatch, lambda: True)
    ctx = SimpleNamespace(bus=_RecordingBus())

    result = await loop._await_privileged_prompt_clearance(
        ctx, "open OBS", 3, None
    )

    assert result == "timeout"


@pytest.mark.asyncio
async def test_cancelled_while_waiting(monkeypatch):
    _set_probe(monkeypatch, lambda: True)
    ctx = SimpleNamespace(bus=_RecordingBus())

    result = await loop._await_privileged_prompt_clearance(
        ctx, "open OBS", 3, _FakeToken(cancelled=True)
    )

    assert result == "cancelled"


@pytest.mark.asyncio
async def test_announces_request_in_turn_language(monkeypatch):
    _set_probe(monkeypatch, lambda: True)
    bus = _RecordingBus()
    ctx = SimpleNamespace(bus=bus)

    await loop._await_privileged_prompt_clearance(
        ctx, "Kannst du bitte eine Aufnahme starten?", 1, None
    )
    await asyncio.sleep(0.01)  # let the detached announcement task run

    texts = [getattr(e, "text", "") for e in bus.events]
    assert action_phrase("cu_awaiting_elevation", "de") in texts


@pytest.mark.asyncio
async def test_announces_request_in_english_for_english_turn(monkeypatch):
    _set_probe(monkeypatch, lambda: True)
    bus = _RecordingBus()
    ctx = SimpleNamespace(bus=bus)

    await loop._await_privileged_prompt_clearance(
        ctx, "Could you start a recording for me?", 1, None
    )
    await asyncio.sleep(0.01)

    texts = [getattr(e, "text", "") for e in bus.events]
    assert action_phrase("cu_awaiting_elevation", "en") in texts


@pytest.mark.asyncio
async def test_probe_failure_treated_as_no_prompt(monkeypatch):
    def _boom() -> bool:
        raise OSError("probe blew up")

    _set_probe(monkeypatch, _boom)
    ctx = SimpleNamespace(bus=_RecordingBus())

    result = await loop._await_privileged_prompt_clearance(ctx, "open OBS", 1, None)

    assert result == "no_prompt"  # never crash the loop on a probe fault
