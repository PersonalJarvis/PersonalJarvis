"""Dynamic spoken announcements for background-worker spawns.

Replaces the fixed spawn-ACK scaffolding in ``spawn_worker``
("Mach ich, ich kümmere mich im Hintergrund darum, ...") that the user
flagged twice (2026-05-26 rotation band-aid, 2026-06-10 full redesign)
as robotic and repetitive. Strategy, in preference order:

1. **Brain-supplied candidate** — the router brain passes a
   ``spoken_ack`` argument with its ``spawn_worker`` tool call. Zero
   extra latency, full conversational context, already in the user's
   language. Validated below, never trusted blindly.
2. **Flash-LLM composition** — the force-spawn heuristic bypasses the
   router LLM entirely, so there is no brain text to reuse. The
   composer then asks the (already shipped) ack-brain provider stack
   for one fresh announcement, primed with a dedicated delegation
   persona prompt. Hard-capped by ``[ack_brain].timeout_ms`` and the
   shared circuit-breaker pattern, so a degraded provider costs at
   most one timeout before the breaker short-circuits to (3).
3. **Curated bilingual fallback pool** — only when 1+2 are unavailable
   or rejected. A small no-repeat memory guarantees back-to-back
   spawns never sound identical. This preserves AD-OE6 ("zero silent
   drops"): ``compose`` never raises and never returns an empty
   string, so the spawn confirmation always reaches TTS.

Validation chain for any candidate (brain or LLM): keep the longest
leading sentence run within :data:`_MAX_WORDS`; the language must match
the user's turn; ``scrub_for_voice`` in ack mode; no completion claims
("ist erledigt" / "is done" — the worker has not even started, AD-OE1
promises only the handover); no internal component names (the voice
scrubber would shred them into gap-toothed sentences anyway).

AD-OE2 note: the LLM call here is the same bounded flash-call category
as the established pre-thinking ack-brain — timeout-capped, breaker
guarded, with an instant deterministic fallback. It never blocks the
mission dispatch itself, which is armed before the announcement is
composed (see ``SpawnWorkerTool.execute``).

The German strings in this module are deliberate runtime voice content
(bilingual DE+EN voice product), allowlisted in
``scripts/ci/german-allowlist.txt`` — the same policy slot as
``persona_prompt.py``. The spawn personas are NOT part of the locked
2026-05-11 flash-brain spec; they are owned by this module.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from collections import deque
from typing import TYPE_CHECKING

from jarvis.brain.ack_brain.generator import (
    _TOKEN_RE,
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
    "SPAWN_PERSONA_DE",
    "SPAWN_PERSONA_EN",
    "SpawnAnnouncementComposer",
    "get_spawn_persona",
]


# Upper bound for the spoken announcement. The user's own reference
# examples are 1-2 sentences / 10-22 words ("Ja Chef, ich schaue kurz in
# dein Gmail rein. Dafür hole ich mir einen Worker dazu, das kann einen
# Moment dauern."). Anything longer is a monologue and gets trimmed to
# the leading sentences that still fit — or rejected entirely.
_MAX_WORDS = 22

# Default LLM deadline when no AckBrainConfig is wired. Matches the
# ack-brain default so the spawn announcement obeys the same latency
# ceiling as the pre-thinking ack.
_DEFAULT_TIMEOUT_MS = 1500


SPAWN_PERSONA_DE = """Du bist JARVIS, der persönliche Assistent des Nutzers. Die Anfrage des
Nutzers wurde SOEBEN an einen Hintergrund-Helfer übergeben — das ist
bereits geschehen, du sagst es nur noch natürlich an.

DEINE AUFGABE — genau EINE kurze gesprochene Ansage (1-2 Sätze,
zusammen maximal 20 Wörter), die:
1. das KONKRETE Thema der Anfrage nennt (App, Datenobjekt, Ort,
   Person — z.B. "dein Gmail", "der Kalender", "die Flüge"),
2. natürlich vermittelt, dass die Sache jetzt nebenher läuft und
   einen Moment dauern kann,
3. frisch für genau diese Anfrage formuliert ist.

