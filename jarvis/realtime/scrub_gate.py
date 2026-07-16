"""Audio-hold voice-scrub gate for realtime duplex mode (AP-11 / ADR-0010).

A duplex model speaks audio natively and its transcript is co-timed but NOT
guaranteed to arrive before the matching audio is audible. So we buffer each
decoded audio delta and release it only once its transcript region has passed
``scrub_for_voice``. A hard leak (stacktrace / raw repr / shell command) drops
the buffered audio and signals the session to cancel + speak the fallback.
Regex-only, no LLM (AP-11).
"""

from __future__ import annotations

from jarvis.brain.output_filter import FALLBACK_PHRASES, ScrubResult, scrub_for_voice
from jarvis.core.protocols import AudioChunk

_HARD_LEAK_ACTIONS = frozenset(
    {
        "removed_tool_json",
        "replaced_stacktrace",
        "replaced_raw_repr",
        "replaced_shell_command",
    }
)
_RESIDUE_ACTION = "replaced_with_fallback_residue"
_NON_BLOCKING_SCRUB_ACTIONS = frozenset(
    {
        "removed_anrede_drift",  # i18n-allow: established telemetry action-name identifier (ADR-0010), not prose
        "removed_background_action_narration",
        "removed_em_dash",
        "removed_engineering_jargon",
        "removed_filler_opener",
        "removed_self_reference",
        "removed_source_artifacts",
        "rephrased_echo",
        "spelled_out_numbers",
        "stripped_end_signal",
        "stripped_markdown",
    }
)
_KNOWN_SCRUB_ACTIONS = (
    _HARD_LEAK_ACTIONS | _NON_BLOCKING_SCRUB_ACTIONS | {_RESIDUE_ACTION}
)
_TRANSCRIPT_TAIL_MAX_CHARS = 4_096


