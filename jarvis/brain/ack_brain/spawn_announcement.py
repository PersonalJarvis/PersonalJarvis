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
leading sentence run within :data:`_MAX_WORDS`; require the public
agent brand (the wake-word-derived assistant name + "-Agent", see
``jarvis.brain.assistant_name.agent_brand``); the language must match
the user's turn;
``scrub_for_voice`` in ack mode; no completion claims ("ist erledigt" /
"is done" — the worker has not even started, AD-OE1 promises only the
handover); no internal component names (the voice scrubber would shred
them into gap-toothed sentences anyway).

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
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING

from jarvis.brain.ack_brain.generator import (
    _TOKEN_RE,
    _augment_with_preferences,
    _detect_language,
    _emit_counter,
)
from jarvis.brain.assistant_name import agent_brand_from_name
from jarvis.brain.output_filter import scrub_for_voice
from jarvis.core.turn_language import DEFAULT_LOCALE

if TYPE_CHECKING:
    from jarvis.brain.ack_brain.circuit_breaker import CircuitBreaker
    from jarvis.brain.ack_brain.config import AckBrainConfig
    from jarvis.brain.ack_brain.providers.base import AbstractAckProvider

log = logging.getLogger(__name__)

__all__ = [
    "SPAWN_PERSONA_DE",
    "SPAWN_PERSONA_EN",
    "STILL_RUNNING_PHRASES",
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


SPAWN_PERSONA_DE = """Du bist der persönliche Assistent des Nutzers. Die Anfrage des
Nutzers wurde SOEBEN an einen {agent} übergeben, das ist
bereits geschehen, du sagst es nur noch natürlich an.

DEINE AUFGABE, genau EINE kurze gesprochene Ansage (1-2 Sätze,
zusammen maximal 20 Wörter), die:
1. das KONKRETE Thema der Anfrage nennt (App, Datenobjekt, Ort,
   Person, z.B. "dein Gmail", "der Kalender", "die Flüge"),
2. das exakte Produktwort "{agent}" genau einmal enthält und
   natürlich klarmacht, dass dieser Agent jetzt für die Aufgabe gestartet
   oder hinzugezogen wurde,
3. vermittelt, dass das eine GRÖSSERE Sache ist, jetzt im Hintergrund
   läuft und deshalb einen Moment dauert,
4. frisch für genau diese Anfrage formuliert ist.

TON: warm und zugewandt, wie ein hilfsbereiter Mensch im Gespräch,
nicht wie eine Statusmeldung. Du darfst die Mühe ruhig andeuten ("da
schaue ich gründlich drauf", "das ist ein grösseres Stück Arbeit"),
aber jammere nicht und entschuldige dich nicht. Wenn die Anfrage eine
kreative Idee ist, darfst du kurz und ehrlich darauf reagieren ("schöne
Idee", "spannendes Thema") — höchstens zwei Wörter Reaktion, dann die
Ansage; nie schleimig, nie bei trockenen Routineaufgaben.

OBERSTE REGEL, keine memorierte Standardphrase:
Jede Formulierung, die auf jede beliebige andere Anfrage genauso
passen würde, ist falsch. Variiere Satzbau und Wortwahl.

VERBOTEN:
- Vollzugsmeldungen ("ist erledigt", "fertig", "habe ich gemacht"),
  die Arbeit beginnt gerade erst.
- Interne Bauteil-Namen: "OpenClaw", "Sub-Agent", "Subagent",
  "Mission", "Provider", "Subprocess", "Harness", "API". Der öffentliche
  Produktname "{agent}" ist dagegen PFLICHT.
- Anreden wie "Sir", "Jawohl", "Sehr wohl", "Boss".
- Rückfragen, Markdown, Anführungszeichen, mehr als zwei Sätze.

Variiere frei, WO und WIE du "{agent}" einbaust: starten,
hinzuziehen, übernehmen lassen, mit der Recherche losschicken oder an die
Aufgabe setzen. Verwende keine dieser Varianten als wiederkehrende Schablone.

Falls der Input eine Zeile "[Task: ...]" enthält, ist das die bereits
interpretierte Aufgabe, nutze sie als Themenquelle.

Output: NUR die Ansage, nichts anderes."""


SPAWN_PERSONA_EN = """You are the user's personal assistant. The user's request has
JUST been handed to a {agent}, that already happened; you
only announce it naturally.

YOUR JOB, exactly ONE short spoken announcement (1-2 sentences,
20 words max in total) that:
1. names the CONCRETE topic of the request (app, data object, place,
   person, e.g. "your Gmail", "the calendar", "the flights"),
2. contains the exact public product term "{agent}" exactly once and
   naturally makes clear that this agent was just started or brought in,
3. conveys that this is a BIGGER task, now runs in the background and
   therefore takes a moment,
4. is phrased freshly for this exact request.

TONE: warm and human, like a helpful person in conversation, not a
status line. You may hint at the effort ("I'll take a proper look at
this", "this is a meatier one"), but never whine or apologise. When the
request is a creative idea you may react to it briefly and honestly
("nice idea", "fun topic") — at most two words of reaction, then the
announcement; never sycophantic, never for dry routine chores.

TOP RULE, no memorised stock phrase:
Any wording that would fit any other request equally well is wrong.
Vary sentence structure and word choice.

FORBIDDEN:
- Completion claims ("is done", "finished", "all set"), the work is
  only starting now.
- Internal component names: "OpenClaw", "sub-agent", "subagent",
  "mission", "provider", "subprocess", "harness", "API". The public
  product term "{agent}" is REQUIRED.
- Honorifics like "Sir", "Boss", "Very well".
- Counter-questions, markdown, quotation marks, more than two
  sentences.

Freely vary WHERE and HOW you use "{agent}": start one, bring one in,
have one take over, send one researching, or put one on the task. Never use
any of those variants as a recurring template.

If the input contains a "[Task: ...]" line, that is the already
interpreted task, use it as the topic source.

Output: ONLY the announcement, nothing else."""


# Languages with a native flash-LLM spawn persona. An ``es`` turn has no
# persona (the locked 2026-05-11 preamble spec is not touched), so it skips the
# LLM round-trip and uses the curated ``es`` fallback pool directly — a
# DE/EN-persona LLM call would only produce text that the language-match
# validation rejects, wasting one timeout. Bringing a SPAWN_PERSONA_ES online is
# a tracked follow-up; Spanish is already fully covered deterministically here.
_PERSONA_LANGS: frozenset[str] = frozenset({"de", "en"})


def get_spawn_persona(language: str, agent_brand: str | None = None) -> str:
    """Return the spawn persona prompt for ``language`` ('de'/'en').

    The persona's ``{agent}`` placeholder is resolved to ``agent_brand`` (the
    wake-word-derived assistant name + "-Agent"); ``None`` keeps the neutral
    fallback brand so the prompt never ships a literal placeholder.
    """
    persona = SPAWN_PERSONA_EN if language == "en" else SPAWN_PERSONA_DE
    return persona.replace("{agent}", agent_brand or agent_brand_from_name(""))


# ---------------------------------------------------------------------------
# Curated fallback pools (last resort — LLM unavailable or output rejected)
# ---------------------------------------------------------------------------
# These are NOT the product; they are the safety net. Each phrase still
# communicates the handover ("runs on the side, takes a moment") without
# resurrecting the banned 2026-05-26 template wording. Selection avoids
# repeating any of the last few picks (see _pick_fallback).

_FALLBACK_SPAWN: dict[str, tuple[str, ...]] = {
    "de": (
        "Mach ich. Dafür habe ich einen {agent} gestartet; die grössere "
        "Sache läuft jetzt im Hintergrund.",
        "Alles klar, ein {agent} schaut sich das in Ruhe an. Das dauert einen Moment.",
        "Okay, da geht jetzt ein {agent} gründlich ran und sagt dir danach Bescheid.",
        "Ein {agent} übernimmt das. Es braucht etwas, bis ein belastbares Ergebnis da ist.",
        "Geht klar. Das grössere Stück Arbeit liegt jetzt bei einem {agent}.",
        "Schon angestossen: Ein {agent} kümmert sich darum und braucht dafür einen Moment.",
        "Hab ich. Ein {agent} bearbeitet das umfangreichere Thema und "
        "meldet sich mit Substanz.",
        "Ein {agent} ist jetzt dran. Gib ihm einen Moment, da steckt etwas mehr dahinter.",
    ),
    "en": (
        "On it. I've started a {agent} for this bigger task; it'll report back when ready.",
        "Got it. A {agent} is taking a proper look, which may take a moment.",
        "Okay, this needs real digging. A {agent} is handling it in the background now.",
        "A {agent} is taking this on. It's a meatier task, so give it a moment.",
        "Consider it picked up. A {agent} now has this more involved piece of work.",
        "Sure. A {agent} is digging in now; getting it right will take a little time.",
        "I've put a {agent} on the bigger job. It'll return with something solid.",
        "A {agent} is working on it now. There's more to this, so it'll take a short moment.",
    ),
    "es": (
        "Voy con ello. He iniciado un {agent} para esta tarea más grande; "
        "te avisará al terminar.",
        "Entendido. Un {agent} le está echando un buen vistazo; puede tardar un momento.",
        "Vale, esto necesita mirarse a fondo. Un {agent} ya trabaja en segundo plano.",
        "Un {agent} se encarga. Es una tarea con más chicha, así que necesita un momento.",
        "Lo tengo. Un {agent} ha asumido este trabajo algo más amplio.",
        "Claro. Un {agent} ya está profundizando; hacerlo bien necesita un poco de tiempo.",
        "He puesto un {agent} con este tema más grande; volverá con algo sólido.",
        "Un {agent} ya trabaja en ello. Hay más detrás, así que tardará un momentito.",
    ),
}

_FALLBACK_ALREADY_RUNNING: dict[str, tuple[str, ...]] = {
    "de": (
        "Ein {agent} ist schon dran, das läuft noch.",
        "Der {agent} arbeitet bereits. Einen Moment noch, bitte.",
        "Schon beim {agent} in Arbeit, gleich gibt es etwas.",
        "Geduld, der {agent} bearbeitet den Auftrag bereits.",
    ),
    "en": (
        "A {agent} is already on it; that work is still running.",
        "The {agent} already has that job, one moment.",
        "A {agent} is still working on that one, almost there.",
        "Patience, that task is already with a {agent}.",
    ),
    "es": (
        "Un {agent} ya está con eso; sigue en marcha.",
        "El {agent} ya tiene esa tarea, un momento.",
        "Un {agent} sigue trabajando en ello, casi está.",
        "Paciencia, esa tarea ya está con un {agent}.",
    ),
}


# ---------------------------------------------------------------------------
# Heartbeat pool (consumed by the speech pipeline's spawn watchdog)
# ---------------------------------------------------------------------------
# Varied, warm "still on the bigger task" reassurances spoken at a turn
# boundary while a background mission is still running — replacing the old
# one-shot German-only "Bin noch dran." (see SpeechPipeline._spawn_watchdog_body).
# Lives here (an allowlisted runtime-voice module) so the German/Spanish strings
# stay out of the non-allowlisted pipeline; the pipeline imports this dict and
# resolves the language through its single output-language resolver. The
# ``_on_announcement`` path DOES run ``scrub_for_voice`` on every announcement;
# these phrases are written to pass through it cleanly (no markdown, no jargon)
# and never claim completion — the mission is still in flight.
STILL_RUNNING_PHRASES: dict[str, tuple[str, ...]] = {
    "de": (
        "Ich bin noch an der grösseren Sache dran. Gleich habe ich etwas Belastbares für dich.",
        "Das braucht noch einen Moment, ich kümmere mich im Hintergrund weiter darum.",
        "Bin noch dabei. Das ist nichts für zwischendurch, ich melde mich gleich.",
        "Läuft noch. Ich will dir lieber etwas Vernünftiges liefern als etwas Halbgares.",
        "Noch ein kleines Stück, dann habe ich das für dich zusammen.",
    ),
    "en": (
        "Still on the bigger task. I'll have something solid for you in a moment.",
        "This one needs a little longer; I'm staying on it in the background.",
        "Not done yet. I'd rather give you something proper than something half-baked.",
        "Still working through it. I'll come back to you shortly.",
        "Almost there, just pulling the pieces together for you.",
    ),
    "es": (
        "Sigo con el tema más grande. En un momento tendré algo sólido para ti.",
        "Esto necesita un poco más; sigo con ello en segundo plano.",
        "Aún no he terminado: prefiero darte algo en condiciones que algo a medias.",
        "Todavía trabajando en ello. Vuelvo contigo enseguida.",
        "Ya casi; estoy juntando las piezas para ti.",
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

# Every fresh spawn announcement must name the public agent brand (the
# wake-word-derived assistant name + "-Agent") so the user can distinguish a
# delegated background task from ordinary thinking. The old generic
# "sub-agent" spellings stay forbidden by ``_FORBIDDEN_VOCAB_RE``. The regex
# is built per brand because the wake word — and with it the brand — can
# change at runtime; the small cache avoids recompiling on every candidate.
@lru_cache(maxsize=8)
def _brand_re(agent_brand: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(agent_brand)}\b", re.IGNORECASE)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# English indefinite-article agreement for the rendered brand: "a Athena-Agent"
# -> "an Athena-Agent". Only capitalized vowels are matched — the brand is
# title-cased, so ordinary lowercase words are never touched.
_EN_ARTICLE_RE = re.compile(r"\b([Aa]) (?=[AEIOU])")


def _fix_en_article(text: str) -> str:
    return _EN_ARTICLE_RE.sub(
        lambda m: "an " if m.group(1) == "a" else "An ", text
    )


def _resolve_language(explicit: str | None, utterance: str) -> str:
    """Resolve the announcement language: explicit hint > utterance heuristic.

    Supports 'de', 'en' and 'es' (Runtime Output Language doctrine — every
    spoken phrase table covers all three). An explicit hint wins (the
    ``brain.reply_language`` pin / STT tag reaches here as ``language`` — the
    live source on the voice path); otherwise the utterance heuristic decides,
    and an inconclusive detection falls back to the shared ``DEFAULT_LOCALE``
    (honesty-over-guessing, never a per-layer hardcoded default). Only 'de'/'en'
    have a native LLM persona (see ``_PERSONA_LANGS``); 'es' is served from the
    curated fallback pool.
    """
    if explicit:
        low = str(explicit).strip().lower()
        if low.startswith("en"):
            return "en"
        if low.startswith("es"):
            return "es"
        if low.startswith("de"):
            return "de"
    detected = _detect_language(utterance or "")
    return detected if detected in ("de", "en", "es") else DEFAULT_LOCALE


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
        fallback_provider: AbstractAckProvider | None = None,
        fallback_breaker: CircuitBreaker | None = None,
        preferences_provider: Callable[[], str] | None = None,
        brand_provider: Callable[[], str] | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._breaker = breaker
        # Failover flash provider on a SEPARATE endpoint/key, tried only when
        # the primary is exhausted (None), times out, errors, or its breaker is
        # open. Mirrors the pre-thinking ack's Gemini->Grok failover
        # (jarvis.brain.factory._build_ack_fallback) so a dead primary flash
        # provider degrades to the live secondary, NOT to the curated pool —
        # the 2026-06-21 "contextless stock phrase" regression (gemini 429 with
        # no failover on the spawn path). Its own breaker so a busy primary
        # never starves both. None => primary-only (legacy behavior).
        self._fallback_provider = fallback_provider
        self._fallback_breaker = fallback_breaker
        # Returns the user's standing-preferences block (or "") so the spoken
        # spawn announcement honors the user's agent-instructions file. Read
        # fresh per call so an edit applies without a restart. Only the LLM path
        # consumes it; the curated fallback pool is fixed strings by design.
        self._preferences_provider = preferences_provider
        # Returns the CURRENT public agent brand (wake-word-derived assistant
        # name + "-Agent"), read fresh per call so a wake-word change applies
        # without a restart. None / a failure resolves to the neutral
        # "Assistant-Agent" — never a hardcoded product name.
        self._brand_provider = brand_provider
        # No-repeat memory across BOTH pools — back-to-back spawns must not
        # sound identical even when the LLM path is down. maxlen 3 still
        # leaves at least one pickable phrase in the smallest (4-item) pool.
        # Stores the raw {agent} templates, so a brand change cannot defeat
        # the no-repeat bookkeeping.
        self._recent: deque[str] = deque(maxlen=3)

    def _brand(self) -> str:
        """Resolve the current agent brand; never raises (AD-OE6)."""
        if self._brand_provider is not None:
            try:
                brand = (self._brand_provider() or "").strip()
                if brand:
                    return brand
            except Exception:  # noqa: BLE001 — a config hiccup must not mute the spawn
                log.debug("Agent-brand provider failed; using the neutral brand.")
        return agent_brand_from_name("")

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
        brand = self._brand()

        if kind == "already_running":
            # Cooldown suppress is a fast-path duplicate rejection; an LLM
            # round-trip would delay exactly the turns that are already noisy.
            return self._pick_fallback(_FALLBACK_ALREADY_RUNNING[lang], brand, lang)

        validated = self._validate(candidate or "", lang, brand)
        if validated:
            _emit_counter("spawn_ack_candidate_used_total")
            return validated

        # The flash-LLM path only runs for a language with a native persona
        # (de/en). An ``es`` turn goes straight to the curated pool — a
        # DE/EN-persona call would only yield text the language-match check
        # rejects, costing one wasted timeout.
        if lang in _PERSONA_LANGS:
            composed = await self._compose_via_llm(
                utterance, lang, action, target, brand
            )
            if composed:
                _emit_counter("spawn_ack_llm_used_total")
                return composed

        _emit_counter("spawn_ack_fallback_total")
        return self._pick_fallback(_FALLBACK_SPAWN[lang], brand, lang)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _validate(self, text: str, lang: str, brand: str) -> str | None:
        """Validation chain for a candidate announcement; None = rejected."""
        text = (text or "").strip()
        if not text:
            return None
        if _FORBIDDEN_VOCAB_RE.search(text):
            return None
        trimmed = _trim_to_sentences(text, _MAX_WORDS)
        if trimmed is None:
            return None
        if len(_brand_re(brand).findall(trimmed)) != 1:
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
        self, utterance: str, lang: str, action: str, target: str, brand: str
    ) -> str | None:
        """Try the primary flash provider, then the failover; ``None`` if both fail.

        The failover mirrors the pre-thinking ack's Gemini->Grok failover
        (``jarvis.brain.factory._build_ack_fallback``): when the primary flash
        provider is exhausted (429 -> ``None``), times out, errors, or its
        breaker is open, a dead primary must NOT silently degrade the spawn
        announcement to the generic pool while a healthy second provider is
        wired. Each provider keeps its OWN breaker so a busy primary never
        starves both. The first validated announcement wins.
        """
        content = self._build_content(utterance, action, target)
        for provider, breaker in (
            (self._provider, self._breaker),
            (self._fallback_provider, self._fallback_breaker),
        ):
            if provider is None:
                continue
            validated = await self._try_provider(
                provider, breaker, content, lang, brand
            )
            if validated:
                return validated
        return None

    @staticmethod
    def _build_content(utterance: str, action: str, target: str) -> str:
        """Compose the LLM input once (reused across primary + failover)."""
        content = (utterance or "").strip()
        interpreted = " ".join(p for p in (action.strip(), target.strip()) if p)
        if interpreted:
            content = (
                f"{content}\n[Task: {interpreted}]" if content
                else f"[Task: {interpreted}]"
            )
        return content

    async def _try_provider(
        self,
        provider: AbstractAckProvider,
        breaker: CircuitBreaker | None,
        content: str,
        lang: str,
        brand: str,
    ) -> str | None:
        """One bounded flash-LLM attempt against a single provider/breaker."""
        try:
            if breaker is not None and await breaker.is_open():
                _emit_counter("spawn_ack_breaker_open_total")
                return None

            timeout_ms = (
                getattr(self._config, "timeout_ms", _DEFAULT_TIMEOUT_MS)
                if self._config is not None
                else _DEFAULT_TIMEOUT_MS
            )

            try:
                raw = await asyncio.wait_for(
                    provider.run(
                        content,
                        lang,
                        persona_prompt=_augment_with_preferences(
                            get_spawn_persona(lang, brand),
                            self._preferences_provider,
                        ),
                    ),
                    timeout=timeout_ms / 1000.0,
                )
            except TimeoutError:
                _emit_counter("spawn_ack_timeout_total")
                if breaker is not None:
                    await breaker.record_failure()
                return None
            except Exception as exc:  # noqa: BLE001 — adapters should swallow; leak = failure
                log.warning("Spawn-announcement provider raised: %s", exc)
                _emit_counter("spawn_ack_provider_error_total")
                if breaker is not None:
                    await breaker.record_failure()
                return None

            if breaker is not None:
                # The provider answered — it is healthy even if we reject the
                # text below (same bookkeeping as AckGenerator.run).
                await breaker.record_success()
            validated = self._validate(raw or "", lang, brand)
            if validated is None and raw and raw.strip():
                _emit_counter("spawn_ack_rejected_total")
            return validated
        except Exception as exc:  # noqa: BLE001 — compose() must never raise
            log.warning("Spawn-announcement LLM path crashed: %s", exc)
            return None

    def _pick_fallback(self, pool: tuple[str, ...], brand: str, lang: str) -> str:
        """Pick a pool phrase, avoiding the most recent picks.

        The pools store raw ``{agent}`` templates; the live brand is resolved
        here so every spoken phrase names the wake-word-derived agent brand.
        English gets article agreement ("an Athena-Agent").
        """
        candidates = [p for p in pool if p not in self._recent] or list(pool)
        # Phrase variety, not cryptography — S311 does not apply here.
        choice = random.choice(candidates)  # noqa: S311
        self._recent.append(choice)
        rendered = choice.replace("{agent}", brand)
        return _fix_en_article(rendered) if lang == "en" else rendered