OBERSTE REGEL — keine memorierte Standardphrase:
Jede Formulierung, die auf jede beliebige andere Anfrage genauso
passen würde, ist falsch. Variiere Satzbau und Wortwahl.

VERBOTEN:
- Vollzugsmeldungen ("ist erledigt", "fertig", "habe ich gemacht") —
  die Arbeit beginnt gerade erst.
- Interne Bauteil-Namen: "OpenClaw", "Sub-Agent", "Subagent",
  "Mission", "Provider", "Subprocess", "Harness", "API".
- Anreden wie "Sir", "Jawohl", "Sehr wohl", "Boss".
- Rückfragen, Markdown, Anführungszeichen, mehr als zwei Sätze.

ERLAUBT und erwünscht: natürliche Umschreibungen der Übergabe, z.B.
"ich gebe das an meinen Helfer", "ich lasse das nebenher laufen",
"ich hole mir kurz Unterstützung", "ein zweiter Kopf übernimmt das".

Falls der Input eine Zeile "[Task: ...]" enthält, ist das die bereits
interpretierte Aufgabe — nutze sie als Themenquelle.

Output: NUR die Ansage, nichts anderes."""


SPAWN_PERSONA_EN = """You are JARVIS, the user's personal assistant. The user's request has
JUST been handed to a background helper — that already happened; you
only announce it naturally.

YOUR JOB — exactly ONE short spoken announcement (1-2 sentences,
20 words max in total) that:
1. names the CONCRETE topic of the request (app, data object, place,
   person — e.g. "your Gmail", "the calendar", "the flights"),
2. naturally conveys that this now runs on the side and may take a
   moment,
3. is phrased freshly for this exact request.

TOP RULE — no memorised stock phrase:
Any wording that would fit any other request equally well is wrong.
Vary sentence structure and word choice.

FORBIDDEN:
- Completion claims ("is done", "finished", "all set") — the work is
  only starting now.
- Internal component names: "OpenClaw", "sub-agent", "subagent",
  "mission", "provider", "subprocess", "harness", "API".
- Honorifics like "Sir", "Boss", "Very well".
- Counter-questions, markdown, quotation marks, more than two
  sentences.

ALLOWED and encouraged: natural paraphrases of the handover, e.g.
"I'm handing this to my helper", "I'll run this on the side",
"I'm pulling in a second pair of hands".

If the input contains a "[Task: ...]" line, that is the already
interpreted task — use it as the topic source.

