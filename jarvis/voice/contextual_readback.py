"""Context-aware spoken readbacks for the deterministic action paths.

The maintainer's standing requirement: Jarvis must not read fixed "stock"
sentences out of a lookup table for its status / outcome / acknowledgement
replies. Those replies should sound like Jarvis reacting to *this* situation,
not like a vending machine. This module is the engine that delivers that, while
honoring the three hard constraints that made the static tables exist in the
first place:

1. **Latency (AP-11 / SLO):** generation is one bounded flash-LLM call
   (``[ack_brain].timeout_ms``, breaker-guarded — the exact category the
   pre-thinking ack and the spawn announcer already use). It never runs inside
   ``scrub_for_voice`` and always has an instant deterministic fallback, so a
   slow/dead provider costs at most one timeout before the canned line is spoken.
2. **Honesty (ADR-0009 / AD-OE6):** the composer is handed the deterministic
   ground truth (``facts``) and is told to *rephrase only that, inventing
   nothing*. A digit-fabrication guard plus, for ``honesty_bound`` situations, a
   content-overlap guard reject any output that adds facts not present in the
   input. It NEVER raises and NEVER returns an empty string — on any failure it
   returns the supplied canned fallback, so a status line is never silently
   dropped.
3. **Language (Runtime Output Language doctrine):** the turn's resolved language
   (de/en/es) is passed in and the model is told to answer in it; a de/en
   mismatch is rejected to the canned fallback, and the canned fallback itself
   already covers all three languages.

Design mirrors :class:`jarvis.brain.ack_brain.spawn_announcement.SpawnAnnouncementComposer`
(brain candidate → flash compose → validate → deterministic fallback, one
failover level, never-raise) so the two flash consumers behave identically and
share the provider/breaker wiring built by ``jarvis.brain.factory``.

This module holds NO German/Spanish phrase pool of its own: the deterministic
fallback is the EXISTING canned table, supplied by the call site as a
``canned`` callable. The persona frame here is an English meta-prompt that
instructs the model to answer in the user's language, so de/en/es all generate
natively (it is a NEW prompt, not the locked 2026-05-11 flash-brain persona).
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

from jarvis.brain.ack_brain.generator import (
    _TOKEN_RE,
    _augment_with_preferences,
    _detect_language,
    _emit_counter,
)
from jarvis.brain.output_filter import scrub_for_voice

if TYPE_CHECKING:
    from jarvis.brain.ack_brain.circuit_breaker import CircuitBreaker
    from jarvis.brain.ack_brain.config import AckBrainConfig
    from jarvis.brain.ack_brain.providers.base import AbstractAckProvider

log = logging.getLogger(__name__)

__all__ = [
    "ReadbackComposer",
    "build_readback_persona",
    "render_readback",
]

# Upper bound for a spoken status sentence. Status readbacks are one short
# sentence; anything longer is the model monologuing and gets trimmed to the
# leading sentences that still fit (or rejected — see _trim_to_sentences).
_MAX_WORDS = 26

# Default flash deadline when no AckBrainConfig is wired. Matches the ack-brain
# default so a readback obeys the same latency ceiling as the pre-thinking ack.
_DEFAULT_TIMEOUT_MS = 1500

# Internal component / diagnostic vocabulary that must never be spoken. The
# voice scrubber would shred most of it into gap-toothed sentences, so reject
# the whole candidate and fall back to a clean line instead. "exit" is included
# so the model can never resurrect the raw "exit N" token the static tables were
# built to hide (live bug: "That didn't work on screen: exit 5").
_FORBIDDEN_VOCAB_RE = re.compile(
    r"\b(?:openclaw|openclore|sub-?agent\w*|subprocess|harness|mcp"
    r"|provider\w*|kontrollierer|stdout|stderr|traceback|exit\s*code|exit\s+\d+"
    r"|json|api|http[s]?)\b",
    re.IGNORECASE,
)

# Completion claims — state/past-tense "it is done" assertions. A lie for an
# in-progress situation (dispatch ack: the work has not started), so reject the
# candidate there. Shared shape with the spawn announcer's guard.
_COMPLETION_CLAIM_RE = re.compile(
    r"(?:\b(?:ist|sind|wurde|wurden|is|are|was|were|has\s+been|have\s+been)\s+"
    r"(?:bereits\s+|schon\s+|already\s+)?"
    r"(?:erledigt|fertig|abgeschlossen|gesendet|verschickt|done|finished|"
    r"complete|completed|sent)\b"
    r"|^\s*(?:erledigt|fertig|done|finished|completed|listo)\s*[.!]?\s*$)",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_DIGITS_RE = re.compile(r"\d+")
# Content words for the honesty overlap guard: alphabetic tokens of length >= 4
# (skips function words / articles in every supported language well enough for a
# defense-in-depth check). Lowercased before comparison.
_CONTENT_WORD_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)

_LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish"}


def build_readback_persona(language: str, *, instruction: str, facts_block: str) -> str:
    """Build the flash-LLM system prompt for one context-aware readback.

    English meta-prompt (instructs the model to answer in ``language``) so all
    supported locales generate natively without a hand-written per-language
    persona. ``instruction`` is the one-line English description of the
    situation supplied by the call site; ``facts_block`` is the rendered,
    deterministic ground truth the model may use and nothing else.
    """
    language_name = _LANGUAGE_NAMES.get(language, "German")
    return f"""You are JARVIS, the user's personal assistant, speaking one short \
status sentence out loud.

