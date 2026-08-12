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

import difflib
import re
import unicodedata
from collections import Counter
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
    "lost_verb",
)

#: Every reason :func:`precision_drift_reason` can return, plus ``""``.
#:
#: A STRICT SUBSET of :data:`DRIFT_REASONS` — same checks, same codes, same
#: meanings, minus ``lost_term``'s rare-token half. Declared as its own tuple
#: anyway, rather than reusing the one above, because the difference is the
#: entire point of the mode: precision mode REPLACES uncommon words on purpose,
#: so a guard that rejects an answer for having done so rejects the feature. A
#: reader who sees this vocabulary knows immediately which protection was traded
#: away, and a test can pin that the trade is exactly one check wide.
#:
#: ``lost_term`` survives, because it still fires for PROTECTED terms — names,
#: the wake word, the STT dictionary. Those come from the user, not from a
#: frequency table, and no amount of "sharpen the wording" licenses touching
#: them.
PRECISION_DRIFT_REASONS: Final[tuple[str, ...]] = (
    "empty",
    "meta_output",
    "ratio_shrink",
    "ratio_growth",
    "language_flip",
    "lost_number",
    "lost_term",
    "lost_verb",
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

#: How different a replacement may be and still count as a REPAIR of the token
#: it replaced, rather than its loss. Levenshtein distance over the longer of
#: the two, so it is a share and not an absolute: at 0.5, half the characters
#: may move.
#:
#: The number is set by the two mistakes it has to tell apart, measured on the
#: live history:
#:
#: * repairs that MUST pass — ``deskto`` -> ``desktop`` (0.14),
#:   ``javas`` -> ``jarvis`` (0.33), ``haboogle`` -> ``hab google`` (0.11),
#:   ``promme`` -> ``prompts`` (0.43);
#: * losses that MUST still fail — ``anushka`` -> ``her`` (1.0),
#:   ``kubernetes`` dropped entirely (no candidate at all).
#:
#: The gap between the two groups is wide, so the exact cut matters far less
#: than being inside it. It sits nearer the repairs because the cost is not
#: symmetric: a rejected repair hands the user a transcript they cannot use,
#: while a wrongly accepted one is a word they can see and edit.
_REPAIR_MAX_DISTANCE_SHARE = 0.5

#: How many output tokens a single misheard token may have been split into.
#: A recognizer that runs two words together (``hab Google`` -> ``haboogle``)
#: is repaired by splitting them apart again, so the candidate has to be looked
#: for across token boundaries as well. Two is what the observed failures need;
#: more would start matching unrelated phrases by coincidence.
_REPAIR_MAX_SPLIT = 2

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
# The two halves of "did anything change at all": a whitespace run CONTAINING a
# line break collapses to one "\n", and every other run collapses to one space.
# Kept apart on purpose — see ``normalize_for_compare`` for why a line break is
# content here and not noise.
_LINE_BREAK_RUN_RE = re.compile(r"\s*\n\s*", re.UNICODE)
_INLINE_SPACE_RE = re.compile(r"[^\S\n]+", re.UNICODE)
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
#
# The ordinals and enumeration adverbs ("firstly", "erstens", "quinto") are
# here for the same reason: the prompt turns a counted enumeration into a
# numbered list, so the spoken ordinal vanishes into the "1." marker that
# replaces it. A German speaker who counts says "erstens, zweitens, drittens"
# — adverb forms no table here carried, so every counted list was rejected as
# ``lost_term`` and the licensed transformation never survived the guard.
_NUMBER_WORDS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset({
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
        "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
        "thousand", "million", "billion", "first", "second", "third", "fourth",
        "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "firstly",
        "secondly", "thirdly", "fourthly", "fifthly", "half", "quarter",
        "dozen",
    }),
    "de": frozenset({  # i18n-allow: German number words (§1 list #3)
        "null", "eins", "zwei", "drei", "vier", "f\u00fcnf", "sechs", "sieben",  # i18n-allow
        "acht", "neun", "zehn", "elf", "zw\u00f6lf", "dreizehn", "vierzehn",  # i18n-allow
        "f\u00fcnfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn",  # i18n-allow
        "zwanzig", "drei\u00dfig", "dreissig", "vierzig", "f\u00fcnfzig",  # i18n-allow
        "sechzig", "siebzig", "achtzig", "neunzig", "hundert", "tausend",  # i18n-allow
        "million", "millionen", "erste", "zweite", "dritte", "vierte",  # i18n-allow
        "fünfte", "sechste", "siebte", "achte", "neunte", "zehnte",  # i18n-allow
        "erstens", "zweitens", "drittens", "viertens", "fünftens",  # i18n-allow
        "sechstens", "siebtens", "siebentens", "achtens", "neuntens",  # i18n-allow
        "zehntens", "halb", "viertel", "dutzend",  # i18n-allow
    }),
    "es": frozenset({  # i18n-allow: Spanish number words (§1 list #3)
        "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete",  # i18n-allow
        "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince",  # i18n-allow
        "diecis\u00e9is", "diecisiete", "dieciocho", "diecinueve", "veinte",  # i18n-allow
        "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta",  # i18n-allow
        "noventa", "cien", "ciento", "mil", "mill\u00f3n", "millones",  # i18n-allow
        "primero", "segundo", "tercero", "quinto", "sexto", "séptimo",  # i18n-allow
        "octavo", "noveno", "décimo", "medio", "cuarto", "docena",  # i18n-allow
    }),
}

