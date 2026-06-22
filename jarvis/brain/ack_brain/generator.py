"""AckGenerator — orchestrator for the Pre-Thinking-Ack Flash-Brain.

Owns one provider adapter (from providers/REGISTRY) and one circuit
breaker. On each call to ``run()`` it walks an 11-step pipeline (see
spec §4) that enforces the latency budget, post-processes the LLM
output, and emits telemetry counters for every failure mode.

Failure-mode coverage (spec §6):
- F1 Timeout (asyncio.wait_for cancels)
- F2 Provider error (any exception during run)
- F3 Empty / whitespace
- F4 Over-long output (truncate at first [.!?])
- F5 Schwarzliste-stripped to < 3 alnum chars
- F6 Language mismatch (top-100 word heuristic)
- F8 Circuit breaker open
- F10 Self-answer (post-filter for answer-shaped output) — spec
       update 2026-05-13

The generator NEVER raises — every failure path returns ``None`` so
the speech-pipeline's silent-on-failure rule (US-4) is by-construction.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from jarvis.brain.ack_brain.circuit_breaker import CircuitBreaker
from jarvis.brain.ack_brain.persona_prompt import get_persona_prompt
from jarvis.brain.output_filter import scrub_for_voice

if TYPE_CHECKING:
    from jarvis.brain.ack_brain.config import AckBrainConfig
    from jarvis.brain.ack_brain.providers.base import AbstractAckProvider

log = logging.getLogger(__name__)


def _augment_with_preferences(
    base_prompt: str, provider: Callable[[], str] | None
) -> str:
    """Append the user's standing-preferences block to a flash persona prompt.

    Shared by the ack preamble and the spawn announcement. ``provider`` returns
    the (already framed) preferences block, or ``""`` when none is set. A faulty
    provider must never break the latency-critical flash call, so any exception
    falls back to the unmodified base prompt (silent-on-failure, US-4).
    """
    if provider is None:
        return base_prompt
    try:
        extra = (provider() or "").strip()
    except Exception:  # noqa: BLE001 — a prefs read fault must not mute the ack
        return base_prompt
    return f"{base_prompt}\n\n{extra}" if extra else base_prompt


# ----------------------------------------------------------------------
# Cheap top-100-word language heuristic
# ----------------------------------------------------------------------
# Tokens that appear in the v2.1 persona-prompt examples + common
# function words. Kept small (~70 each) — frozenset overlap is O(n)
# in token count, dominated by re.findall.

_TOP_DE: frozenset[str] = frozenset({
    "der", "die", "das", "und", "ich", "ist", "nicht", "ein", "eine", "zu",
    "den", "mit", "sich", "auf", "für", "von", "im", "dem", "ja", "kurz",
    "mache", "danke", "hallo", "klar", "okay", "gut", "lass", "mich", "kann",
    "gleich", "schaue", "suche", "prüfe", "starte", "wechsle", "öffne",
    "hole", "schaut", "ändere", "recherchiere", "guten", "tag", "abend",
    "morgen", "schön", "bitte", "leider", "noch", "schon", "aber",
    "oder", "auch", "nur", "weil", "wenn", "dann", "doch", "wie", "was",
    "wer", "wo", "wann", "warum", "diese", "dieser", "dieses", "habe",
    "hast", "hat", "haben", "wird", "werden", "wollen", "kannst", "konnte",
    "sollst", "soll", "muss", "müssen", "Chef",
})

_TOP_EN: frozenset[str] = frozenset({
    "the", "and", "you", "that", "for", "with", "this", "let", "me", "on",
    "check", "sure", "got", "ok", "fine", "good", "morning", "afternoon",
    "evening", "yes", "no", "search", "fetch", "look", "up", "launch",
    "change", "switch", "open", "thanks", "hi", "hello", "hey", "please",
    "still", "now", "but", "or", "also", "only", "because", "if", "then",
    "how", "what", "who", "where", "when", "why", "have", "has", "had",
    "will", "would", "could", "should", "must", "can", "be", "is", "are",
    "was", "were", "am", "do", "does", "did", "going", "want", "need",
    "it", "to", "of", "in", "a", "an",
})

_TOKEN_RE = re.compile(r"\b[\w']+\b", re.UNICODE)


def _detect_language(text: str) -> str:
    """Cheap heuristic: pick the language whose top-100 set has more overlap.

    Returns "de", "en", or "unknown" (both counts zero — e.g. pure
    proper-noun output). The generator treats "unknown" as a non-mismatch
    so single-word names like ``"Spotify."`` don't get false-rejected.
    """
    tokens = {t.lower() for t in _TOKEN_RE.findall(text)}
    if not tokens:
        return "unknown"
    de = len(tokens & _TOP_DE)
    en = len(tokens & _TOP_EN)
    if de == 0 and en == 0:
        return "unknown"
    return "de" if de >= en else "en"


# ----------------------------------------------------------------------
# F4 truncation
# ----------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?]")

def _truncate_at_first_sentence(text: str) -> str:
    """Keep everything up to and including the first sentence end."""
    match = _SENTENCE_END_RE.search(text)
    if not match:
        return text
    return text[: match.end()]


def _word_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


# ----------------------------------------------------------------------
# F10 self-answer post-filter
# ----------------------------------------------------------------------
# Detect three answer-shaped patterns from spec §6 (2026-05-13 update):
#   date_answer / single_word_fact / definition
# An action verb anywhere in the text negates the filter (the ack is
# describing an action, not answering a question).

_ACTION_VERBS = frozenset({
    "suche", "suchst", "sucht", "prüfe", "prüfst", "prüft", "hole", "holst",
    "holt", "schaue", "schaust", "schaut", "starte", "startest", "startet",
    "öffne", "öffnest", "öffnet", "wechsle", "wechselst", "wechselt",
    "recherchiere", "recherchierst", "recherchiert", "ändere", "änderst",
    "ändert", "mache", "machst", "macht", "look", "lookup", "search",
    "fetch", "fetches", "fetching", "check", "checks", "checking",
    "launch", "launches", "launching", "change", "changes", "changing",
    "switch", "switches", "switching", "open", "opens", "opening",
})

_DATE_NUMERIC_RE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_WEEKDAY_RE = re.compile(
    r"\b(montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_MONTH_RE = re.compile(
    r"\b(januar|februar|märz|april|mai|juni|juli|august|september|"
    r"oktober|november|dezember|january|february|march|may|june|july|"
    r"august|september|october|november|december)\b",
    re.IGNORECASE,
)
# Spec §6 calls for `(.*)\s(ist|...)\s(.*)\.` — i.e. multi-word
# subjects ("Die Hauptstadt von Italien ist Rom.") must match. The
# original tightening to `\S+` only caught single-word subjects and
# silently failed on the very examples the spec listed; broaden to
# `.+?` so a multi-word noun-phrase prefix still triggers the filter.
_DEFINITION_RE = re.compile(
    r"^\s*.+?\s+(ist|sind|war|waren|is|are|was|were)\s+.+\.\s*$",
    re.IGNORECASE,
)


def _has_action_verb(text: str) -> bool:
    tokens = {t.lower() for t in _TOKEN_RE.findall(text)}
    return bool(tokens & _ACTION_VERBS)


def _detect_self_answer(text: str) -> str | None:
    """Return matching pattern name if text looks like a substantive answer.

    Returns one of ``"date_answer"``, ``"single_word_fact"``,
    ``"definition"`` — or ``None`` if no pattern fires. The presence of
    an action verb anywhere in the output suppresses all three: it
    means the ack is describing what JARVIS is about to do, not
    answering the user.
    """
    if _has_action_verb(text):
        return None

    # date_answer: any date/time/weekday/month token
    if (
        _DATE_NUMERIC_RE.search(text)
        or _TIME_RE.search(text)
        or _WEEKDAY_RE.search(text)
        or _MONTH_RE.search(text)
    ):
        return "date_answer"

    # single_word_fact: 1-2 words ending with a period
    stripped = text.strip().rstrip()
    tokens = _TOKEN_RE.findall(stripped)
    if (
        1 <= len(tokens) <= 2
        and stripped.endswith(".")
        and stripped.lower() not in {"hi.", "hallo.", "ok.", "okay."}
    ):
        return "single_word_fact"

    # definition: "X ist Y." pattern
    if _DEFINITION_RE.match(stripped):
        return "definition"

    return None


# ----------------------------------------------------------------------
# Telemetry counters
# ----------------------------------------------------------------------
# Until a structured event-bus counter type lands, counters are emitted
# as structured log records. The FlightRecorder can parse them as
# JSONL via its existing wildcard subscriber.

def _emit_counter(name: str, /, **labels: str) -> None:
    log.info(
        "ack_counter name=%s labels=%s",
        name,
        labels if labels else {},
        extra={"counter_name": name, "counter_labels": labels},
    )


def _emit_histogram(name: str, value: float, /, **labels: str) -> None:
    log.info(
        "ack_histogram name=%s value=%.3f labels=%s",
        name,
        value,
        labels if labels else {},
        extra={
            "histogram_name": name,
            "histogram_value": value,
            "histogram_labels": labels,
        },
    )


# ----------------------------------------------------------------------
# AckGenerator
# ----------------------------------------------------------------------

class AckGenerator:
    """Orchestrates a single Flash-Brain acknowledgment call.

    Holds a provider adapter and a circuit breaker, both injected at
    construction. ``run()`` is the only public method — see its
    docstring for the 11-step pipeline.
    """

    def __init__(
        self,
        *,
        provider: AbstractAckProvider,
        config: AckBrainConfig,
        breaker: CircuitBreaker,
        fallback: "AckGenerator | None" = None,
        preferences_provider: Callable[[], str] | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._breaker = breaker
        # Returns the user's standing-preferences block (or "") so the ack
        # preamble honors the user's agent-instructions file on action turns —
        # the deep brain already injects it, but action turns speak the ack.
        # Read fresh per call so an edit applies without a restart.
        self._preferences_provider = preferences_provider
        # Derived name for telemetry labels — config.provider is the
        # registry-key, not a human-readable label, but they coincide
        # by design ("gemini", "grok", "openai", "ollama").
        self._provider_name = config.provider
        # Optional failover AckGenerator on a SEPARATE provider/breaker. Used
        # only when THIS provider is exhausted (timed out / errored / produced
        # nothing), so a busy primary never leaves the user in silence — the
        # very condition the ack exists to bridge (live bug 2026-06-18: the
        # Gemini ack timed out while the Gemini deep brain was slow → 8 s of
        # dead air → user aborted). Wired by the factory to one level only
        # (the fallback has no fallback of its own), so delegation can't loop.
        self._fallback = fallback

    async def run(self, utterance: str, language: str = "de") -> str | None:
        """Generate one short acknowledgment sentence or return None.

        Pipeline (spec §4):
            a) circuit breaker open?       → None
            b) ack_called_total++
            c) pick persona prompt by language
            d) provider.run via asyncio.wait_for(timeout_ms/1000)
            e) TimeoutError                → None
            f) any other Exception         → None
            g) empty / whitespace          → None
            h) word count > 25             → truncate at first [.!?]
            i) language mismatch heuristic → None
            j) scrub_for_voice → < 3 alnum chars → None
            k) F10 self-answer post-filter → None
            l) ack_emitted_total++, latency histogram, return scrubbed
        """
        provider_label = self._provider_name

        # (a) circuit breaker gate
        if await self._breaker.is_open():
            _emit_counter("ack_circuit_breaker_open_total", provider=provider_label)
            return None

        # (b) call counter
        _emit_counter("ack_called_total", provider=provider_label)

        # (c) persona prompt
        prompt = _augment_with_preferences(
            get_persona_prompt(language), self._preferences_provider
        )

        # (d) provider call with hard timeout
        t_start = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                self._provider.run(
                    utterance, language, persona_prompt=prompt
                ),
                timeout=self._config.timeout_ms / 1000.0,
            )
        except TimeoutError:
            # (e) F1
            _emit_counter("ack_timeout_total", provider=provider_label)
            await self._breaker.record_failure()
            return None
        except Exception as exc:  # noqa: BLE001 — adapters should swallow,
            # but defence-in-depth: any leak is F2.
            log.warning("AckGenerator provider raised: %s", exc)
            _emit_counter("ack_provider_error_total", provider=provider_label)
            await self._breaker.record_failure()
            return None

        # (g) empty / whitespace
        if raw is None or not raw.strip():
            _emit_counter("ack_empty_response_total", provider=provider_label)
            await self._breaker.record_success()  # provider answered, just empty
            return None

        text = raw.strip()

        # (h) truncate over-long output
        if _word_count(text) > 25:
            text = _truncate_at_first_sentence(text)
            _emit_counter("ack_truncated_total")

        # (i) cheap language sanity check
        detected = _detect_language(text)
        if detected != "unknown" and detected != language:
            _emit_counter("ack_lang_mismatch_total", provider=provider_label)
            await self._breaker.record_success()  # provider was healthy
            return None

        # (j) schwarzliste scrub
        # scrub_for_voice returns a ScrubResult dataclass; we operate on
        # the .cleaned string. ack_mode=True keeps filler-opener phrases
        # like "Lass mich kurz nachschauen." intact — they are persona-
        # legitimate Flash-Brain output, not the unwanted brain-side
        # filler that the regular scrub mode removes.
        scrub_result = scrub_for_voice(text, language=language, ack_mode=True)
        scrubbed = scrub_result.cleaned
        alnum_count = sum(1 for c in scrubbed if c.isalnum())
        if alnum_count < 3:
            _emit_counter("ack_scrubbed_empty_total", provider=provider_label)
            await self._breaker.record_success()
            return None

        # (k) F10 self-answer post-filter (spec §6, 2026-05-13)
        pattern = _detect_self_answer(scrubbed)
        if pattern:
            _emit_counter(
                "ack_self_answer_suppressed_total",
                provider=provider_label,
                pattern=pattern,
            )
            await self._breaker.record_success()
            return None

        # (l) success path
        latency_ms = (time.perf_counter() - t_start) * 1000.0
        _emit_histogram(
            "ack_latency_ms_histogram", latency_ms, provider=provider_label
        )
        _emit_counter("ack_emitted_total", provider=provider_label)
        await self._breaker.record_success()
        return scrubbed

    def _postprocess(self, text: str, language: str) -> str | None:
        """Apply the F4/F5/F6/F10 filters to one candidate ack sentence.

        Shared by the streaming path so each yielded sentence gets the same
        truncate / language / scrub / self-answer treatment as ``run()``.
        Returns the scrubbed sentence, or ``None`` to drop it.
        """
        text = text.strip()
        if not text:
            return None
        if _word_count(text) > 25:
            text = _truncate_at_first_sentence(text)
        detected = _detect_language(text)
        if detected != "unknown" and detected != language:
            return None
        scrubbed = scrub_for_voice(text, language=language, ack_mode=True).cleaned
        if sum(1 for c in scrubbed if c.isalnum()) < 3:
            return None
        if _detect_self_answer(scrubbed):
            return None
        return scrubbed

    async def run_stream(
        self, utterance: str, language: str = "de"
    ) -> AsyncIterator[str]:
        """Streaming variant of run(): yield scrubbed ack sentences ASAP.

        Consumes the provider's ``run_stream`` (when present), accumulates
        deltas, and yields each validated sentence the moment it completes — so
        the first sentence reaches TTS without awaiting the full response. Falls
        back to ``run()`` when the provider has no ``run_stream`` or the stream
        errors / empties fast (but NOT on timeout, to avoid a double wait).
        Never raises — silent-on-failure (US-4).
        """
        provider_label = self._provider_name

        if await self._breaker.is_open():
            _emit_counter("ack_circuit_breaker_open_total", provider=provider_label)
            return

        run_stream = getattr(self._provider, "run_stream", None)
        if run_stream is None:
            result = await self.run(utterance, language)
            if result:
                yield result
            return

        _emit_counter("ack_called_total", provider=provider_label)
        prompt = _augment_with_preferences(
            get_persona_prompt(language), self._preferences_provider
        )
        t_start = time.perf_counter()
        buffer = ""
        yielded_any = False
        emitted_first = False
        timed_out = False
        try:
            async with asyncio.timeout(self._config.timeout_ms / 1000.0):
                async for delta in run_stream(
                    utterance, language, persona_prompt=prompt
                ):
                    if not delta:
                        continue
                    buffer += delta
                    while True:
                        match = _SENTENCE_END_RE.search(buffer)
                        if not match:
                            break
                        sentence = buffer[: match.end()]
                        buffer = buffer[match.end():]
                        out = self._postprocess(sentence, language)
                        if not out:
                            continue
                        if not emitted_first:
                            emitted_first = True
                            _emit_histogram(
                                "ack_first_sentence_ms_histogram",
                                (time.perf_counter() - t_start) * 1000.0,
                                provider=provider_label,
                            )
                        yielded_any = True
                        yield out
        except TimeoutError:
            timed_out = True
        except Exception as exc:  # noqa: BLE001
            log.warning("AckGenerator run_stream raised: %s", exc)

        # Flush a trailing fragment without sentence-ending punctuation.
        tail = self._postprocess(buffer, language)
        if tail:
            yielded_any = True
            yield tail

        if yielded_any:
            _emit_counter("ack_emitted_total", provider=provider_label)
            await self._breaker.record_success()
            return
        if timed_out:
            _emit_counter("ack_timeout_total", provider=provider_label)
            await self._breaker.record_failure()
        else:
            # No run_stream support / stream error / fast-empty -> proven path
            # on the SAME provider first (cheap, no double-wait).
            result = await self.run(utterance, language)
            if result:
                yield result
                return
        # Primary exhausted with nothing spoken (timed out, errored, or both the
        # stream and the proven run() produced nothing). Fail over to a SEPARATE
        # provider so a busy primary never leaves the user in silence — the very
        # condition the ack exists to bridge (live bug 2026-06-18: the Gemini ack
        # timed out while the Gemini deep brain was slow → 8 s of dead air → user
        # aborted). The fallback is isolated (different endpoint/key), so
        # primary-side load does not starve it too. One level only (the fallback
        # has no fallback), so this cannot recurse.
        if self._fallback is not None:
            _emit_counter("ack_failover_total", provider=provider_label)
            async for out in self._fallback.run_stream(utterance, language):
                yield out