SITUATION: {instruction}

{facts_block}

YOUR JOB — produce exactly ONE short, natural spoken sentence (max ~20 words) \
in {language_name} that conveys the situation to the user the way a helpful \
human would say it in conversation.

HARD RULES:
- Use ONLY the information in the situation and the facts above. Do NOT add, \
infer, guess, or invent ANY detail, number, name, quantity, or claim that is \
not given. If there is no specific detail, just say the plain outcome.
- Answer in {language_name}, nothing else.
- No stock phrasing: a sentence that would fit any other situation equally well \
is wrong. React to THIS situation.
- Forbidden: internal/technical words (exit codes, "subprocess", "harness", \
"provider", "API", "JSON", "stdout"), honorifics ("Sir", "Boss"), markdown, \
quotation marks, counter-questions, more than one sentence.

Output: ONLY the spoken sentence, nothing else."""


def render_facts_block(facts: dict[str, object] | None) -> str:
    """Render ``facts`` as a compact, deterministic FACTS block for the prompt."""
    if not facts:
        return "FACTS: (no extra detail — just confirm the plain outcome)."
    lines = ["FACTS (the only information you may use):"]
    for key, value in facts.items():
        text = str(value).strip()
        if not text:
            continue
        lines.append(f"- {key}: {text}")
    if len(lines) == 1:
        return "FACTS: (no extra detail — just confirm the plain outcome)."
    return "\n".join(lines)


def _trim_to_sentences(text: str, max_words: int) -> str | None:
    """Keep the longest run of leading sentences within ``max_words``.

    Trimming mid-sentence sounds broken on TTS, so cut only at sentence
    boundaries. Returns ``None`` when even the first sentence exceeds the cap.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    kept: list[str] = []
    total = 0
    for sentence in sentences:
        words = len(_TOKEN_RE.findall(sentence))
        if total + words > max_words:
            break
        kept.append(sentence)
        total += words
    if not kept:
        return None
    return " ".join(kept)


def _facts_corpus(facts: dict[str, object] | None, instruction: str) -> str:
    """Lowercased concatenation of all fact values + the instruction.

    The honesty guards check generated tokens against this corpus.
    """
    parts = [instruction]
    if facts:
        parts.extend(str(v) for v in facts.values())
    return " ".join(parts).lower()


def _has_fabricated_digits(text: str, corpus: str) -> bool:
    """True if ``text`` contains a digit-run absent from the facts corpus.

    Fabricated quantities/ids are the most dangerous hallucination on a
    status line ("I opened 5 tabs" when nothing said five), so any number the
    model emits must trace back to the given facts.
    """
    corpus_digits = set(_DIGITS_RE.findall(corpus))
    return any(token not in corpus_digits for token in _DIGITS_RE.findall(text))


#: Minimum share of an honesty-bound output's content words that must trace back
#: to the facts corpus. A ratio (not "all") tolerates natural rephrasing — a
#: spelled-out number ("three" for "3"), a contraction stem ("didn"), a
#: connective — while still rejecting an output that wandered off into a claim
#: the facts never made (wholesale fabrication trends toward 0 overlap).
_MIN_OVERLAP_RATIO = 0.6


def _overlap_ok(text: str, corpus: str) -> bool:
    """Content-word overlap guard for honesty-bound situations.

    At least :data:`_MIN_OVERLAP_RATIO` of the output's length>=4 content words
    must appear in the facts corpus. This keeps an ADR-0009 success readback to a
    *rephrasing* of the signed facts — an output that introduces a cluster of new
    nouns/claims the facts never mentioned falls below the threshold and is
    rejected. Outputs with no content words at all (pure short words) pass — the
    digit guard and forbidden-vocab guard still apply.
    """
    words = _CONTENT_WORD_RE.findall(text.lower())
    if not words:
        return True
    hits = sum(1 for word in words if word in corpus)
    return (hits / len(words)) >= _MIN_OVERLAP_RATIO


