"""Instant acknowledgment for heavy voice turns — the "under two seconds" contract.

When a user request needs the Tool Model or a background agent, the user
waits 5-30 s for the result. Nobody waits that long for a conversation partner
without a sign of life, so the orchestrator speaks ONE short line first that
says what Jarvis is DOING (never what it found). This module is the shared,
provider-neutral core used by BOTH voice engines:

- the classic speech pipeline (``jarvis/speech/pipeline.py``) speaks the line
  through its own TTS queue;
- the realtime session (``jarvis/realtime/session.py``) lets the LIVE model
  voice the line (one voice per call, BUG-090) and validates its transcript.

Design constraints, each of which killed an earlier attempt:

1. **Trigger = the moment heavy work is committed, never a model's guess.**
   :func:`plan_instant_ack` derives the work class from the deterministic
   :class:`~jarvis.brain.turn_planner.TurnPlan` (regex, no I/O), so the
   decision exists at t≈0 — not after the router's first model round
   (BUG-051) and not after a fixed 6 s of silence.
2. **State only what Jarvis is doing.** The closed per-class pools below
   name the kind of work ("checking your records", "looking that up online").
   A model-composed line is allowed ONLY for the ACTION class and ONLY when
   :func:`contextual_ack_is_valid` accepts it: intent grammar, at most 12
   words, every content word taken from the user's own request, no result
   markers, no numbers. An invented outcome needs new content words or a
   non-intent verb and is therefore structurally impossible (BUG-054).
3. **No double-tap.** Each class carries an expected duration; short work
   gets a grace window and only speaks if it is still running.
4. **No stock filler for actions.** The maintainer's rule (2026-08-17): an
   action ack must reference the request ("I'm opening Spotify."), never a
   canned "On it." — so the ACTION pool is deliberately empty and the class
   is marked ``contextual``.

German/Spanish strings below are runtime voice output (CLAUDE.md §1).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from jarvis.brain.turn_planner import TurnPlan, TurnReason, is_lookup_shape

log = logging.getLogger(__name__)

_SUPPORTED_LANGUAGES = ("de", "en", "es")
_DEFAULT_LANGUAGE = "en"

#: Grace window for SHORT work: speak only if the turn is still running
#: afterwards. 3 s covers the local fast paths (local-action gate, wiki
#: ingest, navigation) AND a single-round Tool Model answer on a warm
#: provider — a turn that answers inside it stays chatter-free; anything
#: slower deserves the line, spoken well before the wait turns annoying
#: (maintainer 2026-08-18: 1.2 s acked turns whose answer was seconds away).
SHORT_GRACE_S = 3.0
#: LONG work (web research, screen control, missions) never finishes inside
#: the ack itself — speak immediately.
IMMEDIATE_DELAY_S = 0.0
#: One grounded progress line if the work outlasts the ack by this much.
PROGRESS_AFTER_S = 8.0

_MAX_CONTEXTUAL_WORDS = 12
_MIN_CONTEXTUAL_WORDS = 2
_MAX_INTENT_VERB_POSITION = 4
_INFLECTION_PREFIX_CHARS = 4


class WorkClass(StrEnum):
    """What kind of heavy work the turn committed to (drives the ack line)."""

    RESEARCH = "research"  # web / current / connected data lookups
    PERSONAL = "personal"  # the user's own world: notes, mail, calendar, past (read)
    SCREEN = "screen"  # look at or operate the screen
    MISSION = "mission"  # background agent
    ACTION = "action"  # do something concrete — contextual line only


@dataclass(frozen=True, slots=True)
class InstantAckPlan:
    """Provider-neutral decision for one turn."""

    work_class: WorkClass
    delay_s: float
    contextual: bool

    @property
    def immediate(self) -> bool:
        return self.delay_s <= 0.0


def plan_instant_ack(turn_plan: TurnPlan | None, utterance: str = "") -> InstantAckPlan | None:
    """Return the ack plan for a turn, or ``None`` when no ack is warranted.

    ``None`` covers plain conversation (no orchestrator), voice control, and
    orchestrator turns whose only reasons are too vague to promise anything
    honest (``UNCERTAIN`` / ``WORKSPACE`` / ``CAPABILITY`` / ``SKILL`` alone).
    """
    if turn_plan is None or not turn_plan.requires_orchestrator:
        return None
    reasons = set(turn_plan.reasons)
    if TurnReason.MISSION in reasons:
        return InstantAckPlan(WorkClass.MISSION, IMMEDIATE_DELAY_S, False)
    if TurnReason.SCREEN_CONTEXT in reasons:
        return InstantAckPlan(WorkClass.SCREEN, IMMEDIATE_DELAY_S, False)
    if TurnReason.PRIVATE_DATA in reasons and is_lookup_shape(utterance):
        # A recall QUESTION ("do you remember", "what's in my notes") carries
        # the planner's ACTION reason too (``remember`` is a save verb), but
        # the user is asking, not ordering: the personal-lookup pool line fits
        # and needs no composer. A connected lookup (calendar, mail) never
        # finishes inside the grace, so it speaks at once; a wiki recall may
        # hit the local fast path and gets the grace.
        connected = bool(TurnReason.CONNECTED_DATA in reasons or TurnReason.CURRENT_DATA in reasons)
        return InstantAckPlan(
            WorkClass.PERSONAL,
            IMMEDIATE_DELAY_S if connected else SHORT_GRACE_S,
            False,
        )
    if TurnReason.ACTION in reasons:
        return InstantAckPlan(WorkClass.ACTION, SHORT_GRACE_S, True)
    if (
        TurnReason.CURRENT_DATA in reasons
        or TurnReason.CONNECTED_DATA in reasons
        or turn_plan.requires_public_fact_grounding
        or TurnReason.PUBLIC_FACT in reasons
    ):
        return InstantAckPlan(WorkClass.RESEARCH, IMMEDIATE_DELAY_S, False)
    if TurnReason.PRIVATE_DATA in reasons:
        return InstantAckPlan(WorkClass.PERSONAL, SHORT_GRACE_S, False)
    return None


# ---------------------------------------------------------------------------
# Closed pools — what Jarvis is doing, never what it found.
# ---------------------------------------------------------------------------

# i18n-allow: localized runtime voice output (whole table)
_POOLS: dict[WorkClass, dict[str, tuple[str, ...]]] = {
    WorkClass.RESEARCH: {
        "de": (  # i18n-allow: localized runtime voice output
            "Ich suche das gerade online.",  # i18n-allow
            "Moment, ich schaue online nach.",  # i18n-allow
            "Ich hole mir dazu aktuelle Infos.",  # i18n-allow
            "Kurze Online-Suche, einen Moment.",  # i18n-allow
        ),
        "en": (
            "I'm looking that up online.",
            "One moment, checking online.",
            "Let me pull the latest on that.",
            "Quick online search, one moment.",
        ),
        "es": (  # i18n-allow: localized runtime voice output
            "Lo estoy buscando en línea.",
            "Un momento, lo consulto en línea.",
            "Estoy buscando información actual.",
            "Una búsqueda rápida, un momento.",
        ),
    },
    WorkClass.PERSONAL: {
        "de": (  # i18n-allow: localized runtime voice output
            "Ich schaue kurz in deinen Unterlagen nach.",  # i18n-allow
            "Moment, ich sehe in deinen Sachen nach.",  # i18n-allow
            "Ich suche das bei dir raus.",  # i18n-allow
            "Ich schaue nach, was ich dazu von dir habe.",  # i18n-allow
        ),
        "en": (
            "I'm checking your records.",
            "One moment, looking through your things.",
            "Let me find that in your data.",
            "Checking what I have from you on that.",
        ),
        "es": (  # i18n-allow: localized runtime voice output
            "Estoy revisando tus registros.",
            "Un momento, miro en tus cosas.",
            "Busco eso en tus datos.",
            "Reviso lo que tengo tuyo sobre eso.",
        ),
    },
    WorkClass.SCREEN: {
        "de": (  # i18n-allow: localized runtime voice output
            "Ich schaue mir den Bildschirm an.",  # i18n-allow
            "Moment, ich sehe mir den Bildschirm an.",  # i18n-allow
            "Ich übernehme kurz den Bildschirm.",  # i18n-allow
            "Ich schaue, was auf dem Bildschirm ist.",  # i18n-allow
        ),
        "en": (
            "I'm looking at the screen.",
            "One moment, checking the screen.",
            "I'll take the screen for a moment.",
            "Let me see what's on screen.",
        ),
        "es": (  # i18n-allow: localized runtime voice output
            "Estoy mirando la pantalla.",
            "Un momento, reviso la pantalla.",
            "Tomo la pantalla un momento.",
            "Veo qué hay en pantalla.",
        ),
    },
    # ``{agent}`` is the wake-word-derived agent brand ("<Name>-Agent"),
    # rendered by the caller — never a hardcoded product name (CLAUDE.md §4).
    WorkClass.MISSION: {
        "de": (  # i18n-allow: localized runtime voice output
            "Das gebe ich einem {agent} weiter.",  # i18n-allow
            "Ein {agent} übernimmt das, ich melde mich.",  # i18n-allow
            "Ich setze einen {agent} darauf an.",  # i18n-allow
            "Das läuft ab jetzt bei einem {agent}.",  # i18n-allow
        ),
        "en": (
            "I'm handing that to a {agent}.",
            "A {agent} will take that, I'll report back.",
            "I'm putting a {agent} on it.",
            "That's with a {agent} from now on.",
        ),
        "es": (  # i18n-allow: localized runtime voice output
            "Se lo paso a un {agent}.",
            "Un {agent} se encarga, te aviso.",
            "Pongo a un {agent} en ello.",
            "Eso queda con un {agent} desde ahora.",
        ),
    },
    # ACTION: deliberately empty — an action ack must reference the request.
    WorkClass.ACTION: {"de": (), "en": (), "es": ()},
}

# No-repeat memory per (class, language): back-to-back acks never share a
# wording. Module-level on purpose — both voice engines draw from one memory.
_RECENT: dict[tuple[WorkClass, str], deque[str]] = {}
_RECENT_DEPTH = 2


def _language_key(language: str) -> str:
    code = (language or "").strip().lower()[:2]
    return code if code in _SUPPORTED_LANGUAGES else _DEFAULT_LANGUAGE


def instant_ack_pool(
    work_class: WorkClass, language: str, *, agent_brand: str = ""
) -> tuple[str, ...]:
    """Closed pool for ``work_class`` in ``language`` (rendered, brand filled)."""
    raw = _POOLS.get(work_class, {}).get(_language_key(language), ())
    if work_class is WorkClass.MISSION:
        brand = (agent_brand or "").strip() or "Assistant-Agent"
        return tuple(line.format(agent=brand) for line in raw)
    return tuple(raw)


def pick_instant_ack_text(work_class: WorkClass, language: str, *, agent_brand: str = "") -> str:
    """Pick one pool line, avoiding the most recent wordings. ``""`` when none."""
    pool = instant_ack_pool(work_class, language, agent_brand=agent_brand)
    if not pool:
        return ""
    key = (work_class, _language_key(language))
    recent = _RECENT.setdefault(key, deque(maxlen=_RECENT_DEPTH))
    candidates = [line for line in pool if line not in recent] or list(pool)
    # noqa comment: variety, not security — any pool member is equally safe.
    chosen = random.choice(candidates)  # noqa: S311
    recent.append(chosen)
    return chosen


def normalize_ack_line(text: str) -> str:
    """Casefold + collapse whitespace + drop trailing punctuation (transcript match)."""
    return " ".join(str(text or "").strip().rstrip(".!?¡¿").casefold().split())


def all_instant_ack_lines(language: str, *, agent_brand: str = "") -> frozenset[str]:
    """Every closed-pool line for ``language``, normalized — the transcript whitelist."""
    lines: set[str] = set()
    for work_class in WorkClass:
        for line in instant_ack_pool(work_class, language, agent_brand=agent_brand):
            lines.add(normalize_ack_line(line))
    return frozenset(lines)


# ---------------------------------------------------------------------------
# Contextual (ACTION) line — structural validator
# ---------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Casefold, strip diacritics, and unify apostrophes for token comparison."""
    folded = unicodedata.normalize("NFKD", str(text or "").casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return folded.replace("’", "'").replace("`", "'")


_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_fold(text))