class ScrubHoldGate:
    """Hold audio until its transcript is scrub-cleared; drop on a hard leak."""

    def __init__(self, language: str, *, lookahead_ms: int = 250) -> None:
        self._language = language if language in FALLBACK_PHRASES else "en"
        # Retain the argument for adapter compatibility. Realtime audio and
        # transcript deltas are concurrent, so elapsed wall time cannot prove
        # that a pending audio chunk has no matching transcript.
        del lookahead_ms
        self._pending: list[AudioChunk] = []
        self._pending_audio_ms = 0.0
        self._cleared = False
        self._hard_leak = False
        self._transcript_seen = False
        self._transcript_tail = ""
        self._hard_leak_actions: tuple[str, ...] = ()

    def hard_leak_pending(self) -> bool:
        return self._hard_leak

    def hard_leak_actions(self) -> tuple[str, ...]:
        """Scrub-action names behind the current hard leak (diagnosis only).

        Safe metadata: detector names such as ``replaced_shell_command`` —
        never the flagged content itself, so surfacing them in transcripts
        and latency spans cannot re-leak what the gate withheld (BUG-056:
        the 15:13 abort was undiagnosable because only the generic reason
        string survived).
        """
        return self._hard_leak_actions

    def fallback_phrase(self) -> str:
        return FALLBACK_PHRASES.get(self._language, FALLBACK_PHRASES["en"])

    async def feed_transcript(self, text: str) -> str:
        """Scrub a transcript boundary. Returns display-safe text.

        Sets the clear flag (audio may flow) on clean text; sets the hard-leak
        flag (audio dropped) on a hard leak.
        """
        if self._hard_leak:
            return self.fallback_phrase()

        self._transcript_tail = (
            f"{self._transcript_tail}{text}"[-_TRANSCRIPT_TAIL_MAX_CHARS:]
        )
        self._transcript_seen = True
        aggregate = scrub_for_voice(self._transcript_tail, language=self._language)
        result = scrub_for_voice(text, language=self._language)
        aggregate_is_hard = _is_hard_scrub_result(aggregate)
        result_is_hard = _is_hard_scrub_result(result)
        if aggregate_is_hard or result_is_hard:
            self._hard_leak = True
            self._hard_leak_actions = tuple(
                sorted(set(aggregate.actions) | set(result.actions))
            )
            self._cleared = False
            self._pending.clear()
            self._pending_audio_ms = 0.0
            return self.fallback_phrase()
        if _is_stream_safe_residue(aggregate):
            # A realtime provider may emit punctuation or the first half of a
            # protected compound as its own transcript delta. The complete
            # utterance is not available yet, so this benign residue neither
            # authorizes buffered audio nor aborts the response. The next
            # meaningful delta decides.
            self._cleared = False
            return text
        self._cleared = True
        if _is_stream_safe_residue(result):
            # Preserve the provider's boundary verbatim. Replacing this one
            # harmless delta with the whole-utterance fallback would both
            # corrupt the displayed transcript and false-cancel native audio.
            return text
        if not result.actions:
            # Realtime providers stream transcript deltas with meaningful edge
            # whitespace (for example ``"All"``, ``" right"``). The voice
            # scrubber normalizes each call with ``strip()``, so returning its
            # clean result here would glue every streamed word together. No
            # scrub action means the original delta was safe; preserve it byte
            # for byte, including punctuation-only and whitespace-only deltas.
            return text
        return _restore_edge_whitespace(text, result.cleaned)

    async def push_audio(self, chunk: AudioChunk) -> list[AudioChunk]:
        """Buffer or release an audio delta. Returns chunks safe to play now."""
        if self._hard_leak:
            return []
        if self._cleared:
            out = self._pending + [chunk]
            self._pending = []
            self._pending_audio_ms = 0.0
            self._cleared = False
            return out
        self._pending.append(chunk)
        sample_rate = max(1, int(chunk.sample_rate or 0))
        self._pending_audio_ms += (len(chunk.pcm) / 2) * 1_000 / sample_rate
        return []

    def release_available(self) -> list[AudioChunk]:
        """Release buffered audio only after a transcript cleared the gate."""
        if self._hard_leak or not self._cleared:
            return []
        # Some providers send the transcript delta just before its matching
        # audio delta. Preserve one clean credit when there is nothing to
        # release yet; ``push_audio`` consumes it on exactly one later chunk.
        if not self._pending:
            return []
        out = self._pending
        self._pending = []
        self._pending_audio_ms = 0.0
        self._cleared = False
        return out

    def fail_closed(self) -> bool:
        """Drop a completed response that never produced any transcript."""
        if self._hard_leak or not self._pending or self._transcript_seen:
            return False
        self._pending.clear()
        self._pending_audio_ms = 0.0
        self._cleared = False
        self._hard_leak = True
        self._hard_leak_actions = ("no_transcript",)
        return True

    def fail_if_pending_exceeds(self, max_pending_ms: int) -> bool:
        """Bound audio memory when transcript deltas stop arriving entirely."""
        if (
            self._hard_leak
            or not self._pending
            or self._pending_audio_ms <= max(0, int(max_pending_ms))
        ):
            return False
        self._pending.clear()
        self._pending_audio_ms = 0.0
        self._cleared = False
        self._hard_leak = True
        self._hard_leak_actions = ("transcript_stalled",)
        return True

    def finalize(self) -> list[AudioChunk]:
        """Release the clean transcript-covered tail at the response boundary."""
        if self.fail_closed() or self._hard_leak:
            return []
        out = self._pending
        self._pending = []
        self._pending_audio_ms = 0.0
        self._cleared = False
        return out

    def drain(self) -> None:
        """Barge-in / turn-end: discard buffered audio and reset per-turn state."""
        self._pending.clear()
        self._pending_audio_ms = 0.0
        self._cleared = False
        self._hard_leak = False
        self._transcript_seen = False
        self._transcript_tail = ""
        self._hard_leak_actions = ()


def _restore_edge_whitespace(original: str, cleaned: str) -> str:
    """Keep provider delta separators around content changed by the scrubber."""
    if not cleaned:
        return cleaned
    leading_count = len(original) - len(original.lstrip())
    trailing_count = len(original) - len(original.rstrip())
    leading = original[:leading_count]
    trailing = original[-trailing_count:] if trailing_count else ""
    return f"{leading}{cleaned}{trailing}"


def _is_stream_safe_residue(result: ScrubResult) -> bool:
    """Return whether fallback came from a benign incomplete stream fragment."""
    actions = set(result.actions)
    residue_sources = actions - {_RESIDUE_ACTION}
    return bool(
        result.fallback_used
        and _RESIDUE_ACTION in actions
        and residue_sources
        and residue_sources <= _NON_BLOCKING_SCRUB_ACTIONS
    )


def _is_hard_scrub_result(result: ScrubResult) -> bool:
    """Block only real or unclassified leaks, never presentation residue."""
    actions = set(result.actions)
    if actions - _KNOWN_SCRUB_ACTIONS:
        # Every new scrub action must be classified explicitly. This preserves
        # fail-closed security without conflating known style transforms with
        # machine-data leaks.
        return True
    if _HARD_LEAK_ACTIONS & actions:
        return True
    if _is_stream_safe_residue(result):
        return False
    return bool(result.fallback_used)