class ReadbackComposer:
    """Composes one context-aware spoken readback; never raises, never empty.

    Constructable bare (``ReadbackComposer()``) for fallback-only mode — which
    is also the wiring when ``[ack_brain]`` is disabled or no flash adapter is
    available. ``provider``/``config``/``breaker`` follow the ack-brain stack
    (see ``jarvis.brain.factory.build_readback_composer``).
    """

    def __init__(
        self,
        *,
        provider: AbstractAckProvider | None = None,
        config: AckBrainConfig | None = None,
        breaker: CircuitBreaker | None = None,
        fallback_provider: AbstractAckProvider | None = None,
        fallback_breaker: CircuitBreaker | None = None,
        preferences_provider: Callable[[], str] | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._breaker = breaker
        self._fallback_provider = fallback_provider
        self._fallback_breaker = fallback_breaker
        self._preferences_provider = preferences_provider
        # No-repeat memory so two back-to-back generated readbacks of the same
        # situation never come out word-for-word identical even under load.
        self._recent: deque[str] = deque(maxlen=4)

    @property
    def has_llm(self) -> bool:
        """True when a flash provider is wired (generation can be attempted)."""
        return self._provider is not None

    async def compose(
        self,
        *,
        instruction: str,
        language: str,
        canned: Callable[[], str],
        facts: dict[str, object] | None = None,
        in_progress: bool = False,
        honesty_bound: bool = False,
        latency_budget_ms: int | None = None,
    ) -> str:
        """Return a context-aware spoken sentence, or the canned fallback.

        Args:
            instruction: One-line English description of the situation.
            language: Resolved output language ('de'/'en'/'es').
            canned: Callable returning the deterministic fallback line. Used on
                any failure (no provider, breaker open, timeout, rejected
                output). Must itself never raise; its result is returned as-is.
            facts: Deterministic ground truth the model may rephrase — the ONLY
                information it is allowed to use.
            in_progress: True for a not-yet-started situation (dispatch ack) so a
                completion claim ("done") is rejected.
            honesty_bound: True to additionally require content-word overlap with
                the facts (ADR-0009 success readbacks rephrase, never invent).
            latency_budget_ms: Hard timeout override for this call. Smaller on the
                turn-critical path (dispatch ack), larger off it (background
                outcome readbacks). Defaults to the ack-brain ``timeout_ms``.
        """
        fallback = self._safe_canned(canned)
        if self._provider is None:
            return fallback

        generated = await self._compose_via_llm(
            instruction=instruction,
            language=language,
            facts=facts,
            in_progress=in_progress,
            honesty_bound=honesty_bound,
            latency_budget_ms=latency_budget_ms,
        )
        if generated:
            self._recent.append(generated)
            _emit_counter("readback_llm_used_total")
            return generated
        _emit_counter("readback_fallback_total")
        return fallback

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_canned(canned: Callable[[], str]) -> str:
        """Evaluate the canned fallback defensively — it must never break us."""
        try:
            value = (canned() or "").strip()
        except Exception as exc:  # noqa: BLE001 — a fallback bug must not raise here
            log.warning("Readback canned fallback raised: %s", exc)
            return ""
        return value

    async def _compose_via_llm(
        self,
        *,
        instruction: str,
        language: str,
        facts: dict[str, object] | None,
        in_progress: bool,
        honesty_bound: bool,
        latency_budget_ms: int | None,
    ) -> str | None:
        """Try the primary flash provider, then the failover; ``None`` if both fail."""
        persona = build_readback_persona(
            language, instruction=instruction, facts_block=render_facts_block(facts)
        )
        # The flash adapters take a user "utterance" + a persona prompt; we pass
        # the instruction as the utterance so the adapter has a non-empty turn.
        content = instruction
        corpus = _facts_corpus(facts, instruction)
        timeout_ms = latency_budget_ms or self._timeout_ms()
        for provider, breaker in (
            (self._provider, self._breaker),
            (self._fallback_provider, self._fallback_breaker),
        ):
            if provider is None:
                continue
            validated = await self._try_provider(
                provider=provider,
                breaker=breaker,
                persona=persona,
                content=content,
                language=language,
                corpus=corpus,
                in_progress=in_progress,
                honesty_bound=honesty_bound,
                timeout_ms=timeout_ms,
            )
            if validated:
                return validated
        return None

    def _timeout_ms(self) -> int:
        return (
            getattr(self._config, "timeout_ms", _DEFAULT_TIMEOUT_MS)
            if self._config is not None
            else _DEFAULT_TIMEOUT_MS
        )

    async def _try_provider(
        self,
        *,
        provider: AbstractAckProvider,
        breaker: CircuitBreaker | None,
        persona: str,
        content: str,
        language: str,
        corpus: str,
        in_progress: bool,
        honesty_bound: bool,
        timeout_ms: int,
    ) -> str | None:
        """One bounded flash-LLM attempt against a single provider/breaker."""
        try:
            if breaker is not None and await breaker.is_open():
                _emit_counter("readback_breaker_open_total")
                return None
            try:
                raw = await asyncio.wait_for(
                    provider.run(
                        content,
                        language,
                        persona_prompt=_augment_with_preferences(
                            persona, self._preferences_provider
                        ),
                    ),
                    timeout=timeout_ms / 1000.0,
                )
            except TimeoutError:
                _emit_counter("readback_timeout_total")
                if breaker is not None:
                    await breaker.record_failure()
                return None
            except Exception as exc:  # noqa: BLE001 — adapters should swallow; leak = failure
                log.warning("Readback provider raised: %s", exc)
                _emit_counter("readback_provider_error_total")
                if breaker is not None:
                    await breaker.record_failure()
                return None

            if breaker is not None:
                # The provider answered — healthy even if we reject the text.
                await breaker.record_success()
            validated = self._validate(
                raw or "",
                language=language,
                corpus=corpus,
                in_progress=in_progress,
                honesty_bound=honesty_bound,
            )
            if validated is None and raw and raw.strip():
                _emit_counter("readback_rejected_total")
            return validated
        except Exception as exc:  # noqa: BLE001 — compose() must never raise
            log.warning("Readback LLM path crashed: %s", exc)
            return None

    def _validate(
        self,
        text: str,
        *,
        language: str,
        corpus: str,
        in_progress: bool,
        honesty_bound: bool,
    ) -> str | None:
        """Validation chain for a generated readback; ``None`` = rejected."""
        text = (text or "").strip().strip('"').strip()
        if not text:
            return None
        if _FORBIDDEN_VOCAB_RE.search(text):
            return None
        trimmed = _trim_to_sentences(text, _MAX_WORDS)
        if trimmed is None:
            return None
        if in_progress and _COMPLETION_CLAIM_RE.search(trimmed):
            return None
        # Language sanity (de/en heuristic; es resolves to "unknown" == accept).
        detected = _detect_language(trimmed)
        if detected != "unknown" and detected != language:
            return None
        # Honesty guards: no fabricated numbers ever; content-overlap when bound.
        if _has_fabricated_digits(trimmed, corpus):
            _emit_counter("readback_fabricated_digit_total")
            return None
        if honesty_bound and not _overlap_ok(trimmed, corpus):
            _emit_counter("readback_overlap_reject_total")
            return None
        # Avoid a verbatim repeat of the previous generated readback.
        if trimmed in self._recent:
            return None
        try:
            scrubbed = scrub_for_voice(
                trimmed, language=language, ack_mode=True
            ).cleaned.strip()
        except Exception:  # noqa: BLE001 — a scrubber bug must not mute us
            return None
        if sum(1 for c in scrubbed if c.isalnum()) < 3:
            return None
        return scrubbed


async def render_readback(
    composer: ReadbackComposer | None,
    *,
    instruction: str,
    language: str,
    canned: Callable[[], str],
    facts: dict[str, object] | None = None,
    in_progress: bool = False,
    honesty_bound: bool = False,
    latency_budget_ms: int | None = None,
) -> str:
    """Compose a context-aware readback, or the canned fallback when unwired.

    The single entry point for every call site: pass the (optional) composer and
    the deterministic ``canned`` fallback. When ``composer`` is ``None`` (feature
    not wired / disabled) the canned line is returned with zero behavior change,
    so wiring this in is risk-free. Never raises.
    """
    if composer is None:
        try:
            return (canned() or "").strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("Readback canned fallback raised (no composer): %s", exc)
            return ""
    return await composer.compose(
        instruction=instruction,
        language=language,
        canned=canned,
        facts=facts,
        in_progress=in_progress,
        honesty_bound=honesty_bound,
        latency_budget_ms=latency_budget_ms,
    )
