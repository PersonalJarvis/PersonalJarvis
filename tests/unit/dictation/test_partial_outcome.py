"""A dictation that lost words must not report itself as a success (F12).

The live history row this file exists for: 37.7 seconds of speech, a ``429`` in
the ``error`` column, raw text ``"Aether, Render,"`` — and outcome
``inserted``. Because ``inserted`` is not in ``RECOVERABLE_OUTCOMES`` the audio
sidecar was never written and the history offered no Restore, so the dictation
was unrecoverable BY CONSTRUCTION. Every assertion below pins one half of that
chain: the outcome the user is shown, and the recording that lets them get the
rest of their words back.

The distinction under test is deliberately narrow, and getting it wrong in
either direction is a lie:

* keying on "a transcription error was seen" would degrade a COMPLETE
  transcript whose last probe tick happened to fail — telling a user that words
  are missing when none are, and keeping audio that buys nothing;
* keying on nothing at all is the shipped bug.

So the trigger is "audio was attempted and never read again", which only the
final pass can produce, and it is passed to the delivery half as its own
argument rather than inferred from the error slot.

No ``unittest.mock``: ``_finish_dictation`` is exercised directly with plain
functions substituted for insertion and publishing, exactly as
``test_delivery_and_bar.py`` does it.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted
from jarvis.dictation.insert import InsertResult
from jarvis.dictation.outcomes import (
    DICTATION_OUTCOMES,
    RECOVERABLE_OUTCOMES,
    is_recoverable,
)
from jarvis.speech.pipeline import SpeechPipeline


def _pipeline(
    cfg: DictationConfig | None = None, *, insert: InsertResult | None = None
) -> tuple[SpeechPipeline, list[object], list[dict[str, Any]]]:
    """A delivery-only pipeline plus its published events and recorded rows."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = cfg or DictationConfig(history_enabled=False)
    events: list[object] = []
    recorded: list[dict[str, Any]] = []

    async def _publish(event: object) -> None:
        events.append(event)

    async def _record(**kwargs: Any) -> None:
        recorded.append(kwargs)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._record_dictation = _record  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: insert or InsertResult(  # type: ignore[assignment]
        status="inserted",
        detail="",
        clipboard_holds_text=False,
        method="clipboard+ctrl_v",
    )
    return pipe, events, recorded


def _completed(events: list[object]) -> DictationCompleted:
    return next(e for e in events if isinstance(e, DictationCompleted))


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_partial_is_a_known_outcome_and_a_recoverable_one() -> None:
    """Both halves matter and they are separate registrations.

    Being in ``DICTATION_OUTCOMES`` is what stops the UI rendering a raw
    identifier; being in ``RECOVERABLE_OUTCOMES`` is what keeps the audio. The
    shipped bug was a value that was neither.
    """
    assert "partial" in DICTATION_OUTCOMES
    assert "partial" in RECOVERABLE_OUTCOMES
    assert is_recoverable("partial") is True


def test_a_plain_success_is_still_not_recoverable() -> None:
    """Audio is the most sensitive thing stored here — the widening stops at
    outcomes where the user actually lost words."""
    for outcome in ("inserted", "paste_sent", "clipboard_only", "chat"):
        assert is_recoverable(outcome) is False, outcome


# ---------------------------------------------------------------------------
# The degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lost_audio_degrades_a_delivered_dictation_to_partial() -> None:
    """The maintainer's row, reproduced: text arrived, most of it did not."""
    inserted: list[str] = []
    pipe, events, _recorded = _pipeline()
    pipe._insert_dictation = lambda text: inserted.append(text) or InsertResult(  # type: ignore[assignment]
        status="inserted",
        detail="",
        clipboard_holds_text=False,
        method="clipboard+ctrl_v",
    )

    await pipe._finish_dictation(
        raw_text="Aether, Render,",
        language="en",
        duration_s=37.7,
        target="insert",
        hung_up=False,
        stt_error="rate_limited",
        lost_audio_s=31.4,
    )

    completed = _completed(events)
    assert completed.outcome == "partial"
    # The words that DID arrive are still delivered — a partial dictation is
    # not a cancelled one, and withholding the fragment would lose more.
    assert inserted == ["Aether, Render,"]
    # The reason travels as a code the UI can translate, never provider prose.
    assert completed.error == "rate_limited"


