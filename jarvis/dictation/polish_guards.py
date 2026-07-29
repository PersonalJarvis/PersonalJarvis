"""Deterministic drift guards for the dictation polish and translate passes.

Two guard sets, one discipline
------------------------------
:func:`drift_reason` guards the polish pass and :func:`translate_drift_reason`
guards the translate pass. They share every mechanism in this module and
disagree on exactly one thing — whether a change of language is the defect or
the job — which is why they are two functions rather than one with a flag. A
caller that picks the wrong one does not get a slightly worse guard; it gets a
pass that rejects 100 % of its own correct answers.

What this module is for
-----------------------
The polish pass sends a transcript to a language model and gets text back. The
model is instructed to format, not to rewrite — but instructions are a request,
not a contract. The published zero-edit rate of the market-leading reference
tool is 50-70 %, which means even a mature formatter changes words it was told
to leave alone, and their own shipped regression note ("fewer changes to words
you said") names the failure direction: **over-rewriting**.

So the model's answer is treated as a proposal. Every function here is pure
regex and set arithmetic — no model call, no I/O, no network (same discipline
as ``scrub_for_voice``, AP-11) — and :func:`drift_reason` returns ``""`` only
when the proposal survived every check. Anything else is a short machine
reason, the caller delivers the RAW text, and the user keeps their words.

The asymmetries are deliberate, not oversights
----------------------------------------------
* **Numbers may be ADDED, never lost.** ``"seven"`` -> ``"7"`` is the exact
  normalization the feature exists to perform, so a one-directional check is
  the only one that can be both correct and useful. For the same reason the
  spoken number WORDS are treated as common vocabulary below: the word is
  allowed to disappear precisely because the digit replaces it.
* **The language check skips when the detector is undecided.** The detector
  knows de/en/es and nothing else (``jarvis/core/turn_language.py``); a
  two-sided "must match" rule would veto every Polish, Japanese or Turkish
  dictation on the grounds that we cannot classify it. Silence beats a veto.
* **Rare-token preservation is skipped for languages we have no word list
  for**, for the same reason: without a common-word list every token looks
  rare, and the guard would reject every polish in ~95 of the 100 recognition
  languages. Protected terms are still enforced there — those come from the
  user, not from a frequency table.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Final

from jarvis.core.turn_language import detect_text_language, normalize_language_tag
from jarvis.dictation.cleanup import count_words
from jarvis.dictation.polish_prompt import RAW_CLOSE_DELIMITER, RAW_OPEN_DELIMITER

#: Every reason :func:`drift_reason` can return, plus ``""`` for "safe to
#: deliver". Declared as data so the caller can assert it never invents a code
#: the history and the UI have not heard of.
DRIFT_REASONS: Final[tuple[str, ...]] = (
    "empty",
    "meta_output",
    "ratio_shrink",
    "ratio_growth",
    "language_flip",
    "lost_number",
    "lost_term",
)

#: Every reason :func:`translate_drift_reason` can return, plus ``""``.
#:
#: Deliberately a SEPARATE vocabulary rather than an extension of the tuple
#: above, because the two guard sets disagree about the thing that matters most:
#: for the polish pass a change of language is the defect (``language_flip``),
#: for the translate pass it is the entire job, and the defect is its ABSENCE
#: (``not_translated``). Running a translation through :func:`drift_reason`
#: would reject every single one — first on ``ratio_growth``, then on
#: ``language_flip`` — which is why the caller must pick the right one.
#:
#: The overlapping members mean the same thing in both sets, so a history row or
#: a test can read ``reason`` without first knowing which pass produced it.
TRANSLATE_DRIFT_REASONS: Final[tuple[str, ...]] = (
    "empty",
    "meta_output",
    "ratio_shrink",
    "ratio_growth",
    "not_translated",
    "wrong_language",
    "lost_number",
    "lost_term",
)

#: A rare token must be at least this long. Below it the "rare" signal is noise:
#: short tokens are dominated by inflections, particles and recognizer debris.
_RARE_MIN_CHARS = 4

#: Scripts that do not put spaces between words. A "word count" over these is
#: meaningless — a whole Chinese sentence counts as ONE token — so any
#: length-ratio check that spans one of them and a space-separated script is
#: comparing two different units and will fire on every correct answer.
#:
#: Chinese (incl. the CJK extension block used by Cantonese), Japanese kana,
#: Thai, Lao, Khmer, Burmese and Tibetan. Hangul is deliberately ABSENT: Korean
#: does separate words with spaces, so its word counts are comparable.
_NO_WORD_BOUNDARY_RE: Final[re.Pattern[str]] = re.compile(
    "[぀-ヿ㐀-䶿一-鿿豈-﫿"
    "฀-๿຀-໿ក-៿က-႟ༀ-࿿]"
)

#: How much of a text has to be in such a script before its word count is
#: treated as meaningless. A ratio rather than "any character", so one CJK
#: brand name inside a German sentence does not disable the check.
_NO_WORD_BOUNDARY_SHARE = 0.15

#: The only length rule left when a translation crosses between a spaced and an
#: unspaced script, measured in CHARACTERS because words are not comparable
#: there. Deliberately crude: German into Chinese roughly quarters the character
#: count while Chinese into German roughly quadruples it, so anything tight
#: enough to be informative would reject one of the two directions. It exists to
#: catch a model that wrote an essay, nothing finer.
_CROSS_SCRIPT_MAX_CHAR_GROWTH = 6.0

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_WORDLIKE_RE = re.compile(r"[\w']+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)
_DIGIT_RUN_RE = re.compile(r"\d+")
# A separator BETWEEN two digits is formatting, not content: "1,000" and "1000"
# are the same number, and a formatter is allowed to add or drop the grouping
# (German and English disagree on which mark means which, so both are listed).
# Flattening both sides identically keeps the number guard sound while stopping
# it from firing on a legal thousands separator.
_DIGIT_SEPARATOR_RE = re.compile("(?<=\\d)[.,'\u2019\u00a0\u202f ](?=\\d)")

# A leading phrase that means the model started TALKING instead of formatting.
# It is only a violation when the RAW text did not open the same way — people
# genuinely dictate "Sure, I'll send it over", and rejecting that would punish
# the speaker for the model's habits.
_META_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    "^\\s*[\"'\u201c\u201e\u00ab]?\\s*(?:"
    r"here(?:'s| is| are)\b"
    r"|sure[,!.:-]"
    r"|certainly\b"
    r"|of course\b"
    r"|as requested\b"
    r"|the (?:corrected|formatted|cleaned|polished|revised)\b"
    r"|(?:corrected|formatted|cleaned|polished|revised)\s+(?:text|version|transcript)\b"
    r"|(?:output|result|transcript|formatted text)\s*:"
    r"|i(?:'ve| have)\s+(?:corrected|formatted|cleaned|polished)\b"
    # The two German alternatives below are matching DATA (§1 list #3): the
    # literal meta-preamble a classifier must contain to recognise one.
    r"|hier (?:ist|sind|kommt)\b"  # i18n-allow: DE meta-preamble
    r"|der (?:korrigierte|formatierte) text\b"  # i18n-allow: DE meta-preamble
    r"|aqu\u00ed (?:est\u00e1|tienes|te dejo)\b"
    r"|el texto (?:corregido|formateado)\b"
    r")",
    re.IGNORECASE,
)

# i18n-allow: the two non-English tables below are language-frequency DATA — the
# literal German and Spanish tokens a rarity classifier must contain in order to
# decide that a German or Spanish word is ordinary rather than a proper noun.
# Matching data, not prose (CLAUDE.md §1, category 3), exactly like the filler
# tables in jarvis/dictation/cleanup.py.
#
# Only tokens of _RARE_MIN_CHARS or more are ever looked up, so short function
# words are deliberately absent — they would be dead weight. The lists are
# ordinary vocabulary PLUS the discourse markers a formatter is allowed to drop
# when it removes a false start ("actually", "basically", "eigentlich", "quasi").
_COMMON_WORDS_BASE: Final[dict[str, frozenset[str]]] = {
    "en": frozenset({
        "about", "actually", "after", "again", "against", "alright", "also",
        "although", "always", "another", "anyone", "anything", "around", "away",
        "back", "basically", "because", "been", "before", "being", "believe",
        "below", "best", "better", "between", "both", "bring", "came", "cannot",
        "care", "change", "check", "come", "coming", "could", "couldn", "course",
        "definitely", "didn", "does", "doesn", "doing", "done", "down", "each",
        "else", "enough", "even", "ever", "every", "everything", "exactly",
        "example", "fact", "feel", "felt", "find", "first", "from", "full",
        "gave", "gets", "getting", "give", "given", "going", "gone", "good",
        "great", "guess", "hand", "happen", "happened", "hard", "have",
        "having", "hear", "help", "here", "hope", "idea", "into", "issue",
        "just", "keep", "kind", "know", "last", "later", "least", "left",
        "less", "like", "line", "little", "long", "look", "looking", "made",
        "make", "making", "many", "matter", "maybe", "mean", "means", "meant",
        "might", "mind", "more", "most", "move", "much", "must", "need",
        "never", "next", "nice", "nothing", "only", "open", "other", "over",
        "part", "people", "perhaps", "place", "please", "point", "probably",
        "problem", "quick", "quite", "rather", "read", "real", "really",
        "reason", "right", "said", "same", "saying", "says", "seem", "seems",
        "seen", "send", "sense", "several", "should", "shouldn", "show",
        "side", "simply", "since", "some", "something", "sometimes", "soon",
        "sorry", "sort", "sound", "sounds", "start", "still", "stuff", "such",
        "sure", "take", "taken", "talk", "tell", "than", "thank", "thanks",
        "that", "thats", "their", "them", "then", "there", "these", "they",
        "thing", "things", "think", "this", "those", "though", "thought",
        "through", "time", "times", "today", "together", "told",
        "took", "totally", "tried", "true", "turn", "under", "understand",
        "until", "upon", "used", "using", "usually", "very", "want", "wanted",
        "wants", "well", "went", "were", "what", "when", "where", "whether",
        "which", "while", "whole", "will", "with", "without", "word", "words",
        "work", "working", "would", "wouldn", "write", "wrong", "yeah", "year",
        "years", "your", "yours", "yourself",
    }),
    "de": frozenset({  # i18n-allow: German frequency data (§1 list #3)
        "aber", "alle", "allem", "allen", "aller", "alles", "also", "andere",  # i18n-allow
        "anderen", "auch", "beim", "bereits", "besser", "bisschen", "bist",  # i18n-allow
        "bitte", "brauche", "brauchen", "dabei", "daf\u00fcr", "damit", "dann",  # i18n-allow
        "daran", "darauf", "dass", "dein", "deine", "deinen", "denen", "denn",  # i18n-allow
        "deshalb", "dich", "diese", "diesem", "diesen", "dieser", "dieses",  # i18n-allow
        "doch", "dort", "durch", "eben", "eher", "eigentlich", "eine",  # i18n-allow
        "einem", "einen", "einer", "eines", "einfach", "einmal", "etwas",  # i18n-allow
        "euch", "fast", "ganz", "ganze", "ganzen", "gegen", "gehen", "geht",  # i18n-allow
        "gemacht", "genau", "gerade", "gerne", "gesagt", "gibt", "gleich",  # i18n-allow
        "gro\u00dfe", "gute", "guten", "haben", "habe", "halt", "hast", "hatte",  # i18n-allow
        "hatten", "heute", "hier", "hinter", "ihnen", "ihre", "ihrem", "ihren",  # i18n-allow
        "immer", "irgendwie", "jede", "jeden", "jetzt", "kann", "kannst",  # i18n-allow
        "kein", "keine", "keinen", "klar", "kommen", "kommt", "k\u00f6nnen",  # i18n-allow
        "konnte", "lassen", "leicht", "letzte", "letzten", "machen", "macht",  # i18n-allow
        "mache", "mehr", "mein", "meine", "meinen", "meiner", "meist", "mich",  # i18n-allow
        "muss", "m\u00fcssen", "nach", "nachdem", "nicht", "nichts", "noch",  # i18n-allow
        "oben", "oder", "ohne", "quasi", "richtig", "sagen", "sagt", "schnell",  # i18n-allow
        "schon", "sehen", "sehr", "sein", "seine", "seinen", "selbst", "sich",  # i18n-allow
        "sind", "sofort", "soll", "sollen", "sollte", "sondern", "sonst",  # i18n-allow
        "sp\u00e4ter", "stehen", "steht", "super", "tats\u00e4chlich", "\u00fcber",  # i18n-allow
        "\u00fcberhaupt", "unser", "unsere", "unter", "viel", "viele", "vielen",  # i18n-allow
        "vielleicht", "voll", "w\u00e4hrend", "wann", "waren", "warum", "weil",  # i18n-allow
        "weiter", "welche", "wenig", "wenn", "werde", "werden", "wieder",  # i18n-allow
        "wird", "wirklich", "wissen", "wohl", "wollen", "will", "willst",  # i18n-allow
        "wurde", "wurden", "zeit", "ziemlich", "zusammen", "zwar", "zwischen",  # i18n-allow
    }),
    "es": frozenset({  # i18n-allow: Spanish frequency data (§1 list #3)
        "acuerdo", "adem\u00e1s", "ahora", "algo", "alguna", "algunas",  # i18n-allow
        "alguno", "algunos", "all\u00ed", "ante", "antes", "aquel", "aqu\u00ed",  # i18n-allow
        "aunque", "ayer", "bien", "buena", "bueno", "cada", "casi", "cerca",  # i18n-allow
        "cierto", "claro", "como", "c\u00f3mo", "contra", "cosa", "cosas",  # i18n-allow
        "creo", "cual", "cuando", "cu\u00e1nto", "decir", "dejar", "dem\u00e1s",  # i18n-allow
        "dentro", "desde", "despu\u00e9s", "dice", "dicho", "digo", "donde",  # i18n-allow
        "d\u00f3nde", "durante", "ella", "ellas", "ellos", "entonces", "entre",  # i18n-allow
        "eran", "eres", "esas", "esos", "esta", "est\u00e1", "estaba",  # i18n-allow
        "estamos", "est\u00e1n", "estar", "este", "esto", "estos", "estoy",  # i18n-allow
        "exactamente", "favor", "forma", "fuera", "gran", "grande", "gracias",  # i18n-allow
        "hace", "hacer", "hacia", "hasta", "hecho", "hola", "igual", "incluso",  # i18n-allow
        "luego", "lugar", "ma\u00f1ana", "mejor", "menos", "mientras", "mismo",  # i18n-allow
        "mucha", "mucho", "muchos", "nada", "nadie", "ning\u00fan", "nosotros",  # i18n-allow
        "nunca", "otra", "otras", "otro", "otros", "para", "parece", "parte",  # i18n-allow
        "pero", "poco", "poder", "porque", "posible", "propio", "pues",  # i18n-allow
        "puede", "pueden", "puedo", "quiere", "quiero", "quiz\u00e1s", "sabe",  # i18n-allow
        "saber", "seg\u00fan", "siempre", "siendo", "sido", "sino", "sobre",  # i18n-allow
        "solo", "s\u00f3lo", "somos", "tambi\u00e9n", "tampoco", "tanto",  # i18n-allow
        "tarde", "tener", "tengo", "tiene", "tienen", "tiempo", "toda", "todas",  # i18n-allow
        "todo", "todos", "tras", "tuvo", "unos", "usted", "vale", "vamos",  # i18n-allow
        "verdad", "veces",  # i18n-allow
    }),
}

# Spoken number words are common vocabulary AND explicitly licensed to vanish:
# the prompt asks for "seven" -> "7", so a rarity guard that mourns the missing
# word would reject the single transformation the feature was built to make.
_NUMBER_WORDS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset({
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
        "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
        "thousand", "million", "billion", "first", "second", "third", "fourth",
        "fifth", "half", "quarter", "dozen",
    }),
    "de": frozenset({  # i18n-allow: German number words (§1 list #3)
        "null", "eins", "zwei", "drei", "vier", "f\u00fcnf", "sechs", "sieben",  # i18n-allow
        "acht", "neun", "zehn", "elf", "zw\u00f6lf", "dreizehn", "vierzehn",  # i18n-allow
        "f\u00fcnfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn",  # i18n-allow
        "zwanzig", "drei\u00dfig", "dreissig", "vierzig", "f\u00fcnfzig",  # i18n-allow
        "sechzig", "siebzig", "achtzig", "neunzig", "hundert", "tausend",  # i18n-allow
        "million", "millionen", "erste", "zweite", "dritte", "halb", "viertel",  # i18n-allow
        "dutzend",  # i18n-allow
    }),
    "es": frozenset({  # i18n-allow: Spanish number words (§1 list #3)
        "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete",  # i18n-allow
        "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince",  # i18n-allow
        "diecis\u00e9is", "diecisiete", "dieciocho", "diecinueve", "veinte",  # i18n-allow
        "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta",  # i18n-allow
        "noventa", "cien", "ciento", "mil", "mill\u00f3n", "millones",  # i18n-allow
        "primero", "segundo", "tercero", "medio", "cuarto", "docena",  # i18n-allow
    }),
}

#: The lookup the rarity filter actually uses: ordinary vocabulary plus the
#: number words, per language. Anything absent from here is "rare".
_COMMON_WORDS: Final[dict[str, frozenset[str]]] = {
    code: base | _NUMBER_WORDS.get(code, frozenset())
    for code, base in _COMMON_WORDS_BASE.items()
}


def normalize_for_compare(text: str) -> str:
    """Collapse *text* to the form used to decide "did anything change at all".

    Unicode-normalised (NFKC) and whitespace-collapsed, and nothing else:
    punctuation and capitalization are exactly what the polish pass is FOR, so
    normalising them away would report a successful repunctuation as
    ``unchanged`` and throw the result away.
    """
    flat = unicodedata.normalize("NFKC", str(text or ""))
    return _WHITESPACE_RE.sub(" ", flat).strip()


def _compare_tokens(text: str) -> list[str]:
    """Lower-cased word tokens, punctuation stripped — the term-match alphabet."""
    flat = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return _WORDLIKE_RE.findall(flat)


def _token_haystack(text: str) -> str:
    """A space-fenced token string so a multi-word term can be found as a unit."""
    return " " + " ".join(_compare_tokens(text)) + " "


def _language_key(language: object) -> str:
    """The two-letter key into :data:`_COMMON_WORDS`, or ``""`` when unusable.

    Resolved through the canonical :func:`normalize_language_tag` rather than by
    slicing the string, because the ways this argument arrives spelled are not
    ours to control: the cloud Whisper APIs answer with the language NAME
    ("German", "English"), and a key derived by slicing turns that into
    ``"german"``, misses the table, and returns ``""`` — which does not raise,
    does not log, and silently disables the rare-token half of
    :func:`drift_reason` on the exact dictations where the recognizer was RIGHT.
    A guard whose failure mode is looking like it passed has to be robust to its
    caller, so the normalisation lives here as well as at the caller.

    Anything the resolver cannot place still yields ``""``: a language we hold no
    frequency table for genuinely cannot be reasoned about, and that no-op is
    documented in this module's header.
    """
    raw = str(language or "").strip()
    if not raw:
        return ""
    direct = raw.lower().replace("_", "-").split("-", 1)[0]
    if direct in _COMMON_WORDS:
        return direct
    code = normalize_language_tag(raw.lower())
    return code if code in _COMMON_WORDS else ""


def rare_tokens(text: str, *, language: str) -> frozenset[str]:
    """The tokens of *text* a formatter has no business dropping.

    A token qualifies when it is purely alphabetic, at least
    ``_RARE_MIN_CHARS`` long, and absent from the common-word list of
    *language*. What survives that filter is overwhelmingly proper nouns,
    product names, technical terms and unusual vocabulary — precisely the words
    a rewrite model "corrects" into something familiar.

    Returns an EMPTY set for any language we hold no word list for, which
    disables the rare-token half of the ``lost_term`` guard rather than
    rejecting every polish in a language we cannot reason about.
    """
    common = _COMMON_WORDS.get(_language_key(language))
    if common is None:
        return frozenset()
    flat = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return frozenset(
        token
        for token in _TOKEN_RE.findall(flat)
        if len(token) >= _RARE_MIN_CHARS and token not in common
    )


def _digit_runs(text: str) -> frozenset[str]:
    """Every number in *text*, with grouping separators flattened away."""
    return frozenset(_DIGIT_RUN_RE.findall(_DIGIT_SEPARATOR_RE.sub("", str(text or ""))))


def _echoes_delimiter(raw: str, polished: str) -> bool:
    """True when the answer repeats the fence the transcript was sent inside.

    A model that emits the delimiter has stopped formatting and started
    transcribing the conversation back at us — the single clearest signal that
    the untrusted material was read as an instruction.
    """
    for marker in (RAW_OPEN_DELIMITER, RAW_CLOSE_DELIMITER, "<<<", ">>>"):
        if marker in polished and marker not in raw:
            return True
    return False


def _is_meta_output(raw: str, polished: str) -> bool:
    """True when the answer is framed as a REPLY rather than as the text."""
    if _echoes_delimiter(raw, polished):
        return True
    if "```" in polished and "```" not in raw:
        return True
    return bool(_META_PREFIX_RE.match(polished)) and not _META_PREFIX_RE.match(raw)


def drift_reason(
    raw: str,
    polished: str,
    *,
    language: str,
    protected: Sequence[str],
    max_shrink: float,
    max_growth: float,
) -> str:
    """``""`` when *polished* is safe to deliver; otherwise a short reason code.

    The checks run cheapest-and-most-unambiguous first, and the returned code is
    the FIRST one that fired — it is stored on the history row, so it has to name
    the most informative cause rather than the last one evaluated. In particular
    a translation is reported as ``language_flip`` (which says what happened)
    instead of ``lost_term`` (which is merely its symptom), so the language check
    deliberately precedes the token checks.

    Every returned value is a member of :data:`DRIFT_REASONS`.
    """
    if not str(polished or "").strip():
        return "empty"
    if _is_meta_output(raw, polished):
        return "meta_output"

    raw_words = count_words(raw)
    out_words = count_words(polished)
    if raw_words:
        ratio = out_words / raw_words
        if ratio < max_shrink:
            return "ratio_shrink"
        if ratio > max_growth:
            return "ratio_growth"

    # Skipped whenever EITHER side is undecided: the detector only knows
    # de/en/es, so "unknown" means "no opinion", never "different".
    raw_language = detect_text_language(raw)
    out_language = detect_text_language(polished)
    if raw_language != "unknown" and out_language != "unknown":
        if raw_language != out_language:
            return "language_flip"

    # Asymmetric on purpose: "seven" -> "7" ADDS a digit run and must stay legal.
    if _digit_runs(raw) - _digit_runs(polished):
        return "lost_number"

    haystack = _token_haystack(polished)
    raw_haystack = _token_haystack(raw)
    for term in protected or ():
        tokens = _compare_tokens(term)
        if not tokens:
            continue
        needle = " " + " ".join(tokens) + " "
        if needle in raw_haystack and needle not in haystack:
            return "lost_term"

    # The frequency list has to describe the TEXT, not its label. Where the two
    # disagree the label loses, because trusting it fails silently and
    # completely: an English transcript looked up against the German word list
    # makes every ordinary English word "rare", so the guard fires on any
    # wording change at all and the dictation is delivered unpolished with
    # `rejected_drift` on the row and nothing saying why.
    #
    # The disagreement is real and has its own cause upstream — a segment-sized
    # upload lets Whisper re-decide the language, and on a short segment it
    # sometimes TRANSLATES rather than transcribes, so a row can carry a German
    # tag and an English transcript. That is fixed where it happens; this is the
    # guard declining to compound it. `raw_language` is already computed above,
    # so the correction costs nothing.
    lookup = raw_language if raw_language != "unknown" else language
    if rare_tokens(raw, language=lookup) - rare_tokens(polished, language=lookup):
        return "lost_term"

    return ""


def writes_without_word_spaces(text: str) -> bool:
    """Whether *text* is mostly in a script that puts no spaces between words.

    Public because the answer decides whether a word COUNT means anything at
    all, and that question belongs to whoever is comparing two texts — not
    hidden inside one guard.
    """
    body = str(text or "").strip()
    if not body:
        return False
    hits = len(_NO_WORD_BOUNDARY_RE.findall(body))
    return hits / len(body) >= _NO_WORD_BOUNDARY_SHARE


def translate_drift_reason(
    raw: str,
    translated: str,
    *,
    target_language: str,
    protected: Sequence[str],
    max_shrink: float,
    max_growth: float,
) -> str:
    """``""`` when *translated* is safe to deliver; otherwise a short reason code.

    The translate twin of :func:`drift_reason`, and the differences between them
    are the whole point:

    * **The language check is inverted.** There the output must MATCH the input;
      here it must not — it must match *target_language*. When it comes back in
      the input's language the answer is ``not_translated`` (the model declined
      the job, the single most likely failure), and when it comes back in some
      third language it is ``wrong_language``. Both are only decidable for the
      three languages ``detect_text_language`` knows, so for the other ~96
      targets this check is a documented no-op rather than a veto — the same
      asymmetry, and the same reasoning, as the rare-token filter.
    * **The word-count band is much wider.** A faithful translation legitimately
      shrinks or grows: German compounds collapse into one English word, English
      phrasal verbs expand into German subclauses. The narrow polish band exists
      to catch a formatter that started writing; here it can only catch a model
      that stopped.
    * **Rare-token preservation is gone entirely.** Every content word is
      *supposed* to change. What survives is the check that still means
      something after a language change: numbers may not be lost, and protected
      terms — names, the wake word, the STT dictionary — must cross untranslated.

    Same discipline as its twin: pure regex and set arithmetic, no model call,
    no I/O, and the FIRST reason that fires is the one returned.
    """
    if not str(translated or "").strip():
        return "empty"
    if _is_meta_output(raw, translated):
        return "meta_output"

    # The length check only means something when both sides are counted in the
    # SAME unit. Chinese, Japanese, Thai, Khmer, Lao, Burmese and Tibetan write
    # without spaces, so a whole sentence counts as one or two "words": a German
    # sentence translated into Chinese scores a word ratio of ~0.10 and every
    # single correct translation was rejected as ``ratio_shrink``. That is the
    # bug that made this feature look like it worked for English only.
    #
    # Where the scripts differ, length carries no signal at all and the check is
    # skipped — the same "silence beats a veto" rule the language and rare-token
    # checks already follow. What replaces it is a deliberately crude character
    # ceiling: it cannot tell a good translation from an average one, but it
    # still catches the failure that matters, a model that answered the
    # transcript instead of translating it.
    if writes_without_word_spaces(raw) != writes_without_word_spaces(translated):
        if len(translated) > len(raw) * _CROSS_SCRIPT_MAX_CHAR_GROWTH:
            return "ratio_growth"
    else:
        raw_words = count_words(raw)
        out_words = count_words(translated)
        if raw_words:
            ratio = out_words / raw_words
            if ratio < max_shrink:
                return "ratio_shrink"
            if ratio > max_growth:
                return "ratio_growth"

    # Only meaningful when we can classify the target at all. ``_language_key``
    # is not reused here on purpose: it answers "do we hold a frequency table",
    # which is a different question from "can the detector name this language",
    # and the two tables are only coincidentally the same three languages today.
    target = _language_key(target_language)
    if target:
        out_language = detect_text_language(translated)
        if out_language != "unknown" and out_language != target:
            # Naming WHICH failure happened is worth the extra branch: "the
            # model ignored you" and "the model translated into the wrong
            # language" call for different fixes, and a single code would send
            # every user down the same wrong path.
            if out_language == detect_text_language(raw):
                return "not_translated"
            return "wrong_language"

    # Unchanged from the polish guard, and for an unchanged reason: a number the
    # speaker said and the answer does not carry is lost information in any
    # language. Asymmetric the same way, so "seven" -> "7" stays legal.
    if _digit_runs(raw) - _digit_runs(translated):
        return "lost_number"

    haystack = _token_haystack(translated)
    raw_haystack = _token_haystack(raw)
    for term in protected or ():
        tokens = _compare_tokens(term)
        if not tokens:
            continue
        needle = " " + " ".join(tokens) + " "
        if needle in raw_haystack and needle not in haystack:
            return "lost_term"

    return ""


__all__ = [
    "DRIFT_REASONS",
    "TRANSLATE_DRIFT_REASONS",
    "drift_reason",
    "normalize_for_compare",
    "rare_tokens",
    "translate_drift_reason",
]
