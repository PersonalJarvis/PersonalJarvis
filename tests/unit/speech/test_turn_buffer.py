"""Unit-Tests fuer TurnBuffer.

Regression-Test fuer BUG-001 (2026-04-23): der Stub-TurnBuffer akzeptierte keine
Kwargs, die Pipeline ruft aber ``append(text=..., language=..., confidence=...)``
auf. Ergebnis: TypeError bei jedem User-Utterance, Session bricht nach "Sir?"
lautlos ab. Diese Tests stellen den API-Contract fest.
"""
from __future__ import annotations

import pytest

from jarvis.speech.turn_buffer import Turn, TurnBuffer


def test_append_accepts_pipeline_kwargs() -> None:
    """BUG-001 Regression: append muss text/language/confidence als Kwargs nehmen."""
    buf = TurnBuffer(maxlen=5)
    buf.append(text="Hallo Jarvis", language="de", confidence=0.92)
    assert len(buf) == 1


def test_append_confidence_optional() -> None:
    """Nicht jeder STT-Provider liefert confidence — Default None muss reichen."""
    buf = TurnBuffer()
    buf.append(text="Hello", language="en")
    last = buf.last()
    assert last is not None
    assert last.confidence is None


def test_last_returns_most_recent() -> None:
    buf = TurnBuffer()
    buf.append(text="erstes", language="de")
    buf.append(text="zweites", language="de")
    last = buf.last()
    assert last is not None
    assert last.text == "zweites"


def test_last_returns_none_when_empty() -> None:
    buf = TurnBuffer()
    assert buf.last() is None


def test_pop_last_removes_and_returns() -> None:
    """Korrektur-Command entfernt das gerade gesagte Transkript."""
    buf = TurnBuffer()
    buf.append(text="nein, ich meinte X", language="de")
    buf.append(text="Jarvis, oeffne Chrome", language="de")
    popped = buf.pop_last()
    assert popped is not None
    assert popped.text == "Jarvis, oeffne Chrome"
    assert len(buf) == 1
    assert buf.last() is not None and buf.last().text == "nein, ich meinte X"


def test_pop_last_returns_none_when_empty() -> None:
    buf = TurnBuffer()
    assert buf.pop_last() is None


def test_maxlen_evicts_oldest() -> None:
    buf = TurnBuffer(maxlen=3)
    for i in range(5):
        buf.append(text=f"turn-{i}", language="de")
    assert len(buf) == 3
    texts = [t.text for t in buf]
    assert texts == ["turn-2", "turn-3", "turn-4"]


def test_turn_is_frozen() -> None:
    """Turn ist immutable — kein versehentliches Mutieren eines Buffer-Eintrags."""
    buf = TurnBuffer()
    buf.append(text="hallo", language="de")
    last = buf.last()
    assert last is not None
    with pytest.raises(Exception):
        last.text = "anders"  # type: ignore[misc]


def test_clear_empties_buffer() -> None:
    buf = TurnBuffer()
    buf.append(text="a", language="de")
    buf.append(text="b", language="de")
    buf.clear()
    assert len(buf) == 0
    assert buf.last() is None