@pytest.mark.asyncio
async def test_the_partial_detail_names_the_loss_and_keeps_the_delivery_reason() -> None:
    """``detail`` is what the bar and the CLI show verbatim.

    Both sentences are load-bearing and neither may replace the other: "words
    are missing" is why the outcome degraded, and "it went to the clipboard" is
    what happened to the words that survived.
    """
    blocked = InsertResult(
        status="clipboard_only",
        detail="The window in front is running as administrator.",
        clipboard_holds_text=True,
    )
    pipe, events, _recorded = _pipeline(insert=blocked)

    await pipe._finish_dictation(
        raw_text="the part that came through",
        language="en",
        duration_s=20.0,
        target="insert",
        hung_up=False,
        stt_error="rate_limited",
        lost_audio_s=12.0,
    )

    completed = _completed(events)
    assert completed.outcome == "partial"
    assert "missing" in completed.detail
    assert "administrator" in completed.detail
    # No provider identity, no endpoint, no exception class in front of a user.
    assert "429" not in completed.detail
    assert "http" not in completed.detail.lower()


@pytest.mark.asyncio
async def test_restore_is_only_promised_when_the_audio_is_actually_kept() -> None:
    """``[dictation].keep_failed_audio`` is the user's switch, and it is off here.

    Pointing at a Restore button that will not exist is a worse answer than the
    plain statement of what was lost.
    """
    pipe, events, _recorded = _pipeline(
        DictationConfig(keep_failed_audio=False, history_enabled=False)
    )

    await pipe._finish_dictation(
        raw_text="only a fragment",
        language="en",
        duration_s=25.0,
        target="chat",
        hung_up=False,
        stt_error="rate_limited",
        lost_audio_s=18.0,
    )

    completed = _completed(events)
    assert completed.outcome == "partial"
    assert "missing" in completed.detail
    assert "Restore" not in completed.detail


@pytest.mark.asyncio
async def test_a_stale_error_on_a_complete_transcript_does_not_degrade() -> None:
    """The opposite lie, and the reason the trigger is the LOSS.

    A failure on the last probe tick whose tail then fell under the minimum
    segment size leaves ``stt_error`` set on a transcript that is complete.
    Calling that partial would tell a user that words are missing when none
    are, and would keep a recording that buys nothing back.
    """
    pipe, events, _recorded = _pipeline()

    await pipe._finish_dictation(
        raw_text="every word of this arrived",
        language="en",
        duration_s=6.0,
        target="insert",
        hung_up=False,
        stt_error="rate_limited",
        lost_audio_s=0.0,
    )

    assert _completed(events).outcome == "inserted"


@pytest.mark.asyncio
async def test_a_hangup_still_outranks_a_partial_loss() -> None:
    """The user cancelled; the seconds after that are their decision."""
    pipe, events, _recorded = _pipeline()

    await pipe._finish_dictation(
        raw_text="",
        language="",
        duration_s=4.0,
        target="insert",
        hung_up=True,
        stt_error="rate_limited",
        lost_audio_s=3.0,
    )

    assert _completed(events).outcome == "cancelled"


@pytest.mark.asyncio
async def test_a_total_loss_is_still_failed_not_partial() -> None:
    """There is no "partial" of nothing.

    When the transcript is empty the user did not get a fragment, they got a
    failure — and ``failed`` is already the recoverable, audio-keeping outcome
    for that.
    """
    pipe, events, _recorded = _pipeline()

    await pipe._finish_dictation(
        raw_text="",
        language="en",
        duration_s=15.0,
        target="insert",
        hung_up=False,
        stt_error="rate_limited",
        lost_audio_s=15.0,
    )

    assert _completed(events).outcome == "failed"


# ---------------------------------------------------------------------------
# The audio, which is the whole point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_recording_reaches_the_store_with_the_degraded_outcome() -> None:
    """``_record_dictation`` decides on the OUTCOME, so the outcome is the fix.

    This asserts the hand-off rather than the disk write: the sidecar condition
    is ``is_recoverable(outcome_name)``, already pinned above, and re-testing
    the file write here would test ``DictationHistory``, not this contract.
    """
    pipe, _events, recorded = _pipeline()

    await pipe._finish_dictation(
        raw_text="only the first sentence",
        language="en",
        duration_s=30.0,
        target="insert",
        hung_up=False,
        stt_error="rate_limited",
        lost_audio_s=24.0,
        audio=b"\x00\x01" * 4096,
    )

    assert len(recorded) == 1
    row = recorded[0]
    assert row["outcome_name"] == "partial"
    assert row["audio"], "the recording must reach the store, or Restore has nothing"
    assert is_recoverable(row["outcome_name"]) is True


@pytest.mark.asyncio
async def test_the_default_keeps_every_existing_caller_on_todays_behaviour() -> None:
    """``lost_audio_s`` defaults to zero on purpose.

    The crash path and the Restore route call ``_finish_dictation`` without it,
    and a new argument that changed their outcome would be a second bug shipped
    with the fix for the first.
    """
    pipe, events, _recorded = _pipeline()

    await pipe._finish_dictation(
        raw_text="nothing was lost here",
        language="en",
        duration_s=3.0,
        target="chat",
        hung_up=False,
    )

    assert _completed(events).outcome == "chat"
