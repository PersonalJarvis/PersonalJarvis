"""Deterministic wiki-write intent matcher (spec A1).

Explicit "write this to the wiki" commands must never depend on the router
LLM choosing the ``wiki-ingest`` tool. This matcher runs on the final user
transcript in the brain's fast-path pre-pass and fires the ingest pipeline
model-independently.

Pure regex -- no LLM and no IO (AP-9/AP-11). The de/en/es tokens are speech
recognition input vocabulary (closed-list category 3). Precision remains the
priority: every match requires both a write verb and an explicit wiki target.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Input normalization for supported speech languages.  # i18n-allow: literal input vocabulary
_TRANSLITERATION = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",  # i18n-allow
    "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
})

# Write verbs (normalized, de/en/es).  # i18n-allow: input vocabulary
_WRITE_VERB_RE = re.compile(
    r"\b(?:"
    r"schreib\w*|notier\w*|speicher\w*|merk(?:e|st|t)?\s+(?:es\s+)?dir|"
    r"eintrag\w*|trag\w*|vermerk\w*|hinzufueg\w*|fueg\w*|"  # i18n-allow
    r"write|save|note(?:\s+down)?|add|store|put|record|enter|update|"
    r"escrib\w*|guard\w*|anot\w*|apunt\w*|agreg\w*|anad\w*|"
    r"registr\w*|actualiz\w*|pon"
    r")\b",
    re.IGNORECASE,
)

_DE_POSSESSIVE = r"(?:mein|dein|unser|euer|ihr)(?:e[msn]?)?"
_WIKI_NOUN = r"wiki(?:[\s-]?system)?"

# A target must be grammatically marked as the destination/object. This keeps
# source/recall clauses such as "what the wiki says" out of the write path.
# The tokens are de/en/es speech-input vocabulary.  # i18n-allow
_WIKI_TARGET_RE = re.compile(
    rf"\b(?:"
    rf"(?:ins|im|zum)\s+(?:{_DE_POSSESSIVE}\s+)?{_WIKI_NOUN}|"
    rf"in\s+(?:das|{_DE_POSSESSIVE})\s+{_WIKI_NOUN}|"
    rf"(?:to|in|into)\s+(?:(?:the|my|your)\s+)?{_WIKI_NOUN}|"
    rf"(?:en|al|a)\s+(?:(?:la|el|mi|tu)\s+)?{_WIKI_NOUN}|"
    rf"(?:das|{_DE_POSSESSIVE}|the|my|your|la|el|mi|tu)\s+{_WIKI_NOUN}"
    rf")\b",
    re.IGNORECASE,
)

# Polite question grammar may still express a command.
# The tokens are speech-input vocabulary.  # i18n-allow
_POLITE_PREFIX_RE = re.compile(
    r"^[¿¡\s]*(?:(?:hey\s+)?jarvis(?:[,\s]+|$))?"
    r"(?:(?:(?:kannst|koenntest|wuerdest)\s+du(?:\s+mir)?(?:\s+bitte)?|"
    r"(?:can|could|would|will)\s+you(?:\s+please)?|"
    r"(?:puedes|podrias)(?:\s+por\s+favor)?)(?:[,\s]+|$))?"
    r"(?:(?:bitte|please|por\s+favor)(?:[,\s]+|$))?",
    re.IGNORECASE,
)

# Information questions remain reads even if they mention a write verb later,
# for example "What should I write in my wiki?".  # i18n-allow: input vocabulary
_INFORMATION_QUESTION_RE = re.compile(
    r"^(?:was|wer|wie|wo|wann|warum|welch\w*|what|who|how|where|when|why|"
    r"which|que|quien|como|donde|cuando|cual)\b",
    re.IGNORECASE,
)

# Explicit references to the immediately preceding spoken turn. These are
# anaphoric even though they contain nouns, so the caller must supply bounded
# current-session history rather than storing the literal words "last
# transcript".  # i18n-allow: multilingual speech-input vocabulary
_LATEST_TRANSCRIPT_RE = re.compile(
    r"\b(?:"
    r"(?:die\s+)?letzte\w*\s+(?:transkription|transkript|aussage|nachricht|turn)|"  # i18n-allow: German speech-input vocabulary
    r"(?:das\s+)?zuletzt\s+gesagte|"
    r"(?:the\s+)?(?:last|latest|previous)\s+(?:transcript|utterance|message|turn)|"
    r"(?:la\s+)?ultima\w*\s+(?:transcripcion|mensaje|turno)"
    r")\b",
    re.IGNORECASE,
)

# Anaphoric objects refer to content from the prior conversation exchange.  # i18n-allow
_ANAPHORA = frozenset({
    "das", "es", "dies", "diese", "dieses", "den", "die", "etwas",  # i18n-allow
    "that", "this", "it", "them", "something",
    "eso", "esto", "lo", "la", "algo",
})

_FILLER_WORDS = frozenset({
    "bitte", "mal", "doch", "kurz", "please", "por", "favor", "que",
    "dass", "ein", "hinzu",  # i18n-allow: input vocabulary
})

_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:bitte|mal|doch|kurz|please|por\s+favor|que|dass)\b|[,;:])+\s*",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class WikiIntentMatch:
    #: Inline content to ingest; ``None`` means the caller must supply the
    #: previous conversation exchange.
    content: str | None
    #: Full matched utterance after normalization, for diagnostics.
    matched: str


def _normalize(text: str) -> str:
    normalized = text.strip().lower().translate(_TRANSLITERATION)
    return re.sub(r"\s+", " ", normalized)


def _strip_leading_filler(fragment: str) -> str:
    previous = None
    cleaned = fragment.strip()
    while previous != cleaned:
        previous = cleaned
        cleaned = _LEADING_FILLER_RE.sub("", cleaned).strip()
    return cleaned


def _words(fragment: str) -> list[str]:
    return _WORD_RE.findall(fragment.lower())


def _only_control_words(fragment: str) -> bool:
    return all(word in _ANAPHORA or word in _FILLER_WORDS for word in _words(fragment))


def _content_after_verb(fragment: str, verb: str) -> str | None:
    content = fragment.strip(" \t,;:")
    # German separable verbs put the particle after the target:
    # "trag das ins Wiki ein, dass ...".  # i18n-allow: input syntax example
    if verb.startswith("trag"):
        content = re.sub(r"^ein\b\s*[,;:]?\s*", "", content)
    elif verb.startswith("fueg"):
        content = re.sub(r"^hinzu\b\s*[,;:]?\s*", "", content)
    content = _strip_leading_filler(content).rstrip(" \t.?!")
    meaningful = [
        word for word in _words(content)
        if word not in _ANAPHORA and word not in _FILLER_WORDS
    ]
    return content if meaningful else None


def _match_verb_before_target(
    body: str,
    target: re.Match[str],
) -> tuple[re.Match[str], str | None] | None:
    for verb in _WRITE_VERB_RE.finditer(body, 0, target.start()):
        if not _only_control_words(body[:verb.start()]):
            continue
        if not _only_control_words(body[verb.end():target.start()]):
            continue
        return verb, _content_after_verb(body[target.end():], verb.group(0))
    return None


def _match_target_before_verb(
    body: str,
    target: re.Match[str],
) -> tuple[re.Match[str], str | None] | None:
    if not _only_control_words(body[:target.start()]):
        return None
    for verb in _WRITE_VERB_RE.finditer(body, target.end()):
        if not _only_control_words(body[target.end():verb.start()]):
            continue
        return verb, _content_after_verb(body[verb.end():], verb.group(0))
    return None


def match_wiki_intent(user_text: str) -> WikiIntentMatch | None:
    """Return a match for an explicit wiki-write command, otherwise ``None``."""
    norm = _normalize(user_text)
    if not norm or len(norm) > 600:
        return None

    question_probe = norm.lstrip("¿¡ ")
    if _INFORMATION_QUESTION_RE.match(question_probe):
        return None

    body = _POLITE_PREFIX_RE.sub("", norm, count=1).strip()
    if not body:
        return None

    # A longer wrapper such as "After looking at the last transcript, could you
    # put it in my Wiki?" need not fit the compact command grammar below. The
    # three explicit signals (latest-turn reference + Wiki target + write verb)
    # make this an unambiguous anaphoric write without extracting prose as data.
    if (
        _LATEST_TRANSCRIPT_RE.search(body)
        and _WIKI_TARGET_RE.search(body)
        and _WRITE_VERB_RE.search(body)
    ):
        return WikiIntentMatch(content=None, matched=norm)

    for target in _WIKI_TARGET_RE.finditer(body):
        matched = _match_verb_before_target(body, target)
        if matched is None:
            matched = _match_target_before_verb(body, target)
        if matched is not None:
            _verb, content = matched
            return WikiIntentMatch(content=content, matched=norm)
    return None
