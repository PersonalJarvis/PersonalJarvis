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
import unicodedata
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "DEFAULT_LOCALE",
    "OutputLanguageValidation",
    "detect_text_language",
    "is_substantive_turn",
    "normalize_language_tag",
    "resolve_output_language",
    "resolve_transcript_language",
    "resolve_turn_language",
    "validate_output_language",
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

# Output validation deliberately ignores code and links. They frequently carry
# English keywords or non-Latin identifiers regardless of the language of the
# surrounding answer, so treating them as prose would create false positives.
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\r\n]*`")
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)

# Han text has a unique script signal. A ratio is still required so a German,
# English, or Spanish sentence containing a Chinese name remains valid.
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z\u00c0-\u024f]")
_MIN_GROSS_HAN_CHARS = 10

# Vietnamese uses Latin script, so script alone is insufficient. The validator
# combines its characteristic letters/tones with a vocabulary signal and only
# returns a verdict when both make a gross mismatch unambiguous.
_VI_DISTINCTIVE_CHAR_RE = re.compile(
    r"[\u0103\u00e2\u0111\u00ea\u00f4\u01a1\u01b0"
    r"\u1ea3\u1ea1\u1eb1\u1eaf\u1eb3\u1eb5\u1eb7"
    r"\u1ea7\u1ea5\u1ea9\u1eab\u1ead\u1ebb\u1ebd\u1eb9"
    r"\u1ec1\u1ebf\u1ec3\u1ec5\u1ec7\u1ec9\u1ecb"
    r"\u1ecf\u1ecd\u1ed3\u1ed1\u1ed5\u1ed7\u1ed9"
    r"\u1edd\u1edb\u1edf\u1ee1\u1ee3\u1ee7\u1ee5"
    r"\u1eeb\u1ee9\u1eed\u1eef\u1ef1\u1ef3\u1ef7\u1ef9\u1ef5]",
    re.IGNORECASE,
)
_VI_TOKENS: frozenset[str] = frozenset(
    """
    bạn bằng các chào chúng có của đã được giúp hãy không là một này người
    những sẽ thể tiếng tôi việt với
    """.split()
)

OutputLanguageStatus = Literal["match", "mismatch", "indeterminate"]


@dataclass(frozen=True, slots=True)
class OutputLanguageValidation:
    """Deterministic verdict for text checked against a resolved turn language.

    ``indeterminate`` is intentionally fail-open: short replies, code, names,
    numbers, and ambiguous prose do not provide enough evidence to suppress
    user-facing output. Only ``mismatch`` is safe to block before text/audio
    release.
    """

    status: OutputLanguageStatus
    resolved_language: str
    detected_language: str

    @property
    def should_block(self) -> bool:
        """Whether the caller should suppress this output and retry/fallback."""
        return self.status == "mismatch"


# Strong script signals: German-specific letters and Spanish punctuation or
# accented vowels (minus the pan-European acute-e) are useful hints.
_DE_SCRIPT_RE = re.compile(r"[äöüÄÖÜß]")  # i18n-allow: German-script regex
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


def _prose_for_output_validation(text: str) -> str:
    """Remove content whose syntax is not evidence of the reply language."""
    prose = unicodedata.normalize("NFC", text or "")
    prose = _CODE_FENCE_RE.sub(" ", prose)
    prose = _INLINE_CODE_RE.sub(" ", prose)
    return _URL_RE.sub(" ", prose).strip()


def _detect_gross_han_output(text: str) -> bool:
    han_count = len(_HAN_RE.findall(text))
    # A short Han span is commonly a person, organization, or product name,
    # including a compact label value. It is not enough evidence to suppress
    # an otherwise correctly resolved answer.
    if han_count < _MIN_GROSS_HAN_CHARS:
        return False
    latin_count = len(_LATIN_LETTER_RE.findall(text))
    script_ratio = han_count / max(1, han_count + latin_count)
    return script_ratio >= 0.55


def _detect_gross_vietnamese_output(text: str, tokens: list[str]) -> bool:
    if len(tokens) < 5:
        return False
    characteristic_chars = len(_VI_DISTINCTIVE_CHAR_RE.findall(text))
    distinct_vocabulary_hits = len(set(tokens) & _VI_TOKENS)
    return characteristic_chars >= 2 and distinct_vocabulary_hits >= 2


def _detect_strong_supported_output(text: str, tokens: list[str]) -> str:
    """Return de/en/es only when multiple independent prose signals agree."""
    if len(tokens) < 4:
        return "unknown"
    scores = {
        code: sum(token in vocabulary for token in tokens)
        for code, vocabulary in _SETS
    }
    if _DE_SCRIPT_RE.search(text):
        scores["de"] += 2
    if _ES_SCRIPT_RE.search(text):
        scores["es"] += 2
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (best_code, best_score), (_, second_score) = ranked[:2]
    if best_score < 3 or best_score == second_score:
        return "unknown"
    return best_code


def validate_output_language(
    text: str,
    *,
    resolved_language: object,
) -> OutputLanguageValidation:
    """Validate reply *text* against an already-resolved ``de``/``en``/``es``.

    The target MUST come from :func:`resolve_output_language`; this function
    never derives or changes the turn language. It only detects high-confidence
    output corruption before text or audio is released. Detection is regex/set
    based, has no IO or model call, and treats uncertain content as
    ``indeterminate`` (non-blocking).
    """
    target = normalize_language_tag(resolved_language)
    if target not in _REPLY_PINS:
        return OutputLanguageValidation("indeterminate", target, "unknown")

    prose = _prose_for_output_validation(text)
    if not prose:
        return OutputLanguageValidation("indeterminate", target, "unknown")

    if _detect_gross_han_output(prose):
        detected = "zh"
    else:
        tokens = [token.lower() for token in _TOKEN_RE.findall(prose)]
        if _detect_gross_vietnamese_output(prose, tokens):
            detected = "vi"
        else:
            detected = _detect_strong_supported_output(prose, tokens)

    if detected == "unknown":
        status: OutputLanguageStatus = "indeterminate"
    elif detected == target:
        status = "match"
    else:
        status = "mismatch"
    return OutputLanguageValidation(status, target, detected)


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


def resolve_transcript_language(reported: object, text: str) -> str:
    """Which language a TRANSCRIPT may be run through text rules as.

    Sibling of :func:`resolve_turn_language`, and deliberately stricter: that
    one answers "which language do we SPEAK back", where a wrong guess sounds
    odd; this one answers "whose word list may DELETE tokens from what the user
    said", where a wrong guess removes content and reports success. Returns a
    code (``de`` / ``en`` / ``es``) or ``"unknown"`` — and ``"unknown"`` means
    *run no language's rules*, never *pick a default*.

    Precedence, and the order is the whole point:

    1. **A tag we cannot place stays unplaced.** ``detect_text_language`` only
       knows three languages, so letting it overrule a "French" or "ja" tag
       would relabel that utterance as whichever of the three it scored highest
       on and then run THAT language's word lists over it. ``unknown`` is the
       honest answer for ~95 of the 100 recognition languages STT supports.
    2. **Otherwise the TEXT outranks a tag that contradicts it.** The cloud
       Whisper APIs report a language on every request and report it
       confidently when it is wrong — the live history has German utterances
       tagged "English", and ``[stt].language`` pins make the provider echo the
       pin back for speech in any language at all (forensic 2026-06-10). A
       filler list applied on that word deletes function words from a sentence
       it has no business touching: German "um" is the English hesitation
       sound, so the transcript came back grammatically broken while the
       cleanup reported a clean success (2026-07-30)::

           "Kümmere dich um das Update" -> "Kümmere dich das Update"  # i18n-allow
    3. **Ambiguous text defers to the tag.** Text too short or too neutral to
       place ("ok", a bare proper noun) leaves the provider's reading standing.

    Pure set lookups, no IO — safe on the voice critical path.
    """
    lowered = str(reported or "").strip().lower()
    tag_code = normalize_language_tag(lowered)
    if not (text or "").strip():
        return tag_code
    detected = detect_text_language(text)
    if detected != "unknown" and (
        # Nothing to contradict: no tag at all, or one that says "I could not
        # tell".
        lowered in ("", "auto", "unknown", "und")
        # A tag we CAN place, which the text disagrees with.
        or (tag_code != "unknown" and tag_code != detected)
    ):
        return detected
    return tag_code


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
