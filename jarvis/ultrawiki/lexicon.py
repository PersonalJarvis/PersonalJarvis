"""The word lexicon: turning one word into its meaning-neighbourhood.

Semantic search answers a QUESTION. It cannot answer a WORD — a passage
vector says what a passage is about, never what a single term means on its
own — and a one-word intent is exactly how people hunt a concept they cannot
name precisely. This module is the missing half: it harvests the vocabulary
the corpus actually uses, embeds it into the SAME space as the passages
(``jarvis.ultrawiki.lexicon_store``), and answers "the twenty words nearest to
X" as an ordinary nearest-neighbour query.

Two neighbour paths, and the second one is not a consolation prize
====================================================================

**By meaning (``vector``)** — the query word is embedded and compared against
the term vectors. This is the good answer: it finds words that mean something
similar even when they never appear in the same sentence.

**By company (``cooccurrence``)** — with no embedding provider, no vector
extension, or a lexicon that has not been built yet, the module samples the
passages that literally contain the word and ranks the OTHER words in them by
how much more often they appear there than in the corpus at large (a plain
lift score). It needs no provider, no key and no network, which is why it
exists: an install with a keyword-only store still gets a usable word search
instead of an empty screen. It is blunter — it finds words that keep company
with the query rather than words that mean the same thing — and the caller is
told which path answered so the surface can say so.

What is deliberately NOT here
=============================

No stopword list. A curated "ignore these" list is a per-language asset, and
a supported locale without one would silently get a worse lexicon than the
languages someone remembered (the same bias the runtime-language rule exists
to prevent). Commonness is measured instead — a term in more than
:data:`MAX_DF_RATIO` of the passages carries no information in THIS corpus,
whatever language it is in, and is skipped as a neighbour.

Nothing in this module raises for a missing provider, an empty store or an
unknown word: each is a defined, named outcome.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "TermNeighbour",
    "NEIGHBOUR_SOURCE_VECTOR",
    "NEIGHBOUR_SOURCE_COOCCURRENCE",
    "NEIGHBOUR_SOURCE_NONE",
    "DEFAULT_NEIGHBOURS",
    "MAX_DF_RATIO",
    "MIN_DOC_FREQ",
    "normalize_word",
    "word_tokens",
    "passage_vocabulary",
    "resolve_neighbours",
    "harvest_pass",
    "embed_terms_pass",
    "centroid",
]

#: Unicode-aware word tokenizer, identical in shape to
#: ``search.query_terms`` — German, Spanish and every other supported locale
#: tokenize the same way, and no ASCII-only character class appears anywhere.
_TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)

#: How many neighbours a word search asks for by default. Twenty is the size
#: at which a neighbourhood reads as a THEME rather than as a list of
#: synonyms, while still fitting on one screen.
DEFAULT_NEIGHBOURS = 20

#: A term seen in more than this share of the sampled passages says nothing
#: about any of them, in any language. Used to keep the co-occurrence path
#: from answering every query with the corpus's filler words.
MAX_DF_RATIO = 0.4

#: Below this many passages a term is almost always a typo, an id or a hash.
#: Such terms are still STORED (they answer an exact lookup) but never
#: embedded, which is what keeps the lexicon's cost bounded.
MIN_DOC_FREQ = 2

#: Where a neighbour list came from — reported to the surface so a degraded
#: answer is visibly degraded instead of quietly worse.
NEIGHBOUR_SOURCE_VECTOR = "vector"
NEIGHBOUR_SOURCE_COOCCURRENCE = "cooccurrence"
NEIGHBOUR_SOURCE_NONE = "none"

#: How many passages the co-occurrence path samples. Bounded on purpose: this
#: runs on the search path and must not walk a 200 000-passage corpus.
_COOCCURRENCE_SAMPLE = 80

#: Passages harvested per background lexicon pass, and terms embedded per
#: pass. Both bounded so the lane stays a background lane (AP-26): the
#: harvest is pure CPU over text already in the database, the embed step is
#: one provider call.
HARVEST_BATCH = 400
EMBED_BATCH = 96


@dataclass(frozen=True, slots=True)
class TermNeighbour:
    """One word near the query word, with how near and how common it is."""

    term: str
    #: 0-1, higher = closer. Cosine similarity on the vector path; a
    #: normalized lift score on the co-occurrence path. The two are NOT
    #: comparable across paths, which is why the path is always reported
    #: beside them.
    similarity: float
    #: In how many passages the term was seen while harvesting — a rarity
    #: hint for the reader, never a filter that drops a hit.
    doc_freq: int = 0


def normalize_word(text: str) -> str:
    """The canonical form of a query word or short phrase.

    Lowercased and whitespace-collapsed, nothing else: no stemming, because a
    stemmer is per-language and a wrong one mangles the very word the user is
    hunting.
    """
    return " ".join(str(text or "").split()).lower()


def word_tokens(text: str) -> list[str]:
    """Distinct lowercased word tokens of ``text``, order preserved."""
    return list(
        dict.fromkeys(match.group(0).lower() for match in _TOKEN_RE.finditer(text or ""))
    )


def passage_vocabulary(texts: Iterable[str]) -> dict[str, int]:
    """Per-PASSAGE document frequencies for a batch of passage texts.

    A term is counted once per passage however often it occurs in it, which is
    what makes ``doc_freq`` a rarity measure rather than a word count.
    """
    counts: dict[str, int] = {}
    for text in texts:
        for token in word_tokens(text):
            counts[token] = counts.get(token, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Background passes (harvest + embed)
# ---------------------------------------------------------------------------


async def harvest_pass(store: Any, *, limit: int = HARVEST_BATCH) -> int:
    """Walk the next slice of items into the vocabulary. Returns rows read.

    Incremental and resumable: a cursor in ``uw_meta`` records the highest
    item id already seen, so a restart continues instead of recounting, and a
    200 000-item corpus is never scanned in one go.
    """
    cursor = await store.lexicon_cursor()
    rows = await store.lexicon_scan_batch(after_item_id=cursor, limit=int(limit))
    if not rows:
        return 0
    counts = passage_vocabulary(str(row.get("text", "")) for row in rows)
    if counts:
        await store.bump_terms(counts)
    # Advance only after the terms landed: a crash between the two re-reads
    # the same items and double-counts their frequencies, which skews a
    # ranking hint. Advancing FIRST would lose that vocabulary permanently.
    await store.set_lexicon_cursor(max(int(row["item_id"]) for row in rows))
    return len(rows)


async def embed_terms_pass(
    store: Any,
    backend: Any,
    *,
    model: str,
    dim: int,
    limit: int = EMBED_BATCH,
    max_terms: int = 20000,
    min_doc_freq: int = MIN_DOC_FREQ,
) -> int:
    """Embed the next batch of harvested terms. Returns how many landed.

    ``model``/``dim`` are the store's ACTIVE pinned space, never the config's
    current setting: embedding terms into a space the documents do not live in
    would produce neighbours that cannot be compared with anything (D-3).
    """
    if not model or int(dim) <= 0:
        return 0
    pending = await store.terms_needing_vectors(
        model=model,
        dim=int(dim),
        limit=int(limit),
        min_doc_freq=int(min_doc_freq),
        max_terms=int(max_terms),
    )
    if not pending:
        return 0
    texts = [str(row["term"]) for row in pending]
    vectors = await backend.embed(texts, model=model)
    if len(vectors) != len(texts):
        raise RuntimeError(
            f"backend returned {len(vectors)} vectors for {len(texts)} terms"
        )
    return await store.store_term_vectors(
        [(int(row["term_id"]), vector) for row, vector in zip(pending, vectors, strict=True)],
        model=model,
        dim=int(dim),
    )


# ---------------------------------------------------------------------------
# Neighbour resolution (the read path)
# ---------------------------------------------------------------------------


async def resolve_neighbours(
    store: Any,
    cfg: Any,
    word: str,
    *,
    limit: int = DEFAULT_NEIGHBOURS,
    area_id: str | None = None,
) -> tuple[list[TermNeighbour], str, str, list[float] | None]:
    """The ``limit`` words nearest ``word``, best first.

    Returns ``(neighbours, source, reason, query_vector)``:

    - ``source`` is one of :data:`NEIGHBOUR_SOURCE_VECTOR`,
      :data:`NEIGHBOUR_SOURCE_COOCCURRENCE`, :data:`NEIGHBOUR_SOURCE_NONE`.
    - ``reason`` is empty on the healthy path, and otherwise an honest English
      sentence naming what is missing. It is populated even when the fallback
      succeeded — the surface should be able to say "these are neighbours by
      company, because <reason>".
    - ``query_vector`` is the embedded query word when one could be produced.
      It is handed back rather than recomputed, because the retrieval stage
      right after this needs exactly the same vector.

    Never raises.
    """
    term = normalize_word(word)
    if not term or int(limit) <= 0:
        return [], NEIGHBOUR_SOURCE_NONE, "", None

    query_vector: list[float] | None = None
    vector_reason = ""
    try:
        model, dim = await store.embedding_space()
    except Exception:  # noqa: BLE001 — an unreadable pin degrades, never fails
        log.debug("lexicon: embedding space unreadable", exc_info=True)
        model, dim = "", 0

    if model and dim > 0:
        from jarvis.ultrawiki.search import (  # noqa: PLC0415 — lazy (AP-26)
            embed_query_vector,
        )

        query_vector, vector_reason = await embed_query_vector(cfg, term)
        if query_vector is not None:
            try:
                rows, reason = await store.term_neighbors(
                    query_vector, model=model, dim=dim, limit=int(limit),
                    exclude=word_tokens(term),
                )
            except Exception as exc:  # noqa: BLE001 — fall through to company
                log.warning("lexicon: term neighbour query failed", exc_info=True)
                rows, reason = [], f"the word index could not be read ({type(exc).__name__})"
            if rows:
                return (
                    [
                        TermNeighbour(
                            term=str(row["term"]),
                            similarity=float(row["similarity"]),
                            doc_freq=int(row.get("doc_freq", 0) or 0),
                        )
                        for row in rows
                    ],
                    NEIGHBOUR_SOURCE_VECTOR,
                    "",
                    query_vector,
                )
            vector_reason = reason or vector_reason
    else:
        vector_reason = (
            "nothing has been embedded yet, so there are no word vectors to "
            "compare against — related words come from the text instead"
        )

    company, company_reason = await _neighbours_by_company(
        store, term, limit=int(limit), area_id=area_id
    )
    if company:
        return company, NEIGHBOUR_SOURCE_COOCCURRENCE, vector_reason, query_vector
    return (
        [],
        NEIGHBOUR_SOURCE_NONE,
        vector_reason or company_reason,
        query_vector,
    )


async def _neighbours_by_company(
    store: Any, term: str, *, limit: int, area_id: str | None
) -> tuple[list[TermNeighbour], str]:
    """Provider-free neighbours: words that keep company with ``term``.

    Scored by LIFT — how much more often a word appears in the passages that
    contain the query word than in the corpus as a whole. A plain co-occurrence
    count would return the corpus's filler words for every query; lift returns
    the words that are specifically characteristic of this one.
    """
    reader = getattr(store, "text_samples_for_term", None)
    if not callable(reader):
        return [], (
            "this knowledge store cannot list the text a word occurs in, "
            "so related words are unavailable"
        )
    try:
        rows = await reader(term, limit=_COOCCURRENCE_SAMPLE, area_id=area_id)
    except Exception:  # noqa: BLE001 — a fallback that raises is not a fallback
        log.warning("lexicon: co-occurrence sample failed", exc_info=True)
        return [], "the text containing this word could not be read"
    if not rows:
        return [], ""
    texts = [str(row.get("text", "")) for row in rows]
    local = passage_vocabulary(texts)
    sampled = len(texts)
    query_words = set(word_tokens(term))
    candidates = [
        (candidate, count)
        for candidate, count in local.items()
        if candidate not in query_words
        and count >= 2
        and count <= max(2, int(sampled * MAX_DF_RATIO))
    ]
    if not candidates:
        return [], ""
    # The corpus-wide frequency of the SAME words, so "common everywhere" and
    # "common right here" can be told apart. A store without the probe simply
    # ranks by local frequency, which is worse but not wrong.
    background: Mapping[str, int] = {}
    corpus_size = 0
    df_probe = getattr(store, "term_document_frequency", None)
    count_probe = getattr(store, "live_item_count", None)
    if callable(df_probe) and callable(count_probe):
        try:
            corpus_size = int(await count_probe())
            background = await df_probe([name for name, _ in candidates])
        except Exception:  # noqa: BLE001 — a missing signal ranks worse, never fails
            log.debug("lexicon: background frequency probe failed", exc_info=True)
            background, corpus_size = {}, 0

    scored: list[tuple[float, str, int]] = []
    for candidate, count in candidates:
        local_rate = count / sampled
        if corpus_size > 0:
            global_rate = max(1, int(background.get(candidate, 0) or 0)) / corpus_size
            lift = local_rate / global_rate
        else:
            lift = local_rate
        # log1p keeps a word that is 500x over-represented from dwarfing the
        # rest of the list into an unreadable 0.00 tail.
        seen_in = int(background.get(candidate, count) or count)
        scored.append((math.log1p(max(lift, 0.0)), candidate, seen_in))
    if not scored:
        return [], ""
    scored.sort(key=lambda row: (-row[0], row[1]))
    top = scored[: int(limit)]
    ceiling = max(row[0] for row in top) or 1.0
    return (
        [
            TermNeighbour(
                term=candidate,
                similarity=round(min(1.0, score / ceiling), 4),
                doc_freq=doc_freq,
            )
            for score, candidate, doc_freq in top
        ],
        "",
    )


def centroid(vectors: Sequence[Sequence[float]]) -> list[float] | None:
    """The mean of several vectors, L2-normalized. ``None`` if unusable.

    The neighbourhood's own query vector: averaging the word with its nearest
    neighbours moves the query off the exact term and onto the CONCEPT around
    it, which is what makes an expanded search find passages that never spell
    the word out. Vectors of differing width are refused rather than padded —
    a mixed-width mean is a meaningless direction.
    """
    usable = [list(map(float, vector)) for vector in vectors if vector]
    if not usable:
        return None
    width = len(usable[0])
    usable = [vector for vector in usable if len(vector) == width]
    if not usable:
        return None
    mean = [sum(values) / len(usable) for values in zip(*usable, strict=True)]
    norm = math.sqrt(sum(value * value for value in mean))
    if norm <= 0.0 or not math.isfinite(norm):
        return None
    return [value / norm for value in mean]