# Verbs that make a sentence an INTENT ("I'm opening …"), per language. A
# contextual ack must carry one of these within its first few tokens; a result
# claim ("Spotify is open") has none and is rejected on grammar alone.
# i18n-allow: multilingual output-grammar data
_INTENT_VERBS: dict[str, frozenset[str]] = {
    "de": frozenset(
        {
            "offne",
            "offnen",
            "mache",
            "mach",
            "starte",
            "schaue",
            "schau",
            "gucke",
            "suche",
            "such",
            "hole",
            "hol",
            "prufe",
            "pruf",
            "checke",
            "lese",
            "les",
            "schreibe",
            "schreib",
            "trage",
            "trag",
            "schicke",
            "schick",
            "sende",
            "stelle",
            "stell",
            "schalte",
            "schalt",
            "spiele",
            "spiel",
            "rufe",
            "ruf",
            "gebe",
            "geb",
            "kummere",
            "kummer",
            "erledige",
            "ubernehme",
            "gehe",
            "leite",
            "richte",
            "lege",
            "leg",
            "setze",
            "setz",
            "aktiviere",
            "deaktiviere",
            "wechsle",
            "wechsel",
            "andere",
            "speichere",
            "speicher",
            "notiere",
            "merke",
            "merk",
            "frage",
            "frag",
            "buche",
            "buch",
            "sage",
            "sag",
            "teile",
            "teil",
            "informiere",
            "melde",
            "melden",
            "ubergebe",
            "reiche",
            "tippe",
            "tipp",
            "klicke",
            "klick",
            "schliesse",
            "schließe",
            "verschiebe",
            "kopiere",
            "losche",
            "lösche",
            "erstelle",
            "installiere",
            "aktualisiere",
            "stoppe",
            "stopp",
            "pausiere",
            "diktiere",
            "nehme",
            "plane",
            "erinnere",
            "beende",
            "lade",
            "lad",
        }
    ),
    "en": frozenset(
        {
            "open",
            "opening",
            "start",
            "starting",
            "check",
            "checking",
            "look",
            "looking",
            "search",
            "searching",
            "get",
            "getting",
            "read",
            "reading",
            "write",
            "writing",
            "send",
            "sending",
            "set",
            "setting",
            "switch",
            "switching",
            "play",
            "playing",
            "call",
            "calling",
            "run",
            "running",
            "take",
            "taking",
            "pull",
            "pulling",
            "save",
            "saving",
            "add",
            "adding",
            "put",
            "putting",
            "launch",
            "launching",
            "turn",
            "turning",
            "bring",
            "bringing",
            "book",
            "booking",
            "tell",
            "telling",
            "ask",
            "asking",
            "note",
            "noting",
            "change",
            "changing",
            "handle",
            "handling",
            "do",
            "doing",
            "work",
            "working",
            "queue",
            "queuing",
            "hand",
            "handing",
            "pass",
            "passing",
            "give",
            "giving",
            "brief",
            "briefing",
            "prompt",
            "prompting",
            "kick",
            "kicking",
            "type",
            "typing",
            "click",
            "clicking",
            "close",
            "closing",
            "move",
            "moving",
            "copy",
            "copying",
            "delete",
            "deleting",
            "create",
            "creating",
            "install",
            "installing",
            "update",
            "updating",
            "restart",
            "restarting",
            "stop",
            "stopping",
            "mute",
            "muting",
            "pause",
            "pausing",
            "resume",
            "resuming",
            "dictate",
            "dictating",
            "record",
            "recording",
            "schedule",
            "scheduling",
            "remind",
            "reminding",
        }
    ),
    "es": frozenset(
        {
            "abro",
            "voy",
            "busco",
            "miro",
            "reviso",
            "leo",
            "escribo",
            "envio",
            "mando",
            "pongo",
            "cambio",
            "llamo",
            "guardo",
            "ejecuto",
            "tomo",
            "anoto",
            "activo",
            "desactivo",
            "inicio",
            "arranco",
            "consulto",
            "preparo",
            "reservo",
            "pregunto",
            "hago",
            "empiezo",
            "paso",
            "doy",
            "cierro",
            "muevo",
            "copio",
            "borro",
            "creo",
            "instalo",
            "actualizo",
            "reinicio",
            "paro",
            "detengo",
            "pauso",
            "reanudo",
            "dicto",
            "grabo",
            "programo",
            "recuerdo",
            "termino",
        }
    ),
}

