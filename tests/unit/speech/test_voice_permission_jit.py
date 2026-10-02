"""Voice gestures ask for the microphone at the moment they need it; boot never does.

The contract (docs/macos-permissions.md, 4.6 rows on the voice gates, and 4.7 boot rule):

* A GESTURE (the dictation key or button, push-to-talk, the call key, "speak in
  this conversation") calls ``ensure(MICROPHONE, wait_s=0)``. The first press of an
  undecided microphone makes macOS ask and is refused for now (``DictationRefused``
  with the existing ``microphone_unavailable`` reason, plus the ``PermissionNeeded``
  episode the service publishes); nothing starts retroactively once it is answered,
  the next press does. A denial is stable: the second press adds no native request.
* ``dictation_available()`` no longer has a permission term, so the mic button is
  visible on a fresh Mac.
* BOOT asks nothing: with every permission ``not_determined`` the voice pipeline
  warms up and (wake word on or off) performs no request, no implicit prompt and
  opens no input stream; for an upgrader with every grant present there are zero
  requests and zero ``PermissionNeeded`` episodes.
* Off macOS nothing is consulted at all (the FakeTCC call log stays empty).

The consumer cases use ``FakePermissionService``; the macOS behaviour cases run the
REAL service over ``FakeTCC``. No audio is started anywhere.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.bus import EventBus
from jarvis.core.events import DictationRefused, PermissionNeeded, PermissionResolved
from jarvis.platform.permission_service import (
    PermissionOutcome,
    get_permission_service,
)
from jarvis.platform.permissions import PermissionId
from jarvis.speech.pipeline import PipelineState, SpeechPipeline
from jarvis.ui.desktop_app import _local_voice_permission_granted, _local_voice_permission_usable
from tests.fakes.fake_permission_service import FakePermissionService, make_result
from tests.fakes.fake_tcc import (
    DialogPolicy,
    FakeAudioInput,
    FakeTCC,
    TccService,
    install_port,
    make_non_darwin_port,
)

_MIC = PermissionId.MICROPHONE


class _StubSTT:
    async def transcribe_pcm(self, pcm: bytes):  # pragma: no cover - never called
        raise AssertionError("no transcription in this unit test")


def _pipeline(
    *,
    permission: object | None = None,
    bus: EventBus | None = None,
    activation_gate=None,  # noqa: ANN001
    user_gate=None,  # noqa: ANN001
) -> SpeechPipeline:
    """A pipeline reduced to what the voice gestures read; no audio, no providers."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._bus = bus
    pipe._utterance_stt = _StubSTT()
    pipe._input_device = "default"
    pipe._input_priority = ()
    pipe._dictation_task = None
    pipe._dictation_stop_event = asyncio.Event()
    pipe._dictation_wake_block_until = 0.0
    pipe._ptt_mode = False
    pipe._state = PipelineState.IDLE
    pipe._muted = False
    pipe._call_event = asyncio.Event()
    pipe._ptt_release_event = asyncio.Event()
    pipe._explicit_call_pending = False
    pipe._runtime_loop = None
    pipe._brain = None
    pipe._last_wake_keyword = ""
    pipe._hangup_event = asyncio.Event()
    if permission is not None:
        pipe._permission_gate = permission
    if activation_gate is not None:
        pipe._activation_gate = activation_gate
    if user_gate is not None:
        pipe._user_activation_gate = user_gate
    return pipe


def _record_commits(pipe: SpeechPipeline) -> list[str]:
    """Replace the recording start so a started dictation never touches a microphone."""
    started: list[str] = []

    def _commit(loop, *, target, source):  # noqa: ANN001, ANN202
        started.append(source)
        return True

    pipe._commit_dictation = _commit  # type: ignore[method-assign]
    return started


async def _drain() -> None:
    for _ in range(6):
        await asyncio.sleep(0)


class _Seen:
    """Collects the dictation refusals and permission episodes a test provokes."""

    def __init__(self, bus: EventBus) -> None:
        self.refused: list[DictationRefused] = []
        self.needed: list[PermissionNeeded] = []
        bus.subscribe(DictationRefused, self._refused)
        bus.subscribe(PermissionNeeded, self._needed)

    async def _refused(self, event: DictationRefused) -> None:
        self.refused.append(event)

    async def _needed(self, event: PermissionNeeded) -> None:
        self.needed.append(event)


# --------------------------------------------------------------------------
# Consumer contract (FakePermissionService)
# --------------------------------------------------------------------------


