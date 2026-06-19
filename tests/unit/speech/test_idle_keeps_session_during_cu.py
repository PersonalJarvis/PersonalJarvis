"""Idle-timeout must not hang up while a Computer-Use mission is running.

Live failure 2026-06-10 20:46 (data/jarvis_desktop.log): the user dispatched
"open Chrome and find news on X" as a CU mission; 40 s later the voice
session's idle timeout fired ("⏲ Idle-Timeout — lege auf.") because the user
naturally said nothing while watching the agent work. The mission kept
clicking invisibly for two more minutes (orb gone, session closed) and
finally spoke its failure announcement into a dead session.

The session already knows how to stay open for OpenClaw spawns
(``_live_spawn_watchdogs``); these tests pin the same courtesy for live
Computer-Use missions via ``cu_mission_active()`` /
``SpeechPipeline._background_mission_in_flight``.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk
from jarvis.harness.computer_use_context import (
    cu_mission_active,
    register_active_cu_token,
)
from jarvis.speech.pipeline import SpeechPipeline


@dataclass
class FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, language_code))
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


class FakeToken:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self._cancelled


@pytest.fixture(autouse=True)
def _clear_cu_token():
    register_active_cu_token(None)
    yield
    register_active_cu_token(None)


# ---------------------------------------------------------------------------
# cu_mission_active — the process-wide "a CU mission is running" probe
# ---------------------------------------------------------------------------


def test_cu_mission_active_false_without_token() -> None:
    assert cu_mission_active() is False


def test_cu_mission_active_true_with_live_token() -> None:
    register_active_cu_token(FakeToken())
    assert cu_mission_active() is True


def test_cu_mission_active_false_for_cancelled_token() -> None:
    # A cancelled mission is winding down — it must not keep the session
    # open (the hangup that cancelled it wants the session CLOSED).
    register_active_cu_token(FakeToken(cancelled=True))
    assert cu_mission_active() is False


# ---------------------------------------------------------------------------
# Pipeline consumers: idle-override probe + single-turn hangup decision
# ---------------------------------------------------------------------------


def _pipeline(bus: EventBus) -> SpeechPipeline:
    return SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)


@pytest.mark.asyncio
async def test_background_mission_in_flight_sees_cu_mission() -> None:
    pipe = _pipeline(EventBus())
    assert pipe._background_mission_in_flight() is False
    register_active_cu_token(FakeToken())
    assert pipe._background_mission_in_flight() is True
    register_active_cu_token(None)
    assert pipe._background_mission_in_flight() is False


@pytest.mark.asyncio
async def test_active_session_survives_idle_windows_while_cu_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the REAL ``_active_session`` loop (the exact branch that fired
    '⏲ Idle-Timeout — lege auf.' in the 2026-06-10 20:46 live run) with a
    fake mic/VAD: while a CU token is registered the session must survive
    idle window after idle window; the moment the mission ends (token
    cleared, as the harness does in its ``finally``) the very next idle
    window hangs up with the normal idle reason."""
    import asyncio

    from jarvis.sessions.constants import HANGUP_IDLE_TIMEOUT
    from jarvis.speech import pipeline as pipeline_mod

    pipe = _pipeline(EventBus())
    pipe._idle_timeout_s = 0.05  # tiny idle window so the test is fast

    class FakeMic:
        async def __aenter__(self) -> "FakeMic":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        def stream(self):
            async def gen():
                await asyncio.Event().wait()  # pragma: no cover — never yields
                yield b""
            return gen()

    class FakeVAD:
        def utterances(self, stream):
            async def gen():
                await asyncio.Event().wait()  # silence: never an utterance
                yield b""  # pragma: no cover
            return gen()

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda device=None: FakeMic(),
    )
    pipe._vad = FakeVAD()

    register_active_cu_token(FakeToken())
    session = asyncio.create_task(pipe._active_session())
    # ~6 idle windows elapse — pre-fix the session hung up after ONE.
    await asyncio.sleep(0.3)
    assert not session.done(), (
        "the session must stay open across idle windows while a Computer-Use "
        "mission is running"
    )

    # Mission over: the harness clears the token in its finally block.
    register_active_cu_token(None)
    reason = await asyncio.wait_for(session, timeout=2.0)
    assert reason == HANGUP_IDLE_TIMEOUT


