"""Audio-hold voice-scrub gate for realtime duplex mode (AP-11 / ADR-0010).

A duplex model speaks audio natively and its transcript is co-timed but NOT
guaranteed to arrive before the matching audio is audible. So we buffer each
decoded audio delta and release it only once its transcript region has passed
``scrub_for_voice``. A hard leak (stacktrace / raw repr / shell command) drops
the buffered audio and signals the session to cancel + speak the fallback.
Regex-only, no LLM (AP-11).
"""

from __future__ import annotations

from jarvis.brain.output_filter import FALLBACK_PHRASES, scrub_for_voice
from jarvis.core.protocols import AudioChunk

_HARD_LEAK_ACTIONS = frozenset(
    {
        "replaced_stacktrace",
        "replaced_raw_repr",
        "replaced_shell_command",
        "replaced_with_fallback_residue",
    }
)


class ScrubHoldGate:
    """Hold audio until its transcript is scrub-cleared; drop on a hard leak."""

    def __init__(self, language: str, *, lookahead_ms: int = 250) -> None:
        self._language = language if language in FALLBACK_PHRASES else "en"
        self._lookahead_ms = lookahead_ms
        self._pending: list[AudioChunk] = []
        self._cleared = False
        self._hard_leak = False

    def hard_leak_pending(self) -> bool:
        return self._hard_leak

    def fallback_phrase(self) -> str:
        return FALLBACK_PHRASES.get(self._language, FALLBACK_PHRASES["en"])

    async def feed_transcript(self, text: str) -> str:
        """Scrub a transcript boundary. Returns display-safe text.

        Sets the clear flag (audio may flow) on clean text; sets the hard-leak
        flag (audio dropped) on a hard leak.
        """
        result = scrub_for_voice(text, language=self._language)
        if result.fallback_used or (_HARD_LEAK_ACTIONS & set(result.actions)):
            self._hard_leak = True
            self._cleared = False
            self._pending.clear()
            return result.cleaned  # the canned fallback phrase
        self._cleared = True
        return result.cleaned

    async def push_audio(self, chunk: AudioChunk) -> list[AudioChunk]:
        """Buffer or release an audio delta. Returns chunks safe to play now."""
        if self._hard_leak:
            return []
        if self._cleared:
            out = self._pending + [chunk]
            self._pending = []
            return out
        self._pending.append(chunk)
        return []

    def release_available(self) -> list[AudioChunk]:
        """Availability cap: release whatever is buffered (no transcript came)."""
        if self._hard_leak:
            return []
        out = self._pending
        self._pending = []
        self._cleared = True
        return out

    def drain(self) -> None:
        """Barge-in / turn-end: discard buffered audio and reset per-turn state."""
        self._pending.clear()
        self._cleared = False
        self._hard_leak = False
