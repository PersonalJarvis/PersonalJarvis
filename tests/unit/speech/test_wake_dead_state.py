"""No dead state may block waking (mission "Done when": "no dead state blocks
waking").

Two concrete permanent-dead-state paths found in the wake plumbing:

1. ``_wake_loop`` used to ``await asyncio.Event().wait()`` on a FRESH event that
   nobody ever sets when both detectors are disabled — a permanent sleep that
   not even a later live wake-word change could re-arm (only an app restart).
   The loop must instead park on ``_wake_reload_event`` so a ``set_wake_plan``
   re-enabling a detector wakes it back up, in-app.

2. ``set_wake_plan(engine="stt_match")`` on a box where the local Whisper engine
   cannot be built used to leave a permanently-parked dead listener. It must park
   RECOVERABLY on ``_wake_reload_event`` (both detectors off = the honest
   hotkey-only mode per the 2026-07-04 product rule, re-armable in-app by a later
   ``set_wake_plan``), and must NOT fall back to a branded 'Hey Rhasspy' model
   (listening for a word the user never says).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.speech.pipeline import PipelineState, SpeechPipeline
from jarvis.speech.wake_phrase import resolve_wake_plan


@pytest.fixture(autouse=True)
def _no_vosk_model(monkeypatch):
    """Isolate from any per-install Vosk model: this module pins the
    stt_match/none dead-state contracts. vosk_kws has its own suite in
    test_wake_plan_vosk.py."""
    import jarvis.speech.wake_phrase as wp

    monkeypatch.setattr(wp, "resolve_vosk_model_path", lambda *_: None)


def _cfg(**kw: object) -> SimpleNamespace:
    base = dict(
        phrase="Neko",
        engine="auto",
        custom_model_path="",
        sensitivity=0.5,
        fuzzy_match_ratio=0.8,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_wake_loop_parks_recoverably_when_both_detectors_disabled() -> None:
    """With both detectors off, the loop must PARK on the reload event (not a
    dead one). Re-enabling a detector + flipping _wake_reload_event must re-arm
    it — proving recovery is reachable in-app with no restart."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._openwakeword_enabled = False
    pipe._whisper_wake_enabled = False
    pipe._wake_reload_event = asyncio.Event()
    pipe._state = PipelineState.IDLE
    pipe._muted = False
    pipe._activation_gate = lambda: True
    pipe._wake_phrase_label = "Neko"

    ran = asyncio.Event()

    async def _fake_run_parallel_wake() -> None:
        ran.set()
        await asyncio.sleep(3600)  # park so the loop doesn't spin

    pipe._run_parallel_wake = _fake_run_parallel_wake  # type: ignore[method-assign]

    task = asyncio.create_task(pipe._wake_loop())
    try:
        await asyncio.sleep(0.05)
        assert not ran.is_set(), "loop ran wake while both detectors disabled"

        # In-app recovery: a live wake-plan change enables a detector + signals.
        pipe._openwakeword_enabled = True
        pipe._wake_reload_event.set()

        await asyncio.wait_for(ran.wait(), timeout=2.0)  # re-armed, not dead
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


def _shell_for_set_wake_plan() -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._wake_plan = None
    pipe._wake_matcher = None
    pipe._wake_phrase_label = None
    pipe._stt = None
    pipe._probe_stt = None
    pipe._whisper_wake = None
    pipe._openwakeword_enabled = False
    pipe._whisper_wake_enabled = False
    pipe._wake_reload_event = asyncio.Event()
    pipe._config = SimpleNamespace(stt=SimpleNamespace(language=None))
    return pipe


def test_set_wake_plan_stt_match_without_whisper_is_hotkey_only(monkeypatch) -> None:
    """A live switch to a custom phrase on a box where the wake Whisper cannot be
    built must NOT fall back to a branded 'Hey Rhasspy' model (product rule
    2026-07-04). It arms NO detector — wake OFF, hotkey/PTT activation — which is
    a RECOVERABLE parked state (the wake loop parks on _wake_reload_event so a
    later set_wake_plan re-arms it), not the old permanent dead listener."""
    import jarvis.plugins.stt as stt_pkg

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("no faster-whisper installed")

    monkeypatch.setattr(stt_pkg, "build_wake_whisper", _boom, raising=False)

    plan = resolve_wake_plan(_cfg(phrase="Neko"), local_whisper_available=True)
    assert plan.engine == "stt_match" and plan.needs_local_whisper is True

    pipe = _shell_for_set_wake_plan()
    pipe.set_wake_plan(plan)

    # No branded fallback: both detectors off (honest hotkey-only mode). This is
    # recoverable, not dead — the wake loop parks on _wake_reload_event.
    assert pipe._openwakeword_enabled is False
    assert pipe._whisper_wake_enabled is False


def test_set_wake_plan_stt_match_with_whisper_uses_rolling_wake(monkeypatch) -> None:
    """Control: when a wake Whisper CAN be built, the custom phrase still routes
    to the RollingWhisperWake transcript matcher (no regression)."""
    import jarvis.plugins.stt as stt_pkg

    class _FakeWakeWhisper:
        async def transcribe_pcm(self, *_a: object, **_k: object) -> object:
            return SimpleNamespace(text="", confidence=0.0, segments=())

    monkeypatch.setattr(
        stt_pkg, "build_wake_whisper", lambda *a, **k: _FakeWakeWhisper(), raising=False
    )

    plan = resolve_wake_plan(_cfg(phrase="Neko"), local_whisper_available=True)
    pipe = _shell_for_set_wake_plan()
    pipe.set_wake_plan(plan)

    assert pipe._openwakeword_enabled is False
    assert pipe._whisper_wake_enabled is True
    assert pipe._wake_listening_enabled() is True