def test_the_mic_button_is_visible_on_a_fresh_mac() -> None:
    """``dictation_available`` has no permission term: the first press is what asks."""
    gate = FakePermissionService()
    gate.script(_MIC, PermissionOutcome.PENDING)
    pipe = _pipeline(permission=gate, activation_gate=lambda: False, user_gate=lambda: False)

    assert pipe.dictation_available() is True
    assert gate.calls == []


def test_the_mic_button_still_needs_an_stt_and_an_input_device() -> None:
    pipe = _pipeline()
    pipe._utterance_stt = None
    assert pipe.dictation_available() is False
    pipe = _pipeline()
    pipe._input_device = "none"
    assert pipe.dictation_available() is False


async def test_the_first_dictation_press_asks_and_refuses_with_the_existing_reason() -> None:
    bus = EventBus()
    seen = _Seen(bus)
    gate = FakePermissionService()
    gate.script(_MIC, PermissionOutcome.PENDING)
    pipe = _pipeline(permission=gate, bus=bus)
    started = _record_commits(pipe)

    assert pipe.start_dictation(source="hold_key") is False
    await _drain()

    (call,) = gate.ensure_calls(_MIC)
    assert (call.feature, call.interactive, call.wait_s) == ("dictation", True, 0.0)
    assert [event.reason for event in seen.refused] == ["microphone_unavailable"]
    # The sentence is the permission layer's fixed template (no exception text).
    assert seen.refused[0].detail == make_result(_MIC, PermissionOutcome.PENDING).user_detail
    assert "Microphone" in seen.refused[0].detail
    assert started == [], "nothing may start retroactively"


async def test_the_press_after_the_grant_starts_the_dictation() -> None:
    gate = FakePermissionService()
    gate.script(_MIC, PermissionOutcome.PENDING, PermissionOutcome.GRANTED)
    pipe = _pipeline(permission=gate)
    started = _record_commits(pipe)

    assert pipe.start_dictation(source="hold_key") is False
    assert started == []
    assert pipe.start_dictation(source="hold_key") is True
    assert started == ["hold_key"]


async def test_a_refusing_user_gate_blocks_dictation_even_when_the_service_says_granted() -> None:
    bus = EventBus()
    seen = _Seen(bus)
    pipe = _pipeline(permission=FakePermissionService(), bus=bus, user_gate=lambda: False)
    started = _record_commits(pipe)

    assert pipe.start_dictation() is False
    await _drain()

    assert [event.reason for event in seen.refused] == ["microphone_unavailable"]
    assert started == []


async def test_dictation_without_an_stt_provider_does_not_ask_the_os() -> None:
    """Asking for the microphone just to say "no provider" would be a pointless dialog."""
    gate = FakePermissionService()
    gate.script(_MIC, PermissionOutcome.PENDING)
    pipe = _pipeline(permission=gate)
    pipe._utterance_stt = None
    pipe._publish_event_soon = lambda event: None  # type: ignore[method-assign]

    assert pipe.start_dictation() is False
    assert gate.calls == []


def test_ptt_press_asks_once_per_press_and_arms_only_on_a_grant() -> None:
    gate = FakePermissionService()
    gate.script(_MIC, PermissionOutcome.PENDING, PermissionOutcome.GRANTED)
    pipe = _pipeline(permission=gate)

    pipe._on_ptt_press()
    assert pipe._ptt_mode is False
    assert not pipe._call_event.is_set()

    pipe._ptt_key_seen_at = 0.0
    pipe._on_ptt_press()
    assert pipe._ptt_mode is True
    assert pipe._call_event.is_set()

    calls = gate.ensure_calls(_MIC)
    assert [(c.feature, c.interactive, c.wait_s) for c in calls] == [("voice", True, 0.0)] * 2


def test_a_muted_ptt_press_never_asks_the_os() -> None:
    gate = FakePermissionService()
    gate.script(_MIC, PermissionOutcome.PENDING)
    pipe = _pipeline(permission=gate)
    pipe._muted = True

    pipe._on_ptt_press()

    assert gate.calls == []
    assert pipe._ptt_mode is False


def test_a_denied_ptt_press_does_not_arm_a_recording() -> None:
    gate = FakePermissionService()
    gate.script(_MIC, PermissionOutcome.DENIED)
    pipe = _pipeline(permission=gate)

    pipe._on_ptt_press()

    assert pipe._ptt_mode is False
    assert not pipe._call_event.is_set()
    assert len(gate.ensure_calls(_MIC)) == 1


