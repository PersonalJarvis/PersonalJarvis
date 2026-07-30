"""Three-valued visual-intent classification for Screen Context.

Answers one question about a user turn: *did the user unambiguously ask Jarvis
to look at the screen?* The three-valued answer is the point. A two-valued gate
must resolve "what is that?" as either a capture or a miss, and both are wrong
for that same sentence — it is a question about the screen roughly as often as
it is a question about the topic under discussion. ``AMBIGUOUS`` is what lets
Jarvis ask a one-line question instead of guessing, which is the behaviour this
feature promises.

**Deterministic by mandate.** Regex over a normalized string, O(1), no model
call and no network. An LLM classifier here would sit on the voice path (AP-9),
and it would make "did it look at my screen, and why?" unanswerable after the
fact — ``IntentVerdict.evidence`` exists so that question always has an answer.

**Every supported locale is equal** (CLAUDE.md §1): de, en and es are matched
simultaneously rather than selected by the session locale, because a bilingual
user mixes them inside one sentence and a locale-gated matcher would go deaf on
the half it did not pick. Adding a locale is a data entry below, not code.

The vocabulary is speech-recognition *input matching data* — the closed-list
exception in §1, the same category as ``jarvis/brain/cu_gate.py``.
"""
from __future__ import annotations

import logging
import re

from jarvis.screen_context.models import IntentVerdict, VisualIntent

log = logging.getLogger(__name__)

#: Cap on recorded evidence fragments — a log line, not a transcript.
_MAX_EVIDENCE = 3


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

# German digraphs plus Spanish accent stripping, so the vocabulary below can be
# written in plain ASCII and still match accented input.  # i18n-allow
_TRANSLITERATION = str.maketrans(
    {
        # i18n-allow: character-mapping data for speech-input normalization
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",  # i18n-allow
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n",
        "à": "a", "è": "e", "ì": "i", "ò": "o", "ù": "u", "ç": "c",
    }
)


