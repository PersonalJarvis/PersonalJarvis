"""Audio-hold voice-scrub gate for realtime duplex mode (AP-11 / ADR-0010).

A duplex model speaks audio natively and its transcript is co-timed but NOT
guaranteed to arrive before the matching audio is audible. So we buffer each
decoded audio delta and release it only once its transcript region has passed
``scrub_for_voice``. A hard leak (stacktrace / raw repr / shell command) drops
the buffered audio and signals the session to cancel + speak the fallback.
Regex-only, no LLM (AP-11).

Release accounting is a COVERAGE BUDGET, not a per-delta credit. Providers do
not pace their output transcription against their audio: a Gemini Live probe
(2026-07-17) delivered the ENTIRE reply transcript as one delta alongside the
first audio chunk, and live sessions have shown the opposite — transcription
falling 5-7 s behind the audio. The previous design released exactly one
audio push per transcript delta, so an up-front en-bloc transcript starved
every later chunk until turn end: the audible word-splitting stutter and
multi-second mid-reply holes of BUG-069. Now each clean transcript char funds
``_COVERAGE_MS_PER_CHAR`` of audio, cumulatively per response. The rate is
deliberately FASTER than any real speaking voice (~18 chars/s vs a real
12-17), so the budget systematically underestimates the true spoken duration
of the vetted text — released audio therefore always stays inside the span
the scrubber has already cleared. Audio beyond that span still buffers,
fail-closed, exactly as before.
"""

from __future__ import annotations

import logging
import time

from jarvis.brain.output_filter import FALLBACK_PHRASES, ScrubResult, scrub_for_voice
from jarvis.core.protocols import AudioChunk

