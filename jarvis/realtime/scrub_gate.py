"""Audio-hold voice-scrub gate for realtime duplex mode (AP-11 / ADR-0010).

A duplex model speaks audio natively and its transcript is co-timed but NOT
guaranteed to arrive before the matching audio is audible. So we buffer each
decoded audio delta and release it only once its transcript region has passed
``scrub_for_voice``. A hard leak (stacktrace / raw repr / shell command) drops
the buffered audio and signals the session to cancel + speak the fallback.
Regex-only, no LLM (AP-11).

The hold applies ONLY to the turn opening. Providers do not pace their
output transcription against their audio: Gemini Live has delivered the
entire reply transcript en bloc with the first audio chunk, and live
sessions have shown the opposite — transcription falling 3-22 s behind the
audio. Every mid-reply release-accounting scheme tried against that reality
turned provider lag into audible dead air: the per-delta credit starved
audio into word-splitting stutter (BUG-069), the coverage budget froze the
voice mid-word for the whole lag (BUG-080), and the 400 ms bounded grace
still chopped the reply into rhythmic blocks while transcription lagged
(maintainer test 2026-07-18). The maintainer mandate is zero gate-caused
interruptions: once the turn's AGGREGATE transcript has been vetted clean
at least once, audio flows unconditionally and the scrubber becomes a
trailing kill switch — a hard leak in a later transcript delta still drops
everything not yet played and cancels the response. Before that first clean
transcript the gate stays strictly fail-closed (nothing is audible yet, so
the hold cannot interrupt anything).
"""

from __future__ import annotations

import logging
import time

from jarvis.brain.output_filter import FALLBACK_PHRASES, ScrubResult, scrub_for_voice
from jarvis.core.protocols import AudioChunk
from jarvis.core.turn_language import validate_output_language
from jarvis.speech.hangup import END_CALL_SIGNAL

log = logging.getLogger(__name__)