# i18n-allow: the tables below are speech-input command vocabulary (§1 list
# #3) — the literal German/Spanish words a dictation speaks to command a mark.
#
# The spoken FORMATTING commands, per language: the punctuation the prompt
# turns into the mark itself ("comma" -> ","), and the layout commands behind
# paragraphs and bullet lists ("new paragraph", "bullet point", and their
# input-language equivalents). Two guards read this table:
#
# * the rare-token filter treats these words as common, because the licensed
#   conversion DELETES them — a spoken bullet command has no repaired spelling
#   in the answer, only the "- " that replaced it, and mourning it rejected
#   every commanded list;
# * the word-count band counts them out of both sides of the ratio
#   (:func:`_ratio_word_count`), because a command becomes a mark and not a
#   word — a dictation that leans on commands hardest halves its word count
#   when formatted CORRECTLY, which an unadjusted band reads as a summariser
#   at work.
#
# Carrier words of multi-word commands ride along ("neuer" in "neuer Absatz",  # i18n-allow
# "nueva" in "nueva linea"): the command vanishes whole, so every word of it  # i18n-allow
# must be licensed. The cost of listing a homonym here ("Punkt" the content  # i18n-allow
# word, "mark", "dash") is that the guard no longer mourns it when a model
# wrongly drops it — accepted, because the words are short, common in speech,
# and the alternative rejects every dictation that uses the command.
_FORMAT_COMMAND_WORDS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset({
        "period", "comma", "colon", "semicolon", "hyphen", "dash",
        "quote", "unquote", "quotes", "question", "exclamation", "mark",
        "bullet", "bullets", "paragraph", "newline", "ellipsis",
        "parenthesis", "parentheses", "bracket", "brackets", "slash",
        "next", "point",
    }),
    "de": frozenset({  # i18n-allow: German spoken commands (§1 list #3)
        "punkt", "komma", "doppelpunkt", "semikolon", "strichpunkt",  # i18n-allow
        "fragezeichen", "ausrufezeichen", "bindestrich", "gedankenstrich",  # i18n-allow
        "anführungszeichen", "klammer", "klammern", "schrägstrich",  # i18n-allow
        "absatz", "zeile", "zeilenumbruch", "auslassungspunkte",  # i18n-allow
        "stichpunkt", "stichpunkte", "spiegelstrich", "aufzählungszeichen",  # i18n-allow
        "aufzählungspunkt", "neue", "neuer", "neues", "nächste", "nächster",  # i18n-allow
    }),
    "es": frozenset({  # i18n-allow: Spanish spoken commands (§1 list #3)
        "punto", "puntos", "coma", "aparte", "seguido", "interrogación",  # i18n-allow
        "interrogacion", "exclamación", "exclamacion", "guion", "guión",  # i18n-allow
        "raya", "comillas", "paréntesis", "parentesis", "párrafo", "parrafo",  # i18n-allow
        "línea", "linea", "viñeta", "vineta", "salto", "nueva", "nuevo",  # i18n-allow
        "siguiente",  # i18n-allow
    }),
}

#: The lookup the rarity filter actually uses: ordinary vocabulary plus the
#: number words and the spoken formatting commands, per language. Anything
#: absent from here is "rare".
_COMMON_WORDS: Final[dict[str, frozenset[str]]] = {
    code: base
    | _NUMBER_WORDS.get(code, frozenset())
    | _FORMAT_COMMAND_WORDS.get(code, frozenset())
    for code, base in _COMMON_WORDS_BASE.items()
}

