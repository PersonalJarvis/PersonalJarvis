"""Microphone capture asks the permission layer ONCE, at open, and never per frame.

The old gate probed ``runtime_access_granted`` twice per frame and refused when OUR
preflight said "not granted", so macOS was never asked from the feature. The
contract these tests pin (design-v2 3.5, row "audio/capture.py"):

* one service ``ensure`` when the stream opens: an interactive (user-started)
  open may make macOS ask, a background open never does, and CoreAudio is not
  touched unless the answer is GRANTED / NOT_REQUIRED;
* no probing per frame; the watchdog's existing 1 s tick makes one cheap
  ``check``, and a lost grant ends the stream with ``MicrophoneAccessError``
  carrying the ``EnsureResult`` and publishes the episode through the service;
* macOS only: 5 s of exact zeros while the OS says granted is reported once and
  the stream stays open;
* off macOS nothing changes and the permission port is never touched.

The OS is ``FakeTCC`` (a stateful TCC simulator); no ``unittest.mock``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from jarvis.audio import capture
from jarvis.core.bus import EventBus, get_default_bus, reset_default_bus
from jarvis.core.events import PermissionNeeded
from jarvis.core.protocols import AudioChunk
from jarvis.platform.permission_service import PermissionOutcome, PermissionService
from jarvis.platform.permissions import PermissionId, PermissionState
from tests.fakes.fake_permission_service import FakePermissionService, make_result
from tests.fakes.fake_tcc import (
    DialogPolicy,
    FakeAudioInput,
    FakeTCC,
    TccService,
    install_port,
    make_darwin_port,
    make_non_darwin_port,
)

MIC = TccService.MICROPHONE
_ZERO_FRAME = b"\x00\x00" * capture.BLOCKSIZE


class _Stream:
    """A minimal ``sounddevice.InputStream`` for the legacy ``access_gate`` seam."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def _use_audio(monkeypatch: pytest.MonkeyPatch, audio: FakeAudioInput) -> None:
    monkeypatch.setattr(capture, "sd", SimpleNamespace(InputStream=audio))


def _darwin(
    monkeypatch: pytest.MonkeyPatch, **tcc_kwargs
) -> tuple[FakeTCC, FakeAudioInput]:
    port, tcc = make_darwin_port(**tcc_kwargs)
    install_port(monkeypatch, port)
    audio = FakeAudioInput(tcc)
    _use_audio(monkeypatch, audio)
    return tcc, audio


