"""ContinuationBuffer — coalesce a fragmented voice task into one brain turn.

Sibling helper of :mod:`jarvis.speech.completion` and :mod:`jarvis.speech.hangup`.
Stdlib-only, deterministic, fail-open. Holds an *open-ended* utterance until the
next utterance arrives, then dispatches the joined text as ONE turn. Prevents
the live regression observed 2026-05-26 12:13 where ONE user task was cut by VAD
into two transcripts at a trailing comma and BOTH halves triggered
``spawn_worker`` separately — producing two sub-agent missions for one task.

Design contract:

* **Precision over recall** (inherited from ``completion.is_incomplete``):
  buffering only happens on a CLOSED set of trailing markers
  (conjunction / determiner / preposition / trailing comma). Complete-looking
  utterances pass through immediately — never silently held back.
* **Bounded chain**: after ``max_chain`` consecutive incomplete pieces the
  buffer flushes anyway. No infinite buffering, no live-locks.
* **Wall-clock timeout**: a stale buffer (no continuation within
  ``timeout_s``) is dropped on the NEXT ``process()`` call so it can't pollute
  an unrelated future turn. Because that drop is *lazy* (it needs a next
  ``process()``), the pipeline also arms an autonomous drain timer that calls
  :meth:`flush_pending` on expiry and DISPATCHES the held fragment to the brain
  — so a held fragment whose continuation never comes is answered, not silently
  dropped at the session idle-timeout (live "Jarvis hört für immer zu" wedge
  2026-06-19, session da25113a).
* **Fail-open** (AD-OE6 zero-silent-drop): any exception from the classifier
  is caught and the utterance is dispatched as-is. The buffer never silently
  swallows the user.

Pipeline wiring lives in ``jarvis.speech.pipeline._handle_utterance`` (+ the
``_arm_continuation_drain`` timer); this module exposes ``process(text,
language)``, ``discard()`` and ``flush_pending()``.
"""
from __future__ import annotations

import logging
import time
from typing import Final

from jarvis.speech.completion import is_incomplete

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S: Final[float] = 8.0
_DEFAULT_MAX_CHAIN: Final[int] = 3