# Function words, politeness, and time adverbs a contextual line may use
# freely — none of them can carry a fact.
# i18n-allow: multilingual output-grammar data
_FREE_WORDS: dict[str, frozenset[str]] = {
    "de": frozenset(
        {
            "ich",
            "das",
            "dir",
            "dich",
            "fur",
            "kurz",
            "mal",
            "gleich",
            "jetzt",
            "sofort",
            "gerade",
            "eben",
            "einen",
            "eine",
            "ein",
            "einem",
            "einer",
            "den",
            "die",
            "der",
            "dem",
            "des",
            "in",
            "im",
            "ins",
            "auf",
            "an",
            "am",
            "zu",
            "zum",
            "zur",
            "mit",
            "bei",
            "beim",
            "von",
            "vom",
            "und",
            "es",
            "ja",
            "okay",
            "ok",
            "klar",
            "gut",
            "alles",
            "moment",
            "bitte",
            "sekunde",
            "gern",
            "gerne",
            "dann",
            "so",
            "noch",
            "doch",
            "schon",
            "wird",
            "werde",
            "will",
            "kann",
            "lass",
            "mich",
            "mir",
            "dein",
            "deine",
            "deinen",
            "deinem",
            "deiner",
            "dazu",
            "damit",
            "drauf",
            "darauf",
            "daran",
            "drum",
            "darum",
            "los",
            "direkt",
            "schnell",
            "nach",
            "aus",
            "ab",
            "um",
            "uber",
            "durch",
            "hier",
            "da",
            "dort",
            "wie",
            "gewunscht",
            "naturlich",
            "sehr",
            "einmal",
            "erst",
            "nun",
            "mache",
            "ich's",
            "ichs",
            "auch",
            "dass",
            "ob",
            "wenn",
            "soll",
            "sollen",
            "sich",
            "ihn",
            "ihm",
            "ihr",
            "ihnen",
            "er",
            "sie",
        }
    ),
    "en": frozenset(
        {
            "i",
            "i'm",
            "im",
            "am",
            "will",
            "i'll",
            "ill",
            "let",
            "me",
            "that",
            "this",
            "it",
            "the",
            "a",
            "an",
            "for",
            "you",
            "your",
            "up",
            "on",
            "in",
            "to",
            "of",
            "at",
            "now",
            "right",
            "away",
            "just",
            "quickly",
            "quick",
            "one",
            "moment",
            "sec",
            "second",
            "okay",
            "ok",
            "sure",
            "alright",
            "all",
            "and",
            "then",
            "going",
            "gonna",
            "about",
            "into",
            "with",
            "over",
            "off",
            "out",
            "down",
            "as",
            "asked",
            "requested",
            "straight",
            "here",
            "there",
            "so",
            "yes",
            "yeah",
            "please",
            "course",
            "way",
            "onto",
            "them",
            "those",
            "these",
            "back",
            "again",
            "first",
            "already",
            "next",
            "should",
            "if",
            "whether",
            "he",
            "she",
            "him",
            "her",
            "his",
            "its",
        }
    ),
    "es": frozenset(
        {
            "yo",
            "a",
            "lo",
            "la",
            "el",
            "los",
            "las",
            "un",
            "una",
            "unos",
            "unas",
            "en",
            "de",
            "del",
            "al",
            "para",
            "por",
            "con",
            "te",
            "tu",
            "tus",
            "ti",
            "ahora",
            "mismo",
            "ya",
            "momento",
            "vale",
            "claro",
            "eso",
            "esto",
            "y",
            "que",
            "se",
            "me",
            "enseguida",
            "rapido",
            "rapida",
            "si",
            "bien",
            "sobre",
            "hacia",
            "desde",
            "esta",
            "este",
            "aqui",
            "alli",
            "primero",
            "luego",
            "todo",
            "como",
            "pediste",
            "pedido",
            "voy",
        }
    ),
}