# Idioms that CONTAIN a look-verb but are not a request to look at anything.
# Masked out before matching rather than used as a veto: a veto would let one
# stray idiom kill a turn that also contains a real request ("looks like an
# error — can you see this?"), which is exactly the sentence the feature exists
# for. Masking removes only the idiom and leaves the rest intact.
#
# This mirrors ``_PRODUCT_NAME_NOISE_RE`` in jarvis/brain/cu_gate.py, which
# learned the same lesson from a live incident: a bare verb token swallowed
# every phrase that merely contained it.
_IDIOM_NOISE_RE: re.Pattern[str] = re.compile(
    # --- English ---
    # "you see" is deliberately NOT masked bare: it is the tail of "can you
    # see this?", the single most common way to ask for a look in English.
    # Only its parenthetical and idiomatic forms are.
    r"\b(?:i|we|they)\s+see\b"
    r"|\bas\s+you\s+can\s+see\b|\byou\s+see\s*,|\bsee\s+you\b"
    r"|\blet(?:'|)s\s+see\b|\bwe(?:'|)ll\s+see\b|\bsee\s+if\b|\bsee\s+whether\b"
    r"|\blook(?:s|ed|ing)?\s+(?:for|into|up|like|after|forward)\b"
    r"|\bloo?ks?\s+(?:good|bad|fine|great|ok|okay)\b"
    r"|\bsee\s+(?:to\s+it|about)\b"
    # --- German --- i18n-allow: German speech-input matching data
    # "mal sehen" is NOT masked: "kannst du mal sehen?" is one of the two
    # phrasings this feature is specified around. On its own it matches no
    # request pattern anyway, so masking it would only cost the real request.
    r"|\bschauen\s+wir\b|\bsehen\s+wir\b"
    r"|\bwir\s+sehen\b|\bich\s+sehe\b|\bich\s+seh\b|\bwie\s+gesagt\b"
    r"|\bsieht\s+(?:aus|so\s+aus|gut\s+aus|schlecht\s+aus)\b|\baussehen\b"
    r"|\bsieh\s+zu,?\s+dass\b|\bschau\s+(?:nach|ob)\b"  # i18n-allow: DE input
    # --- Spanish --- i18n-allow: Spanish speech-input matching data
    r"|\bvamos\s+a\s+ver\b|\ba\s+ver\s+si\b|\bya\s+veo\b|\bveremos\b"
    r"|\bse\s+ve\b|\bmira\s+que\b|\bmira,?\s+(?:pero|es\s+que)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    """Casefold, transliterate, then blank out non-visual idioms.

    Substitutes a space rather than the empty string so surrounding word
    boundaries survive and a genuine request in the same sentence still
    matches.
    """
    folded = (text or "").casefold().translate(_TRANSLITERATION)
    return _IDIOM_NOISE_RE.sub(" ", folded)


# --------------------------------------------------------------------------
# Level 1 — window-scoped, unambiguous
# --------------------------------------------------------------------------

# A window/tab/dialog/app noun bound to a demonstrative or "active/current".
# The demonstrative is mandatory: a bare "window" is a common noun in every one
# of these languages ("a window of opportunity", "Fenster zum Hof"), and the
# whole point of this level is that the user pointed at something specific.
_WINDOW_SCOPE_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # --- English ---
    r"\b(?:this|that|the\s+(?:active|current|front|open))\s+"
    r"(?:window|tab|dialog|dialogue|app|application|document|file|page|sheet)\b"
    r"|\bin\s+(?:this|that)\s+(?:window|tab|dialog|app|document|page)\b"
    r"|\bthe\s+(?:window|tab|dialog|app)\s+(?:i(?:'|)m|i\s+am)\s+(?:in|on)\b"
    # --- German --- i18n-allow: German speech-input matching data
    r"|\b(?:diese[srmn]?|das|der|die)\s+"  # i18n-allow: DE input
    r"(?:fenster|tab|reiter|dialog|dialogfeld|anwendung|programm|dokument|seite)\b"
    r"(?=\s|$|[.,!?])"
    r"|\bim\s+(?:aktiven|aktuellen|offenen|vorderen)\s+fenster\b"
    r"|\bin\s+(?:diesem|dem)\s+(?:fenster|tab|reiter|dialog|dokument|programm)\b"
    # --- Spanish --- i18n-allow: Spanish speech-input matching data
    r"|\b(?:esta|esa|est[ae]\s+misma)\s+"
    r"(?:ventana|pestana|pagina|aplicacion|hoja)\b"
    r"|\b(?:este|ese)\s+(?:dialogo|documento|programa|archivo)\b"
    r"|\ben\s+(?:esta|esa)\s+(?:ventana|pestana|pagina|aplicacion)\b"
    r")",
    re.IGNORECASE,
)


# The German branch above deliberately requires a word boundary AFTER the noun
# via lookahead, because "die Seite" is also "the side/page of a book" — the
# demonstrative + noun pair is the signal, and a trailing genitive ("die Seite
# des Vertrags") should not scope a capture to a window.


# --------------------------------------------------------------------------
# Level 2 — screen-scoped, unambiguous
# --------------------------------------------------------------------------

_SCREEN_INTENT_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # ---------------- English ----------------
    # look/see verb bound to a deictic or to the screen itself
    r"\b(?:look|looking)\s+at\s+(?:this|that|it|the\s+screen|my\s+screen|the\s+error)\b"
    r"|\btake\s+a\s+look\s+at\s+(?:this|that|it|the|my)\b"
    r"|\bhave\s+a\s+look\b|\bcheck\s+(?:this|that|it)\s+out\b"
    r"|\bcan\s+you\s+see\s+(?:this|that|it|my\s+screen|the\s+screen)\b"
    r"|\bdo\s+you\s+see\s+(?:this|that|it)\b"
    r"|\bsee\s+(?:this|that)\s+(?:here|error|message|window|screen)\b"
    r"|\bwhat\s+(?:do\s+you\s+see|am\s+i\s+looking\s+at)\b"
    # screen nouns with a possessive/demonstrative
    r"|\b(?:on|at)\s+(?:my|the|this)\s+screen\b|\bmy\s+screen\b"
    r"|\bwhat(?:'|)s\s+on\s+(?:my|the)\s+screen\b"
    r"|\b(?:take|make|capture|grab)\s+(?:a\s+)?screen(?:shot|\s+capture)\b"
    r"|\bscreen(?:shot|\s+capture)\s+(?:this|that|my\s+screen|the\s+screen)\b"
    r"|\b(?:this|that)\s+(?:error|error\s+message|message|button|menu|popup)\b"
    # read-out requests
    r"|\bwhat\s+does\s+(?:it|this|that|the\s+\w+)\s+say\b"
    r"|\bread\s+(?:this|that|it|the\s+\w+)\s*(?:out|aloud|to\s+me)?\b"
    r"|\bwhat\s+does\s+(?:this|that)\s+mean\s+(?:here|on\s+screen)\b"

    # ---------------- German ---------------- i18n-allow: German speech-input matching data
    # The two phrasings the feature is specified around, plus their variants.
    # ``dies\w*`` on purpose: "schau dir DIESES Fenster an" is the natural
    # phrasing, and a bare ``dies\b`` would only match the rarer "schau dir
    # dies an" — costing the common case to save nothing.
    r"|\bschau(?:e|st)?\s+(?:dir\s+)?(?:das|dies\w*|es|mal\s+das|hier)\b"
    r"|\bschau\s+mal\b|\bschau\s+her\b|\bguck\s+mal\b|\bguck\s+(?:dir\s+)?das\b"
    r"|\bsieh\s+(?:dir\s+)?(?:das|dies\w*)\b|\bsieh\s+mal\b"
    r"|\bkannst\s+du\s+(?:mal\s+)?(?:kurz\s+)?(?:sehen|schauen|gucken|draufschauen)\b"
    r"|\bkannst\s+du\s+(?:das|dies|es|meinen\s+bildschirm)\s+sehen\b"
    r"|\bsiehst\s+du\s+(?:das|dies|meinen\s+bildschirm|den\s+fehler)\b"  # i18n-allow: DE input
    r"|\bwas\s+siehst\s+du\b"
    # screen nouns
    r"|\bauf\s+(?:dem|meinem)\s+bildschirm\b|\bam\s+bildschirm\b|\bmein\s+bildschirm\b"
    # i18n-allow: German speech-input matching data
    r"|\b(?:mach|mache|erstelle|nimm)\s+(?:bitte\s+)?(?:einen\s+)?screenshot\b"
    r"|\bbildschirmfoto\s+(?:machen|aufnehmen|erstellen)\b"  # i18n-allow: DE input
    r"|\bauf\s+dem\s+schirm\b|\bhier\s+auf\s+dem\b"  # i18n-allow: DE input
    r"|\b(?:diese|die)\s+fehlermeldung\b"  # i18n-allow: DE input
    r"|\b(?:dieser|der)\s+fehler\s+(?:hier|da)\b"  # i18n-allow: DE input
    r"|\b(?:dieser|der)\s+knopf\b|\b(?:dieses|das)\s+menue\b"  # i18n-allow: DE input
    # read-out requests
    r"|\bwas\s+steht\s+(?:da|hier|dort|drin)\b|\bda\s+steht\b"
    r"|\blies\s+(?:mir\s+)?(?:das|dies|es|die\s+\w+)\b|\bvorlesen\b"  # i18n-allow: DE input
    r"|\bwas\s+ist\s+das\s+(?:hier|da\s+auf)\b"  # i18n-allow: DE input

    # ---------------- Spanish ---------------- i18n-allow: Spanish speech-input matching data
    r"|\bmira\s+(?:esto|eso|esta|la\s+pantalla|mi\s+pantalla|el\s+error)\b"
    r"|\bmirar\s+(?:esto|eso)\b|\becha(?:le)?\s+un\s+vistazo\b"
    r"|\bpuedes\s+ver\s+(?:esto|eso|mi\s+pantalla|la\s+pantalla)\b"
    r"|\bves\s+(?:esto|eso|mi\s+pantalla)\b|\bque\s+ves\b"
    r"|\ben\s+(?:mi|la)\s+pantalla\b|\bmi\s+pantalla\b"
    r"|\b(?:haz|toma|captura)\s+(?:una\s+)?captura\s+de\s+pantalla\b"
    r"|\bque\s+dice\s+(?:ahi|aqui|esto|eso)\b"
    r"|\blee\s+(?:esto|eso|el\s+\w+)\b"
    r"|\beste\s+error\b|\besta\s+ventana\s+de\s+error\b"
    r")",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Level 3 — weak signals: ask, never capture
# --------------------------------------------------------------------------

# A bare deictic with no visual anchor, or an objectless look-verb. These are
# the sentences a capture-happy heuristic gets wrong in BOTH directions, so
# they earn a question rather than a guess.
_AMBIGUOUS_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # --- English ---
    r"\bwhat\s+(?:is|'s)\s+(?:this|that)\b(?!\s+(?:mean|word|called))"
    r"|\bwhat\s+about\s+(?:this|that)\b|\band\s+(?:this|that)\s+one\b"
    r"|\bwhy\s+is\s+(?:this|that)\b|\bis\s+(?:this|that)\s+(?:right|correct|ok|okay)\b"
    r"|\bcan\s+you\s+(?:check|have\s+a\s+look)\b|\bcheck\s+(?:this|that)\b"
    r"|\bwhat\s+does\s+(?:this|that)\s+mean\b"
    # --- German --- i18n-allow: German speech-input matching data
    r"|\bwas\s+ist\s+(?:das|dies)\b(?!\s+(?:fuer\s+ein|denn\s+fuer))"  # i18n-allow: DE input
    r"|\bund\s+(?:das|dies)\s*(?:da|hier)?\s*[.?!]?\s*$"
    r"|\bwarum\s+ist\s+(?:das|dies)\b|\bstimmt\s+(?:das|dies)\b"  # i18n-allow: DE input
    r"|\bkannst\s+du\s+(?:das|dies)\s+(?:mal\s+)?(?:pruefen|checken|kontrollieren)\b"
    r"|\bwas\s+(?:bedeutet|heisst)\s+(?:das|dies)\b"
    r"|\bschau\s+(?:da\s+)?(?:mal\s+)?drauf\b"
    # --- Spanish --- i18n-allow: Spanish speech-input matching data
    r"|\bque\s+es\s+(?:esto|eso)\b|\by\s+(?:esto|eso)\b"
    r"|\bpor\s+que\s+es\s+(?:esto|eso)\b|\besta\s+bien\s+(?:esto|eso)\b"
    r"|\bpuedes\s+(?:revisar|comprobar)\b|\bque\s+significa\s+(?:esto|eso)\b"
    r")",
    re.IGNORECASE,
)


def _evidence(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    """Up to ``_MAX_EVIDENCE`` matched fragments, whitespace-collapsed."""
    found: list[str] = []
    for match in pattern.finditer(text):
        fragment = " ".join(match.group(0).split())
        if fragment and fragment not in found:
            found.append(fragment)
        if len(found) >= _MAX_EVIDENCE:
            break
    return tuple(found)


def classify(text: str, *, locale: str = "") -> IntentVerdict:
    """Classify one user turn's visual intent.

    Precedence is most-specific-first: a window-scoped reference wins over a
    plain screen reference (it says *which* surface), and any unambiguous
    reference wins over a weak one. An empty or whitespace-only turn is
    ``NONE`` — non-conversational callers (REST, a scheduled task) reach the
    service without an utterance and must not be read as having asked to look.
    """
    normalized = _normalize(text).strip()
    if not normalized:
        return IntentVerdict(intent=VisualIntent.NONE, locale=locale)

    window_hits = _evidence(_WINDOW_SCOPE_RE, normalized)
    screen_hits = _evidence(_SCREEN_INTENT_RE, normalized)

    # A window noun alone is only a scope, not a request. It upgrades an
    # unambiguous screen request to window scope, and on its own ("close this
    # window") it is a desktop command for Computer-Use, not a look request.
    if window_hits and screen_hits:
        return IntentVerdict(
            intent=VisualIntent.WINDOW,
            evidence=(window_hits + screen_hits)[:_MAX_EVIDENCE],
            locale=locale,
        )
    if screen_hits:
        return IntentVerdict(
            intent=VisualIntent.SCREEN, evidence=screen_hits, locale=locale
        )

    ambiguous_hits = _evidence(_AMBIGUOUS_RE, normalized)
    # A window reference with no look verb is weak, not absent: "is this tab
    # right?" plausibly wants a look. Ask.
    if window_hits or ambiguous_hits:
        return IntentVerdict(
            intent=VisualIntent.AMBIGUOUS,
            evidence=(ambiguous_hits + window_hits)[:_MAX_EVIDENCE],
            locale=locale,
        )

    return IntentVerdict(intent=VisualIntent.NONE, locale=locale)


# --------------------------------------------------------------------------
# The clarifying question
# --------------------------------------------------------------------------

# One entry per supported locale, keyed by the BCP-47 base tag. Adding a locale
# means adding a line here AND to the matcher above — never a de/en-only table
# (CLAUDE.md §1.3). The phrasing is deliberately a yes/no question about
# looking, so a bare "yes" in the next turn is a complete answer.
# i18n-allow: this IS the runtime multilingual product surface (§1 category 1)
_CLARIFY_QUESTION: dict[str, str] = {
    "en": "Should I take a look at your screen?",
    "de": "Soll ich einmal auf deinen Bildschirm schauen?",  # i18n-allow: spoken
    "es": "¿Quieres que eche un vistazo a tu pantalla?",
}

_CANCELLED_REPLY: dict[str, str] = {
    "en": "Okay, I won't look at your screen.",
    "de": "Okay, ich schaue nicht auf deinen Bildschirm.",  # i18n-allow: spoken
    "es": "De acuerdo, no miraré tu pantalla.",
}

_NO_VISION_PROVIDER_REPLY: dict[str, str] = {
    "en": (
        "I captured the screen, but none of your available assistants can "
        "inspect images right now. Connect a vision-capable provider in API Keys."
    ),
    "de": (
        "Ich habe den Bildschirm aufgenommen, aber keiner deiner verfügbaren "  # i18n-allow
        "Assistenten kann gerade Bilder auswerten. Verbinde unter API-Keys "  # i18n-allow
        "einen Anbieter mit Bildverarbeitung."  # i18n-allow
    ),  # i18n-allow: spoken
    "es": (
        "Capturé la pantalla, pero ninguno de tus asistentes disponibles puede "
        "analizar imágenes ahora. Conecta un proveedor con visión en Claves API."
    ),
}

_CAPTURE_FAILURE_REPLY: dict[str, str] = {
    "en": (
        "I could not inspect the screen safely because Screen Context failed. "
        "No screenshot was attached."
    ),
    "de": (
        "Ich konnte den Bildschirm nicht sicher prüfen, weil der "  # i18n-allow
        "Bildschirmkontext fehlgeschlagen ist. Es wurde kein Screenshot "  # i18n-allow
        "angehängt."  # i18n-allow
    ),  # i18n-allow: spoken
    "es": (
        "No pude revisar la pantalla de forma segura porque falló el contexto "
        "de pantalla. No se adjuntó ninguna captura."
    ),
}

#: Fallback locale for a language with no entry — the repo-wide default.
_FALLBACK_LOCALE = "en"


def clarifying_question(locale: str) -> str:
    """The question Jarvis asks on an ``AMBIGUOUS`` verdict, in ``locale``.

    Takes an ALREADY-RESOLVED locale. This function must never re-derive the
    output language from the utterance — that is
    ``jarvis.core.turn_language.resolve_output_language``'s single job, and a
    second derivation here is exactly the mid-session language flip §1.3
    forbids.
    """
    base = (locale or "").split("-")[0].split("_")[0].strip().lower()
    return _CLARIFY_QUESTION.get(base) or _CLARIFY_QUESTION[_FALLBACK_LOCALE]


def cancelled_reply(locale: str) -> str:
    """The localized outcome after the user vetoes a proposed screen look."""
    base = (locale or "").split("-")[0].split("_")[0].strip().lower()
    return _CANCELLED_REPLY.get(base) or _CANCELLED_REPLY[_FALLBACK_LOCALE]


def no_vision_provider_reply(locale: str) -> str:
    """Honest fallback when no configured brain can inspect an attached image."""
    base = (locale or "").split("-")[0].split("_")[0].strip().lower()
    return (
        _NO_VISION_PROVIDER_REPLY.get(base)
        or _NO_VISION_PROVIDER_REPLY[_FALLBACK_LOCALE]
    )


def capture_failure_reply(locale: str) -> str:
    """Honest fail-closed reply for an unexpected Screen Context defect."""
    base = (locale or "").split("-")[0].split("_")[0].strip().lower()
    return _CAPTURE_FAILURE_REPLY.get(base) or _CAPTURE_FAILURE_REPLY[_FALLBACK_LOCALE]


#: Locales with a clarifying question. Used by the parity test that pins this
#: table to the supported-locale list, so a new locale cannot ship half-wired.
SUPPORTED_CLARIFY_LOCALES: frozenset[str] = frozenset(_CLARIFY_QUESTION)


__all__ = [
    "SUPPORTED_CLARIFY_LOCALES",
    "cancelled_reply",
    "capture_failure_reply",
    "clarifying_question",
    "classify",
    "no_vision_provider_reply",
]
