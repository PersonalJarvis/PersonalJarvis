"""Relevance gate for personal-memory context injection.

Deterministic, LLM-free, IO-free guard that decides whether a turn should
consult the personal memory (wiki vault today, UltraWiki later) at all, and
which retrieved hits are strong enough to be worth putting in front of the
model. Pure regex + set arithmetic — no disk, no network, no provider call
(AP-9/AP-11), so it is safe on the voice critical path and costs microseconds.

Why this exists
---------------
Retrieval has no null element. A similarity or keyword search always returns a
ranked list; "most similar" never means "similar enough". Injecting whatever
came back turns every unrelated question into a personalized non-sequitur —
the model sees a context block in its prompt and assumes it is meant to be
used. The observed failure mode: a general-knowledge question ("what is the
tallest tower in the world?") retrieves an unrelated personal note and the
answer arrives welded to the user's belongings.

Three independent layers, each useful on its own:

1. :func:`should_consult_memory` — BEFORE retrieval. Retrieval-first since the
   2026-08-04 recall audit (a whole two-day live log without a single injected
   turn): the local FTS search costs single-digit milliseconds, so refusing to
   even LOOK was pure loss. Only sub-``_MIN_TOKENS`` fragments still skip.
   Every other turn searches; what the verdict now decides is HOW STRICT the
   post-retrieval filter is. Explicit recollection, planning and personal
   lookups keep the standard coverage bar; world-knowledge-shaped and
   anchor-less turns get a STRICT bar (``strict=True`` →
   :data:`DEFAULT_STRICT_MIN_COVERAGE`), so the tallest-tower case stays
   uninjected not because nobody looked but because nothing retrieved covers
   the question.
2. :func:`relevant_hits` — AFTER retrieval. Coverage of the question's content
   terms plus a within-call relative floor. Deliberately NOT an absolute score
   threshold: ``SearchHit.score`` is documented as comparable only within a
   single call, so a fixed cutoff would be noise. Coverage counts only
   non-stopword terms — a generic question verb ("heisst", "called") in the
   denominator once made the bar unreachable for real German questions.
3. :func:`frame_context_block` — AT injection. The block states what it is and
   grants explicit permission to ignore it, instead of arriving as bare text
   the model reads as an instruction.

Policy note — a WRONG injection is still the worse failure, but the defense
is the strict coverage bar plus the framing contract, no longer a refusal to
search. The router brain additionally holds the ``wiki-recall`` tool and can
look something up deliberately when the injected context is not enough.

Languages: the matcher carries de/en/es tokens as equal peers (CLAUDE.md §1
closed-list item 3 — speech-recognition input vocabulary, matching data rather
than prose). Input is case-, umlaut- and accent-folded before matching, so
the German and Spanish spellings of a word reach the same pattern whether or
not the speech recogniser produced its diacritics.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from jarvis.brain.wiki_relevance_vocab import (
    GENERAL_KNOWLEDGE_PHRASES,
    LOOKUP_MARKERS,
    PERSONAL_MARKERS,
    PLANNING_PHRASES,
    RECOLLECTION_PHRASES,
    STOPWORDS,
)

__all__ = [
    "MemoryVerdict",
    "should_consult_memory",
    "relevant_hits",
    "frame_context_block",
    "content_terms",
    "fold_text",
]


# ---------------------------------------------------------------------------
# Folding
# ---------------------------------------------------------------------------

# German umlauts must become their transliterated digraphs (ä -> ae, ö -> oe,  # i18n-allow: quoted umlaut fold forms
# ü -> ue) because every German vocabulary entry is written in that form  # i18n-allow: quoted umlaut fold forms
# ("wofuer", "erzaehl", "vorschlaege"). A plain NFKD combining-strip produces
# the OTHER ascii form ("wofur", "erzahl") and silently disabled every
# umlaut-carrying German phrase against real STT output — the exact defect
# ``turn_planner._normalize`` documents and fixed for the delegation
# vocabulary; the memory gate never received the same fix.
_UMLAUT_TRANSLITERATION = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue"}  # i18n-allow: umlaut mapping data
)


def fold_text(text: str) -> str:
    """Lower-case and fold umlauts/accents so one pattern matches all spellings.

    The German sharp-s is expanded to a double s and the umlauts to their
    digraphs BEFORE the NFKD accent strip (which handles the remaining
    diacritics, e.g. Spanish accents). Public because keyword extraction in
    ``wiki_context`` must fold with exactly these rules to hit the same
    pre-folded :data:`~jarvis.brain.wiki_relevance_vocab.STOPWORDS`.
    """
    lowered = text.lower().replace("ß", "ss")  # i18n-allow: sharp-s input folding
    lowered = lowered.translate(_UMLAUT_TRANSLITERATION)
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# Internal shorthand kept for readability of the matchers below.
_fold = fold_text


# ---------------------------------------------------------------------------
# Patterns (all written already folded — no umlauts, no accents)
# ---------------------------------------------------------------------------

def _alternation(tokens: tuple[str, ...]) -> re.Pattern[str]:
    """Compile a word-bounded alternation over pre-folded vocabulary tokens.

    Longest first, so a multi-word phrase wins over a single word that is its
    prefix ("wie lange" must not be shadowed by "wie").
    """
    ordered = sorted(set(tokens), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in ordered) + r")\b")


# Ownership / self-reference: separates "the billing rules" from "MY rules".
_PERSONAL_RE = _alternation(PERSONAL_MARKERS)

# Explicit recollection phrasing — an unambiguous request to the memory,
# strong enough to consult even without an ownership marker.
_MEMORY_RE = _alternation(RECOLLECTION_PHRASES)

# Planning / recommendation / decision phrasing. The second turn class that
# earns a lookup without a possessive: advice for THIS person depends on what
# is known about them. Checked BEFORE the general-knowledge skip, because the
# two shapes overlap ("what is the fastest way home" is both a "what is" and a
# request for a plan) and the advice reading is the one that wants context.
_PLANNING_RE = _alternation(PLANNING_PHRASES)

# General-knowledge / definitional shape. On its own this is the world's
# knowledge, not the user's — the tallest-tower case. Only an ownership
# marker turns it into a memory question.
_WORLD_KNOWLEDGE_RE = _alternation(GENERAL_KNOWLEDGE_PHRASES)

# Question / lookup shape. Combined with an ownership marker this is a memory
# question; without one it is not enough on its own.
_LOOKUP_RE = _alternation(LOOKUP_MARKERS)

#: Utterances shorter than this (in tokens) are greetings, acknowledgements or
#: fragments — never worth a memory lookup.
_MIN_TOKENS = 3

_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class MemoryVerdict:
    """Outcome of the pre-retrieval gate.

    ``consult`` is the decision to search; ``strict`` asks the caller to apply
    the strict coverage bar (:data:`DEFAULT_STRICT_MIN_COVERAGE`) to whatever
    comes back — the retrieval-first stand-in for the old refusal to search.
    ``reason`` is a short machine-stable slug for logging and telemetry,
    never user-facing prose.
    """

    consult: bool
    reason: str
    strict: bool = False


_SKIP_TOO_SHORT = MemoryVerdict(False, "too_short")
_CONSULT_RECOLLECTION = MemoryVerdict(True, "recollection_phrase")
_CONSULT_PLANNING = MemoryVerdict(True, "planning_advice")
_CONSULT_PERSONAL_LOOKUP = MemoryVerdict(True, "personal_lookup")
_PROBE_WORLD_SHAPE = MemoryVerdict(True, "world_shape_probe", strict=True)
_PROBE_NO_ANCHOR = MemoryVerdict(True, "no_anchor_probe", strict=True)


def should_consult_memory(user_text: str) -> MemoryVerdict:
    """Decide how this turn consults the personal memory.

    Retrieval-first: the only turns that skip the (single-digit-millisecond,
    local) search are fragments too short to mean anything. Everything else
    searches, and the verdict grades the injection bar instead:

    1. Too short / no words -> skip (greetings, "ok", "danke").
    2. Explicit recollection phrasing -> consult, standard bar, even without a
       possessive ("do you remember where we were?").
    3. Planning / recommendation / decision phrasing -> consult, standard bar
       ("what should I do", "any ideas for the weekend"): advice is where
       knowing the person changes the answer. Checked before step 5 because
       the two shapes overlap and the advice reading is the useful one.
    4. Personal marker AND a lookup shape -> consult, standard bar.
    5. General-knowledge shape without a personal marker -> consult with the
       STRICT bar. The tallest-tower case: a weather question finds nothing
       that covers it and stays clean, while "who is <person in the vault>?"
       — the same grammatical shape — finally gets its page.
    6. Anything else -> consult with the STRICT bar. Statements ("Bruno hat
       morgen Geburtstag") used to be dropped unsearched; now the vault gets
       to offer a page that genuinely covers them.  # i18n-allow: quoted German utterance

    Opening the gate is not injecting: every consult verdict still has to pass
    :func:`relevant_hits` (strict verdicts against the strict coverage bar)
    and arrives wrapped by :func:`frame_context_block`.

    Never raises: an unusable input degrades to a skip verdict.
    """
    if not user_text or not user_text.strip():
        return _SKIP_TOO_SHORT
    folded = _fold(user_text)
    if len(_WORD_RE.findall(folded)) < _MIN_TOKENS:
        return _SKIP_TOO_SHORT
    if _MEMORY_RE.search(folded):
        return _CONSULT_RECOLLECTION
    if _PLANNING_RE.search(folded):
        return _CONSULT_PLANNING
    personal = bool(_PERSONAL_RE.search(folded))
    if personal and _LOOKUP_RE.search(folded):
        return _CONSULT_PERSONAL_LOOKUP
    if _WORLD_KNOWLEDGE_RE.search(folded) and not personal:
        return _PROBE_WORLD_SHAPE
    return _PROBE_NO_ANCHOR


# ---------------------------------------------------------------------------
# Post-retrieval filtering
# ---------------------------------------------------------------------------

#: A hit must cover at least this share of the question's content terms.
#: With two terms that means both; with three, two. One term matching out of
#: four is what produces an unrelated page that merely shares a common word.
DEFAULT_MIN_COVERAGE = 0.5

#: The coverage bar for STRICT verdicts (world-shaped or anchor-less turns
#: that only search because retrieval is cheap). A page must cover nearly the
#: whole question before it may ride along uninvited — "Berlin weather
#: tomorrow" must not inject the Berlin travel page on one shared word.
DEFAULT_STRICT_MIN_COVERAGE = 0.75

#: A hit must also score at least this share of the best hit in the SAME call.
#: Within one call the scores are documented as monotonic, so a relative floor
#: is meaningful where an absolute cutoff would not be.
DEFAULT_MIN_RELATIVE_SCORE = 0.35


def content_terms(query: str, *, min_length: int = 3) -> tuple[str, ...]:
    """Folded, deduplicated content words of a query, order preserved.

    Stopwords (function words plus generic question verbs, shared with the
    keyword extractor) are excluded: they inflate the coverage denominator
    without pointing at any page — "wie hiess mein Zahnarzt nochmal" must be
    judged on "zahnarzt", not on "hiess"/"nochmal".  # i18n-allow: quoted German utterance
    """
    seen: set[str] = set()
    terms: list[str] = []
    for token in _WORD_RE.findall(_fold(query)):
        if len(token) < min_length or token in seen or token in STOPWORDS:
            continue
        seen.add(token)
        terms.append(token)
    return tuple(terms)


def _covers(haystack: str, term: str) -> bool:
    """True when *term* appears in *haystack*, tolerating simple inflection.

    Substring containment on folded text handles the common German/Spanish
    endings ("dinner"/"dinners", "reise"/"reisen") without a stemmer
    dependency. Terms shorter than four characters must match as whole words,
    since a three-letter substring hits almost anything.
    """
    if len(term) < 4:
        return re.search(rf"\b{re.escape(term)}\b", haystack) is not None
    return term in haystack


def relevant_hits(
    hits: list[Any],
    query: str,
    *,
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    min_relative_score: float = DEFAULT_MIN_RELATIVE_SCORE,
) -> list[Any]:
    """Keep only the hits that actually answer *query*, order preserved.

    Each hit must satisfy BOTH:

    - **Coverage** — its title plus snippet contains at least ``min_coverage``
      of the query's content terms. A keyword index matches on ANY term, so a
      page sharing one common word with the question is otherwise indis-
      tinguishable from one that is on topic.
    - **Relative score** — at least ``min_relative_score`` of the best hit's
      score in this same call. Relative, never absolute: scores are documented
      as incomparable across calls.

    Coverage is judged over title + snippet + frontmatter. Including the
    frontmatter is load-bearing, not incidental: a page found through a
    ``search_aliases`` entry (the language bridge — a German question reaching
    an English page) matches ONLY in the frontmatter column and therefore has
    no body snippet at all. Judging such a hit on title+snippet alone would
    discard precisely the hits aliases exist to produce.

    Hits are expected to expose ``title``, ``snippet`` and ``score``; anything
    missing an attribute is treated as empty/zero rather than raising, so a
    future hit type cannot break the voice path.
    """
    if not hits:
        return []
    terms = content_terms(query)
    if not terms:
        return list(hits)

    scores = [float(getattr(hit, "score", 0.0) or 0.0) for hit in hits]
    best = max(scores) if scores else 0.0
    floor = best * min_relative_score if best > 0 else 0.0
    needed = max(1, round(len(terms) * min_coverage))

    kept: list[Any] = []
    for hit, score in zip(hits, scores, strict=False):
        if score < floor:
            continue
        haystack = _fold(
            f"{getattr(hit, 'title', '') or ''} "
            f"{getattr(hit, 'snippet', '') or ''} "
            f"{getattr(hit, 'frontmatter', '') or ''}"
        )
        if sum(1 for term in terms if _covers(haystack, term)) >= needed:
            kept.append(hit)
    return kept


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

#: Heading + usage contract for the injected block. The permission to ignore
#: is the point: a bare context block reads to a model as an instruction to
#: use it, which is how unrelated personal facts end up welded onto general
#: answers.
_CONTEXT_HEADER = (
    "## Wiki context — possibly relevant notes from the user's personal memory\n"
    "These were retrieved automatically by keyword and may have nothing to do "
    "with the question. Use a note only if it directly helps answer what was "
    "actually asked. If it does not, ignore it completely and do not mention "
    "it, allude to it, or work it into the answer. Never volunteer a personal "
    "detail the question did not ask for. If a note already answers the "
    "question, answer directly from it — do not call a knowledge-search tool "
    "to re-find the same fact; search only for what these notes do not cover."
)


def frame_context_block(entries: list[str]) -> str:
    """Wrap rendered ``**Title**: snippet`` entries in the usage contract.

    Returns an empty string for an empty list, so the caller can append
    unconditionally.
    """
    if not entries:
        return ""
    return _CONTEXT_HEADER + "\n\n" + "\n".join(entries)