# A completion / result claim in an ack is the one thing that must never
# pass, however short. Present-perfect and copula-state shapes per language.
# i18n-allow: multilingual output matcher
_RESULT_MARKER_RE = re.compile(
    r"(?:"
    r"\b(?:ist|sind|war|waren)\s+(?:jetzt\s+|nun\s+|bereits\s+|schon\s+)?"
    r"(?:offen|geoffnet|fertig|erledigt|gestartet|gespeichert|eingetragen|"
    r"gesendet|geschickt|aktiviert|deaktiviert|an|aus|da|dran|drin|bereit)\b|"
    r"\b(?:habe|hab|hatte)\s+(?:ich\s+)?(?:\w+\s+){0,3}"
    r"(?:geoffnet|gemacht|gestartet|gespeichert|eingetragen|gesendet|"
    r"geschickt|gefunden|nachgeschaut|nachgesehen|geprueft|gepruft|erledigt)\b|"
    r"\b(?:erledigt|fertig|geschafft|gefunden)\b|"
    r"\b(?:is|are|was|were)\s+(?:now\s+|already\s+)?"
    r"(?:open|opened|done|ready|started|saved|sent|on|off|set|running|"
    r"playing|finished|complete|completed)\b|"
    r"\b(?:i've|i have|ive)\s+(?:\w+\s+){0,3}"
    r"(?:opened|done|started|saved|sent|found|checked|finished|set|played)\b|"
    r"\b(?:done|finished|found it|all set)\b|"
    r"\b(?:esta|estan|ya esta|ya estan)\s+"
    r"(?:abierto|abierta|listo|lista|hecho|hecha|guardado|guardada|"
    r"enviado|enviada|activado|activada)\b|"
    r"\b(?:he|ya he)\s+(?:\w+\s+){0,3}"
    r"(?:abierto|hecho|guardado|enviado|encontrado|revisado|terminado)\b|"
    r"\b(?:hecho|listo|terminado|encontrado)\b"
    r")"
)

