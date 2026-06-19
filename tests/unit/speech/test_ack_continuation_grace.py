"""The Pre-Thinking-Ack must never speak over a user who is still talking.

Live incident (2026-06-17 12:42, session 2260c291): the user spoke ONE long
sentence with a natural breath-pause after a grammatically complete question
("Kannst du … das Google Cloud-System einstellen? … das die Cloud-CLI nutzt
und mir sagt …"). The VAD endpointed at the pause, the brain entered
PROCESSING, and ~689 ms later the streaming Flash-Brain ack spoke
"Ich rufe die Konfigurationsparameter für Google Cloud auf." — ~795 ms BEFORE
the VAD detected the user's continuation. So the ack fired in the gap *after*
the endpoint but *before* the resume crossed the VAD threshold. It happened
twice in the same turn.

Root cause: the streaming ack path speaks its first sentence the instant it is
ready, with no settle window (deliberately, for latency — pipeline.py:2263),
and its redundancy gate only suppresses JARVIS_SPEAKING/LISTENING/IDLE, never
USER_SPEAKING. Doctrine AD-OE5 mandates the opposite: speak ONLY at the next
turn-boundary, never mid-utterance.

Contract pinned here:
  * Before the FIRST audible sentence, the ack waits out a continuation grace.
    If the turn leaves PROCESSING during the grace (user resumed → continuation
    interrupt, or the brain already answered), the ack is dropped.
  * The ack speaks ONLY while the turn is still PROCESSING the committed
    utterance — any non-PROCESSING state suppresses it.
  * A genuinely committed turn (no continuation) still gets its ack.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.core.events import AnnouncementRequested
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


class _FakeAckBrain:
    """Streams the given ack sentences when ``run_stream`` is consumed."""

    def __init__(self, sentences: list[str]) -> None:
        self._sentences = sentences

    async def run_stream(self, utterance: str, language: str = "de"):
        for sentence in self._sentences:
            yield sentence


def _make_ack_pipeline(
    ack_brain: _FakeAckBrain,
    *,
    turn_state: TurnTakingState,
    grace_ms: int,
    streaming: bool = True,
) -> tuple[SpeechPipeline, list[AnnouncementRequested]]:
    """Bare pipeline wired with exactly what ``_spawn_flash_brain_ack`` reads.

    Published events are captured directly at the ``_publish_event`` boundary —
    that event IS the ack's audible output (it travels to ``_on_announcement``).
    """
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._ack_brain = ack_brain  # type: ignore[attr-defined]
    pipe._config = SimpleNamespace(  # type: ignore[attr-defined]
        ack_brain=SimpleNamespace(
            streaming=streaming,
            ack_continuation_grace_ms=grace_ms,
            suppress_if_brain_faster_than_ms=2000,
        )
    )
    pipe._turn_state = turn_state  # type: ignore[attr-defined]
    pipe._latency_tracker = None  # type: ignore[attr-defined]

    published: list[AnnouncementRequested] = []

    async def _record(event: object) -> None:
        if isinstance(event, AnnouncementRequested):
            published.append(event)

    pipe._publish_event = _record  # type: ignore[assignment]
    return pipe, published


@pytest.mark.asyncio
async def test_ack_dropped_when_user_resumes_during_grace() -> None:
    """The live incident: ack ready while PROCESSING, user resumes during the
    grace → the ack must be dropped, never spoken over the continuation."""
    ack_brain = _FakeAckBrain(["Ich rufe die Konfigurationsparameter auf."])
    pipe, published = _make_ack_pipeline(
        ack_brain, turn_state=TurnTakingState.PROCESSING, grace_ms=300
    )

    async def _resume_mid_grace() -> None:
        # The continuation interrupt flips the turn out of PROCESSING.
        await asyncio.sleep(0.02)
        pipe._turn_state = TurnTakingState.USER_SPEAKING

    flip = asyncio.create_task(_resume_mid_grace())
    await pipe._spawn_flash_brain_ack("…", "de")
    await flip

    assert published == [], (
        "the ack spoke even though the user resumed during the continuation "
        "grace — it talked over the user mid-sentence (AD-OE5 violation)"
    )


@pytest.mark.asyncio
async def test_ack_dropped_when_already_user_speaking() -> None:
    """If the user is already (re)speaking when the ack sentence arrives, the
    ack must be suppressed by the gate — USER_SPEAKING is not PROCESSING."""
    ack_brain = _FakeAckBrain(["Ich schaue kurz nach."])
    pipe, published = _make_ack_pipeline(
        ack_brain, turn_state=TurnTakingState.USER_SPEAKING, grace_ms=200
    )

    await pipe._spawn_flash_brain_ack("…", "de")

    assert published == [], (
        "the ack spoke while the turn-state was USER_SPEAKING — the gate must "
        "only let the ack through while the turn is still PROCESSING"
    )


@pytest.mark.asyncio
async def test_ack_speaks_on_committed_turn_without_continuation() -> None:
    """The happy path is preserved: a committed turn (stays PROCESSING through
    the grace, no continuation) still gets its bridging ack."""
    ack_brain = _FakeAckBrain(["Einen Moment, ich schaue nach."])
    pipe, published = _make_ack_pipeline(
        ack_brain, turn_state=TurnTakingState.PROCESSING, grace_ms=60
    )

    await pipe._spawn_flash_brain_ack("…", "de")

    assert len(published) == 1, "a committed turn lost its bridging ack"
    assert published[0].text == "Einen Moment, ich schaue nach."
    assert published[0].kind == "preamble"