# i18n-allow: number-word DATA (§1 list #3), same category as the tables above.
#
# The number words that mean a QUANTITY and nothing else — the only ones a guard
# may insist on. Every member of ``_NUMBER_WORDS`` that also has a common
# non-numeric sense is deliberately absent, because this check has no way to
# tell the two apart and the module's rule is that silence beats a veto:
#
# * ordinals ("first", "erste", "primero") — a rewrite legitimately drops them
#   ("the first thing we need" -> "the requirement");
# * fractions and vague amounts ("half", "quarter", "dozen", "halb", "viertel")
#   — "a quarter of the team" survives as "some of the team" in any faithful
#   sharpening, and "quarter" is also a period of the year;
# * "one" / "uno" — far more often an impersonal pronoun or an article than the
#   number 1 ("one of the things", "one never knows").
#
# What is left is unambiguous: a speaker who said "three" and got back a text
# with no three in it, in digits or in words, lost information.
_QUANTITY_WORDS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset({
        "zero", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
        "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
        "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
        "thousand", "million", "billion",
    }),
    "de": frozenset({  # i18n-allow: German quantity words (§1 list #3)
        "null", "zwei", "drei", "vier", "fünf", "sechs", "sieben",  # i18n-allow
        "acht", "neun", "zehn", "elf", "zwölf", "dreizehn", "vierzehn",  # i18n-allow
        "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn",  # i18n-allow
        "zwanzig", "dreißig", "dreissig", "vierzig", "fünfzig",  # i18n-allow
        "sechzig", "siebzig", "achtzig", "neunzig", "hundert", "tausend",  # i18n-allow
        "million", "millionen",  # i18n-allow
    }),
    "es": frozenset({  # i18n-allow: Spanish quantity words (§1 list #3)
        "cero", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho",  # i18n-allow
        "nueve", "diez", "once", "doce", "trece", "catorce", "quince",  # i18n-allow
        "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte",  # i18n-allow
        "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta",  # i18n-allow
        "noventa", "cien", "ciento", "mil", "millón", "millones",  # i18n-allow
    }),
}


# i18n-allow: the two non-English tables below are grammar DATA (§1 list #3) —
# the literal German and Spanish finite-verb and negation forms a guard must
# contain in order to notice that a clause lost the word that carried it.
#
# THE WORDS A FORMATTER MAY NEVER SIMPLY DELETE.
#
# Every other loss check here is about words that are unusual (``rare_tokens``),
# countable (``_digit_runs``) or named by the user (``protected``). This one
# covers the opposite end of the frequency curve, and it exists because that end
# turned out to be completely unguarded: measured on 68 live polished dictations,
# 33 lost at least one word and exactly ONE was ever rejected. The reported
# symptom was "it cuts the 'is' out of my sentence" — verbatim, "what I've meant
# IS this expanded term" came back as "I meant this expanded term", and the
# German "weil IST die Transkription" lost it too.  # i18n-allow: quoted defect
#
# Nothing could catch that. A copula is two or three characters, so it is below
# ``_RARE_MIN_CHARS``; it is in ``_COMMON_WORDS`` by construction, so it is never
# "rare"; and one word out of sixty-seven is a 0.985 ratio, nowhere near
# ``max_shrink``. The word-count band cannot be tightened to see it either — a
# band narrow enough to notice one missing verb rejects every legitimate
# tightening the pass exists to perform.
#
# ADMISSION RULE: an entry qualifies only if a sentence that loses it becomes
# ungrammatical or untrue. That is finite verb forms — copula, auxiliary, modal —
# and negations. Deliberately ABSENT, even though they are function words:
#
# * articles and pronouns ("the", "der", "it", "es") — a faithful restructuring
#   legitimately drops them ("look how they did it" -> "look how it works");
# * conjunctions ("and", "und", "dass") — splitting a run-on into two  # i18n-allow
#   sentences is the pass's job, and the conjunction is what it spends;
# * prepositions — they move with the phrase they govern in any repunctuation.
#
# The lists overlap across languages ("will" is a German verb and an English
# modal, "es" is a German pronoun and the Spanish copula), and that costs
# nothing: unlike a filler table, a wrong language tag here can only make the
# guard fire where it need not, and the penalty for firing is that the user
# keeps their own words.
_ESSENTIAL_WORDS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset({
        "is", "are", "am", "was", "were", "be", "been",
        "has", "have", "had", "do", "does", "did",
        "will", "would", "can", "could", "shall", "should", "may", "might",
        "must", "ought",
        "not", "no", "never", "none", "nothing", "nobody", "neither", "nor",
        "without", "cannot",
        "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't",
        "haven't", "hasn't", "hadn't", "won't", "wouldn't", "can't",
        "couldn't", "shouldn't", "mustn't",
    }),
    "de": frozenset({  # i18n-allow: German grammar data (§1 list #3)
        "ist", "sind", "bin", "bist", "seid", "war", "warst", "waren",  # i18n-allow
        "wart", "sei", "seien", "wäre", "wären",  # i18n-allow
        "habe", "hast", "hat", "haben", "habt", "hatte", "hattest",  # i18n-allow
        "hatten", "hättest", "hätte", "hätten",  # i18n-allow
        "werde", "wirst", "wird", "werden", "werdet", "wurde", "wurden",  # i18n-allow
        "würde", "würden",  # i18n-allow
        "kann", "kannst", "können", "könnt", "konnte", "konnten",  # i18n-allow
        "könnte", "könnten", "muss", "musst", "müssen", "müsst",  # i18n-allow
        "musste", "mussten", "müsste", "müssten",  # i18n-allow
        "soll", "sollst", "sollen", "sollt", "sollte", "sollten",  # i18n-allow
        "will", "willst", "wollen", "wollt", "wollte", "wollten",  # i18n-allow
        "darf", "darfst", "dürfen", "dürft", "durfte", "durften",  # i18n-allow
        "mag", "mögen", "möchte", "möchten",  # i18n-allow
        "nicht", "kein", "keine", "keinen", "keinem", "keiner", "keines",  # i18n-allow
        "nichts", "nie", "niemals", "niemand", "ohne",  # i18n-allow
    }),
    "es": frozenset({  # i18n-allow: Spanish grammar data (§1 list #3)
        "es", "son", "soy", "eres", "somos", "era", "eran", "eras",  # i18n-allow
        "fue", "fueron", "fui", "sea", "sean",  # i18n-allow
        "está", "están", "estoy", "estás", "estamos", "estaba",  # i18n-allow
        "estaban", "estuvo", "estuvieron",  # i18n-allow
        "ha", "han", "he", "hemos", "había", "habían", "hay", "haya",  # i18n-allow
        "puede", "pueden", "puedo", "puedes", "podemos", "podía",  # i18n-allow
        "podría", "podrían", "debe", "deben", "debo", "debes",  # i18n-allow
        "debería", "deberían", "tiene", "tienen", "tengo", "tienes",  # i18n-allow
        "no", "nunca", "jamás", "nada", "nadie", "ninguno", "ninguna",  # i18n-allow
        "ni", "sin",  # i18n-allow
    }),
}


