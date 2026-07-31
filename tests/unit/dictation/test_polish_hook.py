"""Where the polish pass plugs into the delivery path, and what it may cost.

``polish.py`` is tested on its own; this file tests the WIRING, which is the
half that decides whether the feature is safe to ship on by default:

* the deterministic punctuation repair runs on EVERY dictation, not only on one
  a filler was removed from — the damage it fixes is manufactured by our own
  segmented transcription, so gating it on the cleanup's outcome (where it used
  to sit) left every untouched stretch broken;
* the polished string reaches the composer, the bar and the insertion ALIKE,
  because they all read the one final transcript published here;
* ``raw_text`` never changes, so the history keeps what the user actually said;
* every failure mode of the pass — off, unreachable, timed out, rejected,
  crashed — delivers the unpolished text rather than nothing;
* the pass never sees text the delivery gates already rejected, so a
  hallucinated video outro does not cost a model call;
* and the three facts about the pass travel outward on the completion event, so
  a surface showing polished text can say that is what it is showing.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted, DictationTranscript
from jarvis.dictation.polish import POLISH_STATUSES, PolishOutcome
from jarvis.speech.pipeline import SpeechPipeline

# A transcript with the two defects our own segmenting produces: an ellipsis
# glued to a sentence that had already ended, and a segment restarting in lower
# case. Long enough to clear the pass's minimum-word floor.
SEGMENTED = "We talked about it. ... and then the report went out on Tuesday."

POLISHED = "We talked about it. And then the report went out on Tuesday."


def _pipeline(cfg: DictationConfig | None = None):
    """A pipeline with only the delivery half wired, and no disk anywhere."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = cfg or DictationConfig(history_enabled=False, polish=False)
    events: list[object] = []
    inserted: list[str] = []

    async def _publish(event: object) -> None:
        events.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: (  # type: ignore[assignment]
        inserted.append(text)
        or SimpleNamespace(status="inserted", detail="", method="clipboard+ctrl_v")
    )
    pipe._dictation_protected_terms = lambda: ()  # type: ignore[assignment]
    return pipe, events, inserted


def _completed(events: list[object]) -> DictationCompleted:
    return next(e for e in events if isinstance(e, DictationCompleted))


def _final_transcript(events: list[object]) -> DictationTranscript:
    return next(e for e in events if isinstance(e, DictationTranscript) and e.is_final)


def _install_polish(
    monkeypatch: pytest.MonkeyPatch, outcome: PolishOutcome | Exception
) -> list[dict[str, Any]]:
    """Replace the pass with a recorder. Returns the list of calls it saw."""
    import jarvis.dictation.polish as polish

    calls: list[dict[str, Any]] = []

    async def _fake(raw: str, **kwargs: Any) -> PolishOutcome:
        calls.append({"raw": raw, **kwargs})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(polish, "polish_transcript", _fake)
    return calls


# --------------------------------------------------------------------------
# The deterministic repair, which is not gated on anything
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_punctuation_is_repaired_even_with_the_filler_cleanup_off() -> None:
    """The two are not variants of one step. Filler removal is a preference
    covering three languages; this damage is ours and exists in all of them."""
    pipe, events, _inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=False, remove_fillers=False)
    )

    text = await pipe._finish_dictation(
        raw_text=SEGMENTED, language="en", duration_s=6.0,
        target="chat", hung_up=False,
    )

    assert "...." not in text
    assert " ... and" not in text
    assert _final_transcript(events).text == text


@pytest.mark.asyncio
async def test_punctuation_is_repaired_in_a_language_with_no_filler_rules() -> None:
    """~95 of the 100 recognition languages have no filler table, and every one
    of them still gets its segments joined by us."""
    pipe, _events, _inserted = _pipeline()

    text = await pipe._finish_dictation(
        raw_text="Chiedo scusa. ... e poi il documento.",  # i18n-allow: fixture
        language="it",
        duration_s=6.0,
        target="chat",
        hung_up=False,
    )

    assert "...." not in text
    assert "..." not in text


# --------------------------------------------------------------------------
# The polish pass — the happy path and everything the composer sees
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_polished_text_is_what_gets_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_polish(
        monkeypatch,
        PolishOutcome(
            text=POLISHED, status="applied", provider="groq",
            model="llama-3.1-8b-instant", latency_ms=412,
        ),
    )
    pipe, events, inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=True)
    )

    text = await pipe._finish_dictation(
        raw_text=SEGMENTED, language="en", duration_s=6.0,
        target="insert", hung_up=False,
    )

    assert text == POLISHED
    # The composer, the bar and the insertion all read the SAME string. A
    # polished paste next to an unpolished composer is a divergence nobody can
    # explain afterwards.
    assert inserted == [POLISHED]
    assert _final_transcript(events).text == POLISHED
    assert len(calls) == 1
    # It polished the CLEANED text, not the raw one: the deterministic repairs
    # are cheaper and more reliable than asking a model to redo them.
    assert "...." not in calls[0]["raw"]


