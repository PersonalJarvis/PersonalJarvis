"""Single-slot buffer for a syntactically open-ended user fragment.

When :mod:`jarvis.speech.completion` classifies a finalized transcript as
dangling, the pipeline parks the fragment here and stays silent for one
continuation cycle. On the next utterance the orchestrator concatenates and
re-classifies. The buffer itself is intentionally dumb — it knows nothing about
classification, the brain, the timeout task, or the state machine.

Why a dedicated buffer instead of extending ``TurnBuffer``: that buffer owns the
rolling window of the last N user turns for the "nein, ich meinte X" correction
command (``jarvis/speech/turn_buffer.py``). Mixing the two would couple unrelated
concerns. Single responsibility each.

The clock is injected (``clock`` ctor arg) so the age tests are deterministic
without monkeypatching the ``time`` module.
"""
from __future__ import annotations

import time
from collections.abc import Callable


class PendingPromptBuffer:
    """Holds at most one dangling fragment between voice turns.

    State transitions:
        empty   --start(text)-->   pending
        pending --extend(text)-->  pending (chain_count += 1, age timer reset)
        pending --start(text)-->   pending (REPLACES contents, chain_count = 1)
        pending --flush()-->       empty (returns joined text)
        pending --clear()-->       empty (discards)
    """

    def __init__(self, *, clock: Callable[[], int] | None = None) -> None:
        self._clock = clock or time.monotonic_ns
        self._fragment: str = ""
        self._language: str = ""
        self._chain_count: int = 0
        self._last_active_ns: int | None = None

    # --- Properties ------------------------------------------------------ #

    @property
    def is_pending(self) -> bool:
        return self._chain_count > 0

    @property
    def fragment(self) -> str:
        return self._fragment

    @property
    def language(self) -> str:
        return self._language

    @property
    def chain_count(self) -> int:
        return self._chain_count

    # --- Mutators -------------------------------------------------------- #

    def start(self, text: str, language: str = "") -> None:
        """Begin a new pending fragment, replacing any previous contents."""
        self._fragment = text.strip()
        self._language = language
        self._chain_count = 1
        self._last_active_ns = self._clock()

    def extend(self, text: str) -> None:
        """Append a continuation to the pending fragment.

        Raises :class:`RuntimeError` if no fragment is pending — the orchestrator
        must ``start`` before it ``extend``s.
        """
        if not self.is_pending:
            raise RuntimeError("extend() called on an empty PendingPromptBuffer")
        addition = text.strip()
        self._fragment = f"{self._fragment} {addition}".strip()
        self._chain_count += 1
        self._last_active_ns = self._clock()

    def flush(self) -> str | None:
        """Return the joined fragment text and clear the buffer.

        Returns ``None`` when nothing is pending (avoids spurious empty prompts).
        """
        if not self.is_pending:
            return None
        text = self._fragment
        self.clear()
        return text

    def clear(self) -> None:
        """Discard the pending fragment without returning it."""
        self._fragment = ""
        self._language = ""
        self._chain_count = 0
        self._last_active_ns = None

    # --- Timing ---------------------------------------------------------- #

    def age_ms(self) -> int | None:
        """Milliseconds since the last activity (``start`` or ``extend``).

        Returns ``None`` when the buffer is empty.
        """
        if self._last_active_ns is None:
            return None
        return (self._clock() - self._last_active_ns) // 1_000_000


__all__ = ["PendingPromptBuffer"]
