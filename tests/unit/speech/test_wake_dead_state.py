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

3. A wake loop whose only blocker is the MICROPHONE PERMISSION must park, not
   poll: it opens one background episode (``ensure(interactive=False)``: never an
   ask), then waits on ``_wake_reload_event``, which a microphone
   ``PermissionResolved``, ``set_wake_plan`` or the fallback timeout sets. A grant
   given later in System Settings therefore wakes it in-app, with no restart and
   no 0.25 s re-probing. Mute / a running dictation keep their short poll.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.audio.capture import MicrophoneAccessError
from jarvis.core.bus import EventBus
from jarvis.core.events import PermissionResolved
from jarvis.platform.permission_service import PermissionOutcome
from jarvis.platform.permissions import PermissionId
from jarvis.speech.pipeline import PipelineState, SpeechPipeline
from jarvis.speech.wake_phrase import resolve_wake_plan
from tests.fakes.fake_permission_service import FakePermissionService


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
    2026-07-04). It arms NO detector — wake OFF, Call-shortcut activation — which is
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


# --------------------------------------------------------------------------
# The wake loop parks on the microphone permission (it never polls, never asks)
# --------------------------------------------------------------------------


class _Wake:
    """A wake-loop harness: a gate the test opens, a permission fake, no audio."""

    def __init__(self, *, granted: bool = False) -> None:
        self.granted = granted
        self.gate_reads = 0
        self.listened = asyncio.Event()
        self.permission = FakePermissionService()
        self.permission.script(PermissionId.MICROPHONE, PermissionOutcome.PENDING)
        self.pipe = SpeechPipeline.__new__(SpeechPipeline)
        pipe = self.pipe
        pipe._openwakeword_enabled = True
        pipe._whisper_wake_enabled = False
        pipe._wake_reload_event = asyncio.Event()
        pipe._wake_parked_on_permission = False
        pipe._state = PipelineState.IDLE
        pipe._muted = False
        pipe._dictation_task = None
        pipe._dictation_wake_block_until = 0.0
        pipe._wake_phrase_label = "Neko"
        pipe._permission_gate = self.permission
        pipe._activation_gate = self._gate
        pipe._run_parallel_wake = self._listen  # type: ignore[method-assign]

    def _gate(self) -> bool:
        self.gate_reads += 1
        return self.granted

    async def _listen(self) -> None:
        self.listened.set()
        await asyncio.sleep(3600)  # listening: the loop does not spin

    def grant(self) -> None:
        """The user allowed the microphone: the gate reads open from now on."""
        self.granted = True
        self.permission.grant(PermissionId.MICROPHONE)

    async def run(self, body) -> None:  # noqa: ANN001
        task = asyncio.create_task(self.pipe._wake_loop())
        try:
            await body()
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass


async def test_wake_loop_parks_on_the_permission_instead_of_polling() -> None:
    """The old loop slept 0.25 s and re-probed the permission forever (4 reads a
    second). The new one reads the gate, opens ONE background episode and waits."""
    wake = _Wake()

    async def body() -> None:
        await asyncio.sleep(0.8)
        assert not wake.listened.is_set(), "the wake loop listened without a grant"
        assert wake.pipe._wake_parked_on_permission is True
        # One iteration reads the gate for the decision and once more for the log
        # reason; the old 0.25 s churn would have read it about ten times by now.
        assert wake.gate_reads <= 3, f"the parked wake loop re-probed {wake.gate_reads} times"

    await wake.run(body)

    calls = wake.permission.ensure_calls(PermissionId.MICROPHONE)
    assert len(calls) == 1, "the park opens exactly one episode"
    assert calls[0].feature == "wake_word"
    assert calls[0].interactive is False, "a background consumer never asks the OS"
    assert wake.permission.native_free()