def test_the_call_key_asks_and_opens_a_session_only_on_a_grant() -> None:
    gate = FakePermissionService()
    gate.script(_MIC, PermissionOutcome.PENDING, PermissionOutcome.GRANTED)
    pipe = _pipeline(permission=gate)

    pipe._dispatch_hotkey_event("call")
    assert not pipe._call_event.is_set()
    assert pipe._explicit_call_pending is False

    pipe._dispatch_hotkey_event("call")
    assert pipe._call_event.is_set()
    assert pipe._explicit_call_pending is True
    assert [(c.feature, c.interactive) for c in gate.ensure_calls(_MIC)] == [("voice", True)] * 2


def test_request_voice_session_asks_and_arms_only_on_a_grant() -> None:
    gate = FakePermissionService()
    gate.script(_MIC, PermissionOutcome.PENDING, PermissionOutcome.GRANTED)
    brain = SimpleNamespace(seeded=None)
    brain.seed_history = lambda turns: setattr(brain, "seeded", list(turns))
    pipe = _pipeline(permission=gate)
    pipe._brain = brain

    assert pipe.request_voice_session(seed_messages=[("user", "hi")]) is False
    assert not pipe._call_event.is_set()
    assert brain.seeded is None, "a refused request must not pollute the brain history"

    assert pipe.request_voice_session(seed_messages=[("user", "hi")]) is True
    assert pipe._call_event.is_set()
    assert brain.seeded == [("user", "hi")]


def test_an_unreadable_permission_refuses_the_gesture() -> None:
    class _Broken:
        def ensure(self, *_a: object, **_k: object) -> object:
            raise RuntimeError("native failure with a /secret/path")

    pipe = _pipeline(permission=_Broken())

    pipe._on_ptt_press()

    assert pipe._ptt_mode is False


# --------------------------------------------------------------------------
# macOS behaviour: the REAL service over FakeTCC
# --------------------------------------------------------------------------