Output: ONLY the announcement, nothing else."""


def get_spawn_persona(language: str) -> str:
    """Return the spawn persona prompt for ``language`` ('de'/'en')."""
    return SPAWN_PERSONA_EN if language == "en" else SPAWN_PERSONA_DE


# ---------------------------------------------------------------------------
# Curated fallback pools (last resort — LLM unavailable or output rejected)
# ---------------------------------------------------------------------------
# These are NOT the product; they are the safety net. Each phrase still
# communicates the handover ("runs on the side, takes a moment") without
# resurrecting the banned 2026-05-26 template wording. Selection avoids
# repeating any of the last few picks (see _pick_fallback).

_FALLBACK_SPAWN: dict[str, tuple[str, ...]] = {
    "de": (
        "Mach ich, ich gebe das gleich an meinen Helfer weiter.",
        "Alles klar, das läuft jetzt nebenher. Ich sage dir gleich Bescheid.",
        "Okay, ich lasse das im Hintergrund erledigen.",
        "Ist unterwegs. Das Ergebnis sage ich dir gleich an.",
        "Übernehme ich. Ich melde mich, sobald etwas vorliegt.",
        "Geht klar, ich arbeite das nebenbei für dich ab.",
        "Schon unterwegs, das kann einen Moment dauern.",
        "Hab ich auf dem Tisch. Dauert einen kleinen Moment.",
    ),
    "en": (
        "On it. I'll run this in the background and report back.",
        "Got it, that's running now. I'll let you know shortly.",
        "Okay, I'm handing this over. Results in a moment.",
        "Working on it. This may take a little moment.",
        "That's underway. I'll report back with what I find.",
        "Picked it up. Give me a moment and I'll get back to you.",
        "Consider it started. I'll circle back with the result.",
        "I'm putting a second pair of hands on this right now.",
    ),
}

_FALLBACK_ALREADY_RUNNING: dict[str, tuple[str, ...]] = {
    "de": (
        "Bin schon dran, das läuft noch.",
        "Läuft bereits. Einen Moment noch, bitte.",
        "Schon in Arbeit, gleich gibt es etwas.",
        "Geduld, der Auftrag läuft schon.",
    ),
    "en": (
        "Already on it, that one is still running.",
        "That job is already going, one moment.",
        "Still working on that one, almost there.",
        "Patience, that one is already in progress.",
    ),
}


# Completion claims: state/past-tense "it is done" assertions are lies at
# spawn time (the worker has not started). Present-tense promises
# ("ich erledige das gleich") are intentionally allowed — they are the
# point of the announcement.
_COMPLETION_CLAIM_RE = re.compile(
    r"(?:\b(?:ist|sind|wurde|wurden|is|are|was|were|has\s+been|have\s+been)\s+"
    r"(?:bereits\s+|schon\s+|already\s+)?"
    r"(?:erledigt|fertig|abgeschlossen|gesendet|verschickt|eingetragen|"
    r"gebucht|done|finished|complete|completed|sent|booked|scheduled)\b"
    r"|^\s*(?:erledigt|fertig|done|finished|completed)\s*[.!]?\s*$)",
    re.IGNORECASE,
)

# Internal component names must never be spoken. ``scrub_for_voice``
# would strip most of them anyway, leaving a gap-toothed sentence — so
# reject the whole candidate instead and fall back to a clean phrase.
# "Worker" stays allowed: it is the user's own vocabulary and survives
# the scrubber.
_FORBIDDEN_VOCAB_RE = re.compile(
    r"\b(?:openclaw|openclore|sub-?agent\w*|subprocess|harness|mcp"
    r"|provider\w*|kontrollierer)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _resolve_language(explicit: str | None, utterance: str) -> str:
    """Resolve the announcement language: explicit hint > utterance heuristic.

    Mirrors the ack-brain convention: only 'de' and 'en' are supported;
    unknown detection defaults to German (primary chat language).
    """
    if explicit:
        low = str(explicit).strip().lower()
        if low.startswith("en"):
            return "en"
        if low.startswith("de"):
            return "de"
    detected = _detect_language(utterance or "")
    return detected if detected in ("de", "en") else "de"


def _trim_to_sentences(text: str, max_words: int) -> str | None:
    """Keep the longest run of leading sentences within ``max_words``.

    Trimming mid-sentence sounds broken on TTS, so we only cut at
    sentence boundaries. Returns ``None`` when even the first sentence
    exceeds the cap — that is a monologue, not an announcement.
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