def normalize_for_compare(text: str) -> str:
    """Collapse *text* to the form used to decide "did anything change at all".

    Unicode-normalised (NFKC), with runs of INLINE whitespace collapsed to one
    space — and nothing else: punctuation, capitalization and line structure are
    exactly what the polish pass is FOR, so normalising them away would report a
    successful repunctuation as ``unchanged`` and throw the result away.

    Line breaks survive as a single ``"\\n"`` on purpose. The pass's paragraph
    and list work is made ENTIRELY of line breaks — a transcript split into
    paragraphs, or an enumeration laid out one item per line, is often the same
    words with only ``"\\n"`` added — and the first version of this function
    collapsed those into spaces, reported the answer as ``unchanged``, and threw
    the only formatting the user asked for on the floor. How a break is spelled
    (``"\\n"``, ``"\\r\\n"``, a blank line) still normalises to one form: the
    comparison cares that the text was split, not which convention split it.
    """
    flat = unicodedata.normalize("NFKC", str(text or ""))
    flat = flat.replace("\r\n", "\n").replace("\r", "\n")
    flat = _LINE_BREAK_RUN_RE.sub("\n", flat)
    return _INLINE_SPACE_RE.sub(" ", flat).strip()


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


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance between two short tokens.

    Written out rather than pulled from a dependency because this module is on
    the dictation path and must stay import-free (same discipline as
    ``scrub_for_voice``, AP-11). Two rolling rows, so the cost is O(len(a) *
    len(b)) time and O(len(b)) space over strings that are words.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        current = [i]
        for j, ch_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + (ch_a != ch_b),  # substitution
                )
            )
        previous = current
    return previous[-1]


def _is_repair_of(lost: str, candidate: str) -> bool:
    """Whether *candidate* is *lost* spelled correctly, rather than a new word."""
    if not candidate:
        return False
    span = max(len(lost), len(candidate))
    if not span:
        return False
    return _edit_distance(lost, candidate) / span <= _REPAIR_MAX_DISTANCE_SHARE


