"""AudioDuckController: session-boundary mute/restore with a fake ducker."""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.audio.ducking.controller import AudioDuckController
from jarvis.audio.ducking.protocol import DuckPermissionReport


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


async def test_mutes_on_start_restores_on_end_when_enabled():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())
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
    await c.set_enabled(False)
    assert d.restored == [[111, 222]] and c._muted == []
    assert c._cfg.ducking.enabled is False


async def test_double_start_does_not_remute():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    await bus.subs["VoiceSessionStarted"](object())
    assert d.muted_calls == 1  # already muted → no second sweep


async def test_restore_idempotent_when_nothing_muted():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await c.restore()
    assert d.restored == []  # nothing muted → no restore call


async def test_new_session_during_restore_delay_keeps_music_muted():
    import asyncio

    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True, delay=50), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())  # session 1 mutes [111, 222]
    end_task = asyncio.create_task(bus.subs["VoiceSessionEnded"](object()))
    await asyncio.sleep(0.01)  # let _on_end capture the generation + enter the sleep
    await bus.subs["VoiceSessionStarted"](object())  # session 2 bumps the generation
    await end_task  # session 1's delayed restore wakes → generation changed → SKIP
    assert d.restored == []  # music stayed muted for the still-active session 2
    assert c._muted == [111, 222]


async def test_restore_sync_unmutes_on_shutdown():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d)
    c.attach()
    await bus.subs["VoiceSessionStarted"](object())
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
    await bus.subs["VoiceSessionEnded"](object())
    assert d.prewarms == 0 and d.muted_calls == 1