# Words no spoken line may contain (defense in depth next to scrub_for_voice).
_FORBIDDEN_RE = re.compile(
    r"\b(?:sub-?agent|worker|provider|sir|jawohl|sehr wohl|boss|chef|"  # i18n-allow: matcher
    r"router|orchestrator|delegate|tool[- ]?model|backend|api|json)\b",
    re.IGNORECASE,
)


def _matches_utterance(token: str, utterance_tokens: set[str]) -> bool:
    if token in utterance_tokens:
        return True
    if len(token) < _INFLECTION_PREFIX_CHARS:
        return False
    # Inflection tolerance ("Notiz" / "Notizen", "Spotify" / "Spotifys"):
    # both words are at least four characters and share their first four.
    stem = token[:_INFLECTION_PREFIX_CHARS]
    return any(
        len(candidate) >= _INFLECTION_PREFIX_CHARS and candidate[:_INFLECTION_PREFIX_CHARS] == stem
        for candidate in utterance_tokens
    )


def contextual_ack_is_valid(
    text: str,
    *,
    utterance: str,
    language: str,
    extra_allowed_words: tuple[str, ...] = (),
) -> bool:
    """Accept a model-composed ACTION ack only when it is structurally an intent.

    Rules (all deterministic, no model):
    - 2..12 words, no question mark, no forbidden vocabulary;
    - no digit unless that very digit token appears in the user's request;
    - no result / completion marker (present-perfect, copula-state, "done");
    - an intent verb of the language within the first four tokens;
    - every remaining content word comes from the user's own request (exact
      or by 4-char inflection prefix), the free-word list, the intent verbs,
      or ``extra_allowed_words`` (the assistant/agent brand).

    Anything the model adds beyond the user's words is rejected — which is
    exactly the property that makes an invented result impossible.
    """
    raw = str(text or "").strip()
    if not raw or "?" in raw:
        return False
    if _FORBIDDEN_RE.search(raw):
        return False
    lang = _language_key(language)
    tokens = _tokens(raw)
    if not (_MIN_CONTEXTUAL_WORDS <= len(tokens) <= _MAX_CONTEXTUAL_WORDS):
        return False
    folded = _fold(raw)
    if _RESULT_MARKER_RE.search(folded):
        return False
    utterance_tokens = set(_tokens(utterance))
    intent_verbs = _INTENT_VERBS.get(lang, frozenset())
    if not any(token in intent_verbs for token in tokens[:_MAX_INTENT_VERB_POSITION]):
        return False
    free = _FREE_WORDS.get(lang, frozenset())
    allowed_extra = {tok for word in extra_allowed_words for tok in _tokens(word)}
    # A line made only of verbs and free words ("Mache ich.", "On it.") is the
    # stock filler this class forbids: at least one word must name the
    # request's own subject.
    names_subject = False
    for token in tokens:
        if token.isdigit():
            if token in utterance_tokens:
                names_subject = True
                continue
            return False
        if token in free or token in intent_verbs or token in allowed_extra:
            continue
        # Contractions: "i'm" -> "i" + "m"; check the head.
        head = token.split("'", 1)[0]
        if head in free or head in intent_verbs:
            continue
        if _matches_utterance(token, utterance_tokens):
            names_subject = True
            continue
        return False
    return names_subject


