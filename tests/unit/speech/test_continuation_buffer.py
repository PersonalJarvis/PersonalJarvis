"""Tests for ContinuationBuffer — coalesces fragmented voice turns into one.

Live regression 2026-05-26: one user task was fragmented across two voice turns
(VAD endpoint cut at a trailing comma), and EACH fragment independently triggered
`spawn_worker` → multiple sub-agent missions for one task. The buffer holds an
incomplete utterance until the next utterance arrives, then dispatches the joined
text as ONE turn. See SUBAGENT_SPAWN_REVIEW + Screenshot mission_019e63c6-*.

The buffer is a tiny stdlib-only helper, sibling to ``hangup.py`` and
``completion.py``. Pipeline wiring is in ``_handle_utterance``.
"""
from __future__ import annotations

import pytest

from jarvis.speech.continuation_buffer import ContinuationBuffer


# --------------------------------------------------------------------------- #
# Pass-through path: empty buffer + complete utterance → returned verbatim.    #
# --------------------------------------------------------------------------- #


def test_empty_buffer_complete_text_passes_through() -> None:
    """A complete utterance with no buffered fragment is returned as-is."""
    buf = ContinuationBuffer(timeout_s=8.0)
    result = buf.process("Open the browser", language="en")
    assert result == "Open the browser"


# --------------------------------------------------------------------------- #
# Buffering path: incomplete utterance → None, buffer grows.                   #
# --------------------------------------------------------------------------- #


def test_incomplete_utterance_is_buffered_returns_none() -> None:
    """Trailing-comma incomplete utterance must be held back."""
    buf = ContinuationBuffer(timeout_s=8.0)
    result = buf.process("Schreib eine Mail an Tom,", language="de")
    assert result is None, "incomplete utterance must NOT be dispatched immediately"
    assert buf.has_pending(), "buffer must hold the fragment for continuation"


def test_last_reason_exposes_buffered_fragment_reason() -> None:
    """The buffer surfaces WHY it held the fragment so the pipeline can scope
    the clarifying question to trail-offs only (2026-06-14)."""
    from jarvis.speech.completion import (
        REASON_TRAILING_COMMA,
        REASON_TRAILING_ELLIPSIS,
    )

    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.process("Kannst du mir sagen, was genau...", language="de") is None
    assert buf.last_reason == REASON_TRAILING_ELLIPSIS

    buf2 = ContinuationBuffer(timeout_s=8.0)
    assert buf2.process("Schreib eine Mail an Tom,", language="de") is None
    assert buf2.last_reason == REASON_TRAILING_COMMA


def test_last_reason_is_cleared_after_join_dispatch() -> None:
    """After a held fragment is joined + dispatched, ``last_reason`` returns ""
    so a stale reason can never force a clarify on a later unrelated turn."""
    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.process("Kannst du mir sagen, was genau...", language="de") is None
    assert buf.last_reason != ""
    # A completing continuation arrives → joined & dispatched, buffer drained.
    joined = buf.process("die Curie entdeckt hat", language="de")
    assert joined is not None
    assert buf.has_pending() is False
    assert buf.last_reason == ""


# --------------------------------------------------------------------------- #
# Join path: pending fragment + complete continuation → joined text returned.  #
# --------------------------------------------------------------------------- #


def test_pending_fragment_plus_complete_returns_joined() -> None:
    """The exact live-bug pattern: fragment with trailing comma + completion."""
    buf = ContinuationBuffer(timeout_s=8.0)
    first = buf.process(
        "Kannst du mir einen Subagent spawnen, welcher die HTML-Datei baut,",
        language="de",
    )
    assert first is None
    second = buf.process(
        "die die aktuelle Situation von KI gründlich darlegt.",
        language="de",
    )
    assert second is not None, "complete continuation must release the buffer"
    assert "Subagent" in second and "darlegt" in second, (
        "joined utterance must contain BOTH fragments"
    )
    assert not buf.has_pending(), "buffer must be cleared after a successful join"


# --------------------------------------------------------------------------- #
# Re-buffer: pending + still-incomplete continuation → None, both kept.        #
# --------------------------------------------------------------------------- #


