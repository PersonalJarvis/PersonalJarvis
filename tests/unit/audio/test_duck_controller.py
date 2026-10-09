"""AudioDuckController: session-boundary mute/restore with a fake ducker."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from jarvis.audio.ducking.controller import AudioDuckController
from jarvis.audio.ducking.protocol import DuckPermissionReport
from jarvis.core.bus import EventBus
from jarvis.core.events import VoiceSessionEnded, VoiceSessionStarted


class FakeDucker:
    def __init__(self):
        self.muted_calls = 0
        self.restored: list[list[int]] = []

    def mute_others(self, *, own_pid, never):
        self.muted_calls += 1
        return [111, 222]

    def restore(self, pids):
        self.restored.append(list(pids))


class FakeBus:
    def __init__(self):
        self.subs = {}

    def subscribe(self, ev, h):
        self.subs[ev.__name__] = h


def _cfg(enabled=True, delay=0):
    return SimpleNamespace(
        ducking=SimpleNamespace(enabled=enabled, restore_delay_ms=delay, never_mute=[])
    )


async def _settle(controller):
    while controller._tasks:
        await asyncio.gather(*controller._tasks)


async def test_mutes_on_start_restores_on_end_when_enabled():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    await _settle(c)
    assert d.muted_calls == 1 and c._muted == [111, 222]
    await bus.subs["VoiceSessionEnded"](object())
    assert d.restored == [[111, 222]] and c._muted == []


async def test_disabled_does_nothing():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=False), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    assert d.muted_calls == 0 and c._muted == []


async def test_set_enabled_false_midsession_restores():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    await _settle(c)
    await c.set_enabled(False)
    assert d.restored == [[111, 222]] and c._muted == []
    assert c._cfg.ducking.enabled is False


async def test_double_start_does_not_remute():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    await bus.subs["VoiceSessionStarted"](object())
    await _settle(c)
    assert d.muted_calls == 1  # already muted → no second sweep


async def test_restore_idempotent_when_nothing_muted():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await c.restore()
    assert d.restored == []  # nothing muted → no restore call


async def test_new_session_during_restore_delay_keeps_music_muted():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True, delay=50), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())  # session 1 mutes [111, 222]
    await _settle(c)
    end_task = asyncio.create_task(bus.subs["VoiceSessionEnded"](object()))
    await asyncio.sleep(0.01)  # let _on_end capture the generation + enter the sleep
    await bus.subs["VoiceSessionStarted"](object())  # session 2 bumps the generation
    await end_task  # session 1's delayed restore wakes → generation changed → SKIP
    await _settle(c)
    assert d.restored == []  # music stayed muted for the still-active session 2
    assert c._muted == [111, 222]


async def test_restore_sync_unmutes_on_shutdown():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    await _settle(c)
    c.restore_sync()  # synchronous (shutdown path)
    assert d.restored == [[111, 222]] and c._muted == []
    c.restore_sync()  # idempotent
    assert d.restored == [[111, 222]]


class AskingDucker(FakeDucker):
    """A backend with an asking path, like the macOS one."""

    def __init__(self):
        super().__init__()
        self.prewarms = 0

    def prewarm(self):
        self.prewarms += 1
        return DuckPermissionReport(note="a full sentence")


async def test_set_enabled_true_returns_the_backend_permission_report():
    d, bus = AskingDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=False), ducker=d)
    report = await c.set_enabled(True)
    assert d.prewarms == 1 and report.note == "a full sentence"
    assert c._cfg.ducking.enabled is True


async def test_set_enabled_true_without_an_asking_path_returns_none():
    d = FakeDucker()  # no prewarm: Windows, or no backend at all
    c = AudioDuckController(bus=FakeBus(), cfg=_cfg(enabled=False), ducker=d)
    assert await c.set_enabled(True) is None


async def test_set_enabled_false_never_asks():
    d = AskingDucker()
    c = AudioDuckController(bus=FakeBus(), cfg=_cfg(enabled=True), ducker=d)
    assert await c.set_enabled(False) is None
    assert d.prewarms == 0


async def test_a_failing_prewarm_is_not_a_permission_report():
    class Broken(AskingDucker):
        def prewarm(self):
            raise RuntimeError("native bridge down")

    c = AudioDuckController(bus=FakeBus(), cfg=_cfg(enabled=False), ducker=Broken())
    assert await c.set_enabled(True) is None  # logged by _run; the toggle still applies
    assert c._cfg.ducking.enabled is True


async def test_a_session_never_asks_the_backend_to_prewarm():
    d, bus = AskingDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    await _settle(c)
    await bus.subs["VoiceSessionEnded"](object())
    assert d.prewarms == 0 and d.muted_calls == 1


class DelayedDucker(FakeDucker):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.restored_event = threading.Event()

    def mute_others(self, *, own_pid, never):
        self.entered.set()
        assert self.release.wait(5), "test did not release the mute worker"
        return super().mute_others(own_pid=own_pid, never=never)

    def restore(self, pids):
        super().restore(pids)
        self.restored_event.set()


async def test_slow_mute_does_not_hold_up_the_lifecycle_publisher():
    d, bus = DelayedDucker(), EventBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(), ducker=d)
    c.attach()
    published = asyncio.create_task(bus.publish(VoiceSessionStarted()))
    try:
        assert await asyncio.to_thread(d.entered.wait, 2)
        await asyncio.wait_for(asyncio.shield(published), 0.5)
        assert not d.release.is_set() and d.muted_calls == 0
    finally:
        d.release.set()
        await published
        await _settle(c)
        await c.restore()


@pytest.mark.parametrize("cleanup", ["hangup", "toggle", "restore"])
async def test_late_mute_is_restored_after_session_cleanup(cleanup):
    d = DelayedDucker()
    c = AudioDuckController(bus=FakeBus(), cfg=_cfg(), ducker=d)
    await c._on_start(object())
    try:
        assert await asyncio.to_thread(d.entered.wait, 2)
        if cleanup == "hangup":
            ending = asyncio.create_task(c._on_end(object()))
        elif cleanup == "toggle":
            ending = asyncio.create_task(c.set_enabled(False))
        else:
            ending = asyncio.create_task(c.restore())
        await asyncio.sleep(0)
        assert not ending.done()
    finally:
        d.release.set()
    await ending
    await _settle(c)
    assert d.muted_calls == 1 and d.restored == [[111, 222]]
    assert c._muted == []


async def test_cancelled_hangup_waiter_keeps_late_mute_cleanup_owned():
    d = DelayedDucker()
    c = AudioDuckController(bus=FakeBus(), cfg=_cfg(), ducker=d)
    await c._on_start(object())
    try:
        assert await asyncio.to_thread(d.entered.wait, 2)
        ending = asyncio.create_task(c._on_end(object()))
        await asyncio.sleep(0)
        ending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ending
    finally:
        d.release.set()
    await _settle(c)
    assert d.restored == [[111, 222]] and c._muted == []


@pytest.mark.parametrize("cancel_waiter", [False, True])
async def test_shutdown_bounds_native_wait_and_restores_the_late_mute(cancel_waiter, caplog):
    d = DelayedDucker()
    c = AudioDuckController(bus=FakeBus(), cfg=_cfg(), ducker=d)
    await c._on_start(object())
    try:
        assert await asyncio.to_thread(d.entered.wait, 2)
        if cancel_waiter:
            (muting,) = c._tasks
            muting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await muting
        shutdown = asyncio.create_task(asyncio.to_thread(c.restore_sync))
        await asyncio.wait_for(asyncio.shield(shutdown), 1)
        assert c._closed.is_set() and not d.release.is_set()
        assert d.restored == []
        assert "shutdown cleanup is pending" in caplog.text
    finally:
        d.release.set()
    await shutdown
    assert await asyncio.to_thread(d.restored_event.wait, 2)
    await _settle(c)
    assert d.restored == [[111, 222]] and c._muted == []
    c.restore_sync()
    assert d.restored == [[111, 222]]
    await c._on_start(object())
    await _settle(c)
    assert d.muted_calls == 1


async def test_shutdown_before_queued_mute_prevents_a_later_native_effect():
    d = FakeDucker()
    c = AudioDuckController(bus=FakeBus(), cfg=_cfg(), ducker=d)
    await c._on_start(object())
    c.restore_sync()
    await _settle(c)
    assert d.muted_calls == 0 and d.restored == []


async def test_old_restore_waiting_for_mute_does_not_unmute_a_new_session():
    d = DelayedDucker()
    c = AudioDuckController(bus=FakeBus(), cfg=_cfg(), ducker=d)
    await c._on_start(object())
    try:
        assert await asyncio.to_thread(d.entered.wait, 2)
        ending = asyncio.create_task(c._on_end(object()))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not c._duck_requested
        await c._on_start(object())
    finally:
        d.release.set()
    await ending
    await _settle(c)
    assert d.muted_calls == 1 and d.restored == []
    assert c._muted == [111, 222]
    await c._on_end(VoiceSessionEnded())
    assert d.restored == [[111, 222]]