_HARD_LEAK_ACTIONS = frozenset(
    {
        "removed_tool_json",
        "replaced_stacktrace",
        "replaced_raw_repr",
        "replaced_shell_command",
        "output_language_mismatch",
    }
)
_RESIDUE_ACTION = "replaced_with_fallback_residue"
_NON_BLOCKING_SCRUB_ACTIONS = frozenset(
    {
        "removed_anrede_drift",  # i18n-allow
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
# Diagnosis only (no release decision hangs on it): estimated audio one
# vetted transcript char accounts for. 55 ms/char is ~18 chars/s — faster
# than any real TTS voice speaks (measured Gemini Live German: ~14 chars/s)
# — so released audio far beyond this estimate proves transcription lagged.
_COVERAGE_MS_PER_CHAR = 55.0
# A finalize() tail this far beyond the coverage estimate cannot be explained
# by the deliberate underestimation alone; log it as a transcription stall.
_FINALIZE_EXCESS_LOG_MS = 5_000.0
# Release budget for ONE injected direct-speech utterance — an OVERestimate
# of its rendering time. 110 ms/char is ~1.5x the SLOWEST real voice this
# module has measured (Gemini Live German, ~14 chars/s = ~71 ms/char), so a
# legitimate readback keeps headroom at every length while the floor covers
# leading pauses on short phrases. Clamping the trusted answer itself would
# recreate "the action ran and the user heard nothing", so the bound must
# stay generous — but not so generous it misses the incident class it exists
# for: the live 2026-08-04 case was 8.2 s of foreign audio riding a
# stall-phrase-length clearance, which a 10 s floor waved through entirely.
_DIRECT_SPEECH_MS_PER_CHAR = 110.0
_DIRECT_SPEECH_BUDGET_FLOOR_MS = 3_000.0


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
        # Provider-neutral identity of the response whose PCM/transcript this
        # gate currently holds.  Empty means the adapter cannot expose an id
        # and preserves the legacy ordered-stream contract.  Once an adapter
        # does expose one, a different id can never clear or release this
        # response's audio: that would pair text from answer B with PCM from
        # answer A, the exact synchronization failure this gate exists to
        # prevent.
        self._response_id = ""
        self._cleared = False
        self._hard_leak = False
        self._transcript_seen = False
        self._transcript_tail = ""
        # Language classification needs more context than the first streamed
        # words often provide.  Until the aggregate is a positive match, PCM
        # stays buffered: an indeterminate prefix must not authorize audio that
        # a later delta proves belongs to the wrong language.  A still-
        # indeterminate *complete* reply is released by ``finalize`` because
        # the shared validator deliberately treats that verdict as fail-open.
        self._language_validation_active = False
        self._language_match_seen = False
        # A provider may split a control token across transcript deltas. The
        # ordinary scrubber sees each delta independently for display, so a
        # split token used to pass through unchanged even though the aggregate
        # scrub correctly recognized it. Hold only a possible token prefix
        # until the next delta decides it.
        self._control_tail = ""
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
        # Clearance granted to ONE injected, already-scrubbed utterance that
        # will never carry a model transcript (see ``trust_direct_speech``).
        # Scoped rather than sticky: it survives a ``drain()`` only until its
        # own audio has actually played.
        self._direct_speech_pending = False
        self._direct_speech_released_ms = 0.0
        self._direct_speech_budget_ms = 0.0

    def _expire_direct_speech_budget(self) -> None:
        """Retire a direct-speech clearance whose audio must be over by now.

        The injected utterance renders once; audio released far beyond its
        generous rendering budget belongs to some OTHER response and goes back
        to fail-closed vetting. When real vetted transcript chars exist, the
        ordinary coverage machinery keeps its own verdict.
        """
        if not self._direct_speech_pending:
            return
        if self._direct_speech_released_ms <= self._direct_speech_budget_ms:
            return
        self._direct_speech_pending = False
        if self._covered_chars <= 0:
            self._coverage_active = False
            self._cleared = False
        log.info(
            "scrub gate expired a direct-speech clearance after %d ms of "
            "released audio (budget %d ms); later audio is vetted again",
            int(self._direct_speech_released_ms),
            int(self._direct_speech_budget_ms),
        )
        self._direct_speech_budget_ms = 0.0

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

    def begin_response(self, response_id: str = "") -> bool:
        """Bind this gate to one provider response.

        Adapters that cannot identify responses pass the default empty value
        and keep the existing ordered-stream behaviour.  A non-empty mismatch
        fails closed and drops every held chunk; callers then cancel the
        provider response and speak the ordinary safe fallback.
        """
        identity = str(response_id or "").strip()
        if not identity:
            return True
        if not self._response_id:
            self._response_id = identity
            return True
        if identity == self._response_id:
            return True
        self._hard_leak = True
        self._hard_leak_actions = ("response_identity_mismatch",)
        self._cleared = False
        self._pending.clear()
        self._pending_audio_ms = 0.0
        self._pending_since = None
        return False

    @property
    def response_id(self) -> str:
        """The non-empty provider response currently owned by this gate."""
        return self._response_id

    async def feed_transcript(
        self,
        text: str,
        *,
        response_id: str = "",
        enforce_output_language: bool = False,
    ) -> str:
        """Scrub a transcript boundary. Returns display-safe text.

        Sets the clear flag (audio may flow) on clean text; sets the hard-leak
        flag (audio dropped) on a hard leak.
        """
        if not self.begin_response(response_id) or self._hard_leak:
            return self.fallback_phrase()

        text = self._strip_stream_controls(str(text or ""))
        if not text:
            self._transcript_seen = True
            return ""

        self._transcript_tail = (
            f"{self._transcript_tail}{text}"[-_TRANSCRIPT_TAIL_MAX_CHARS:]
        )
        self._transcript_seen = True
        aggregate = scrub_for_voice(self._transcript_tail, language=self._language)
        result = scrub_for_voice(text, language=self._language)
        language_verdict = None
        if enforce_output_language:
            self._language_validation_active = True
            language_verdict = validate_output_language(
                aggregate.cleaned,
                resolved_language=self._language,
            )
            if language_verdict.should_block:
                self._hard_leak = True
                self._hard_leak_actions = ("output_language_mismatch",)
                self._cleared = False
                self._pending.clear()
                self._pending_audio_ms = 0.0
                self._pending_since = None
                return self.fallback_phrase()
            if language_verdict.status == "match":
                self._language_match_seen = True
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
        if (
            language_verdict is not None
            and language_verdict.status == "indeterminate"
            and not self._language_match_seen
        ):
            # Keep the opening PCM fail-closed until enough aggregate prose
            # establishes the resolved language.  Display text may continue;
            # it has already passed the ordinary regex scrub above.
            self._covered_chars += len(text)
            self._cleared = False
            if not result.actions:
                return text
            return _restore_edge_whitespace(text, result.cleaned)
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

    def _strip_stream_controls(self, text: str) -> str:
        """Remove complete or delta-split pipeline control tokens."""
        combined = f"{self._control_tail}{text}"
        self._control_tail = ""
        combined = combined.replace(END_CALL_SIGNAL, "")
        max_prefix = min(len(combined), len(END_CALL_SIGNAL) - 1)
        for size in range(max_prefix, 0, -1):
            if combined.endswith(END_CALL_SIGNAL[:size]):
                self._control_tail = combined[-size:]
                combined = combined[:-size]
                break
        return combined

    async def push_audio(
        self,
        chunk: AudioChunk,
        *,
        response_id: str = "",
    ) -> list[AudioChunk]:
        """Buffer or release an audio delta. Returns chunks safe to play now.

        Two states only (maintainer mandate 2026-07-18, BUG-080 follow-up —
        zero gate-caused mid-reply interruptions):
        1. Turn opening (aggregate transcript never clean yet): buffer,
           fail-closed. Nothing is audible yet, so this hold cannot
           interrupt speech — it only delays the reply start by the co-timed
           transcript's few-ms head start. A clean transcript delta clears
           the backlog (via ``_cleared``/``release_available``).
        2. After the first clean aggregate transcript: everything flows
           unconditionally, however far the provider transcription lags its
           audio. The scrubber keeps running as a trailing kill switch — a
           hard leak in a later delta drops all unplayed audio and cancels
           the response.
        """
        if not self.begin_response(response_id) or self._hard_leak:
            return []
        self._expire_direct_speech_budget()
        if not self._cleared and not self._coverage_active:
            if not self._pending and self._pending_since is None:
                self._pending_since = time.monotonic()
            self._pending.append(chunk)
            self._pending_audio_ms += _duration_ms((chunk,))
            return []
        out = self._pending + [chunk]
        self._pending = []
        self._pending_audio_ms = 0.0
        self._cleared = False
        released = _duration_ms(out)
        self._released_ms += released
        if self._direct_speech_pending:
            self._direct_speech_released_ms += released
        self._consume_hold_clock()
        return out

    def release_available(self) -> list[AudioChunk]:
        """Release buffered audio only after a transcript cleared the gate."""
        self._expire_direct_speech_budget()
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
        released = _duration_ms(out)
        self._released_ms += released
        if self._direct_speech_pending:
            self._direct_speech_released_ms += released
        self._consume_hold_clock()
        return out

    def trust_direct_speech(self, text: str = "") -> None:
        """Clear the gate for audio rendering text Jarvis itself scrubbed.

        A delegate readback is injected through the provider's direct-speech
        channel, so its audio has no MODEL transcript to vet — the opening
        hold can never clear, and ``fail_closed`` then drops the whole answer
        at the turn boundary. Measured live 2026-08-02: the action ran, the
        reply existed, and the user heard nothing but "keeping the turn
        text-only".

        Re-gating this audio protects nothing: the text passed
        ``scrub_for_voice`` before it was ever sent (ADR-0010) — which is also
        why the clearance cannot be re-derived later: AP-11 forbids an LLM call
        in the voice scrubber, and the model emits no transcript of its own for
        this audio. The trailing kill switch is untouched — a later transcript
        that does leak still sets ``_hard_leak`` and drops everything unplayed.

        The clearance is bound to THIS utterance (``_direct_speech_pending``):
        a ``drain()`` belonging to some OTHER response — on an
        automatic-response transport the model's own concurrent generation
        routinely ends between the injection and the first readback frame —
        must not revoke it. ``text`` is accepted for call-site clarity and for
        the debug line; the gate never re-scrubs it.
        """
        self._transcript_seen = True
        self._coverage_active = True
        self._cleared = True
        self._direct_speech_pending = True
        self._direct_speech_released_ms = 0.0
        # Bounded, not sticky: the clearance covers THIS utterance's rendering
        # (generously overestimated), never the rest of the call.
        self._direct_speech_budget_ms = (
            _DIRECT_SPEECH_BUDGET_FLOOR_MS
            + len(str(text or "")) * _DIRECT_SPEECH_MS_PER_CHAR
        )
        if text:
            log.debug(
                "scrub gate trusts %d chars of injected direct speech",
                len(str(text)),
            )

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

    def finalize(self, *, response_id: str = "") -> list[AudioChunk]:
        """Release the clean transcript-covered tail at the response boundary.

        Trust basis: at a genuine response boundary every transcript delta of
        the turn has arrived and passed the aggregate scrub, so the buffered
        tail is covered by vetted text. The coverage estimate deliberately
        UNDERESTIMATES spoken duration, so a legitimate tail routinely sits
        somewhat above the budget — never drop it for that. But a tail far
        beyond the estimate means transcription lagged or died mid-turn;
        log it so the next incident names this producer (BUG-069 review).
        """
        if not self.begin_response(response_id):
            return []
        self._expire_direct_speech_budget()
        if self.fail_closed() or self._hard_leak:
            return []
        # A complete short/ambiguous reply can remain indeterminate forever.
        # That verdict is intentionally non-blocking; only the response
        # boundary, after every delta has been inspected, may release it.
        if self._language_validation_active and not self._language_match_seen:
            self._cleared = True
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
        if self._direct_speech_pending:
            self._direct_speech_released_ms += tail_ms
        self._consume_hold_clock()
        return out

    def drain(self) -> None:
        """Barge-in / turn-end: discard buffered audio and reset per-turn state.

        One exception, and it is the whole point of ``trust_direct_speech``:
        an injected, already-scrubbed utterance whose audio has not started
        playing yet keeps its clearance. On a transport that generates its own
        responses, the model's concurrent generation ends — and drains this
        gate — between the injection and the first readback frame; revoking
        there left the readback with no clearance and no model transcript, so
        the next boundary dropped the whole answer as ``no_transcript``. The
        action had already run and the user heard nothing.
        """
        keep_direct_speech = bool(
            self._direct_speech_pending
            and self._direct_speech_released_ms <= 0.0
            and not self._hard_leak
        )
        self._pending.clear()
        self._pending_audio_ms = 0.0
        self._cleared = False
        self._hard_leak = False
        self._transcript_seen = False
        self._transcript_tail = ""
        self._language_validation_active = False
        self._language_match_seen = False
        self._control_tail = ""
        self._hard_leak_actions = ()
        self._response_id = ""
        self._pending_since = None
        self.last_hold_ms = 0.0
        self._released_ms = 0.0
        self._covered_chars = 0
        self._coverage_active = False
        self._direct_speech_pending = False
        self._direct_speech_released_ms = 0.0
        if keep_direct_speech:
            self._direct_speech_pending = True
            self._transcript_seen = True
            self._coverage_active = True
            self._cleared = True


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
