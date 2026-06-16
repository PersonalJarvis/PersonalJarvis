"""ContinuationWindow — re-attach a fast-follow utterance to the in-flight turn.

Sibling of :mod:`jarvis.speech.continuation_buffer`. Where ContinuationBuffer
coalesces a *syntactically* open fragment BEFORE dispatch, ContinuationWindow
covers the case the maintainer reported (2026-06-16): the user keeps talking
while the brain is ALREADY thinking/speaking. The pipeline aborts the
half-formed answer (Unit B) and, on the next utterance, prepends the
just-dispatched text so the whole sentence is re-thought as ONE turn.

Stdlib-only, deterministic (clock injected), fail-open. The window holds:

* ``text``        — the last dispatched user text, eligible to be extended.
* ``chain``       — fragments coalesced into the current window (bounded).
* ``deadline_ns`` — ``None`` while the arming turn is still in flight (always
                    active); a wall-clock deadline once the turn went idle
                    (grace countdown). Expiry is checked lazily on the next
                    ``try_recombine`` — never via a background timer that could
                    fire across turns (BUG-032 watchdog-class avoidance).

Design contract: ``try_recombine`` is NON-destructive on success (it leaves the
window armed with the prior text); the pipeline overwrites the text via
``note_dispatch`` only once it actually commits the combined turn to the brain.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

_DEFAULT_GRACE_MS: Final[int] = 2500
_DEFAULT_MAX_CHAIN: Final[int] = 3


class ContinuationWindow:
    """Tracks the last dispatched utterance so a continuation can re-attach."""

    def __init__(
        self,
        *,
        grace_ms: int = _DEFAULT_GRACE_MS,
        max_chain: int = _DEFAULT_MAX_CHAIN,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if grace_ms < 0:
            raise ValueError("grace_ms must be >= 0")
        if max_chain < 1:
            raise ValueError("max_chain must be >= 1")
        self._grace_ns = int(grace_ms) * 1_000_000
        self._max_chain = int(max_chain)
        self._clock = clock or time.monotonic_ns
        self._text: str = ""
        self._chain: int = 0
        self._deadline_ns: int | None = None

    @property
    def text(self) -> str:
        return self._text

    @property
    def is_armed(self) -> bool:
        return self._chain > 0

    def note_dispatch(self, text: str, *, continued: bool) -> None:
        """Record a turn that just committed to the brain.

        ``continued`` marks whether THIS dispatch was itself a recombine
        (chain grows) or a fresh turn (chain resets to 1). The window becomes
        'in flight' (no deadline) until ``mark_idle`` starts the grace countdown.
        """
        self._text = text.strip()
        self._chain = (self._chain + 1) if continued else 1
        self._deadline_ns = None

    def mark_idle(self) -> None:
        """The armed turn finished (answer spoken or aborted): start grace."""
        if self.is_armed:
            self._deadline_ns = self._clock() + self._grace_ns

    def try_recombine(self, new_text: str) -> str | None:
        """Return ``prior + new`` if a continuation is live, else ``None``.

        Non-destructive on success (window stays armed with the prior text).
        Expired or over-cap -> clears and returns ``None`` (fresh turn).
        """
        if not self.is_armed:
            return None
        if self._deadline_ns is not None and self._clock() > self._deadline_ns:
            self.clear()
            return None
        if self._chain >= self._max_chain:
            self.clear()
            return None
        return f"{self._text} {new_text.strip()}".strip()

    def clear(self) -> None:
        self._text = ""
        self._chain = 0
        self._deadline_ns = None


__all__ = ["ContinuationWindow"]