# ---------------------------------------------------------------------------
# Process-wide "what did we just say" note — so a later spoken line for the
# SAME request (the router's grounded tool ack, the spawn handover) can
# continue instead of re-announcing. One user, one voice, one process.
# ---------------------------------------------------------------------------

_last_spoken: tuple[str, float] | None = None


def note_spoken(text: str, *, now: float | None = None) -> None:
    """Record that an instant ack was actually released to the speaker."""
    global _last_spoken  # noqa: PLW0603 — deliberate process-wide note
    line = str(text or "").strip()
    if not line:
        return
    _last_spoken = (line, time.monotonic() if now is None else float(now))


def recently_spoken(within_s: float = PROGRESS_AFTER_S, *, now: float | None = None) -> str:
    """The instant ack spoken less than ``within_s`` ago, or ``""``."""
    if _last_spoken is None:
        return ""
    line, at = _last_spoken
    current = time.monotonic() if now is None else float(now)
    return line if (current - at) < float(within_s) else ""


# ---------------------------------------------------------------------------
# Progress line — grounded in the tool that is ACTUALLY running
# ---------------------------------------------------------------------------


class ToolActivity(StrEnum):
    """Coarse class of a running tool, for an honest "still on it" line."""

    SEARCH = "search"
    READ = "read"
    SCREEN = "screen"
    HANDOVER = "handover"  # a background agent took over — the reply says so
    OTHER = "other"


_TOOL_ACTIVITY_MARKERS: tuple[tuple[ToolActivity, tuple[str, ...]], ...] = (
    (ToolActivity.HANDOVER, ("spawn", "mission", "dispatch_harness", "worker")),
    (
        ToolActivity.SCREEN,
        (
            "computer_use",
            "click",
            "type_text",
            "hotkey",
            "scroll",
            "screen",
            "open_app",
            "switch_window",
            "move_mouse",
            "window",
            "screenshot",
        ),
    ),
    (ToolActivity.SEARCH, ("search", "web", "browse", "fetch", "http", "crawl")),
    (
        ToolActivity.READ,
        (
            "wiki",
            "memory",
            "recall",
            "note",
            "read",
            "calendar",
            "mail",
            "gmail",
            "contact",
            "list",
            "get",
            "inspect",
            "status",
            "lookup",
        ),
    ),
)


def classify_tool_activity(tool_name: str) -> ToolActivity:
    """Map a tool name (any registry, any casing) onto a progress class."""
    name = str(tool_name or "").casefold()
    if not name:
        return ToolActivity.OTHER
    for activity, markers in _TOOL_ACTIVITY_MARKERS:
        if any(marker in name for marker in markers):
            return activity
    return ToolActivity.OTHER