def _was_repaired(lost: str, out_tokens: Sequence[str]) -> bool:
    """Whether the output carries a corrected spelling of the token *lost*.

    This is the difference between the two things a vanished rare token can
    mean, and the guard is useless until it can tell them apart:

    * the formatter **dropped** a word the speaker said — ``Anushka`` becoming
      ``her``, a protected term deleted outright. Nothing in the answer stands
      where it was, and the guard must reject.
    * the formatter **repaired** what the recognizer misheard — ``deskto``
      becoming ``desktop``, ``haboogle`` becoming ``hab Google``. The word is
      still there; only its spelling changed, towards the one the speaker
      actually said.

    Treating the second as the first is not a conservative default, it is the
    guard failing exactly where it is needed most: a misrecognized word is
    ALWAYS rare — that is what being misrecognized makes it — so the rarity
    filter protects the recognizer's mistakes in direct proportion to how badly
    it mangled them. Measured on the live history, 30 of 71 polish runs (42 %)
    were thrown away this way, and the worse the transcript, the likelier the
    repair was refused.

    Candidates are single output tokens plus runs of up to
    :data:`_REPAIR_MAX_SPLIT`, because one misheard token is often two spoken
    words run together.
    """
    for size in range(1, _REPAIR_MAX_SPLIT + 1):
        for start in range(len(out_tokens) - size + 1):
            if _is_repair_of(lost, "".join(out_tokens[start : start + size])):
                return True
    return False


def _digit_runs(text: str) -> frozenset[str]:
    """Every number in *text*, with grouping separators flattened away."""
    return frozenset(_DIGIT_RUN_RE.findall(_DIGIT_SEPARATOR_RE.sub("", str(text or ""))))


#: A produced list line: "- ", "• ", "* ", "3. ", "3) " at the start of a
#: line. What the discount below accepts as EVIDENCE that a spoken marker
#: command was actually converted rather than merely deleted.
_LIST_LINE_RE = re.compile(r"(?m)^[ \t]*(?:[-•*]|\d{1,3}[.)])\s")

#: A marker command is at most two words ("next point", "nächster Punkt"),  # i18n-allow
#: so one produced list line licenses at most this many discounted tokens.
_MAX_MARKER_WORDS_PER_LINE = 2


def _list_lines(text: str) -> int:
    """How many list-marker lines *text* contains."""
    return len(_LIST_LINE_RE.findall(str(text or "")))


def _ratio_word_counts(
    raw: str, polished: str, commands: frozenset[str]
) -> tuple[int, int]:
    """The two sides of the word-count band, with formatting commands settled.

    The band compares CONTENT, and a spoken command is not content: converted
    correctly it becomes a mark ("comma" -> ","), so a bullet dictation that
    names the marker before every item HALVES its word count and an
    unadjusted band rejects exactly the answers the prompt asked for. But a
    naive "subtract every command word" discount opens two holes the first
    version of this function shipped with, so what is subtracted is decided
    per occurrence:

    * an occurrence present on BOTH sides is a content homonym (a German
      sentence about the most important "Punkt") — subtracted from both  # i18n-allow
      sides, so it cancels instead of shrinking only the denominator and
      pushing an unchanged answer over the growth ceiling;
    * a RAW-only occurrence is a command the model may have converted — but
      the discount has to be EARNED: it is granted only up to
      ``_MAX_MARKER_WORDS_PER_LINE`` tokens per NEW list line in the answer.
      A model that summarised a commanded dictation into plain prose shows no
      list lines, earns no discount, and is caught by ``ratio_shrink``
      exactly as before this function existed;
    * an OUTPUT-only occurrence is a word the model ADDED — never discounted,
      so padding an answer with command vocabulary ("mark mark mark") counts
      as the growth it is.

    Punctuation commands earn no discount from list lines and need none: a
    dictation would have to be nearly half command words before their loss
    trips the shrink floor, and real punctuation-command density sits far
    below that.

    ``total`` and ``hits`` come from two different tokenizers on purpose
    (``count_words`` = ``\\w+``, ``_compare_tokens`` = ``[\\w']+``); the
    mismatch is at most the apostrophes, and both sides share it.

    A side that discounts to zero is returned as zero: the caller's
    ``if raw_words:`` skip then treats a commands-only dictation like an
    empty one, which it is, content-wise.
    """
    raw_total = count_words(raw)
    out_total = count_words(polished)
    if not commands or not raw_total:
        return raw_total, out_total
    raw_hits = Counter(t for t in _compare_tokens(raw) if t in commands)
    out_hits = Counter(t for t in _compare_tokens(polished) if t in commands)
    shared = sum((raw_hits & out_hits).values())
    converted = sum((raw_hits - out_hits).values())
    evidence = (
        max(0, _list_lines(polished) - _list_lines(raw))
        * _MAX_MARKER_WORDS_PER_LINE
    )
    return (
        max(0, raw_total - shared - min(converted, evidence)),
        max(0, out_total - shared),
    )