def test_pending_fragment_plus_incomplete_re_buffers() -> None:
    """A continuation that itself dangles must stay buffered."""
    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.process("Schick mir bitte den Bericht,", language="de") is None
    assert buf.process("oder auch nur einen Auszug,", language="de") is None
    assert buf.has_pending(), "buffer must still hold two fragments"


# --------------------------------------------------------------------------- #
# Timeout: stale buffer is discarded on next process() (wall-clock based).     #
# --------------------------------------------------------------------------- #


def test_stale_buffer_is_discarded_on_next_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no continuation arrives within timeout_s, the fragment is dropped on
    the next process() call rather than polluting an unrelated next turn."""
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "jarvis.speech.continuation_buffer.time.monotonic",
        lambda: clock["now"],
    )
    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.process("Erinnere mich morgen daran, dass", language="de") is None
    assert buf.has_pending()
    # Advance past the deadline
    clock["now"] += 9.0
    result = buf.process("Was steht heute im Kalender", language="de")
    # The stale fragment must NOT contaminate the new turn — return the new
    # utterance as-is, drop the old buffer.
    assert result == "Was steht heute im Kalender", (
        "stale buffer must not be joined onto an unrelated turn"
    )
    assert not buf.has_pending()


# --------------------------------------------------------------------------- #
# Explicit discard for hangup / cancel.                                        #
# --------------------------------------------------------------------------- #


def test_discard_clears_buffer() -> None:
    """Hangup handler / cancel phrase must be able to drop pending state."""
    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.process("Schreib eine Mail an Tom,", language="de") is None
    assert buf.has_pending()
    buf.discard()
    assert not buf.has_pending()


# --------------------------------------------------------------------------- #
# Failsafe: classifier raise is treated as COMPLETE (fail-open, AP-OE6).       #
# --------------------------------------------------------------------------- #


def test_classifier_failure_falls_open_to_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the classifier blows up, we MUST NOT silently swallow the user — the
    utterance is dispatched as-is (treat-as-complete fail-open)."""
    def _boom(_text: str, language: str = "") -> None:  # noqa: ARG001
        raise RuntimeError("simulated classifier failure")

    monkeypatch.setattr(
        "jarvis.speech.continuation_buffer.is_incomplete", _boom
    )
    buf = ContinuationBuffer(timeout_s=8.0)
    result = buf.process("anything goes", language="de")
    assert result == "anything goes"
    assert not buf.has_pending()


# --------------------------------------------------------------------------- #
# Max-chain bound (per spec MAX(3) — avoid infinite buffering).                #
# --------------------------------------------------------------------------- #


def test_max_chain_forces_flush() -> None:
    """After MAX_CHAIN consecutive incomplete utterances, the buffer flushes
    its joined fragments to the brain rather than buffering forever."""
    buf = ContinuationBuffer(timeout_s=8.0, max_chain=3)
    # 2+ tokens each so is_incomplete actually fires (_MIN_TOKENS=2)
    assert buf.process("Erstens dies,", language="de") is None
    assert buf.process("zweitens jenes,", language="de") is None
    # The third incomplete continuation hits the cap — must release joined text.
    third = buf.process("drittens das,", language="de")
    assert third is not None, "max-chain must force a flush, not buffer forever"
    assert "Erstens" in third and "drittens" in third
    assert not buf.has_pending()


# --------------------------------------------------------------------------- #
# Speech-resume re-arm: a continuation that BEGINS inside the window but is     #
# slow to finalize must still coalesce (live bug 2026-06-18, session 241a1984).#
# --------------------------------------------------------------------------- #