# i18n-allow: localized runtime voice output (whole table)
_PROGRESS_POOLS: dict[ToolActivity, dict[str, tuple[str, ...]]] = {
    ToolActivity.SEARCH: {
        "de": (  # i18n-allow: localized runtime voice output
            "Die Suche läuft noch.",  # i18n-allow
            "Ich bin noch am Suchen.",  # i18n-allow
            "Die Online-Suche braucht noch einen Moment.",  # i18n-allow
        ),
        "en": (
            "Still searching.",
            "The search is still running.",
            "The online lookup needs another moment.",
        ),
        "es": (  # i18n-allow: localized runtime voice output
            "Sigo buscando.",
            "La búsqueda sigue en marcha.",
            "La consulta en línea necesita un momento más.",
        ),
    },
    ToolActivity.READ: {
        "de": (  # i18n-allow: localized runtime voice output
            "Ich lese noch in deinen Unterlagen.",  # i18n-allow
            "Bin noch am Nachlesen.",  # i18n-allow
            "Ich gehe das noch durch.",  # i18n-allow
        ),
        "en": (
            "Still reading through your records.",
            "Still going through it.",
            "Reading on, one moment.",
        ),
        "es": (  # i18n-allow: localized runtime voice output
            "Sigo leyendo tus registros.",
            "Todavía lo estoy revisando.",
            "Sigo con ello, un momento.",
        ),
    },
    ToolActivity.SCREEN: {
        "de": (  # i18n-allow: localized runtime voice output
            "Ich bin noch am Bildschirm dran.",  # i18n-allow
            "Der Bildschirm-Schritt läuft noch.",  # i18n-allow
            "Noch einen Moment am Bildschirm.",  # i18n-allow
        ),
        "en": (
            "Still working on the screen.",
            "The screen step is still running.",
            "One more moment on the screen.",
        ),
        "es": (  # i18n-allow: localized runtime voice output
            "Sigo con la pantalla.",
            "El paso en pantalla sigue en marcha.",
            "Un momento más en la pantalla.",
        ),
    },
    ToolActivity.OTHER: {
        "de": (  # i18n-allow: localized runtime voice output
            "Ich bin noch dran.",  # i18n-allow
            "Dauert noch einen kleinen Moment.",  # i18n-allow
            "Bin gleich so weit.",  # i18n-allow
        ),
        "en": (
            "I'm still working on it.",
            "Still on it, give me a moment.",
            "Almost there.",
        ),
        "es": (  # i18n-allow: localized runtime voice output
            "Sigo trabajando en ello.",
            "Un momento más.",
            "Ya casi está.",
        ),
    },
    # HANDOVER: no line of its own — the spawn reply states the handover.
    ToolActivity.HANDOVER: {"de": (), "en": (), "es": ()},
}

_RECENT_PROGRESS: dict[tuple[ToolActivity, str], deque[str]] = {}


def progress_pool(activity: ToolActivity, language: str) -> tuple[str, ...]:
    return tuple(_PROGRESS_POOLS.get(activity, {}).get(_language_key(language), ()))


def pick_progress_text(activity: ToolActivity, language: str) -> str:
    """One honest progress line for the running activity; ``""`` for handover."""
    pool = progress_pool(activity, language)
    if not pool:
        return ""
    key = (activity, _language_key(language))
    recent = _RECENT_PROGRESS.setdefault(key, deque(maxlen=_RECENT_DEPTH))
    candidates = [line for line in pool if line not in recent] or list(pool)
    # noqa comment: variety, not security — any pool member is equally safe.
    chosen = random.choice(candidates)  # noqa: S311
    recent.append(chosen)
    return chosen


def all_progress_lines(language: str) -> frozenset[str]:
    """Every progress-pool line for ``language``, normalized (transcript whitelist)."""
    return frozenset(
        normalize_ack_line(line)
        for activity in ToolActivity
        for line in progress_pool(activity, language)
    )


# ---------------------------------------------------------------------------
# Chat surface — the visual twin of the spoken line
# ---------------------------------------------------------------------------