async def test_a_microphone_grant_wakes_the_parked_loop_without_a_restart() -> None:
    wake = _Wake()
    bus = EventBus()
    bus.subscribe(PermissionResolved, wake.pipe._on_permission_resolved)

    async def body() -> None:
        await asyncio.sleep(0.1)
        assert wake.pipe._wake_parked_on_permission is True
        wake.grant()
        await bus.publish(
            PermissionResolved(permissions=("microphone",), feature="wake_word", granted=True)
        )
        await asyncio.wait_for(wake.listened.wait(), timeout=2.0)

    await wake.run(body)
    assert wake.pipe._wake_parked_on_permission is False


async def test_only_a_microphone_grant_wakes_the_parked_loop() -> None:
    wake = _Wake()

    async def body() -> None:
        await asyncio.sleep(0.1)
        handler = wake.pipe._on_permission_resolved
        await handler(PermissionResolved(permissions=("microphone",), granted=False))
        await handler(PermissionResolved(permissions=("accessibility",), granted=True))
        await asyncio.sleep(0.1)
        assert wake.pipe._wake_parked_on_permission is True
        assert not wake.pipe._wake_reload_event.is_set()

    await wake.run(body)


async def test_a_grant_for_a_loop_that_is_not_parked_does_not_restart_the_wake_mic() -> None:
    """A dictation's grant must not tear down a wake capture that is already running."""
    wake = _Wake(granted=True)

    async def body() -> None:
        await asyncio.wait_for(wake.listened.wait(), timeout=2.0)
        assert wake.pipe._wake_parked_on_permission is False
        await wake.pipe._on_permission_resolved(
            PermissionResolved(permissions=("microphone",), feature="dictation", granted=True)
        )
        assert not wake.pipe._wake_reload_event.is_set()

    await wake.run(body)
    assert wake.permission.ensure_calls() == [], "an open gate never opens an episode"


async def test_a_wake_plan_change_also_wakes_the_parked_loop() -> None:
    """``set_wake_plan`` flips the same event: the park is recoverable in-app."""
    wake = _Wake()

    async def body() -> None:
        await asyncio.sleep(0.1)
        wake.grant()  # the grant landed, but its edge never arrived
        wake.pipe._wake_reload_event.set()  # what set_wake_plan does
        await asyncio.wait_for(wake.listened.wait(), timeout=2.0)

    await wake.run(body)


async def test_the_fallback_timeout_rereads_the_gate_when_an_edge_was_missed(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_mod, "_WAKE_PERMISSION_PARK_S", 0.05)
    wake = _Wake()

    async def body() -> None:
        await asyncio.sleep(0.1)
        assert not wake.listened.is_set()
        wake.granted = True  # no event, no listener call: only the timeout can see it
        await asyncio.wait_for(wake.listened.wait(), timeout=2.0)

    await wake.run(body)


async def test_a_lost_grant_at_open_parks_instead_of_looping_on_an_exception() -> None:
    """``MicrophoneAccessError`` from the capture open is a permission answer, not a
    crash: no traceback, no 0.5 s retry churn; the loop parks and waits for the grant."""
    wake = _Wake(granted=True)  # the gate said yes, the open then found it gone
    attempts = 0

    async def _refused() -> None:
        nonlocal attempts
        attempts += 1
        wake.granted = False
        raise MicrophoneAccessError("Microphone access is not granted for Personal Jarvis.")

    wake.pipe._run_parallel_wake = _refused  # type: ignore[method-assign]

    async def body() -> None:
        await asyncio.sleep(0.8)
        assert attempts == 1, "the loop retried a refused open instead of parking"
        assert wake.pipe._wake_parked_on_permission is True

    await wake.run(body)
    assert len(wake.permission.ensure_calls(PermissionId.MICROPHONE)) == 1


async def test_mute_keeps_the_short_poll_and_never_opens_a_permission_episode() -> None:
    """Only a missing PERMISSION parks. A mute changes on its own clock (no event
    announces the unmute), so the loop keeps polling the state - and asks nothing."""
    wake = _Wake(granted=True)
    wake.pipe._muted = True

    async def body() -> None:
        await asyncio.sleep(0.1)
        assert not wake.listened.is_set()
        assert wake.pipe._wake_parked_on_permission is False
        wake.pipe._muted = False
        await asyncio.wait_for(wake.listened.wait(), timeout=1.0)

    await wake.run(body)
    assert wake.permission.calls == []