@pytest.mark.asyncio
async def test_the_raw_transcript_is_never_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user must always be able to recover the words they actually said."""
    _install_polish(
        monkeypatch,
        PolishOutcome(text=POLISHED, status="applied", provider="groq"),
    )
    pipe, events, _inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=True)
    )

    await pipe._finish_dictation(
        raw_text=SEGMENTED, language="en", duration_s=6.0,
        target="chat", hung_up=False,
    )

    assert _completed(events).raw_text == SEGMENTED


@pytest.mark.asyncio
async def test_the_completion_carries_status_provider_and_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_polish(
        monkeypatch,
        PolishOutcome(
            text=POLISHED, status="applied", provider="gemini",
            model="gemini-3.1-flash-lite", latency_ms=737,
        ),
    )
    pipe, events, _inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=True)
    )

    await pipe._finish_dictation(
        raw_text=SEGMENTED, language="en", duration_s=6.0,
        target="chat", hung_up=False,
    )

    completed = _completed(events)
    assert completed.polish_status == "applied"
    assert completed.polish_status in POLISH_STATUSES
    assert completed.polish_provider == "gemini"
    assert completed.polish_latency_ms == 737


@pytest.mark.asyncio
async def test_the_resolved_language_and_the_protected_terms_are_handed_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pass must not re-derive either: the language is already resolved, and
    the protected terms are what stop a technical word being "corrected"."""
    calls = _install_polish(
        monkeypatch, PolishOutcome(text=POLISHED, status="applied", provider="groq")
    )
    pipe, _events, _inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=True, language="de")
    )
    pipe._dictation_protected_terms = lambda: ("Vokando", "Adex")  # type: ignore[assignment]

    await pipe._finish_dictation(
        raw_text=SEGMENTED, language="en", duration_s=6.0,
        target="chat", hung_up=False,
    )

    assert calls[0]["language"] == "de"
    assert calls[0]["protected_terms"] == ("Vokando", "Adex")


# --------------------------------------------------------------------------
# Every way it can fail delivers the user's own words
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_switch_off_is_byte_identical_and_costs_no_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_polish(
        monkeypatch, PolishOutcome(text="NEVER", status="applied", provider="groq")
    )
    pipe, events, _inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=False)
    )

    text = await pipe._finish_dictation(
        raw_text=SEGMENTED, language="en", duration_s=6.0,
        target="chat", hung_up=False,
    )

    assert calls == []
    assert "NEVER" not in text
    assert _completed(events).polish_status == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ["unavailable", "timeout", "provider_error", "rejected_drift", "skipped_short"],
)
async def test_every_non_applied_status_delivers_the_unpolished_text(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    """``PolishOutcome.text`` is the raw string on all of them; the hook must
    take it as given rather than second-guessing which ones are safe."""
    unpolished: list[str] = []

    import jarvis.dictation.polish as polish

    async def _fake(raw: str, **_kwargs: Any) -> PolishOutcome:
        unpolished.append(raw)
        return PolishOutcome(text=raw, status=status, provider="")

    monkeypatch.setattr(polish, "polish_transcript", _fake)
    pipe, events, inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=True)
    )

    text = await pipe._finish_dictation(
        raw_text=SEGMENTED, language="en", duration_s=6.0,
        target="insert", hung_up=False,
    )

    assert text == unpolished[0]
    assert inserted == [text]
    assert _completed(events).polish_status == status


@pytest.mark.asyncio
async def test_a_crashing_polish_pass_still_delivers_the_dictation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pass promises never to raise. This is the belt to that brace: a
    formatting feature may never be the reason a dictation is lost."""
    _install_polish(monkeypatch, RuntimeError("the module blew up"))
    pipe, events, inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=True)
    )

    text = await pipe._finish_dictation(
        raw_text=SEGMENTED, language="en", duration_s=6.0,
        target="insert", hung_up=False,
    )

    assert text.strip()
    assert inserted == [text]
    assert _completed(events).polish_status == "provider_error"


@pytest.mark.asyncio
async def test_a_pass_that_hangs_is_bounded_by_its_own_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real ``polish_transcript`` owns the ceiling, so the hook must not add
    a second one — but it must also not be able to hang the delivery. Verified
    against the real function with a deliberately tiny budget and a transport
    that never answers."""
    import jarvis.dictation.polish as polish
    from jarvis.dictation.polish_client import POLISH_FAMILIES

    class _Slow:
        async def complete(self, *_a: Any, **_k: Any) -> str:
            await asyncio.sleep(30)
            return "never"  # pragma: no cover

    polish.reset_polish_state()
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: (POLISH_FAMILIES[0],))
    monkeypatch.setattr(polish, "build_polish_client", lambda family, *, model: _Slow())
    pipe, events, _inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=True, polish_timeout_ms=200)
    )

    try:
        text = await asyncio.wait_for(
            pipe._finish_dictation(
                raw_text=SEGMENTED, language="en", duration_s=6.0,
                target="chat", hung_up=False,
            ),
            timeout=10,
        )
    finally:
        polish.reset_polish_state()

    assert text.strip()
    assert _completed(events).polish_status == "timeout"


# --------------------------------------------------------------------------
# Order — the gates run first, so rejected text costs nothing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejected_boilerplate_never_reaches_the_polish_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 1.4 s "Thank you for watching!" is thrown away by the delivery gates.
    Paying a model call to format it would be spending on nothing."""
    calls = _install_polish(
        monkeypatch, PolishOutcome(text="x", status="applied", provider="groq")
    )
    pipe, events, _inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=True)
    )

    text = await pipe._finish_dictation(
        raw_text="Thank you for watching!", language="en", duration_s=1.4,
        target="insert", hung_up=False,
    )

    assert text == ""
    assert calls == []
    assert _completed(events).outcome == "empty"


@pytest.mark.asyncio
async def test_a_hangup_polishes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hangup means "stop"; there is no delivery to format."""
    calls = _install_polish(
        monkeypatch, PolishOutcome(text="x", status="applied", provider="groq")
    )
    pipe, events, _inserted = _pipeline(
        DictationConfig(history_enabled=False, polish=True)
    )

    await pipe._finish_dictation(
        raw_text=SEGMENTED, language="en", duration_s=6.0,
        target="chat", hung_up=True,
    )

    assert calls == []
    assert _completed(events).polish_status == ""
