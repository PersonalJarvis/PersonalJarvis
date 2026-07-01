"""Turn-language detection and resolution (de / en / es).

Live forensic 2026-06-10 23:12 (data/jarvis_desktop.log): ``[stt].language``
pins Groq Whisper to German, and Whisper echoes the pin back in its response —
so every transcript was tagged ``language=german``, including pure-English
speech (``text="What's weather like tomorrow?" language=German``). Every
consumer trusting that tag (TTS voice pin, ack-brain language, spoken fallback
phrases) then spoke German to an English speaker.

This module is the single source of truth for the language of a turn:

* :func:`detect_text_language` — cheap token-overlap heuristic over the
  transcribed text. Returns a code only when the text is clearly one language;
  ``"unknown"`` otherwise (single proper nouns, "ok", ...). Tokens shared by
  two of the three languages ("in", "an", "es", "was", "me", "no", "a") are
  deliberately excluded from all sets — the historical "'in' counts as EN"
  trap.
* :func:`normalize_language_tag` — maps the two tag shapes seen live (Whisper
  language NAMES like ``"german"`` from the cloud API, ISO codes like ``"de"``
  from local faster-whisper, BCP-47 like ``"de-DE"``) to plain codes, so
  downstream maps such as ``{"de": "de-DE"}.get(lang)`` stop silently missing.
* :func:`resolve_turn_language` — text wins when decisive, the STT tag breaks
  ties, an explicit default comes last.

Pure regex / set lookups — no LLM, no IO. Safe on the voice critical path
(AP-9 / AP-11).
"""
from __future__ import annotations

import re

__all__ = [
    "DEFAULT_LOCALE",
    "detect_text_language",
    "is_substantive_turn",
    "normalize_language_tag",
    "resolve_output_language",
    "resolve_turn_language",
]

#: The fallback spoken/written language for a turn whose language cannot be
#: detected AND no ``brain.reply_language`` pin is set. ONE shared constant so
#: every output layer agrees on the auto-mode default instead of each hardcoding
#: its own (the historical "pipeline defaults en, action phrases default de"
#: split that let two layers diverge on the same ambiguous turn).
DEFAULT_LOCALE = "en"

#: The codes an explicit ``brain.reply_language`` pin may carry (``"auto"`` is
#: deliberately absent — it means "no pin, mirror the input").
_REPLY_PINS: frozenset[str] = frozenset({"de", "en", "es"})

#: A turn with at most this many word tokens is a "thin" turn — a one- or
#: two-word interjection ("Now", "Stop now", "jetzt", a lone loanword). A thin
#: turn must NOT redefine an established conversation's language; it is spoken in
#: the conversation language instead. Only a longer (substantive) turn may switch
#: the conversation. Natural-flow forensic 2026-06-18: a single English "Now" in
#: a German voice chat flipped the whole turn to English.
_THIN_TURN_MAX_TOKENS = 2

_TOKEN_RE = re.compile(r"\b[\w']+\b", re.UNICODE)

# Strong script signals: umlauts/ß occur in German only; inverted punctuation  # i18n-allow: English comment; literal umlaut/ß characters named for illustration only
# and accented vowels (minus the pan-European é) point to Spanish.
_DE_SCRIPT_RE = re.compile(r"[äöüÄÖÜß]")  # i18n-allow: German-script detection regex, matched in logic (core of the language resolver)
_ES_SCRIPT_RE = re.compile(r"[áíóúñÁÍÓÚÑ¿¡]")

# Function-word sets, kept mutually disjoint. Words common to more than one of
# the three languages are excluded on purpose (see module docstring).
_DE_TOKENS: frozenset[str] = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem",  # i18n-allow
    "und", "oder", "aber", "nicht", "ist", "sind", "bin", "bist", "wird",  # i18n-allow
    "ich", "du", "wir", "ihr", "mir", "mich", "dir", "dich", "uns", "euch",  # i18n-allow
    "bitte", "danke", "wie", "wo", "wann", "warum", "wieso", "welche",  # i18n-allow
    "kann", "kannst", "koennen", "können", "soll", "sollst", "muss", "musst",  # i18n-allow
    "mach", "mache", "machst", "macht", "oeffne", "öffne", "zeig", "zeige",  # i18n-allow
    "lies", "sag", "schreib", "heute", "morgen", "jetzt", "gleich", "schon",  # i18n-allow
    "noch", "auch", "doch", "mal", "sehr", "gut", "ja", "nein", "kein",  # i18n-allow
    "keine", "mein", "meine", "dein", "deine", "fuer", "für", "mit", "von",  # i18n-allow
    "auf", "aus", "bei", "nach", "ueber", "über", "wetter", "geht", "gibt",  # i18n-allow
})
_EN_TOKENS: frozenset[str] = frozenset({
    "the", "and", "you", "your", "that", "this", "these", "those", "with",
    "for", "what", "what's", "whats", "how", "when", "where", "why", "who",
    "which", "can", "can't", "could", "couldn't", "would", "should", "will",
    "won't", "please", "tell", "give", "show", "open", "read", "write",
    "is", "isn't", "are", "aren't", "do", "does", "did", "don't", "doesn't",
    "it", "it's", "its", "like", "today", "tomorrow", "tonight", "now",
    "yes", "thanks", "thank", "hello", "of", "to", "from", "my", "mine",
    "our", "have", "has", "had", "want", "need", "get", "got", "going",
    "be", "been", "i'm", "i", "we", "they", "he", "she", "weather",
    "yesterday",
})
_ES_TOKENS: frozenset[str] = frozenset({
    "el", "la", "los", "las", "un", "una", "unos", "unas", "qué", "que",
    "cómo", "como", "cuándo", "cuando", "dónde", "donde", "quién", "quien",
    "por", "favor", "para", "hace", "hacer", "hoy", "mañana", "ahora",
    "está", "estás", "estoy", "tiempo", "gracias", "hola", "sí", "puedes",
    "puedo", "quiero", "necesito", "dime", "dame", "abre", "muestra", "lee",
    "escribe", "y", "pero", "con", "del", "al", "muy", "bien", "clima",
})