log = logging.getLogger(__name__)

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
# How much audio one vetted transcript char funds. 55 ms/char is ~18 chars/s
# — faster than any real TTS voice speaks (measured Gemini Live German:
# ~14 chars/s, i.e. ~70 ms/char) — so the budget UNDERESTIMATES the spoken
# duration of already-scrubbed text and released audio cannot outrun it.
_COVERAGE_MS_PER_CHAR = 55.0
# A finalize() tail this far beyond the coverage estimate cannot be explained
# by the deliberate underestimation alone; log it as a transcription stall.
_FINALIZE_EXCESS_LOG_MS = 5_000.0


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
        # Coverage budget (BUG-069): audio released so far vs. the estimated
        # spoken duration of every transcript char the scrubber has vetted.
        # ``_coverage_active`` stays False until the AGGREGATE transcript has
        # been clean at least once — a turn that opens with residue (a lone
        # dash, a filler opener) must not fund any release.
        self._released_ms = 0.0
        self._covered_chars = 0
        self._coverage_active = False
        # Hold-time telemetry: how long the batch released last waited for its
        # clearing transcript. Lets the session attribute an audible mid-reply
        # hole to a late transcript delta instead of a silent provider
        # (live forensic 2026-07-16 10:26).
        self._pending_since: float | None = None
        self.last_hold_ms = 0.0

    @property
    def pending_audio_ms(self) -> float:
        """Milliseconds of audio currently held while awaiting a transcript."""
        return self._pending_audio_ms

    def _consume_hold_clock(self) -> None:
        if self._pending_since is not None:
            self.last_hold_ms = (time.monotonic() - self._pending_since) * 1_000.0
            self._pending_since = None
        else:
            self.last_hold_ms = 0.0

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
            self._pending_since = None
            return self.fallback_phrase()
        if _is_stream_safe_residue(aggregate):
            # A realtime provider may emit punctuation or the first half of a
            # protected compound as its own transcript delta. The complete
            # utterance is not available yet, so this benign residue neither
            # authorizes buffered audio nor aborts the response. The next
            # meaningful delta decides. Its chars still count toward coverage
            # (they are part of the spoken text and keep being re-checked via
            # the aggregate), but the budget stays dormant until the aggregate
            # has been clean once.
            self._covered_chars += len(text)
            self._cleared = False
            return text
        self._covered_chars += len(text)
        self._coverage_active = True
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
        """Buffer or release an audio delta. Returns chunks safe to play now.

        Release order of precedence:
        1. A clean transcript delta clears everything buffered before it plus
           this chunk (audio received before the delta belongs to text up to
           that delta on a co-timed stream) — the pre-budget behavior,
           unchanged.
        2. The coverage budget: this chunk flows immediately while cumulative
           released audio stays inside the estimated spoken duration of all
           vetted transcript chars. This is what keeps playback continuous
           when a provider sends its transcript up-front en bloc (BUG-069).
        Anything else buffers, fail-closed.
        """
        if self._hard_leak:
            return []
        if self._cleared:
            out = self._pending + [chunk]
            self._pending = []
            self._pending_audio_ms = 0.0
            self._cleared = False
            self._released_ms += _duration_ms(out)
            self._consume_hold_clock()
            return out
        chunk_ms = _duration_ms((chunk,))
        if self._coverage_active:
            # Evaluate the WHOLE backlog plus this chunk so a budget that
            # grew since the backlog formed can un-stick it (FIFO preserved:
            # either everything flows or everything keeps buffering).
            total_ms = self._pending_audio_ms + chunk_ms
            if (
                self._released_ms + total_ms
                <= self._covered_chars * _COVERAGE_MS_PER_CHAR
            ):
                out = self._pending + [chunk]
                self._pending = []
                self._pending_audio_ms = 0.0
                self._released_ms += total_ms
                self._consume_hold_clock()
                return out
        if not self._pending and self._pending_since is None:
            self._pending_since = time.monotonic()
        self._pending.append(chunk)
        self._pending_audio_ms += chunk_ms
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
        self._released_ms += _duration_ms(out)
        self._consume_hold_clock()
        return out

    def fail_closed(self) -> bool:
        """Drop a completed response that never produced any transcript."""
        if self._hard_leak or not self._pending or self._transcript_seen:
            return False
        self._pending.clear()
        self._pending_audio_ms = 0.0
        self._pending_since = None
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
        self._pending_since = None
        self._cleared = False
        self._hard_leak = True
        self._hard_leak_actions = ("transcript_stalled",)
        return True

    def finalize(self) -> list[AudioChunk]:
        """Release the clean transcript-covered tail at the response boundary.

        Trust basis: at a genuine response boundary every transcript delta of
        the turn has arrived and passed the aggregate scrub, so the buffered
        tail is covered by vetted text. The coverage estimate deliberately
        UNDERESTIMATES spoken duration, so a legitimate tail routinely sits
        somewhat above the budget — never drop it for that. But a tail far
        beyond the estimate means transcription lagged or died mid-turn;
        log it so the next incident names this producer (BUG-069 review).
        """
        if self.fail_closed() or self._hard_leak:
            return []
        out = self._pending
        self._pending = []
        self._pending_audio_ms = 0.0
        self._cleared = False
        tail_ms = _duration_ms(out)
        excess_ms = (
            self._released_ms
            + tail_ms
            - self._covered_chars * _COVERAGE_MS_PER_CHAR
        )
        if out and excess_ms > _FINALIZE_EXCESS_LOG_MS:
            log.info(
                "scrub gate released a %d ms tail at the response boundary, "
                "%d ms beyond the vetted-text coverage estimate — the "
                "provider transcription lagged or stopped mid-turn",
                int(tail_ms),
                int(excess_ms),
            )
        self._released_ms += tail_ms
        self._consume_hold_clock()
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
        self._pending_since = None
        self.last_hold_ms = 0.0
        self._released_ms = 0.0
        self._covered_chars = 0
        self._coverage_active = False


def _duration_ms(chunks: tuple[AudioChunk, ...] | list[AudioChunk]) -> float:
    """Total playback duration of 16-bit mono PCM chunks in milliseconds."""
    total = 0.0
    for chunk in chunks:
        sample_rate = max(1, int(chunk.sample_rate or 0))
        total += (len(chunk.pcm) / 2) * 1_000.0 / sample_rate
    return total


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