@pytest.mark.asyncio
async def test_finish_after_response_stays_listening_during_cu_mission() -> None:
    """Single-turn mode: with a CU mission in flight the turn is not
    semantically complete — the pipeline must stay LISTENING so the
    progress/failure announcements land in a live session (and the idle
    branch in ``_active_session`` uses the same probe to not hang up)."""
    from jarvis.speech.pipeline import TurnTakingState

    pipe = _pipeline(EventBus())
    pipe._continue_listening_after_response = False  # single-turn mode

    register_active_cu_token(FakeToken())
    assert await pipe._finish_after_response(barged=False) is True
    assert pipe._turn_state == TurnTakingState.LISTENING
    assert pipe._session_end_reason is None

    # Mission over (harness cleared the token in its finally) — single-turn
    # mode must hang up normally again.
    register_active_cu_token(None)
    assert await pipe._finish_after_response(barged=False) is False
    assert pipe._session_end_reason is not None


@pytest.mark.asyncio
async def test_session_survives_idle_window_after_mission_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a Computer-Use mission ENDS and its failure/completion readback is
    spoken out-of-band, the user must get a fresh idle window to respond — the
    session must NOT hang up on the stale idle window armed mid-mission.

    Live failure 2026-06-18 08:52 (session b55afd02): a CU mission failed at
    08:52:02, Jarvis spoke 'Das am Bildschirm hat nicht geklappt …', and the
    idle window armed at the previous 08:51:48 boundary expired at 08:52:18 and
    hung up with ``idle_timeout`` ~10 s after the user heard the failure — no
    hangup command was ever given. The readback is delivered via
    ``_on_announcement``, OFF the ``_active_session`` loop, so it never reset the
    idle window. It must re-arm a fresh window (bounded by the grace), exactly
    like a normal inline answer hands the floor back to the user.
    """
    import asyncio

    from jarvis.core.events import AnnouncementRequested
    from jarvis.sessions.constants import HANGUP_IDLE_TIMEOUT
    from jarvis.speech import pipeline as pipeline_mod

    pipe = _pipeline(EventBus())
    pipe._idle_timeout_s = 0.05          # tiny idle window so the test is fast
    pipe._post_readback_grace_s = 0.6    # generous grace, >> one idle window

    class FakeMic:
        async def __aenter__(self) -> FakeMic:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        def stream(self):
            async def gen():
                await asyncio.Event().wait()  # pragma: no cover — never yields
                yield b""
            return gen()

    class FakeVAD:
        def utterances(self, stream):
            async def gen():
                await asyncio.Event().wait()  # silence: never an utterance
                yield b""  # pragma: no cover
            return gen()

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda device=None: FakeMic(),
    )
    pipe._vad = FakeVAD()

    register_active_cu_token(FakeToken())
    session = asyncio.create_task(pipe._active_session())
    await asyncio.sleep(0.2)  # several idle windows — open while the mission runs
    assert not session.done()

    # Mission ends: the harness clears the token in its finally, THEN the mission
    # completion path speaks the failure readback (the live 130 ms ordering).
    register_active_cu_token(None)
    await pipe._on_announcement(
        AnnouncementRequested(
            text="Das am Bildschirm hat nicht geklappt.",
            language="de",
            priority="normal",
        )
    )

    # The readback just handed the floor back to the user. The session must
    # survive the next idle windows for the grace duration — pre-fix it hung up
    # on the very first post-mission idle window (~0.05 s later).
    await asyncio.sleep(0.25)  # 0.25 < 0.6 grace, but five idle windows
    assert not session.done(), (
        "the session hung up on the stale mid-mission idle window — a spoken "
        "mission readback must re-arm a fresh idle window so the user can respond"
    )

    # Once the grace elapses with continued silence, idle-timeout hangs up
    # normally — never wedged open.
    reason = await asyncio.wait_for(session, timeout=2.0)
    assert reason == HANGUP_IDLE_TIMEOUT