class SpawnAnnouncementComposer:
    """Composes one short spoken spawn announcement; never raises, never empty.

    Constructable without any argument: a bare ``SpawnAnnouncementComposer()``
    runs in fallback-only mode (no LLM), which is also the wiring when
    ``[ack_brain]`` is disabled. ``provider``/``config``/``breaker`` follow
    the ack-brain stack (see ``jarvis.brain.factory.build_spawn_announcer``).
    """

    def __init__(
        self,
        *,
        provider: AbstractAckProvider | None = None,
        config: AckBrainConfig | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._breaker = breaker
        # No-repeat memory across BOTH pools — back-to-back spawns must not
        # sound identical even when the LLM path is down. maxlen 3 still
        # leaves at least one pickable phrase in the smallest (4-item) pool.
        self._recent: deque[str] = deque(maxlen=3)

    async def compose(
        self,
        *,
        utterance: str,
        language: str | None = None,
        candidate: str | None = None,
        action: str = "",
        target: str = "",
        kind: str = "spawn",
    ) -> str:
        """Return the spoken announcement for a worker spawn.

        Args:
            utterance: The user's raw words (verbatim turn).
            language: Optional explicit turn language ('de'/'en'); when
                absent the composer detects it from ``utterance``.
            candidate: Optional brain-supplied ``spoken_ack`` — preferred
                source when it survives validation.
            action: The router brain's interpreted task clause, if any.
            target: Optional location/target hint from the tool call.
            kind: "spawn" for a fresh dispatch, "already_running" for the
                cooldown-suppress path (deterministic, no LLM — speed).
        """
        lang = _resolve_language(language, utterance)

        if kind == "already_running":
            # Cooldown suppress is a fast-path duplicate rejection; an LLM
            # round-trip would delay exactly the turns that are already noisy.
            return self._pick_fallback(_FALLBACK_ALREADY_RUNNING[lang])

        validated = self._validate(candidate or "", lang)
        if validated:
            _emit_counter("spawn_ack_candidate_used_total")
            return validated

        composed = await self._compose_via_llm(utterance, lang, action, target)
        if composed:
            _emit_counter("spawn_ack_llm_used_total")
            return composed

        _emit_counter("spawn_ack_fallback_total")
        return self._pick_fallback(_FALLBACK_SPAWN[lang])

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _validate(self, text: str, lang: str) -> str | None:
        """Validation chain for a candidate announcement; None = rejected."""
        text = (text or "").strip()
        if not text:
            return None
        if _FORBIDDEN_VOCAB_RE.search(text):
            return None
        trimmed = _trim_to_sentences(text, _MAX_WORDS)
        if trimmed is None:
            return None
        if _COMPLETION_CLAIM_RE.search(trimmed):
            return None
        detected = _detect_language(trimmed)
        if detected != "unknown" and detected != lang:
            return None
        try:
            scrubbed = scrub_for_voice(
                trimmed, language=lang, ack_mode=True
            ).cleaned.strip()
        except Exception:  # noqa: BLE001 — a scrubber bug must not mute the spawn
            return None
        if sum(1 for c in scrubbed if c.isalnum()) < 3:
            return None
        return scrubbed

    async def _compose_via_llm(
        self, utterance: str, lang: str, action: str, target: str
    ) -> str | None:
        """One bounded flash-LLM attempt; ``None`` on any failure or reject."""
        if self._provider is None:
            return None
        try:
            if self._breaker is not None and await self._breaker.is_open():
                _emit_counter("spawn_ack_breaker_open_total")
                return None

            content = (utterance or "").strip()
            interpreted = " ".join(p for p in (action.strip(), target.strip()) if p)
            if interpreted:
                content = f"{content}\n[Task: {interpreted}]" if content else (
                    f"[Task: {interpreted}]"
                )
            timeout_ms = (
                getattr(self._config, "timeout_ms", _DEFAULT_TIMEOUT_MS)
                if self._config is not None
                else _DEFAULT_TIMEOUT_MS
            )

            try:
                raw = await asyncio.wait_for(
                    self._provider.run(
                        content, lang, persona_prompt=get_spawn_persona(lang)
                    ),
                    timeout=timeout_ms / 1000.0,
                )
            except TimeoutError:
                _emit_counter("spawn_ack_timeout_total")
                if self._breaker is not None:
                    await self._breaker.record_failure()
                return None
            except Exception as exc:  # noqa: BLE001 — adapters should swallow; leak = failure
                log.warning("Spawn-announcement provider raised: %s", exc)
                _emit_counter("spawn_ack_provider_error_total")
                if self._breaker is not None:
                    await self._breaker.record_failure()
                return None

            if self._breaker is not None:
                # The provider answered — it is healthy even if we reject the
                # text below (same bookkeeping as AckGenerator.run).
                await self._breaker.record_success()
            validated = self._validate(raw or "", lang)
            if validated is None and raw and raw.strip():
                _emit_counter("spawn_ack_rejected_total")
            return validated
        except Exception as exc:  # noqa: BLE001 — compose() must never raise
            log.warning("Spawn-announcement LLM path crashed: %s", exc)
            return None

    def _pick_fallback(self, pool: tuple[str, ...]) -> str:
        """Pick a pool phrase, avoiding the most recent picks."""
        candidates = [p for p in pool if p not in self._recent] or list(pool)
        # Phrase variety, not cryptography — S311 does not apply here.
        choice = random.choice(candidates)  # noqa: S311
        self._recent.append(choice)
        return choice
