"""Incomplete-prompt completion classifier — structural twin of ``hangup.py``.

A deterministic, stdlib-only detector that decides whether a *finalized* voice
transcript is syntactically open-ended ("Erinnere mich morgen daran, dass" …
silence) and should be held for a continuation, rather than answered.

Design contract (see
``docs/superpowers/specs/2026-05-25-incomplete-prompt-completion-design.md``):

* **Precision over recall / "answer when in doubt".** A complete prompt must
  NEVER be held back. We fire ONLY on a closed, curated set of trailing tokens
  that practically never end a complete sentence.
* **No dictionary, no morphology.** The hard German ambiguity — a separable verb
  prefix that is spelled like a stranded preposition ("Ruf Tom *an*" is complete;
  "Schick die Mail *an*" is dangling) — is not *resolved*, it is *avoided*: every
  ambiguous token (``an/auf/mit/ab/zu/aus/vor/nach/über/durch/von/in/um/unter``)
  is simply absent from every fire-list, so both sentences return ``None``. We
  knowingly miss the genuine dangling case to guarantee zero false holds.
* **English strands prepositions grammatically** ("What's it *for*?"), so the
  English path fires only on conjunctions and determiners, never on a bare
  trailing preposition.
* **``der/die/das`` are excluded** — they are article *or* demonstrative pronoun
  ("Mach *das*" is complete), so only unambiguous noun-requiring determiners fire.

Standard-library only (``re``, ``dataclasses``). Like ``hangup.py`` it must stay
free of ``sounddevice`` and any heavy import so it can run cheaply on the voice
critical path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# --- Verdict reasons (telemetry contract) --------------------------------- #
REASON_CONJUNCTION: Final[str] = "conjunction"
REASON_DETERMINER: Final[str] = "determiner"
REASON_PREPOSITION: Final[str] = "preposition"
# Live regression 2026-05-26: a user pausing mid-task at a comma was the most
# common continuation signal observed (one task → multiple sub-agents). The
# stricter trailing-token signals above never fire on it because the last token
# is usually a complete verb / noun before the comma. Checked AFTER the more
# specific reasons so "...send it to Tom and," still reports CONJUNCTION.
REASON_TRAILING_COMMA: Final[str] = "trailing_comma"


@dataclass(frozen=True)
class IncompleteVerdict:
    """Result of a positive incompleteness classification.

    ``reason`` is one of the ``REASON_*`` constants (for telemetry / logging),
    ``marker`` is the trailing token that triggered the verdict, and
    ``language`` is the detected (or hinted) language code.
    """

    reason: str
    marker: str = ""
    language: str = ""


# --- Fire-lists (closed, curated) ----------------------------------------- #
# Conjunctions are language-agnostic: none of them legitimately ends a complete
# utterance, and there is no cross-language collision. Deliberately EXCLUDED:
# ``denn`` / ``dann`` / ``so`` / ``then`` / ``while`` / ``for`` — these double as
# particles/prepositions that DO end a sentence ("Was ist denn", "Bis dann",
# "I think so", "Who is this for").
_CONJUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        # German subordinating
        "dass", "weil", "damit", "sodass", "sobald", "ob", "falls", "obwohl",
        "wenn", "bevor", "nachdem", "seitdem", "indem", "wohingegen",
        # German coordinating
        "und", "oder", "aber", "sondern",
        # English subordinating
        "that", "because", "whether", "although", "unless", "until", "if",
        # English coordinating
        "and", "or", "but", "nor",
    }
)

# Noun-requiring determiners. ``der/die/das`` excluded (article ↔ demonstrative
# pronoun). German and English are kept separate because the English article
# ``an`` collides with the German separable prefix ``an`` — only the English
# branch may fire on it.
_DE_DETERMINERS: Final[frozenset[str]] = frozenset(
    {
        "ein", "eine", "einen", "einem", "einer", "eines",
        "mein", "meine", "meinen", "meinem", "meiner",
        "dein", "deine", "deinen", "deinem",
        "sein", "seine", "seinen", "seinem",
        "kein", "keine", "keinen", "keinem", "keiner",
        "dem", "den", "des",
    }
)
_EN_DETERMINERS: Final[frozenset[str]] = frozenset({"the", "a", "an"})

# Unambiguous German prepositions: not separable verb prefixes, and German does
# not strand prepositions. EXCLUDED: an/auf/aus/ab/mit/nach/vor/zu/in/um/über/
# unter/durch/von (all prefix- or particle-ambiguous).
_DE_PREPOSITIONS: Final[frozenset[str]] = frozenset(
    {
        "für", "gegen", "ohne", "bei", "wegen", "trotz", "während",
        "innerhalb", "außerhalb", "bezüglich", "seit", "aufgrund",
        "mithilfe", "anhand", "statt", "samt", "gemäß", "laut",
    }
)

# --- Lightweight language markers (only to disambiguate the ``an`` collision) #
# ``an`` is intentionally NOT a marker so it never self-biases the detection.
_EN_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "i", "you", "he", "she", "it", "we", "they", "the", "a", "is", "are",
        "am", "was", "were", "do", "does", "did", "can", "could", "will",
        "would", "should", "what", "who", "when", "where", "why", "how", "me",
        "my", "your", "this", "that", "these", "those", "to", "of", "for",
        "with", "about", "at", "and", "or", "but", "play", "open", "give",
        "need", "want", "time", "come", "looking", "some", "music", "remind",
        "send", "take", "know", "whether", "tomorrow",
    }
)
_DE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "ich", "du", "er", "sie", "es", "wir", "ihr", "der", "die", "das",
        "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "und",
        "oder", "aber", "mach", "ruf", "gib", "hör", "spiel", "schalt", "räum",
        "reservier", "buch", "triff", "stell", "schreib", "nimm", "wie", "was",
        "ist", "sind", "mir", "mich", "dir", "dich", "daran", "nicht", "bitte",
        "etwas", "heute", "morgen", "kalender", "hätte", "gerne", "brauche",
        "kann", "kommen", "komm", "klar", "spricht", "nachher", "von", "zu",
        "mit", "auf", "ab", "aus", "weg", "licht", "fenster", "lied", "tisch",
        "termin", "synthwave", "wecker", "bescheid", "bus", "müllers",
        "gehört", "erzähl", "meiner", "wegen", "gegen", "ohne", "für", "bei",
        "dass", "weil", "damit", "sobald", "falls", "obwohl", "bevor",
        "sondern", "steht", "spät", "glaube", "will",
    }
)

# Minimum word count: a single token ("und", "the", "Jarvis") is never a held
# fragment — it is a wake word, filler, or noise.
_MIN_TOKENS: Final[int] = 2

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[a-zA-ZäöüÄÖÜß']+")

# --- Cancel phrases (discard a pending fragment) -------------------------- #
_CANCEL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:"
    r"vergiss(?:\s+(?:das|es|den|alles))?"
    r"|ach\s+nein"
    r"|lass\s+(?:stecken|(?:es\s+)?gut\s+sein)"
    r"|schon\s+gut"
    r"|egal\s+jetzt"
    r"|never\s*mind"
    r"|forget\s+it"
    r")\b",
    re.IGNORECASE,
)


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _detect_language(tokens: list[str], hint: str) -> str:
    """Return ``"de"`` or ``"en"``. Honours an explicit hint, else counts
    marker words; ties resolve to German (the assistant's primary language)."""
    if hint:
        return "en" if hint.lower().startswith("en") else "de"
    en = sum(1 for t in tokens if t in _EN_MARKERS)
    de = sum(1 for t in tokens if t in _DE_MARKERS)
    return "en" if en > de else "de"


def is_incomplete(text: str | None, language: str = "") -> IncompleteVerdict | None:
    """Classify a finalized transcript as syntactically open-ended.

    Returns an :class:`IncompleteVerdict` only when the trailing token is an
    unambiguous open marker (conjunction, noun-requiring determiner, or — German
    only — an unambiguous preposition). Returns ``None`` for everything else,
    including all ambiguous tails ("answer when in doubt").
    """
    if not text:
        return None
    tokens = _tokens(text)
    if len(tokens) < _MIN_TOKENS:
        return None

    last = tokens[-1]
    lang = _detect_language(tokens, language)

    if last in _CONJUNCTIONS:
        return IncompleteVerdict(reason=REASON_CONJUNCTION, marker=last, language=lang)

    determiners = _EN_DETERMINERS if lang == "en" else _DE_DETERMINERS
    if last in determiners:
        return IncompleteVerdict(reason=REASON_DETERMINER, marker=last, language=lang)

    # German does not strand prepositions; the English path never fires on one.
    if lang == "de" and last in _DE_PREPOSITIONS:
        return IncompleteVerdict(reason=REASON_PREPOSITION, marker=last, language=lang)

    # Trailing comma — the empirically dominant continuation signal in live
    # voice sessions (live bug 2026-05-26: "...beschrieben wird," cut by VAD
    # before the continuation). Checked LAST so a more specific tail (conjunction
    # such as "Send it to Tom and,") keeps its REASON_CONJUNCTION verdict.
    if text.rstrip().endswith(","):
        return IncompleteVerdict(
            reason=REASON_TRAILING_COMMA, marker=",", language=lang
        )

    return None


def is_cancel(text: str | None) -> bool:
    """True if the utterance is an explicit abort of a pending fragment."""
    return bool(text) and _CANCEL_RE.search(text) is not None


__all__ = [
    "REASON_CONJUNCTION",
    "REASON_DETERMINER",
    "REASON_PREPOSITION",
    "REASON_TRAILING_COMMA",
    "IncompleteVerdict",
    "is_cancel",
    "is_incomplete",
]