def start_chat_instant_ack(
    bus: Any,
    *,
    text: str,
    thread_id: str,
    trace_id: Any = None,
    brain: Any = None,
    agent_brand: str = "",
    language: str = "",
) -> asyncio.Task[None] | None:
    """Show the instant ack as a muted pre-ack bubble in a text-chat thread.

    The chat path has no streaming and no interim voice: the user sees
    "thinking…" until the whole turn returns. This publishes the same line the
    voice engines would speak — as ``MessageSent(role="preamble")``, the
    bubble the chat view already renders for pre-acks — after the plan's
    delay, and only if the task is still alive (the caller cancels it the
    moment the reply arrives, so a fast turn shows nothing). Never speaks:
    it is a bus message for the UI, not an announcement.

    Returns the task (cancel it in the caller's ``finally``) or ``None`` when
    the turn warrants no ack.
    """
    request = str(text or "").strip()
    if bus is None or not request:
        return None
    try:
        from jarvis.brain.ack_generator import is_voice_control_utterance
        from jarvis.brain.turn_planner import plan_turn

        if is_voice_control_utterance(request):
            return None
        plan = plan_instant_ack(plan_turn(request), request)
    except Exception:  # noqa: BLE001 — planning must never break a chat turn
        log.debug("chat instant ack: planning failed", exc_info=True)
        return None
    if plan is None:
        return None
    if not language:
        try:
            from jarvis.core.turn_language import resolve_output_language

            language = resolve_output_language(
                getattr(brain, "_reply_language", None),
                "unknown",
                request,
                conversation_language=getattr(brain, "_conversation_language", None),
            )
        except Exception:  # noqa: BLE001 — fall back to the module default
            language = _DEFAULT_LANGUAGE

    async def _body() -> None:
        try:
            if plan.delay_s > 0:
                await asyncio.sleep(plan.delay_s)
            if plan.contextual:
                line = await compose_contextual_ack(
                    getattr(brain, "_readback_composer", None),
                    utterance=request,
                    language=language,
                    agent_brand=agent_brand,
                )
            else:
                line = pick_instant_ack_text(plan.work_class, language, agent_brand=agent_brand)
            if not line:
                return
            from jarvis.core.events import MessageSent

            kwargs: dict[str, Any] = {
                "thread_id": thread_id,
                "role": "preamble",
                "text": line,
                "source_layer": "brain.instant_ack",
            }
            if trace_id is not None:
                kwargs["trace_id"] = trace_id
            await bus.publish(MessageSent(**kwargs))
            note_spoken(line)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort by design
            log.debug("chat instant ack failed", exc_info=True)

    return asyncio.create_task(_body(), name="chat-instant-ack")


#: Hard budget for a flash-LLM composed ACTION line. The line is worthless
#: after the result, so a slow provider simply costs the ack, never the turn.
CONTEXTUAL_BUDGET_MS = 700

_LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish"}


def language_name(language: str) -> str:
    return _LANGUAGE_NAMES.get(_language_key(language), "English")


async def compose_contextual_ack(
    composer: object | None,
    *,
    utterance: str,
    language: str,
    agent_brand: str = "",
    budget_ms: int = CONTEXTUAL_BUDGET_MS,
) -> str:
    """Compose a request-specific ACTION line with a flash composer, or ``""``.

    ``composer`` is a :class:`jarvis.voice.contextual_readback.ReadbackComposer`
    (duck-typed: ``has_llm`` + ``compose``). Never raises. The composer's own
    guards run first; :func:`contextual_ack_is_valid` is the final boundary,
    so a line the model padded with anything beyond the user's words is
    dropped and the caller stays silent — silence beats a stock filler for
    an action (maintainer rule 2026-08-17).
    """
    if composer is None or not bool(getattr(composer, "has_llm", False)):
        return ""
    compose = getattr(composer, "compose", None)
    if not callable(compose):
        return ""
    request = str(utterance or "").strip()
    if not request:
        return ""
    try:
        text = await compose(
            instruction=contextual_ack_prompt(
                language_name=language_name(language), utterance=request
            ),
            language=_language_key(language),
            canned=lambda: "",
            facts={"user_request": request},
            in_progress=True,
            honesty_bound=False,
            latency_budget_ms=int(budget_ms),
        )
    except Exception:  # noqa: BLE001 — an ack composer must never break a turn
        return ""
    line = str(text or "").strip()
    if not line or not contextual_ack_is_valid(
        line,
        utterance=request,
        language=language,
        extra_allowed_words=(agent_brand,) if agent_brand else (),
    ):
        return ""
    return line


def contextual_ack_prompt(*, language_name: str, utterance: str) -> str:
    """Instruction for a model that composes the ACTION ack (live or flash).

    The validator above is the trust boundary; this prompt only raises the
    hit rate. It names the shape, forbids results, and pins the vocabulary to
    the user's own words.
    """
    return (
        "The Jarvis orchestrator is executing the user's request right now and "
        f"has no result yet. Say ONE short sentence in {language_name}, at most "
        "ten words, that tells the user what you are doing for exactly this "
        "request — name its concrete subject with the user's own words (the "
        "app, the person, the file, the setting, the terminal, the topic), for "
        "example the shape 'I'm opening <thing>.', 'I'm sending that to "
        "<thing>.', 'I'm looking up <thing>.' or 'I'm checking your <thing>.'. "
        "Present tense only. No result, no success, no promise about the "
        "outcome, no question, no new facts, no numbers, no words the user did "
        "not use except 'I'm', 'now', 'for you'. Then stop. "
        f'The user\'s request was: "{str(utterance or "").strip()}"'
    )