_SETS: tuple[tuple[str, frozenset[str]], ...] = (
    ("de", _DE_TOKENS),
    ("en", _EN_TOKENS),
    ("es", _ES_TOKENS),
)

# Whisper cloud APIs return language NAMES; local faster-whisper returns ISO
# codes; some TTS configs use BCP-47. All collapse to de/en/es here.
_TAG_TO_CODE: dict[str, str] = {
    "de": "de", "deu": "de", "ger": "de", "german": "de", "deutsch": "de",
    "en": "en", "eng": "en", "english": "en", "englisch": "en",
    "es": "es", "spa": "es", "spanish": "es", "spanisch": "es",
    "espanol": "es", "español": "es", "castellano": "es",
}


def normalize_language_tag(tag: object) -> str:
    """Collapse an STT/TTS language tag to ``de``/``en``/``es``/``unknown``."""
    if not tag:
        return "unknown"
    head = str(tag).strip().lower().replace("_", "-").split("-", 1)[0]
    return _TAG_TO_CODE.get(head, "unknown")


def detect_text_language(text: str) -> str:
    """Classify *text* as ``de``/``en``/``es`` — or ``unknown`` when unclear.

    A language must score strictly higher than both others to win; ties and
    zero-overlap text (proper nouns, "ok") return ``"unknown"`` so the caller
    can fall back to the STT tag.
    """
    t = (text or "").strip()
    if not t:
        return "unknown"
    tokens = {tok.lower() for tok in _TOKEN_RE.findall(t)}
    scores = {code: len(tokens & vocab) for code, vocab in _SETS}
    if _DE_SCRIPT_RE.search(t):
        scores["de"] += 2
    if _ES_SCRIPT_RE.search(t):
        scores["es"] += 2
    best_code, best = max(scores.items(), key=lambda kv: kv[1])
    if best == 0 or sum(1 for s in scores.values() if s == best) > 1:
        return "unknown"
    return best_code


def resolve_turn_language(
    stt_language: object, text: str, *, default: str = "en"
) -> str:
    """Resolve the language of a turn: text first, STT tag second, default last.

    The transcribed text is the most reliable signal — the STT tag merely
    echoes a configured pin when ``[stt].language`` is set (the 2026-06-10
    live bug). Only ambiguous text defers to the tag.
    """
    detected = detect_text_language(text)
    if detected != "unknown":
        return detected
    code = normalize_language_tag(stt_language)
    if code != "unknown":
        return code
    return default


def is_substantive_turn(text: str) -> bool:
    """True if *text* is long enough to (re)define the conversation language.

    A one- or two-word interjection ("Now", "Stop now", "jetzt", a lone
    loanword) is NOT substantive — it inherits the running conversation language
    rather than switching it. Used by the conversation-stickiness logic so a
    stray English word never flips an established German chat (forensic
    2026-06-18).
    """
    return len(_TOKEN_RE.findall(text or "")) > _THIN_TURN_MAX_TOKENS


def resolve_output_language(
    reply_language: object,
    stt_language: object,
    text: str,
    *,
    default: str = DEFAULT_LOCALE,
    conversation_language: object = "",
) -> str:
    """The SINGLE authoritative output language for one turn (de/en/es).

    Every spoken or written layer — the deep-brain reply, the ack-brain
    preamble, spawn announcements, every canned status / error / clarify /
    timeout / provider-down phrase, the deterministic Computer-Use readbacks,
    and the TTS voice pin — must resolve language through THIS function so no
    layer can diverge from another (CLAUDE.md "Runtime Output Language";
    2026-06-18 forensic).

    Precedence, highest first:

    1. an explicit ``brain.reply_language`` pin (``de``/``en``/``es``) — the
       user-selected language wins over everything, including what STT heard;
    2. else, in auto mode, conversation stickiness: a "thin" turn (a one- or
       two-word interjection like "Now"/"Stop"/"jetzt", or a lone loanword) is
       spoken in ``conversation_language`` — it must NOT flip an established
       conversation. Only a substantive turn may switch the language;
    3. else the detected input language of the turn (``resolve_turn_language``:
       text heuristic first, STT tag breaks ties), an ambiguous substantive turn
       inheriting ``conversation_language`` when one is set;
    4. else the configured ``default`` locale (``DEFAULT_LOCALE``).

    ``reply_language`` is tolerant: case/whitespace-insensitive, and any value
    that is not a pin (``"auto"``, ``""``, ``None``, a typo) means "no pin —
    mirror the input". ``conversation_language`` (de/en/es) is the language of
    the conversation so far; pass ``""`` when none is established yet.
    """
    pin = str(reply_language or "").strip().lower()
    if pin in _REPLY_PINS:
        return pin
    conv = str(conversation_language or "").strip().lower()
    conv = conv if conv in _REPLY_PINS else ""
    if conv and len(_TOKEN_RE.findall(text or "")) <= _THIN_TURN_MAX_TOKENS:
        return conv
    return resolve_turn_language(stt_language, text, default=(conv or default))
