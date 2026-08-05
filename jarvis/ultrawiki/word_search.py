"""Word search — one word in, the passages its meaning-neighbourhood reaches.

The shape of the problem
========================

Hybrid search (``jarvis.ultrawiki.search``) is built around a QUESTION: it has
enough words to fuse a keyword leg with a vector leg and grade the result. A
single word gives it almost nothing to work with. Yet a one-word query is how
people actually hunt a concept they cannot name exactly — they know roughly
what it is *about* and nothing about how the corpus spells it.

So this module inverts the order. It first asks the word lexicon
(:mod:`jarvis.ultrawiki.lexicon`) for the ~20 terms nearest the query word by
meaning, and only THEN retrieves — with four legs instead of two:

    word          the exact term, keyword leg          (what you typed)
    semantic      the term's own vector, over PASSAGES (what it means)
    related       the neighbour terms, keyword leg     (how the corpus spells it)
    neighbourhood the neighbourhood's centroid vector  (the concept around it)
              │
              ▼
    RRF FUSION  (jarvis.ultrawiki.search.fuse_legs — the same ranking rules
                 as hybrid search: one vote per item per leg, term rarity,
                 age decay, recency tiebreak)
              │
              ▼
    PASSAGE LOCALIZATION  which chunk of each winning item actually carries
                          the vocabulary, with its char span

Passage localization is the point
=================================

An item can be a 200 KB file. Migration 0001 already stores it as many
passages with ``chunk_index``/``char_start``/``char_end``, but retrieval
collapsed every hit back to one row per item and threw the span away, so the
answer was "it is somewhere in this file". Here every hit carries the passages
that actually hold the vocabulary, with the offsets to find them — which is
also what makes the neighbour expansion worth anything: a neighbour term
usually matches ONE paragraph of a long document, not the document.

Deliberately absent
===================

**No rerank.** The rerank stage grades "how well does this answer the
question", and a word is not a question — the grade would be noise wearing a
number. Fusion order stands, and ``rerank_score`` is honestly ``None``.

**No relevance floor.** This is an explicit exploration surface: the user
typed the word and can see the evidence. Floors belong to unsolicited
surfaces (design doc 03).

Every degraded path is a NAMED outcome (:class:`WordSearchStatus`), never an
empty list the reader has to interpret: an empty store, an unknown word, a
known word whose matches all lost, and a lexicon that could not produce
neighbours are four different situations with four different next steps.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from jarvis.ultrawiki.lexicon import (
    DEFAULT_NEIGHBOURS,
    NEIGHBOUR_SOURCE_NONE,
    TermNeighbour,
    centroid,
    normalize_word,
    word_tokens,
)
from jarvis.ultrawiki.types import SearchResult, WordSearchStatus

log = logging.getLogger(__name__)

__all__ = [
    "Passage",
    "WordHit",
    "WordSearchOutcome",
    "word_search",
    "MAX_NEIGHBOURS",
    "MAX_PASSAGES_PER_HIT",
]

#: Candidates each leg contributes to the fusion pool, matching
#: ``search.LEG_POOL`` so no leg can dominate by returning more rows.
_LEG_POOL = 20

#: Hard ceiling on the neighbourhood a caller may request. Twenty is the
#: default and the useful size; the cap exists so a REST parameter cannot turn
#: one search into a corpus-wide term dump.
MAX_NEIGHBOURS = 50

#: How many neighbour terms enter the KEYWORD expansion. Fewer than the list
#: the user is shown, on purpose: an FTS query ORs its tokens, so every extra
#: term widens the candidate set, and past roughly a dozen the weakest
#: neighbours contribute noise instead of recall. The full list still drives
#: passage scoring, where a weak term costs nothing.
_EXPANSION_TERMS = 12

#: Passages reported per hit. Three is enough to show that an answer spans a
#: document without turning one hit into a wall of text.
MAX_PASSAGES_PER_HIT = 3

#: Total passages loaded for localization across ALL hits of one search. One
#: large file is hundreds of passages, and the scoring below needs a sample of
#: the winners, never the corpus.
_PASSAGE_BUDGET = 600

#: A neighbour term never counts as much as the word that was actually typed,
#: however similar it is. These are the RRF leg weights, expressed as a share
#: of the corresponding hybrid-search knob so a user who silences the keyword
#: or vector leg silences it here too.
_RELATED_LEG_SHARE = 0.6
_NEIGHBOURHOOD_LEG_SHARE = 0.7

#: Floor under a neighbour's weight in passage scoring. Even the twentieth
#: neighbour is evidence that a passage is on topic; it just is not much.
_MIN_TERM_WEIGHT = 0.2

#: Passages per item pulled from the vector legs. More than one, because
#: "which part of this document" is exactly what a word search is asking.
_VECTOR_PER_ITEM = 2

#: How much a passage gains for being the one a vector leg actually matched.
#: Additive and small: it breaks ties in favour of the passage the embedding
#: model chose, without letting it outrank a passage that visibly carries more
#: of the vocabulary.
_VECTOR_MATCH_BONUS = 0.25

#: The score an UNRELATED passage gets. Both backends measure cosine
#: distance and the store maps it with ``1 / (1 + distance)``, so a vector at
#: right angles to the query (cosine similarity 0 — no relation either way)
#: lands exactly here, anything positively related above, anything actively
#: opposed below. It is a property of that transform, not a tuned number.
_UNRELATED_SCORE = 0.5

#: How much of the BEST hit's relatedness a vector hit must keep to stay.
#:
#: Why a floor exists here at all: the hybrid path can hand a weak candidate
#: to the rerank model, which grades it absolutely and drops it. A word search
#: has no rerank (a word is not a question), so without a floor the ANN leg
#: would return its k nearest passages no matter how far away they are, and
#: "no matches" could never be said honestly — a search for a word this corpus
#: knows nothing about would answer with its twenty least-unrelated passages.
#: Relative rather than absolute on purpose: embedding models disagree wildly
#: about what an absolute cosine of 0.3 means, but "less than a third as
#: related as the best thing I found" travels across all of them.
_VECTOR_RELATIVE_FLOOR = 0.35


@dataclass(frozen=True, slots=True)
class Passage:
    """One located span of an item's text.

    ``char_start``/``char_end`` are offsets into the item's body, so a surface
    can jump to it or highlight it. ``terms`` names which of the searched
    words this span actually shows — the difference between "this file is
    relevant" and "here is the sentence".
    """

    document_id: int
    chunk_index: int
    char_start: int
    char_end: int
    text: str
    terms: tuple[str, ...] = ()
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class WordHit:
    """One item the word neighbourhood reached, with its located passages."""

    item_id: int
    source_id: str
    title: str
    snippet: str
    permalink: str
    timestamp_utc: str
    score: float
    matched_by: tuple[str, ...] = ()
    passages: tuple[Passage, ...] = ()


@dataclass(frozen=True, slots=True)
class WordSearchOutcome:
    """Everything one word search produced, including WHY it produced it."""

    word: str
    status: str = WordSearchStatus.OK.value
    #: ``"vector"`` | ``"cooccurrence"`` | ``"none"`` — how the neighbours
    #: were derived. See :mod:`jarvis.ultrawiki.lexicon`.
    neighbour_source: str = NEIGHBOUR_SOURCE_NONE
    #: Honest English explanation whenever something is degraded or empty.
    reason: str = ""
    neighbours: tuple[TermNeighbour, ...] = ()
    hits: tuple[WordHit, ...] = ()
    #: Vocabulary size / embedded share / harvest progress, so the surface can
    #: say "the word index is still being built" instead of "no results".
    lexicon: dict[str, int] = field(default_factory=dict)


async def word_search(
    store: Any,
    cfg: Any,
    word: str,
    *,
    k: int = 10,
    neighbours: int = DEFAULT_NEIGHBOURS,
    area_id: str | None = None,
) -> WordSearchOutcome:
    """Expand ``word`` into its meaning-neighbourhood and retrieve passages.

    ``k`` bounds the items returned; ``neighbours`` the size of the
    neighbourhood (capped at :data:`MAX_NEIGHBOURS`). Never raises for a
    missing provider, an empty corpus or an unknown word — each is a named
    :class:`~jarvis.ultrawiki.types.WordSearchStatus`.
    """
    term = normalize_word(word)
    wanted = max(0, min(int(neighbours), MAX_NEIGHBOURS))
    if not term:
        return WordSearchOutcome(
            word="",
            status=WordSearchStatus.NO_MATCHES.value,
            reason="the search word was blank",
        )

    lexicon_counts = await _lexicon_counts(store)
    if not await _corpus_has_items(store):
        return WordSearchOutcome(
            word=term,
            status=WordSearchStatus.EMPTY_INDEX.value,
            reason=(
                "nothing has been imported yet — approve a source and run a "
                "sync, then word search has something to look through"
            ),
            lexicon=lexicon_counts,
        )

    from jarvis.ultrawiki.lexicon import resolve_neighbours  # noqa: PLC0415 — lazy (AP-26)

    neighbour_list, source, reason, query_vector = await resolve_neighbours(
        store, cfg, term, limit=wanted, area_id=area_id
    )

    weights = _term_weights(term, neighbour_list)
    hits = await _retrieve(
        store,
        cfg,
        term,
        neighbour_list,
        query_vector=query_vector,
        k=int(k),
        area_id=area_id,
    )
    located = await _localize(store, hits, weights) if hits else []

    status = _status_for(
        hits=located,
        neighbour_source=source,
        word_is_known=await _word_is_known(store, term),
    )
    # The status explains the RESULT and leads; a degraded neighbour path is
    # secondary context. Told the other way round, "no word vectors yet" reads
    # as the reason a word the corpus has never seen found nothing, which
    # sends the reader to fix a provider instead of trying another word.
    reason = _join_reasons(_status_reason(status, term), reason)
    return WordSearchOutcome(
        word=term,
        status=status,
        neighbour_source=source,
        reason=reason,
        neighbours=tuple(neighbour_list),
        hits=tuple(located),
        lexicon=lexicon_counts,
    )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _term_weights(term: str, neighbours: list[TermNeighbour]) -> dict[str, float]:
    """How much each searched word counts when scoring a passage.

    The typed word (and every token of a typed phrase) counts fully; a
    neighbour counts as much as it is similar, floored so the tail of the list
    still contributes something.
    """
    weights = {token: 1.0 for token in word_tokens(term)}
    for neighbour in neighbours:
        for token in word_tokens(neighbour.term):
            weight = max(_MIN_TERM_WEIGHT, min(1.0, float(neighbour.similarity)))
            weights[token] = max(weights.get(token, 0.0), weight)
    return weights


async def _retrieve(
    store: Any,
    cfg: Any,
    term: str,
    neighbours: list[TermNeighbour],
    *,
    query_vector: list[float] | None,
    k: int,
    area_id: str | None,
) -> list[SearchResult]:
    """Run the four legs concurrently and fuse them.

    Every leg degrades to an empty list on its own: no vector provider means
    the two vector legs are simply absent and the keyword legs answer alone,
    which is the same contract the hybrid path keeps.
    """
    from jarvis.ultrawiki.search import fuse_legs, ranking_settings  # noqa: PLC0415 — lazy (AP-26)

    expansion = [
        neighbour.term for neighbour in neighbours[:_EXPANSION_TERMS] if neighbour.term
    ]
    knobs = ranking_settings(cfg)
    keyword_weight = knobs["keyword_weight"]
    vector_weight = knobs["vector_weight"]

    neighbourhood_vector = await _neighbourhood_vector(
        store, query_vector, neighbours
    )
    word_hits, related_hits, semantic_hits, concept_hits = await asyncio.gather(
        _keyword_leg(store, term, area_id=area_id),
        _keyword_leg(store, " ".join(expansion), area_id=area_id),
        _vector_leg(store, query_vector, area_id=area_id),
        _vector_leg(store, neighbourhood_vector, area_id=area_id),
    )
    # Leg ORDER decides which row survives the merge as the representative.
    # The exact-word keyword hit leads because its snippet is rendered around
    # the match; the vector hit follows because it carries passage provenance.
    fused = fuse_legs(
        [
            ("word", keyword_weight, word_hits),
            ("semantic", vector_weight, semantic_hits),
            ("related", keyword_weight * _RELATED_LEG_SHARE, related_hits),
            (
                "neighbourhood",
                vector_weight * _NEIGHBOURHOOD_LEG_SHARE,
                concept_hits,
            ),
        ],
        cfg=cfg,
        # No term-rarity signal here, deliberately: that factor scales a
        # candidate by how much of the query's RARE vocabulary its snippet
        # shows, and an EXPANDED query has no stable rare vocabulary — a
        # neighbour term absent from a snippet would push down exactly the
        # passages the expansion was meant to surface.
        signals=None,
    )
    return fused[: max(0, int(k))]


async def _keyword_leg(
    store: Any, query: str, *, area_id: str | None
) -> list[SearchResult]:
    """Keyword hits for one query string; empty and logged on any failure."""
    if not query.strip():
        return []
    try:
        return list(await store.keyword_search(query, k=_LEG_POOL, area_id=area_id))
    except Exception:  # noqa: BLE001 — one leg never fails the search
        log.warning("word search: keyword leg failed", exc_info=True)
        return []


async def _vector_leg(
    store: Any, query_vector: list[float] | None, *, area_id: str | None
) -> list[SearchResult]:
    """Passage-level ANN hits, or nothing when there is no vector to run.

    Uses ``vector_search_passages`` when the store has it so one document can
    contribute more than its opening passage; an older or third-party store
    falls back to ``vector_search`` and simply reports fewer passages.
    """
    if not query_vector:
        return []
    passages = getattr(store, "vector_search_passages", None)
    try:
        if callable(passages):
            results, reason = await passages(
                query_vector, _LEG_POOL, area_id=area_id, per_item=_VECTOR_PER_ITEM
            )
        else:
            results, reason = await store.vector_search(
                query_vector, _LEG_POOL, area_id=area_id
            )
    except Exception:  # noqa: BLE001 — one leg never fails the search
        log.warning("word search: vector leg failed", exc_info=True)
        return []
    if reason:
        log.info("word search: vector leg degraded: %s", reason)
    return _drop_unrelated(list(results))


def _drop_unrelated(hits: list[SearchResult]) -> list[SearchResult]:
    """Keep the vector hits that are actually related to the query.

    Two gates, both cheap and both necessary (see
    :data:`_VECTOR_RELATIVE_FLOOR`): a passage at right angles to the query is
    dropped outright, and one far behind the best hit of the SAME leg is
    dropped as well. The list is already sorted best-first, so this only ever
    trims a tail — it can never reorder anything.
    """
    if not hits:
        return hits
    best = hits[0].score
    if best <= _UNRELATED_SCORE:
        return []
    threshold = _UNRELATED_SCORE + (best - _UNRELATED_SCORE) * _VECTOR_RELATIVE_FLOOR
    return [hit for hit in hits if hit.score > _UNRELATED_SCORE and hit.score >= threshold]


async def _neighbourhood_vector(
    store: Any, query_vector: list[float] | None, neighbours: list[TermNeighbour]
) -> list[float] | None:
    """The centroid of the word and its nearest neighbours, or ``None``.

    Built from the term vectors the lexicon ALREADY stored, never from fresh
    provider calls: one search must cost at most one embedding round trip, not
    one per neighbour. When no term vectors are available the centroid is
    simply absent and that leg does not run.
    """
    if not query_vector or not neighbours:
        return None
    lookup = getattr(store, "term_vectors_for", None)
    if not callable(lookup):
        return None
    try:
        model, dim = await store.embedding_space()
        if not model or dim <= 0:
            return None
        stored = await lookup(
            [neighbour.term for neighbour in neighbours], model=model, dim=dim
        )
    except Exception:  # noqa: BLE001 — the concept leg is a bonus, never a failure
        log.debug("word search: neighbour vectors unavailable", exc_info=True)
        return None
    if not stored:
        return None
    vector = centroid([query_vector, *stored.values()])
    if vector is None or vector == list(query_vector):
        return None
    return vector


# ---------------------------------------------------------------------------
# Passage localization
# ---------------------------------------------------------------------------


async def _localize(
    store: Any, hits: list[SearchResult], weights: dict[str, float]
) -> list[WordHit]:
    """Attach the passages of each hit that actually carry the vocabulary.

    Best-effort and last: a store without ``passages_for_items`` — a
    third-party backend, a test fake — yields hits whose ``passages`` are
    empty, and the snippet still stands. Nothing here can change the ranking
    above it.
    """
    by_item: dict[int, list[dict[str, Any]]] = {}
    reader = getattr(store, "passages_for_items", None)
    if callable(reader):
        try:
            rows = await reader(
                [hit.item_id for hit in hits], limit=_PASSAGE_BUDGET
            )
        except Exception:  # noqa: BLE001 — localization is evidence, never a failure
            log.warning("word search: passage lookup failed", exc_info=True)
            rows = []
        for row in rows or []:
            by_item.setdefault(int(row["item_id"]), []).append(row)

    located: list[WordHit] = []
    for hit in hits:
        passages = _rank_passages(by_item.get(hit.item_id, []), weights, hit)
        located.append(
            WordHit(
                item_id=hit.item_id,
                source_id=hit.source_id,
                title=hit.title,
                snippet=hit.snippet,
                permalink=hit.permalink,
                timestamp_utc=hit.timestamp_utc,
                score=hit.score,
                matched_by=hit.matched_by,
                passages=passages,
            )
        )
    return located


def _rank_passages(
    rows: list[dict[str, Any]], weights: dict[str, float], hit: SearchResult
) -> tuple[Passage, ...]:
    """The best :data:`MAX_PASSAGES_PER_HIT` passages of one item.

    Scored on how much of the searched vocabulary a passage visibly shows,
    weighted by how close each word is to the one that was typed, plus a small
    bonus for the passage a vector leg matched. Ties keep document order, so
    two equally-good passages read top to bottom.
    """
    if not rows:
        # The vector leg already told us WHICH passage matched even when the
        # passage table is unreadable — reporting that beats reporting nothing.
        if hit.document_id is not None:
            return (
                Passage(
                    document_id=int(hit.document_id),
                    chunk_index=int(hit.chunk_index or 0),
                    char_start=int(hit.char_start or 0),
                    char_end=int(hit.char_end or 0),
                    text=hit.snippet,
                    terms=(),
                    score=0.0,
                ),
            )
        return ()
    scored: list[tuple[float, int, Passage]] = []
    for row in rows:
        text = str(row.get("text_norm", ""))
        present = set(word_tokens(text))
        matched = tuple(
            token for token in weights if token in present
        )
        score = sum(weights[token] for token in matched)
        if hit.document_id is not None and int(row["document_id"]) == int(hit.document_id):
            score += _VECTOR_MATCH_BONUS
        if score <= 0.0:
            continue
        scored.append(
            (
                score,
                int(row.get("chunk_index", 0) or 0),
                Passage(
                    document_id=int(row["document_id"]),
                    chunk_index=int(row.get("chunk_index", 0) or 0),
                    char_start=int(row.get("char_start", 0) or 0),
                    char_end=int(row.get("char_end", 0) or 0),
                    text=_trim(text),
                    terms=matched,
                    score=round(score, 4),
                ),
            )
        )
    if not scored:
        return ()
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple(passage for _, _, passage in scored[:MAX_PASSAGES_PER_HIT])


#: Passage text handed to the surface. Long enough to read as a paragraph,
#: short enough that three of them per hit stay scannable.
_PASSAGE_CHARS = 420


def _trim(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _PASSAGE_CHARS:
        return collapsed
    return collapsed[:_PASSAGE_CHARS].rstrip() + "…"


# ---------------------------------------------------------------------------
# Honest outcomes
# ---------------------------------------------------------------------------


def _status_reason(status: str, term: str) -> str:
    """The sentence that explains a status on its own."""
    if status == WordSearchStatus.UNKNOWN_WORD.value:
        return (
            f"'{term}' does not occur anywhere in the imported text, and no "
            "related word led anywhere either"
        )
    if status == WordSearchStatus.NO_MATCHES.value:
        return (
            f"'{term}' occurs in the corpus, but nothing it points at "
            "survived ranking — try a broader word"
        )
    return ""


def _join_reasons(*parts: str) -> str:
    """Chain the honest sentences that apply, most important first."""
    kept = [part.strip() for part in parts if part and part.strip()]
    return " — ".join(dict.fromkeys(kept))


def _status_for(
    *, hits: list[WordHit], neighbour_source: str, word_is_known: bool
) -> str:
    if hits:
        if neighbour_source == NEIGHBOUR_SOURCE_NONE:
            # Hits, but the neighbourhood is missing: the user got a plain
            # word search and should be told the expansion did not run.
            return WordSearchStatus.NEIGHBOURS_UNAVAILABLE.value
        return WordSearchStatus.OK.value
    if not word_is_known:
        return WordSearchStatus.UNKNOWN_WORD.value
    return WordSearchStatus.NO_MATCHES.value


async def _corpus_has_items(store: Any) -> bool:
    """Is there anything to search at all? Unknown counts as yes.

    A store that cannot answer must not be reported as empty — "nothing
    imported" sends the user to the Sources screen, and sending them there
    over a missing probe would be a lie.
    """
    probe = getattr(store, "live_item_count", None)
    if not callable(probe):
        return True
    try:
        return int(await probe()) > 0
    except Exception:  # noqa: BLE001 — an unreadable count is not an empty store
        log.debug("word search: item count probe failed", exc_info=True)
        return True


async def _word_is_known(store: Any, term: str) -> bool:
    """Does the word occur in the imported text at all?

    Distinguishes "I have never seen this word" from "I know it but nothing
    it points at ranked" — two empty screens with different next steps.
    Unknown counts as KNOWN, so a missing probe never accuses the user of
    typing a word the corpus might well contain.
    """
    probe = getattr(store, "term_document_frequency", None)
    if not callable(probe):
        return True
    tokens = word_tokens(term)
    if not tokens:
        return False
    try:
        frequencies = await probe(tokens)
    except Exception:  # noqa: BLE001 — an unreadable probe never accuses the user
        log.debug("word search: term frequency probe failed", exc_info=True)
        return True
    return any(int(frequencies.get(token, 0) or 0) > 0 for token in tokens)


async def _lexicon_counts(store: Any) -> dict[str, int]:
    """Vocabulary size + embedded share, or ``{}`` when unavailable."""
    probe = getattr(store, "lexicon_counts", None)
    if not callable(probe):
        return {}
    try:
        model, dim = await store.embedding_space()
        return dict(await probe(model=model, dim=dim))
    except Exception:  # noqa: BLE001 — a size report never fails a search
        log.debug("word search: lexicon counts unavailable", exc_info=True)
        return {}