def _mac(monkeypatch: pytest.MonkeyPatch, **tcc_kwargs: object) -> FakeTCC:
    tcc = FakeTCC(**tcc_kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    return tcc


async def test_first_press_makes_one_microphone_request_and_nothing_starts_retroactively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _mac(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    bus = EventBus()
    seen = _Seen(bus)
    get_permission_service().attach_bus(bus, asyncio.get_running_loop())
    pipe = _pipeline(bus=bus)
    started = _record_commits(pipe)

    assert pipe.start_dictation(source="hold_key") is False  # the dialog is up
    await _drain()

    assert len(tcc.requests(TccService.MICROPHONE)) == 1
    assert tcc.implicit_prompts() == []
    assert [event.reason for event in seen.refused] == ["microphone_unavailable"]
    (needed,) = seen.needed
    assert needed.permissions == ("microphone",)
    assert (needed.feature, needed.reason, needed.phase, needed.origin) == (
        "dictation",
        "not_determined",
        "os_dialog",
        "user",
    )

    # The user answers Allow in the macOS dialog: still nothing starts by itself.
    tcc.answer(TccService.MICROPHONE, DialogPolicy.ALLOW)
    await _drain()
    assert started == []
    await asyncio.sleep(0.3)  # a human takes longer than the service's 250 ms negative cache

    # "Allowed - press again": the second press starts, without a second request.
    assert pipe.start_dictation(source="hold_key") is True
    assert started == ["hold_key"]
    assert len(tcc.requests(TccService.MICROPHONE)) == 1


async def test_denied_microphone_second_press_adds_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _mac(monkeypatch, default_policy=DialogPolicy.DENY)
    bus = EventBus()
    seen = _Seen(bus)
    get_permission_service().attach_bus(bus, asyncio.get_running_loop())
    pipe = _pipeline(bus=bus)
    _record_commits(pipe)

    assert pipe.start_dictation() is False
    await _drain()
    assert len(tcc.requests(TccService.MICROPHONE)) == 1

    assert pipe.start_dictation() is False
    await _drain()
    assert len(tcc.requests(TccService.MICROPHONE)) == 1, "a denial is a stable state"
    assert tcc.ignored_requests() == [], "macOS was not even asked a second time"
    assert [event.reason for event in seen.refused] == ["microphone_unavailable"] * 2
    assert any(event.reason == "denied" and event.phase == "blocked" for event in seen.needed)


async def test_a_denied_microphone_refuses_ptt_and_speak_with_one_card_per_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The microphone was denied in an earlier run: the silent user predicate is closed,
    yet the PTT press and the speak button still reach ``ensure`` so the refusal is
    never silent. They publish ONE ``PermissionNeeded`` (denied, blocked, user) per
    episode, make no native request and never arm anything."""
    tcc = _mac(monkeypatch)
    tcc.deny(TccService.MICROPHONE)
    bus = EventBus()
    seen = _Seen(bus)
    get_permission_service().attach_bus(bus, asyncio.get_running_loop())
    pipe = _pipeline(
        bus=bus, user_gate=lambda: _local_voice_permission_usable(platform_name="darwin")
    )
    assert pipe._user_capture_allowed() is False, "the harness must model the closed predicate"

    pipe._on_ptt_press()
    await _drain()
    assert pipe._ptt_mode is False
    assert not pipe._call_event.is_set()
    assert [(e.permissions, e.reason, e.phase, e.origin, e.feature) for e in seen.needed] == [
        (("microphone",), "denied", "blocked", "user", "voice")
    ]

    # Another gesture family in the same episode adds neither a request nor a card.
    pipe._ptt_key_seen_at = 0.0
    pipe._on_ptt_press()
    assert pipe.request_voice_session() is False
    await _drain()
    assert len(seen.needed) == 1, "a refusal publishes once per episode"
    assert not pipe._call_event.is_set()
    assert tcc.requests() == []


async def test_dictation_is_unaffected_when_the_microphone_is_granted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _mac(monkeypatch, granted=(TccService.MICROPHONE,))
    pipe = _pipeline()
    started = _record_commits(pipe)

    assert pipe.start_dictation() is True
    assert started == ["api"]
    assert tcc.requests() == []


async def test_non_macos_gestures_consult_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    bus = EventBus()
    seen = _Seen(bus)
    get_permission_service().attach_bus(bus, asyncio.get_running_loop())
    pipe = _pipeline(bus=bus)
    started = _record_commits(pipe)

    assert pipe.start_dictation() is True
    pipe._on_ptt_press()
    assert pipe._ptt_mode is True
    pipe._ptt_mode = False
    pipe._state = PipelineState.IDLE
    assert pipe.request_voice_session() is True
    await _drain()

    assert started == ["api"]
    tcc.assert_silent()
    assert seen.needed == [] and seen.refused == []


# --------------------------------------------------------------------------
# The boot rule: nothing is asked at launch
# --------------------------------------------------------------------------


class _Capture:
    """Stands in for ``MicrophoneCapture``: opening one at boot is a failure."""

    constructed: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        type(self).constructed.append(kwargs)


class _Boot:
    """The voice pipeline's boot (warm-up, then the wake loop when it is on) over FakeTCC."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tcc: FakeTCC, *, wake_word: bool) -> None:
        self.tcc = tcc
        self.audio = FakeAudioInput(tcc, caller="boot")
        _Capture.constructed = []
        monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", _Capture)
        import jarvis.audio.capture as capture

        monkeypatch.setattr(capture, "sd", SimpleNamespace(InputStream=self.audio))
        monkeypatch.setattr(
            pipeline_mod,
            "wait_for_stable_audio_devices",
            lambda **kw: {"available": True, "device_count": 1, "stable": True, "waited_s": 0.0},
        )
        monkeypatch.setattr(pipeline_mod, "iter_all_start_ack", lambda: [])
        self.bus = EventBus()
        self.seen = _Seen(self.bus)
        get_permission_service().attach_bus(self.bus, asyncio.get_running_loop())
        self.listened = asyncio.Event()

        pipe = SpeechPipeline.__new__(SpeechPipeline)
        self.pipe = pipe
        pipe._bus = self.bus
        pipe._output_device = "auto-headset"
        pipe._player = SimpleNamespace(set_device=lambda device: None, play_pcm=_no_audio)
        pipe._stt = None
        pipe._whisper_wake_enabled = False
        pipe._openwakeword_enabled = wake_word
        pipe._vad = SimpleNamespace(_ensure_model=lambda: None)
        pipe._tts = SimpleNamespace(_ensure_client=lambda: None)
        pipe._wake = SimpleNamespace(start=_no_audio)
        pipe._ack_phrase = "Ja?"
        pipe._ack_pcm = b""
        pipe._task_ack_pcm = {}
        pipe._wake_reload_event = asyncio.Event()
        pipe._wake_parked_on_permission = False
        pipe._state = PipelineState.IDLE
        pipe._muted = False
        pipe._dictation_task = None
        pipe._dictation_wake_block_until = 0.0
        pipe._wake_phrase_label = "Hey Jarvis"
        # The gates are the desktop app's own: silent reads of the real service.
        pipe._activation_gate = lambda: _local_voice_permission_granted(platform_name="darwin")
        pipe._user_activation_gate = lambda: _local_voice_permission_usable(platform_name="darwin")
        pipe._run_parallel_wake = self._listen  # type: ignore[method-assign]

    async def _listen(self) -> None:
        self.listened.set()
        await asyncio.sleep(3600)

    async def run(self) -> None:
        """Warm up, then start the wake loop exactly when ``run()`` would."""
        await self.pipe._warmup()
        await self.pipe._deferred_warmup_task
        wake_task = (
            asyncio.create_task(self.pipe._wake_loop())
            if self.pipe._wake_listening_enabled()
            else None
        )
        try:
            await asyncio.sleep(0.2)
            await _drain()
        finally:
            if wake_task is not None:
                wake_task.cancel()
                try:
                    await wake_task
                except asyncio.CancelledError:
                    pass
            await self.pipe._cancel_warmup_background()

    def assert_nothing_was_asked(self) -> None:
        assert self.tcc.requests() == [], self.tcc.format_log()
        assert self.tcc.implicit_prompts() == [], self.tcc.format_log()
        assert self.audio.starts == [] and self.audio.streams == []
        assert _Capture.constructed == [], "boot opened a microphone capture"


async def _no_audio(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.mark.parametrize("wake_word", [False, True], ids=["wake_word_off", "wake_word_on"])
async def test_boot_with_every_permission_not_determined_asks_nothing(
    monkeypatch: pytest.MonkeyPatch, wake_word: bool
) -> None:
    tcc = _mac(monkeypatch)
    boot = _Boot(monkeypatch, tcc, wake_word=wake_word)

    await boot.run()

    boot.assert_nothing_was_asked()
    assert not boot.listened.is_set(), "the wake loop listened without a grant"
    if wake_word:
        # The wake word waits (parked) and says so in the status snapshot only: one
        # background-origin episode, never a toast, never a native ask.
        origins = {(n.feature, n.origin, n.reason) for n in boot.seen.needed}
        assert origins == {("wake_word", "background", "not_determined")}
    else:
        assert boot.seen.needed == []
        assert get_permission_service().outstanding() == []


@pytest.mark.parametrize("wake_word", [False, True], ids=["wake_word_off", "wake_word_on"])
async def test_boot_of_an_upgrader_with_every_grant_present_shows_no_card_and_makes_no_request(
    monkeypatch: pytest.MonkeyPatch, wake_word: bool
) -> None:
    tcc = _mac(monkeypatch, granted=tuple(TccService))
    boot = _Boot(monkeypatch, tcc, wake_word=wake_word)

    await boot.run()

    assert tcc.requests() == [], tcc.format_log()
    assert tcc.implicit_prompts() == []
    assert boot.seen.needed == [], "zero permission episodes for an upgrader"
    assert get_permission_service().outstanding() == []
    assert boot.listened.is_set() is wake_word  # granted: the wake loop listens at once


@pytest.mark.parametrize(
    ("granted", "permissions", "parked", "wakes"),
    [
        (True, ("microphone",), True, True),
        (False, ("microphone",), True, False),  # an expired episode is not a grant: no churn
        (True, ("accessibility",), True, False),
        (True, ("microphone",), False, False),  # a wake capture that runs is never torn down
    ],
)
async def test_only_a_microphone_grant_wakes_a_parked_wake_loop(
    granted: bool, permissions: tuple[str, ...], parked: bool, wakes: bool
) -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._wake_reload_event = asyncio.Event()
    pipe._wake_parked_on_permission = parked

    await pipe._on_permission_resolved(
        PermissionResolved(permissions=permissions, feature="wake_word", granted=granted)
    )

    assert pipe._wake_reload_event.is_set() is wakes


async def test_boot_on_a_non_macos_host_never_consults_tcc(monkeypatch: pytest.MonkeyPatch) -> None:
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    boot = _Boot(monkeypatch, tcc, wake_word=True)

    await boot.run()

    tcc.assert_silent()
    assert boot.seen.needed == []
    assert boot.listened.is_set()
