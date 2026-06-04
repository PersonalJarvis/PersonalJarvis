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