class ContinuationBuffer:
    """Holds an open-ended voice fragment until the continuation arrives."""

    def __init__(
        self,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_chain: int = _DEFAULT_MAX_CHAIN,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if max_chain < 1:
            raise ValueError("max_chain must be >= 1")
        self._timeout_s = float(timeout_s)
        self._max_chain = int(max_chain)
        self._fragments: list[str] = []
        self._deadline: float | None = None
        # Reason of the most recently buffered fragment (one of the
        # ``completion.REASON_*`` constants), or ``""`` when nothing is held.
        # The pipeline reads this right after ``process()`` returns ``None`` to
        # scope the clarifying question to trail-offs only (2026-06-14).
        self._last_reason: str = ""

    # ------------------------------------------------------------------ #
    # Introspection                                                      #
    # ------------------------------------------------------------------ #

    def has_pending(self) -> bool:
        """``True`` iff a fragment is currently buffered."""
        return bool(self._fragments)

    @property
    def timeout_s(self) -> float:
        """The stale-fragment timeout in seconds (read-only).

        Exposed so the pipeline can arm an autonomous drain timer that matches
        the buffer's own discard deadline — the buffer itself has no timer, it
        only drops a stale fragment lazily on the next ``process()`` call.
        """
        return self._timeout_s

    @property
    def last_reason(self) -> str:
        """Reason the currently-held fragment was buffered (``""`` if none).

        One of the ``completion.REASON_*`` constants. Lets the pipeline ask a
        clarifying question for trail-offs (``REASON_TRAILING_ELLIPSIS``) while
        keeping every other incomplete reason on the silent-hold default.
        """
        return self._last_reason if self._fragments else ""

    def discard(self) -> None:
        """Drop the buffer unconditionally. Called by hangup / cancel paths."""
        if self._fragments:
            logger.info(
                "ContinuationBuffer: discarding %d pending fragment(s)",
                len(self._fragments),
            )
        self._fragments.clear()
        self._deadline = None
        self._last_reason = ""

    def flush_pending(self) -> str | None:
        """Drain the held fragment(s) and clear, for an autonomous dispatch.

        Like :meth:`discard` it empties the buffer, but it RETURNS the joined
        text so the caller can DISPATCH it to the brain instead of dropping the
        user's words silently. This is the zero-silent-drop (AD-OE6) escape
        hatch for a fragment that was held as "incomplete" but never got a
        continuation: rather than letting it rot until the session idle-timeout
        silently discards it (live "Jarvis hört für immer zu" wedge 2026-06-19,
        session da25113a — "…morgen ist ja Montag, oder?" was held as a trailing
        conjunction and dropped 30 s later, never answered), the pipeline drains
        it to the brain after the grace window so the user always gets an answer
        attempt. Returns ``None`` when nothing is buffered.
        """
        if not self._fragments:
            return None
        joined = " ".join(self._fragments)
        self._fragments.clear()
        self._deadline = None
        self._last_reason = ""
        return joined

    def note_speech_resumed(self) -> None:
        """Re-arm the discard countdown when the user starts speaking again.

        The deadline set at buffer time measures THINKING silence; once the user
        is actively forming the continuation, that clock must not keep running
        against them — a slow-to-finalize continuation would otherwise miss the
        window even though it BEGAN well inside it (live bug 2026-06-18, session
        241a1984: "Kannst du bitte..." was held, the user resumed ~1 s later but
        the continuation only FINALIZED 0.6 s past the 8 s deadline → the held
        fragment was dropped and the turn split into an empty Turn 0). The
        sibling :meth:`ContinuationWindow.note_speech_resumed` already does this
        for the in-flight window; the pre-dispatch buffer needs the same freeze.

        Re-arms to ``now + timeout_s`` (rather than clearing the deadline
        outright) so the anti-pollution bound is preserved: a continuation that
        never finalizes is still dropped before an unrelated later turn. Only
        re-arms while a fragment is pending and the deadline has NOT already
        passed — a genuinely late resume cannot resurrect a dead buffer.
        Fail-open / idempotent; wired from ``pipeline._on_vad_speech_start``.
        """
        if not self._fragments:
            return
        now = time.monotonic()
        if self._deadline is not None and now > self._deadline:
            return
        self._deadline = now + self._timeout_s

    # ------------------------------------------------------------------ #
    # Main API                                                           #
    # ------------------------------------------------------------------ #

    def process(self, text: str, language: str = "") -> str | None:
        """Classify ``text`` and decide whether to dispatch or buffer.

        Returns:
            * ``str`` — the utterance (possibly joined with prior fragments)
              that should now be dispatched to the brain. Caller continues
              normal turn-handling with this text.
            * ``None`` — the utterance was buffered. Caller MUST skip the
              brain for this turn and return to LISTENING.
        """
        if not text:
            return text  # empty input — pass through; nothing to buffer

        now = time.monotonic()

        # 1. Drop a stale buffer first so it cannot pollute an unrelated turn.
        if (
            self._fragments
            and self._deadline is not None
            and now > self._deadline
        ):
            logger.info(
                "ContinuationBuffer: expired after %.1fs without continuation — "
                "dropping %d stale fragment(s)",
                self._timeout_s,
                len(self._fragments),
            )
            self._fragments.clear()
            self._deadline = None
            self._last_reason = ""

        # 2. Classify. Fail open: any exception treats the utterance as COMPLETE
        #    (AD-OE6 — we MUST NOT silently swallow the user on a bug here).
        try:
            verdict = is_incomplete(text, language=language)
        except Exception:  # noqa: BLE001 — fail-open by contract
            logger.warning(
                "ContinuationBuffer: classifier raised; failing open (treating as complete)",
                exc_info=True,
            )
            verdict = None

        if verdict is not None:
            # 3a. INCOMPLETE — buffer this fragment.
            self._fragments.append(text)
            self._last_reason = verdict.reason
            chain_len = len(self._fragments)
            if chain_len >= self._max_chain:
                # Bounded buffering: flush rather than buffer forever.
                joined = " ".join(self._fragments)
                logger.info(
                    "ContinuationBuffer: max-chain %d reached — flushing joined "
                    "fragments to brain (reason=%s)",
                    self._max_chain,
                    verdict.reason,
                )
                self._fragments.clear()
                self._deadline = None
                self._last_reason = ""
                return joined
            self._deadline = now + self._timeout_s
            logger.info(
                "ContinuationBuffer: buffered fragment %d/%d (reason=%s, "
                "deadline=+%.1fs)",
                chain_len,
                self._max_chain,
                verdict.reason,
                self._timeout_s,
            )
            return None

        # 3b. COMPLETE — join with pending fragments (if any) and dispatch.
        if self._fragments:
            joined = " ".join(self._fragments + [text])
            logger.info(
                "ContinuationBuffer: joining %d fragment(s) + completion → "
                "dispatching as one turn",
                len(self._fragments),
            )
            self._fragments.clear()
            self._deadline = None
            self._last_reason = ""
            return joined
        return text


__all__ = ["ContinuationBuffer"]