def _lost_quantity_word(raw: str, out: str, *, language: str) -> bool:
    """Whether a spoken QUANTITY vanished without a digit taking its place.

    The gap :func:`_digit_runs` cannot see. That check compares digits, so a
    speaker who said "send it to three people" and got back "send it to the
    team" loses the three silently: there was never a digit to miss, and
    "three" is common vocabulary so the rare-token filter waves it through too.

    Used ONLY by :func:`precision_drift_reason`, and that narrowness is the
    point rather than an omission. In the ordinary polish pass the model is
    forbidden from replacing words at all, so this failure needs a prompt
    violation AND a second guard to already have missed it; in precision mode
    replacing words is the licensed behaviour and the rare-token check that
    backstopped it is gone, which is exactly when a quantity can walk out
    unnoticed. Adding it to both would also change the verdict on transcripts
    the ordinary guard has been passing correctly for months, for a failure it
    is not exposed to.

    A quantity is NOT lost when the answer carries it as a digit — "seven" ->
    "7" is the normalization the whole feature exists to perform, so any digit
    at all in the output excuses a vanished number word. That is deliberately
    coarse: pairing each word with its value would need a spelled-number parser
    per language, and getting it wrong rejects a correct answer.

    Returns ``False`` for any language with no quantity table, like every other
    frequency-driven check here.
    """
    quantities = _QUANTITY_WORDS.get(_language_key(language))
    if not quantities:
        return False
    spoken = {token for token in _compare_tokens(raw) if token in quantities}
    if not spoken:
        return False
    survivors = set(_compare_tokens(out))
    vanished = spoken - survivors
    if not vanished:
        return False
    # Any digit in the answer that the transcript did not already carry is a
    # spelled number written out as a numeral. Which word became which digit is
    # not worth resolving — see the docstring.
    return not (_digit_runs(out) - _digit_runs(raw))