def test_speech_resume_rearms_deadline_so_slow_continuation_still_joins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live bug 2026-06-18 (session 241a1984): "Kannst du bitte..." was buffered,
    the user resumed speaking ~1 s later (deep inside the 8 s window) but the
    continuation only FINALIZED 0.6 s past the deadline — so the lazy deadline
    check dropped the held fragment and the turn split into an empty Turn 0.

    note_speech_resumed() must re-arm the discard deadline from the moment the
    user resumes, mirroring ContinuationWindow.note_speech_resumed, so a
    slow-to-finalize continuation that began inside the window still joins.
    """
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "jarvis.speech.continuation_buffer.time.monotonic",
        lambda: clock["now"],
    )
    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.process("Kannst du mir sagen, was genau...", language="de") is None
    assert buf.has_pending()
    # User resumes speaking 1 s in — deep inside the 8 s window.
    clock["now"] += 1.0
    buf.note_speech_resumed()
    # The continuation takes 7.5 s to finalize: 8.5 s after the ORIGINAL buffer
    # time (past the original 8 s deadline) but inside the re-armed window.
    clock["now"] += 7.5
    joined = buf.process("die Curie entdeckt hat", language="de")
    assert joined is not None, (
        "a continuation that BEGAN inside the window must still join after re-arm"
    )
    assert "Kannst du" in joined and "Curie" in joined
    assert not buf.has_pending()


def test_speech_resume_still_bounds_a_never_finalizing_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-arming on resume must NOT make the buffer hold forever: if the resumed
    speech never produces a finalized continuation, an unrelated later turn must
    still drop the stale fragment (anti-pollution bound preserved)."""
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "jarvis.speech.continuation_buffer.time.monotonic",
        lambda: clock["now"],
    )
    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.process("Erinnere mich morgen daran, dass", language="de") is None
    clock["now"] += 1.0
    buf.note_speech_resumed()  # re-arm to 1001 + 8 = 1009
    # No continuation finalizes; a totally unrelated complete turn 30 s later.
    clock["now"] += 30.0
    result = buf.process("Was steht heute im Kalender", language="de")
    assert result == "Was steht heute im Kalender"
    assert not buf.has_pending()


def test_speech_resume_after_expiry_does_not_resurrect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resume that arrives AFTER the deadline already passed is a no-op — it
    must not revive a dead buffer (the late fragment is genuinely stale)."""
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "jarvis.speech.continuation_buffer.time.monotonic",
        lambda: clock["now"],
    )
    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.process("Schreib eine Mail an Tom,", language="de") is None
    clock["now"] += 9.0  # past the 8 s deadline
    buf.note_speech_resumed()  # too late — must not re-arm
    result = buf.process("Was ist das Wetter", language="de")
    assert result == "Was ist das Wetter"
    assert not buf.has_pending()


# --------------------------------------------------------------------------- #
# Autonomous drain (AD-OE6 zero-silent-drop): flush_pending RETURNS the held    #
# text so the pipeline can DISPATCH it to the brain instead of dropping it.     #
# Live wedge 2026-06-19, session da25113a: "…morgen ist ja Montag, oder?" was   #
# held as a trailing conjunction, no continuation arrived, and the fragment was #
# silently discarded at idle-timeout — the brain was never called and Jarvis    #
# "listened forever". The buffer itself has no timer; the pipeline arms a drain  #
# timer (see test_continuation_drain.py) that calls flush_pending() on expiry.  #
# --------------------------------------------------------------------------- #


def test_flush_pending_returns_joined_and_clears() -> None:
    """flush_pending() drains the held fragment(s) for an autonomous dispatch.

    Unlike :meth:`discard` it RETURNS the joined text so the caller can send it
    to the brain (AD-OE6) rather than dropping the user's words silently.
    """
    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.process("Schreib eine Mail an Tom,", language="de") is None
    assert buf.process("und schick sie auch an Lisa,", language="de") is None
    assert buf.has_pending()
    drained = buf.flush_pending()
    assert drained is not None
    assert "Tom" in drained and "Lisa" in drained, "must join ALL held fragments"
    assert not buf.has_pending(), "buffer must be cleared after a drain"
    assert buf.last_reason == ""


def test_flush_pending_returns_none_when_empty() -> None:
    """Draining an empty buffer is a harmless no-op returning None."""
    buf = ContinuationBuffer(timeout_s=8.0)
    assert buf.flush_pending() is None
    assert not buf.has_pending()


def test_timeout_s_property_exposes_configured_value() -> None:
    """The pipeline arms its drain timer at the buffer's own discard deadline,
    so the configured timeout must be readable."""
    assert ContinuationBuffer(timeout_s=3.5).timeout_s == 3.5
    assert ContinuationBuffer().timeout_s == 8.0
