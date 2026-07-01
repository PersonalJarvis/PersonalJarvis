"""TurnBuffer — rolling window of the most recent user transcripts.

Serves the voice correction command ("no, I meant X") as the source for the
second-to-last transcript. Real Phase-6 logic (multi-turn context replay)
follows later; this implementation persists, minimally, whatever the pipeline
currently passes in, so `last()` / `pop_last()` work as soon as the
BrainManager correction command goes live.

API contract (as called from `SpeechPipeline._handle_utterance`):

    turn_buffer.append(text=..., language=..., confidence=...)

The earlier stub version was missing the kwargs — the pipeline crashed with
``TypeError: TurnBuffer.append() got an unexpected keyword argument 'text'``
immediately after every user utterance and never reached the brain call.
Result: wake triggered, "Sir?" was spoken, then the session died silently.
See AGENTS.md (BUG-001 — corrected root cause) for the full story.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    """A single user turn in the rolling buffer."""
    text: str
    language: str = ""
    confidence: float | None = None


class TurnBuffer:
    """Rolling window of the last N user turns."""

    def __init__(self, maxlen: int = 10) -> None:
        self._maxlen = maxlen
        self._items: deque[Turn] = deque(maxlen=maxlen)

    def append(
        self,
        *,
        text: str,
        language: str = "",
        confidence: float | None = None,
    ) -> None:
        """Adds a new turn (the oldest is evicted once maxlen is reached)."""
        self._items.append(
            Turn(text=text, language=language, confidence=confidence)
        )

    def last(self) -> Turn | None:
        """The most recently stored turn, or ``None`` if empty."""
        if not self._items:
            return None
        return self._items[-1]

    def pop_last(self) -> Turn | None:
        """Removes and returns the last turn (for the correction command)."""
        if not self._items:
            return None
        return self._items.pop()

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