def _lost_essential_word(raw: str, out: str, *, language: str) -> bool:
    """Whether a finite verb or a negation was CUT OUT rather than rewritten.

    The distinction in that sentence is the entire guard, and it is what makes
    the check usable instead of merely strict. Both of these remove an "is" from
    the transcript, and only one of them is a defect:

    * ``"which actions is called"`` -> ``"which actions are called"`` is the
      agreement repair the pass is FOR. The word was replaced, in place.
    * ``"what I've meant is this expanded term"`` -> ``"I meant this expanded
      term"`` is the reported bug. The word was deleted and nothing stands
      where it stood; the sentence lost its verb and the speaker cannot see
      what is missing, because nothing marks the hole.

    A frequency table cannot tell those apart — both lose exactly one "is" — so
    the question is answered POSITIONALLY instead, with :mod:`difflib` over the
    two token sequences. An essential word inside a ``delete`` opcode was cut;
    inside a ``replace`` opcode something took its place and the pass is
    entitled to the benefit of the doubt. Measured over the same 68 live
    dictations, that split clears every repair a count-based rule would have
    mistaken for a loss: the two German agreement fixes, ``"will catch up"`` ->
    ``"catches up"``, and ``"what I have meaning"`` -> ``"what I mean"``.

    The deleted span must also consist of NOTHING BUT these words, and that
    second condition is what keeps the check off two things the pass is
    expressly allowed to do. Both delete a listed word, and neither is the
    defect:

    * a false start — ``"I will, I mean I would rather send it"`` -> ``"I would
      rather send it"`` deletes ``["i", "will", "i", "mean"]``;
    * a reduced relative clause — ``"the people who are in charge"`` -> ``"the
      people in charge"`` deletes ``["who", "are"]``.

    In each the model removed a CONSTRUCTION, and the word travelled out with
    it. The reported defect looks different: the deleted span is the bare word
    and nothing else — ``["is"]``, ``["ist"]``, ``["can"]``, ``["not"]`` — the
    sentence around it untouched, which is a formatter reaching into a finished
    clause and taking the verb out of it.

    Two implementation details are load-bearing rather than incidental:

    * ``autojunk=False``. :class:`difflib.SequenceMatcher` otherwise treats a
      token appearing in more than 1 % of a sequence longer than 200 as junk —
      which is a description of exactly these words. Left on, the opcodes for a
      long dictation stop meaning what this function reads them as.
    * The ``Counter`` pre-check. The diff is O(n²) in the worst case and the
      overwhelmingly common answer is "nothing was lost", so the cheap set
      arithmetic decides that case and the diff only runs on the transcripts
      that could still fail.

    Returns ``False`` for a language with no table, like every other
    vocabulary-driven check here — silence beats a veto.
    """
    essential = _ESSENTIAL_WORDS.get(_language_key(language))
    if not essential:
        return False
    before = _compare_tokens(raw)
    after = _compare_tokens(out)
    if not before:
        return False
    # Cheap gate: no essential token lost its last occurrence count-wise means
    # no essential token can sit in a delete opcode either.
    if not (essential & set(Counter(before) - Counter(after))):
        return False
    opcodes = difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes()
    return any(
        tag == "delete" and all(token in essential for token in before[i1:i2])
        for tag, i1, i2, _j1, _j2 in opcodes
    )


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

    The guard for the ORDINARY polish pass, where the model was told to change
    how the text is written and nothing about which words it is made of. Every
    check runs, the rare-token half of ``lost_term`` included.

    The checks run cheapest-and-most-unambiguous first, and the returned code is
    the FIRST one that fired — it is stored on the history row, so it has to name
    the most informative cause rather than the last one evaluated. In particular
    a translation is reported as ``language_flip`` (which says what happened)
    instead of ``lost_term`` (which is merely its symptom), so the language check
    deliberately precedes the token checks.

    Every returned value is a member of :data:`DRIFT_REASONS`.
    """
    return _polish_drift_reason(
        raw,
        polished,
        language=language,
        protected=protected,
        max_shrink=max_shrink,
        max_growth=max_growth,
        preserve_rare_tokens=True,
    )


def precision_drift_reason(
    raw: str,
    polished: str,
    *,
    language: str,
    protected: Sequence[str],
    max_shrink: float,
    max_growth: float,
) -> str:
    """The guard for a polish run with PRECISION MODE on.

    Identical to :func:`drift_reason` in every check but one: the rare-token
    half of ``lost_term`` is skipped. That check rejects an answer in which an
    uncommon word from the transcript is gone and nothing in the output is a
    repaired spelling of it — which is the exact shape of the substitution
    precision mode exists to make ("the program is broken" -> "the application
    is faulty" loses both ``program`` and ``broken``). Running precision answers
    through the ordinary guard does not make them safer; it rejects nearly all
    of them, and the user sees a switch that changes nothing.

    What is traded away is real and worth naming: the rare-token check is the
    strongest defence against a model quietly replacing a word it did not
    understand. Everything that survives is what still means something once
    substitution is licensed —

    * ``empty`` / ``meta_output`` — the model started talking instead of writing;
    * the word-count band — it may sharpen a sentence, not write a new one;
    * ``language_flip`` — precision is not a licence to translate;
    * ``lost_number`` — a quantity is never a wording choice;
    * ``lost_term`` for PROTECTED terms — names, the wake word and the STT
      dictionary come from the user, and no register change touches them;
    * ``lost_verb`` — sharpening a sentence never means deleting the verb or
      the negation that made it a sentence.

    That is also why the mode ships off and is a deliberate switch: the trade is
    the user's to make, not ours to make for them.

    Every returned value is a member of :data:`PRECISION_DRIFT_REASONS`.
    """
    return _polish_drift_reason(
        raw,
        polished,
        language=language,
        protected=protected,
        max_shrink=max_shrink,
        max_growth=max_growth,
        preserve_rare_tokens=False,
    )


def _polish_drift_reason(
    raw: str,
    polished: str,
    *,
    language: str,
    protected: Sequence[str],
    max_shrink: float,
    max_growth: float,
    preserve_rare_tokens: bool,
) -> str:
    """The shared body of the two polish guards above.

    Written once with a flag — deliberately unlike the polish/translate split,
    which is two functions because those two disagree about what the job IS
    (a language change is the defect in one and the whole point in the other).
    These two agree about everything and differ by one check, so a second copy
    would be an invitation for the checks to drift apart silently: the pair that
    matters most, ``language_flip`` and the protected-term half of
    ``lost_term``, must stay byte-identical in both modes.

    The flag is keyword-only and has no default on purpose. Both callers state
    which contract they want, at the call site, where a reader can see it.
    """
    if not str(polished or "").strip():
        return "empty"
    if _is_meta_output(raw, polished):
        return "meta_output"

    # Detected up front because TWO checks below want the text's own language
    # rather than its label: the command-word discount here and the rare-token
    # lookup further down. The check ORDER is unchanged — a ratio rejection
    # still outranks a language flip on the history row.
    raw_language = detect_text_language(raw)
    out_language = detect_text_language(polished)

    # The band runs on content words: the spoken formatting commands are
    # settled first (see ``_ratio_word_counts``). The table follows the TEXT
    # where the detector has an opinion and the label only as a fallback,
    # exactly like the rare-token lookup below — a mislabelled command-heavy
    # dictation would otherwise earn no discount and be rejected as a shrink,
    # the precise failure this discount exists to prevent.
    commands = _FORMAT_COMMAND_WORDS.get(
        _language_key(raw_language if raw_language != "unknown" else language),
        frozenset(),
    )
    raw_words, out_words = _ratio_word_counts(raw, polished, commands)
    if raw_words:
        ratio = out_words / raw_words
        if ratio < max_shrink:
            return "ratio_shrink"
        if ratio > max_growth:
            return "ratio_growth"

    # Skipped whenever EITHER side is undecided: the detector only knows
    # de/en/es, so "unknown" means "no opinion", never "different".
    if raw_language != "unknown" and out_language != "unknown":
        if raw_language != out_language:
            return "language_flip"

    # Asymmetric on purpose: "seven" -> "7" ADDS a digit run and must stay legal.
    if _digit_runs(raw) - _digit_runs(polished):
        return "lost_number"

    # The spelled-out twin of the check above, and precision-only — see
    # ``_lost_quantity_word`` for why the ordinary pass is not exposed to this
    # failure and must not inherit the check.
    if not preserve_rare_tokens and _lost_quantity_word(
        raw, polished, language=raw_language if raw_language != "unknown" else language
    ):
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

    # Runs in BOTH modes, and unlike the rare-token check below it is not
    # something precision mode may trade away. Precision licenses a change of
    # WORD CHOICE — "the program is broken" -> "the application is faulty" — and
    # nothing in that licence reaches the verb that holds the clause together.
    # A sharpened sentence still has one; a sentence the formatter cut the "is"
    # out of has none, and it is the one loss the speaker cannot see for
    # themselves, because a missing word leaves no mark on the page.
    if _lost_essential_word(
        raw, polished, language=raw_language if raw_language != "unknown" else language
    ):
        return "lost_verb"

    # Skipped entirely in precision mode, where replacing an uncommon word with
    # a more precise one is the job rather than the defect. See
    # :func:`precision_drift_reason` for what is traded away and what still
    # covers it.
    if preserve_rare_tokens:
        # The frequency list has to describe the TEXT, not its label. Where the
        # two disagree the label loses, because trusting it fails silently and
        # completely: an English transcript looked up against the German word
        # list makes every ordinary English word "rare", so the guard fires on
        # any wording change at all and the dictation is delivered unpolished
        # with `rejected_drift` on the row and nothing saying why.
        #
        # The disagreement is real and has its own cause upstream — a
        # segment-sized upload lets Whisper re-decide the language, and on a
        # short segment it sometimes TRANSLATES rather than transcribes, so a row
        # can carry a German tag and an English transcript. That is fixed where
        # it happens; this is the guard declining to compound it.
        # `raw_language` is already computed above, so the correction costs
        # nothing.
        lookup = raw_language if raw_language != "unknown" else language
        vanished = rare_tokens(raw, language=lookup) - rare_tokens(
            polished, language=lookup
        )
        if vanished:
            # A vanished rare token is only a LOSS when nothing in the answer
            # stands where it was. Where a corrected spelling does, it is the
            # repair this pass exists to make — see ``_was_repaired`` for why
            # conflating the two disarmed the guard on precisely the transcripts
            # that needed it.
            out_tokens = _compare_tokens(polished)
            if any(not _was_repaired(token, out_tokens) for token in vanished):
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
        # The same command-word settlement the polish band runs
        # (``_ratio_word_counts``), keyed on the SOURCE language the detector
        # names — the commands were spoken there, and the translated side
        # rarely contains them at all, which makes the settlement effectively
        # a raw-side discount gated on produced list lines. The wide translate
        # band absorbs single-word markers on its own, but a two-word marker
        # ("nächster Punkt") spends two-thirds of a bullet dictation's words  # i18n-allow
        # on commands and lands under even this band's floor.
        commands = _FORMAT_COMMAND_WORDS.get(
            _language_key(detect_text_language(raw)), frozenset()
        )
        raw_words, out_words = _ratio_word_counts(raw, translated, commands)
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
    "PRECISION_DRIFT_REASONS",
    "TRANSLATE_DRIFT_REASONS",
    "drift_reason",
    "normalize_for_compare",
    "precision_drift_reason",
    "rare_tokens",
    "translate_drift_reason",
]