async def _wait_for(predicate: Callable[[], bool], *, timeout_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


async def _pump_zeros(stream, *, frames: int, interval_s: float = 0.01) -> None:
    """Deliver exact-zero frames through the capture callback (a muted input)."""
    for _ in range(frames):
        stream.callback(_ZERO_FRAME, capture.BLOCKSIZE, None, None)
        await asyncio.sleep(interval_s)


@pytest.fixture
def owned_service() -> Callable[..., PermissionService]:
    """A service the test owns (zero GRANTED cache, so a revoke is seen at once)."""
    made: list[PermissionService] = []

    def build(**kwargs) -> PermissionService:
        service = PermissionService(granted_ttl_s=0.0, watch_interval_s=0.05, **kwargs)
        made.append(service)
        return service

    yield build
    for service in made:
        service._shutdown()


# ---------------------------------------------------------------------------
# The ask: one request at open, then the stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_interactive_open_makes_one_request_and_opens_only_after_the_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, audio = _darwin(monkeypatch)  # not_determined, the dialog answer is Allow

    mic = capture.MicrophoneCapture(device=0, interactive=True)
    async with mic:
        assert tcc.state(MIC).value == "granted"

    (request,) = tcc.requests(MIC)
    assert request.outcome.value == "dialog_shown"
    assert len(audio.starts) == 1
    assert tcc.implicit_prompts() == []  # opening the device never made macOS ask
    assert not mic.revoked


@pytest.mark.asyncio
async def test_open_waits_for_the_dialog_before_any_device_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, audio = _darwin(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)

    mic = capture.MicrophoneCapture(device=0, interactive=True, permission_wait_s=5.0)
    opening = asyncio.create_task(mic.__aenter__())
    await _wait_for(lambda: tcc.dialog_open(MIC))
    assert audio.streams == []  # the dialog is on screen: no stream object yet
    tcc.answer(MIC, DialogPolicy.ALLOW)
    await asyncio.wait_for(opening, timeout=5.0)

    assert len(tcc.requests(MIC)) == 1
    assert len(audio.starts) == 1
    await mic.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_edge_gesture_open_with_a_pending_dialog_refuses_and_opens_no_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held key passes wait 0: the dialog is shown, the press is not retroactive."""
    tcc, audio = _darwin(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)

    mic = capture.MicrophoneCapture(device=0, interactive=True)
    with pytest.raises(capture.MicrophoneAccessError) as refused:
        await mic.__aenter__()

    assert refused.value.result.outcome is PermissionOutcome.PENDING
    assert len(tcc.requests(MIC)) == 1
    assert audio.streams == []


@pytest.mark.asyncio
async def test_denied_open_asks_nothing_opens_no_device_and_carries_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, audio = _darwin(monkeypatch)
    tcc.deny(MIC)

    for _ in range(2):  # a second press must not ask again either
        mic = capture.MicrophoneCapture(device=0, interactive=True)
        with pytest.raises(capture.MicrophoneAccessError) as refused:
            await mic.__aenter__()
        result = refused.value.result
        assert result is not None and not result.granted
        assert result.outcome is PermissionOutcome.DENIED and result.reason == "denied"
        assert result.agent_detail.startswith("[permission_needed:microphone] ")
        assert str(refused.value) == result.user_detail

    assert tcc.requests() == []  # not even an ignored request
    assert audio.streams == []


@pytest.mark.asyncio
async def test_background_open_never_asks_and_never_opens_a_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, audio = _darwin(monkeypatch)

    mic = capture.MicrophoneCapture(device=0, permission_feature="wake_word")  # interactive=False
    with pytest.raises(capture.MicrophoneAccessError) as refused:
        await mic.__aenter__()

    tcc.assert_no_prompts()
    assert audio.streams == []
    assert refused.value.result.reason == "not_determined"
    from jarvis.platform.permission_service import get_permission_service

    (episode,) = get_permission_service().outstanding()
    assert episode.feature == "wake_word" and episode.origin == "background"


@pytest.mark.asyncio
async def test_a_granted_open_needs_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc, audio = _darwin(monkeypatch, granted=[MIC])

    async with capture.MicrophoneCapture(device=0):
        pass

    assert tcc.requests() == [] and len(audio.starts) == 1


def test_unknown_permission_feature_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        capture.MicrophoneCapture(device=0, permission_feature="not_a_feature")


# ---------------------------------------------------------------------------
# Injected gates (test seams)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_passes_the_feature_and_the_gesture_flag_to_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = FakePermissionService()
    _use_audio(monkeypatch, FakeAudioInput(None))

    mic = capture.MicrophoneCapture(
        device=0, permission_feature="dictation", interactive=True, permission_gate=gate
    )
    async with mic:
        pass

    (call,) = gate.ensure_calls(PermissionId.MICROPHONE)
    assert (call.feature, call.interactive, call.wait_s) == ("dictation", True, 0.0)


@pytest.mark.asyncio
async def test_a_denied_gate_fails_before_portaudio_open(monkeypatch: pytest.MonkeyPatch) -> None:
    opens: list[object] = []

    def _input_stream(**_kwargs):
        opens.append(object())
        return _Stream()

    monkeypatch.setattr(capture, "sd", SimpleNamespace(InputStream=_input_stream))
    gate = FakePermissionService({PermissionId.MICROPHONE: PermissionOutcome.DENIED})
    mic = capture.MicrophoneCapture(device=0, permission_gate=gate)

    with pytest.raises(capture.MicrophoneAccessError) as refused:
        await mic.__aenter__()

    assert opens == []
    assert refused.value.result.outcome is PermissionOutcome.DENIED


@pytest.mark.asyncio
async def test_the_legacy_access_gate_replaces_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, audio = _darwin(monkeypatch)
    mic = capture.MicrophoneCapture(device=0, access_gate=lambda: False)

    with pytest.raises(capture.MicrophoneAccessError) as refused:
        await mic.__aenter__()

    assert refused.value.result.outcome is PermissionOutcome.DENIED
    assert audio.streams == [] and tcc.calls == ()  # the service was never consulted


# ---------------------------------------------------------------------------
# No per-frame probing; the watchdog tick owns the revoke check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frames_cost_no_permission_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc, audio = _darwin(monkeypatch, granted=[MIC])
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 3600.0)

    async with capture.MicrophoneCapture(device=0) as mic:
        (stream,) = audio.starts
        mark = tcc.mark()
        frames = mic.stream()
        for _ in range(60):
            stream.pump()
            chunk = await asyncio.wait_for(anext(frames), timeout=2.0)
            assert isinstance(chunk, AudioChunk) and any(chunk.pcm)
        await frames.aclose()
        # Neither a frame nor an idle wait asks the OS anything (the old gate
        # called ``runtime_access_granted`` twice per frame and every 0.25 s).
        assert tcc.calls_since(mark) == ()


@pytest.mark.asyncio
async def test_an_idle_stream_makes_no_probe_between_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, audio = _darwin(monkeypatch, granted=[MIC])
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 3600.0)

    async with capture.MicrophoneCapture(device=0) as mic:
        mark = tcc.mark()
        reader = asyncio.create_task(anext(mic.stream()))
        await asyncio.sleep(0.6)  # more than two of the old 0.25 s idle probes
        assert tcc.calls_since(mark) == ()
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader


@pytest.mark.asyncio
async def test_mid_stream_revoke_ends_the_stream_and_publishes_one_episode(
    monkeypatch: pytest.MonkeyPatch, owned_service: Callable[..., PermissionService]
) -> None:
    tcc, audio = _darwin(monkeypatch, granted=[MIC])
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    bus = EventBus()
    seen: list[PermissionNeeded] = []

    async def _collect(event: PermissionNeeded) -> None:
        seen.append(event)

    bus.subscribe(PermissionNeeded, _collect)
    service = owned_service()
    service.attach_bus(bus, asyncio.get_running_loop())

    mic = capture.MicrophoneCapture(
        device=0, permission_feature="dictation", interactive=True, permission_gate=service
    )
    with pytest.raises(capture.MicrophoneAccessError) as revoked:
        async with mic:
            (stream,) = audio.starts
            stream.pump()
            frames = mic.stream()
            assert any((await asyncio.wait_for(anext(frames), timeout=2.0)).pcm)
            tcc.deny(MIC)  # the user flips the switch off in System Settings
            await asyncio.wait_for(anext(frames), timeout=3.0)

    assert mic.revoked
    assert revoked.value.result.outcome is PermissionOutcome.DENIED
    assert stream.stopped and stream.closed
    await _wait_for(lambda: len(seen) >= 1)
    await asyncio.sleep(0.1)  # give a (wrong) second publish the chance to show up
    (event,) = seen
    assert (event.permissions, event.feature, event.reason) == (
        ("microphone",),
        "dictation",
        "denied",
    )
    assert event.origin == "user"  # a user-started capture may raise the card
    assert tcc.requests() == []  # losing the stream never asks again


@pytest.mark.asyncio
async def test_mid_stream_revoke_of_a_background_capture_is_published_as_background(
    monkeypatch: pytest.MonkeyPatch, owned_service: Callable[..., PermissionService]
) -> None:
    tcc, audio = _darwin(monkeypatch, granted=[MIC])
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    service = owned_service()

    mic = capture.MicrophoneCapture(
        device=0, permission_feature="wake_word", permission_gate=service
    )
    with pytest.raises(capture.MicrophoneAccessError):
        async with mic:
            tcc.deny(MIC)
            await asyncio.wait_for(anext(mic.stream()), timeout=3.0)

    (episode,) = service.outstanding()
    assert (episode.feature, episode.origin, episode.reason) == (
        "wake_word",
        "background",
        "denied",
    )


@pytest.mark.asyncio
async def test_revoke_wakes_a_reader_parked_on_a_silent_stream(
    monkeypatch: pytest.MonkeyPatch, owned_service: Callable[..., PermissionService]
) -> None:
    tcc, _audio = _darwin(monkeypatch, granted=[MIC])
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    mic = capture.MicrophoneCapture(device=0, permission_gate=owned_service())

    with pytest.raises(capture.MicrophoneAccessError):
        async with mic:
            reader = asyncio.create_task(anext(mic.stream()))  # no frame ever arrives
            await asyncio.sleep(0.05)
            tcc.deny(MIC)
            await asyncio.wait_for(reader, timeout=3.0)


@pytest.mark.asyncio
async def test_a_regrant_between_check_and_ensure_does_not_end_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``check`` says no, the ensure that follows says yes: the capture stays alive."""
    gate = FakePermissionService()
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    _use_audio(monkeypatch, FakeAudioInput(None))
    mic = capture.MicrophoneCapture(device=0, permission_gate=gate)

    async with mic:
        # check() reads "denied", the ensure that follows answers granted.
        gate.script(
            PermissionId.MICROPHONE,
            make_result(
                PermissionId.MICROPHONE, PermissionOutcome.GRANTED, state=PermissionState.DENIED
            ),
        )
        await asyncio.sleep(0.1)
        assert not mic.revoked
        assert gate.check_calls(PermissionId.MICROPHONE)  # the tick did look


@pytest.mark.asyncio
async def test_a_gate_that_raises_in_a_tick_does_not_end_the_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real service never raises, but an injected ``PermissionGate`` may: the tick
    is treated as "not revoked" and the watchdog task keeps running (it would
    otherwise die silently and take stall recovery with it)."""

    class _RaisingCheck(FakePermissionService):
        raised = 0

        def check(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            type(self).raised += 1
            raise RuntimeError("custom gate failure")

    gate = _RaisingCheck()
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    _use_audio(monkeypatch, FakeAudioInput(None))
    mic = capture.MicrophoneCapture(device=0, permission_gate=gate)

    async with mic:
        await _wait_for(lambda: _RaisingCheck.raised >= 3)  # ticked repeatedly after raising
        assert not mic.revoked
        assert mic._watchdog_task is not None and not mic._watchdog_task.done()


@pytest.mark.asyncio
async def test_a_single_unreadable_tick_is_forgiven_but_a_persistent_one_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = FakePermissionService()
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    _use_audio(monkeypatch, FakeAudioInput(None))
    mic = capture.MicrophoneCapture(device=0, permission_gate=gate)

    with pytest.raises(capture.MicrophoneAccessError):
        async with mic:
            gate.script(PermissionId.MICROPHONE, PermissionOutcome.UNAVAILABLE)
            await asyncio.wait_for(anext(mic.stream()), timeout=3.0)

    assert mic.revoked


@pytest.mark.asyncio
async def test_the_legacy_gate_revoke_closes_the_device(monkeypatch: pytest.MonkeyPatch) -> None:
    allowed = True
    stream = _Stream()
    monkeypatch.setattr(capture, "sd", SimpleNamespace(InputStream=lambda **_kwargs: stream))
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    mic = capture.MicrophoneCapture(device=0, access_gate=lambda: allowed)

    with pytest.raises(capture.MicrophoneAccessError) as revoked:
        async with mic:
            iterator = mic.stream()
            mic._safe_put(
                AudioChunk(pcm=b"\x00\x01", sample_rate=16_000, timestamp_ns=1, channels=1)
            )
            assert (await anext(iterator)).timestamp_ns == 1
            allowed = False
            await asyncio.wait_for(anext(iterator), timeout=3.0)

    assert revoked.value.result is not None
    assert stream.started and stream.stopped and stream.closed


# ---------------------------------------------------------------------------
# Watchdog restarts are never a gesture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stall_restart_ensures_non_interactively(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = FakePermissionService()
    audio = FakeAudioInput(None)
    _use_audio(monkeypatch, audio)
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    monkeypatch.setattr(capture.MicrophoneCapture, "_STALL_THRESHOLD_S", 0.05)

    mic = capture.MicrophoneCapture(
        device=0, permission_feature="dictation", interactive=True, permission_gate=gate
    )
    async with mic:
        await _wait_for(lambda: mic.restart_count >= 1)

    first, restart = gate.ensure_calls(PermissionId.MICROPHONE)[:2]
    assert first.interactive is True
    assert restart.interactive is False and restart.feature == "dictation"


@pytest.mark.asyncio
async def test_a_stall_restart_that_loses_the_grant_ends_the_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = FakePermissionService()
    gate.script(PermissionId.MICROPHONE, PermissionOutcome.GRANTED, PermissionOutcome.DENIED)
    _use_audio(monkeypatch, FakeAudioInput(None))
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    monkeypatch.setattr(capture.MicrophoneCapture, "_STALL_THRESHOLD_S", 0.05)

    mic = capture.MicrophoneCapture(device=0, permission_gate=gate)
    with pytest.raises(capture.MicrophoneAccessError):
        async with mic:
            await asyncio.wait_for(anext(mic.stream()), timeout=3.0)

    assert mic.revoked


# ---------------------------------------------------------------------------
# Digital-silence guard (macOS only)
# ---------------------------------------------------------------------------


@pytest.fixture
def default_bus_events() -> list[PermissionNeeded]:
    reset_default_bus()
    seen: list[PermissionNeeded] = []

    async def _collect(event: PermissionNeeded) -> None:
        seen.append(event)

    get_default_bus().subscribe(PermissionNeeded, _collect)
    yield seen
    reset_default_bus()


@pytest.mark.asyncio
async def test_five_seconds_of_zeros_while_granted_reports_once_and_keeps_the_stream(
    monkeypatch: pytest.MonkeyPatch, default_bus_events: list[PermissionNeeded]
) -> None:
    _tcc, audio = _darwin(monkeypatch, granted=[MIC])
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    monkeypatch.setattr(capture, "_SILENCE_REPORT_AFTER_S", 0.05)

    mic = capture.MicrophoneCapture(device=0, permission_feature="voice", interactive=True)
    async with mic:
        (stream,) = audio.starts
        await _pump_zeros(stream, frames=40)  # ~0.4 s of digital silence, state GRANTED
        await _wait_for(lambda: len(default_bus_events) >= 1)
        await _pump_zeros(stream, frames=10)  # still silent: no second report

        assert not mic.revoked and not stream.closed  # a notice, not a verdict
        frames = mic.stream()
        chunk = await asyncio.wait_for(anext(frames), timeout=2.0)
        assert chunk.pcm.strip(b"\x00") == b""  # the zeros were not dropped or faked
        await frames.aclose()

    (event,) = default_bus_events
    assert event.permissions == ("microphone",)
    # Background even for a user-started capture: the OS says granted, so this is
    # more likely a muted input than a denial and must not raise the floating card.
    assert event.feature == "voice" and event.reason == "denied" and event.origin == "background"
    assert "denied or muted" in event.detail


@pytest.mark.asyncio
async def test_audible_frames_never_trigger_the_silence_guard(
    monkeypatch: pytest.MonkeyPatch, default_bus_events: list[PermissionNeeded]
) -> None:
    _tcc, audio = _darwin(monkeypatch, granted=[MIC])
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    monkeypatch.setattr(capture, "_SILENCE_REPORT_AFTER_S", 0.05)

    async with capture.MicrophoneCapture(device=0):
        (stream,) = audio.starts
        for _ in range(30):
            stream.pump()
            await asyncio.sleep(0.01)
        # One real sample in an otherwise silent block still counts as audio.
        for _ in range(30):
            frame = bytearray(_ZERO_FRAME)
            frame[10] = 1
            stream.callback(bytes(frame), capture.BLOCKSIZE, None, None)
            await asyncio.sleep(0.01)

    assert default_bus_events == []


@pytest.mark.asyncio
async def test_zeros_with_a_grant_that_is_gone_end_as_a_revoke_not_as_a_silence_notice(
    monkeypatch: pytest.MonkeyPatch,
    default_bus_events: list[PermissionNeeded],
    owned_service: Callable[..., PermissionService],
) -> None:
    """A denied mic feeds zeros; the re-check sees the denial and the revoke path takes over."""
    tcc, audio = _darwin(monkeypatch, granted=[MIC])
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    monkeypatch.setattr(capture, "_SILENCE_REPORT_AFTER_S", 0.05)

    mic = capture.MicrophoneCapture(device=0, permission_gate=owned_service())
    with pytest.raises(capture.MicrophoneAccessError):
        async with mic:
            (stream,) = audio.starts
            tcc.deny(MIC)
            await _pump_zeros(stream, frames=40)
            await asyncio.wait_for(anext(mic.stream()), timeout=3.0)

    assert default_bus_events == []  # the service's episode is the notice here


# ---------------------------------------------------------------------------
# Non-darwin: nothing changes, the permission port is never touched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform_name", ["win32", "linux"])
@pytest.mark.asyncio
async def test_off_macos_the_capture_never_touches_the_permission_port(
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    default_bus_events: list[PermissionNeeded],
) -> None:
    port, tcc = make_non_darwin_port(platform_name)
    install_port(monkeypatch, port)
    audio = FakeAudioInput(None)  # no TCC off macOS: nothing to consult
    _use_audio(monkeypatch, audio)
    monkeypatch.setattr(capture.MicrophoneCapture, "_WATCHDOG_TICK_S", 0.01)
    monkeypatch.setattr(capture, "_SILENCE_REPORT_AFTER_S", 0.02)

    mic = capture.MicrophoneCapture(device=0, interactive=True)
    async with mic:
        (stream,) = audio.starts
        await _pump_zeros(stream, frames=15)  # silence off macOS is not a permission signal
        frames = mic.stream()
        chunk = await asyncio.wait_for(anext(frames), timeout=2.0)
        await frames.aclose()

    assert chunk.sample_rate == capture.SAMPLE_RATE
    assert stream.closed and not mic.revoked
    assert default_bus_events == []
    tcc.assert_silent()