async def test_switching_the_wake_word_off_while_parked_stops_touching_the_permission(
    monkeypatch,
) -> None:
    """P7: a wake word the user switched OFF owns no permission. The park used to
    re-open a ``wake_word`` episode on every wake-up because only the pre-loop block
    looked at the detector flags."""
    monkeypatch.setattr(pipeline_mod, "_WAKE_PERMISSION_PARK_S", 0.05)
    wake = _Wake()

    async def body() -> None:
        await asyncio.sleep(0.2)
        assert wake.pipe._wake_parked_on_permission is True
        before = len(wake.permission.ensure_calls(PermissionId.MICROPHONE))
        assert before >= 1
        # What ``set_wake_activation(False)`` does: detectors off, reload event set.
        wake.pipe._openwakeword_enabled = False
        wake.pipe._whisper_wake_enabled = False
        wake.pipe._wake_reload_event.set()
        await asyncio.sleep(0.4)  # many park intervals
        assert wake.pipe._wake_parked_on_permission is False
        assert len(wake.permission.ensure_calls(PermissionId.MICROPHONE)) == before
        assert not wake.listened.is_set()
        # Switching it back on re-arms the loop in-app (the park is recoverable).
        wake.grant()
        wake.pipe._openwakeword_enabled = True
        wake.pipe._wake_reload_event.set()
        await asyncio.wait_for(wake.listened.wait(), timeout=2.0)

    await wake.run(body)


async def test_the_park_itself_opens_no_episode_for_a_switched_off_wake_word() -> None:
    """The guard also holds for a caller that read the gate before the switch-off."""
    wake = _Wake()
    wake.pipe._openwakeword_enabled = False
    wake.pipe._whisper_wake_enabled = False

    await wake.pipe._park_until_microphone_allowed()

    assert wake.permission.calls == []
    assert wake.pipe._wake_parked_on_permission is False


async def test_the_real_pipeline_subscribes_the_wake_loop_to_microphone_grants() -> None:
    """The wiring, not just the handler: a ``PermissionResolved(microphone)`` published
    on the bus a REAL ``SpeechPipeline`` was built with wakes its parked loop well
    inside the 30 s fallback. Deleting the ``subscribe`` line in ``__init__`` makes it
    wait for that fallback instead."""
    bus = EventBus()
    pipe = SpeechPipeline(tts=_NoTTS(), bus=bus, enable_whisper_wake=False)
    assert pipeline_mod._WAKE_PERMISSION_PARK_S >= 10.0, "the test needs a long fallback"
    permission = FakePermissionService()
    permission.script(PermissionId.MICROPHONE, PermissionOutcome.PENDING)
    pipe._permission_gate = permission
    pipe._openwakeword_enabled = True
    pipe._whisper_wake_enabled = False
    pipe._wake_phrase_label = "Neko"
    pipe._state = PipelineState.IDLE
    state = {"granted": False}
    pipe._activation_gate = lambda: state["granted"]
    listened = asyncio.Event()

    async def _listen() -> None:
        listened.set()
        await asyncio.sleep(3600)

    pipe._run_parallel_wake = _listen  # type: ignore[method-assign]
    task = asyncio.create_task(pipe._wake_loop())
    try:
        await asyncio.sleep(0.1)
        assert pipe._wake_parked_on_permission is True
        state["granted"] = True
        await bus.publish(
            PermissionResolved(permissions=("microphone",), feature="wake_word", granted=True)
        )
        await asyncio.wait_for(listened.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass


class _NoTTS:
    name = "no-tts"
    supports_streaming = False

    async def synthesize(self, text: str, language_code=None):  # noqa: ANN001, ANN201
        if False:  # pragma: no cover - never called
            yield b""
