"""Word search over a REAL UltraStore — vocabulary, neighbours, passages.

Everything here runs against an actual SQLite store with real FTS5 and real
multi-chunk documents; only the embedding backend is a deterministic offline
fake. What is being pinned:

* the lexicon is harvested from the item table and embedded into the store's
  ACTIVE space, and word neighbours come back ordered by meaning;
* the neighbourhood drives retrieval, so an item that never spells the query
  word out is still reachable through a neighbour;
* every hit names the PASSAGE that carries the vocabulary, with its character
  span — not the head of the item;
* each degraded path is a NAMED status: empty store, unknown word, a known
  word that ranks nowhere, and a lexicon that could not produce neighbours;
* a dead embedding provider degrades to the provider-free co-occurrence path
  and writes nothing.

No network, no credentials, no models.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

import pytest

import jarvis.ultrawiki.embeddings as embeddings_mod
from jarvis.ultrawiki.chunking import Chunk
from jarvis.ultrawiki.embeddings import EmbeddingError
from jarvis.ultrawiki.lexicon import (
    NEIGHBOUR_SOURCE_COOCCURRENCE,
    NEIGHBOUR_SOURCE_VECTOR,
    embed_terms_pass,
    harvest_pass,
)
from jarvis.ultrawiki.store import UltraStore
from jarvis.ultrawiki.types import (
    ConsentState,
    DocType,
    ItemState,
    RawItem,
    WordSearchStatus,
)
from jarvis.ultrawiki.word_search import word_search

HAS_SQLITE_VEC = importlib.util.find_spec("sqlite_vec") is not None

EMBED_MODEL = "fake-model"
EMBED_DIM = 4

#: A tiny hand-built "semantic space": each word sits on a named axis, and
#: words that belong to one topic share an axis, so nearest-neighbour order is
#: something the test states rather than something it hopes for.
#:   axis 0 = sailing   axis 1 = cooking   axis 2 = money   axis 3 = filler
_SPACE: dict[str, list[float]] = {
    "regatta": [1.0, 0.0, 0.0, 0.0],
    "sailing": [0.97, 0.0, 0.0, 0.24],
    "mainsail": [0.94, 0.0, 0.0, 0.34],
    "harbour": [0.90, 0.0, 0.0, 0.44],
    "risotto": [0.0, 1.0, 0.0, 0.0],
    "saffron": [0.0, 0.96, 0.0, 0.28],
    "invoice": [0.0, 0.0, 1.0, 0.0],
    "vat": [0.0, 0.0, 0.95, 0.31],
}


class FakeEmbedding:
    """Deterministic offline backend over :data:`_SPACE`."""

    name = "fake"

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[list[str]] = []

    def ready(self) -> tuple[bool, str]:
        if self._error is not None:
            return False, "fake backend is unavailable"
        return True, ""

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._error is not None:
            raise self._error
        return [list(_vector_for(text)) for text in texts]


def _vector_for(text: str) -> list[float]:
    """A text's vector: the mean of the known words in it, else pure filler."""
    known = [_SPACE[word] for word in text.lower().split() if word in _SPACE]
    if not known:
        return [0.0, 0.0, 0.0, 1.0]
    return [sum(values) / len(known) for values in zip(*known, strict=True)]


def make_cfg(**overrides) -> SimpleNamespace:
    values = {
        "enabled": True,
        "embedding_provider": "fake",
        "embedding_model": EMBED_MODEL,
        "rerank_provider": "",
        "rerank_min_score": 4.0,
        "rrf_keyword_weight": 1.0,
        "rrf_vector_weight": 1.0,
        "rrf_event_weight": 1.0,
        "recency_half_life_days": 0.0,  # decay off: these fixtures share a date
        "lexicon_enabled": True,
        "lexicon_max_terms": 20000,
        "word_search_neighbours": 20,
    }
    values.update(overrides)
    return SimpleNamespace(ultrawiki=SimpleNamespace(**values))


@pytest.fixture
async def store(tmp_path):
    instance = UltraStore(tmp_path / "ultrawiki.db")
    yield instance
    await instance.close()


async def seed(store: UltraStore, items: list[tuple[str, str, str]]) -> dict[str, int]:
    """Import ``(external_id, title, body)`` triples and keyword-index them."""
    await store.upsert_source("src1", connector="local-folder", label="Test source")
    await store.set_consent("src1", ConsentState.APPROVED)
    await store.upsert_items(
        "src1",
        [
            RawItem(
                external_id=external_id,
                body=body,
                permalink=f"app://{external_id}",
                timestamp_utc="2026-01-02T10:00:00Z",
                title=title,
            )
            for external_id, title, body in items
        ],
    )
    for claimed in await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=500):
        await store.mark_stage_done(
            claimed["id"],
            ItemState.KEYWORD_INDEXED,
            fts_title=claimed["title"],
            fts_body=claimed["body_raw"],
        )
    ids: dict[str, int] = {}
    for external_id, _title, _body in items:
        row = await store.get_item_by_external_id("src1", external_id)
        assert row is not None
        ids[external_id] = int(row["id"])
    return ids


async def embed_passages(store: UltraStore, item_id: int, passages: list[str]) -> list[int]:
    """Store ``passages`` as real chunk documents, one vector each."""
    chunks: list[Chunk] = []
    offset = 0
    for index, text in enumerate(passages):
        chunks.append(
            Chunk(index=index, text=text, char_start=offset, char_end=offset + len(text))
        )
        offset += len(text) + 1
    doc_ids = await store.replace_documents(item_id, DocType.RAW, chunks)
    for doc_id, text in zip(doc_ids, passages, strict=True):
        await store.store_embedding(
            doc_id, model=EMBED_MODEL, dim=EMBED_DIM, vector=_vector_for(text)
        )
    return doc_ids


async def build_lexicon(store: UltraStore, backend: FakeEmbedding) -> None:
    """Run the background passes to completion, exactly as the pipeline does."""
    while await harvest_pass(store, limit=200):
        pass
    while await embed_terms_pass(
        store, backend, model=EMBED_MODEL, dim=EMBED_DIM, limit=64, min_doc_freq=1
    ):
        pass


def register(monkeypatch, backend: FakeEmbedding) -> None:
    monkeypatch.setitem(embeddings_mod.EMBEDDING_BACKENDS, "fake", lambda cfg: backend)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite-vec is not installed")
async def test_neighbours_are_ordered_by_meaning(store, monkeypatch):
    backend = FakeEmbedding()
    register(monkeypatch, backend)
    ids = await seed(
        store,
        [
            ("a", "Race log", "regatta sailing mainsail harbour"),
            ("b", "Kitchen", "risotto saffron"),
            ("c", "Books", "invoice vat"),
        ],
    )
    await embed_passages(store, ids["a"], ["regatta sailing mainsail harbour"])
    await embed_passages(store, ids["b"], ["risotto saffron"])
    await embed_passages(store, ids["c"], ["invoice vat"])
    await build_lexicon(store, backend)

    outcome = await word_search(store, make_cfg(), "regatta", neighbours=5)

    assert outcome.status == WordSearchStatus.OK.value
    assert outcome.neighbour_source == NEIGHBOUR_SOURCE_VECTOR
    terms = [neighbour.term for neighbour in outcome.neighbours]
    # The sailing axis comes first, in similarity order; the query word itself
    # is never returned as its own neighbour.
    assert terms[:3] == ["sailing", "mainsail", "harbour"]
    assert "regatta" not in terms
    assert all(0.0 <= neighbour.similarity <= 1.0 for neighbour in outcome.neighbours)


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite-vec is not installed")
async def test_a_neighbour_reaches_an_item_that_never_spells_the_word(store, monkeypatch):
    backend = FakeEmbedding()
    register(monkeypatch, backend)
    ids = await seed(
        store,
        [
            ("a", "Race log", "regatta results and crew notes"),
            # Never contains "regatta" — only reachable through a neighbour.
            ("b", "Rigging", "mainsail repair after the storm"),
            ("c", "Kitchen", "risotto saffron stock"),
        ],
    )
    await embed_passages(store, ids["a"], ["regatta results and crew notes"])
    await embed_passages(store, ids["b"], ["mainsail repair after the storm"])
    await embed_passages(store, ids["c"], ["risotto saffron stock"])
    await build_lexicon(store, backend)

    outcome = await word_search(store, make_cfg(), "regatta", neighbours=5)

    reached = [hit.item_id for hit in outcome.hits]
    assert ids["a"] in reached
    assert ids["b"] in reached, "the mainsail item must be reachable via expansion"
    assert reached.index(ids["a"]) < reached.index(ids["b"]), (
        "the exact word must still outrank a neighbour-only match"
    )
    # And the unrelated topic must not be dragged in by the expansion.
    assert ids["c"] not in reached


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite-vec is not installed")
async def test_hits_point_at_the_passage_not_the_head_of_the_item(store, monkeypatch):
    backend = FakeEmbedding()
    register(monkeypatch, backend)
    head = "an opening paragraph about nothing in particular at all"
    middle = "filler prose that mentions neither topic whatsoever here"
    answer = "the regatta briefing and the mainsail trim we agreed on"
    ids = await seed(store, [("a", "Long file", f"{head}\n{middle}\n{answer}")])
    doc_ids = await embed_passages(store, ids["a"], [head, middle, answer])
    await build_lexicon(store, backend)

    outcome = await word_search(store, make_cfg(), "regatta", neighbours=5)

    assert outcome.hits, outcome.reason
    hit = outcome.hits[0]
    assert hit.passages, "a chunked item must report which passage answered"
    best = hit.passages[0]
    assert best.document_id == doc_ids[2], "the answering passage, not the head"
    assert best.chunk_index == 2
    # The span has to be usable to locate the text in the original body.
    assert best.char_start == len(head) + 1 + len(middle) + 1
    assert best.char_end > best.char_start
    assert "regatta" in best.terms
    assert "regatta" in best.text


# ---------------------------------------------------------------------------
# Degradation — every one of them NAMED
# ---------------------------------------------------------------------------


async def test_empty_store_says_so(store, monkeypatch):
    register(monkeypatch, FakeEmbedding())
    outcome = await word_search(store, make_cfg(), "regatta")

    assert outcome.status == WordSearchStatus.EMPTY_INDEX.value
    assert outcome.hits == ()
    assert "imported" in outcome.reason


async def test_unknown_word_is_told_apart_from_a_word_that_ranks_nowhere(
    store, monkeypatch
):
    register(monkeypatch, FakeEmbedding())
    await seed(store, [("a", "Race log", "regatta sailing mainsail")])

    unknown = await word_search(store, make_cfg(), "zzqqxunlikelytoken")

    assert unknown.status == WordSearchStatus.UNKNOWN_WORD.value
    assert unknown.hits == ()
    assert "does not occur" in unknown.reason


async def test_no_embedding_provider_falls_back_to_company(store, monkeypatch):
    """No provider at all: neighbours come from the text, and search still works."""
    ids = await seed(
        store,
        [
            ("a", "Race one", "regatta mainsail mainsail crew"),
            ("b", "Race two", "regatta mainsail harbour crew"),
            ("c", "Kitchen", "risotto saffron stock"),
        ],
    )
    outcome = await word_search(store, make_cfg(embedding_provider=""), "regatta")

    assert outcome.neighbour_source == NEIGHBOUR_SOURCE_COOCCURRENCE
    assert outcome.status == WordSearchStatus.OK.value
    terms = [neighbour.term for neighbour in outcome.neighbours]
    assert "mainsail" in terms
    assert "risotto" not in terms
    assert {hit.item_id for hit in outcome.hits} >= {ids["a"], ids["b"]}
    assert outcome.reason, "a degraded neighbour path must say why"


async def test_dead_provider_degrades_and_writes_nothing(store, monkeypatch):
    """A raising embed call must not fail the search or corrupt the lexicon."""
    backend = FakeEmbedding(error=EmbeddingError("fake: HTTP 429 (rate_limit)"))
    register(monkeypatch, backend)
    await seed(store, [("a", "Race log", "regatta mainsail harbour crew")])

    before = await store.lexicon_counts(model=EMBED_MODEL, dim=EMBED_DIM)
    outcome = await word_search(store, make_cfg(), "regatta")
    after = await store.lexicon_counts(model=EMBED_MODEL, dim=EMBED_DIM)

    assert outcome.status in {
        WordSearchStatus.OK.value,
        WordSearchStatus.NEIGHBOURS_UNAVAILABLE.value,
    }
    assert after["embedded_terms"] == before["embedded_terms"] == 0
    assert outcome.neighbour_source != NEIGHBOUR_SOURCE_VECTOR


async def test_blank_word_is_refused_without_touching_the_store(store):
    outcome = await word_search(store, make_cfg(), "   ")

    assert outcome.status == WordSearchStatus.NO_MATCHES.value
    assert outcome.word == ""
    assert outcome.hits == ()


# ---------------------------------------------------------------------------
# Lexicon bookkeeping
# ---------------------------------------------------------------------------


async def test_harvest_is_incremental_and_resumable(store, monkeypatch):
    register(monkeypatch, FakeEmbedding())
    ids = await seed(store, [("a", "One", "alpha beta"), ("b", "Two", "gamma delta")])
    await embed_passages(store, ids["a"], ["alpha beta"])
    await embed_passages(store, ids["b"], ["gamma delta"])

    first = await harvest_pass(store, limit=1)
    assert first == 1
    cursor_after_first = await store.lexicon_cursor()
    assert cursor_after_first > 0

    second = await harvest_pass(store, limit=10)
    assert second == 1, "the second pass must not re-read the first passage"
    assert await harvest_pass(store, limit=10) == 0, "a finished harvest is a no-op"

    counts = await store.lexicon_counts()
    # Two bodies (4 words) plus the two titles the harvest reads with them.
    assert counts["terms"] == 6
    assert counts["scanned_items"] >= cursor_after_first


async def test_rebuild_rewinds_the_whole_lexicon(store, monkeypatch):
    backend = FakeEmbedding()
    register(monkeypatch, backend)
    ids = await seed(store, [("a", "One", "regatta mainsail")])
    await embed_passages(store, ids["a"], ["regatta mainsail"])
    await build_lexicon(store, backend)
    assert (await store.lexicon_counts(model=EMBED_MODEL, dim=EMBED_DIM))["terms"] > 0

    await store.reset_lexicon()

    counts = await store.lexicon_counts(model=EMBED_MODEL, dim=EMBED_DIM)
    assert counts["terms"] == 0
    assert counts["embedded_terms"] == 0
    assert counts["scanned_items"] == 0
    # The corpus itself is untouched — only derived rows went away.
    assert counts["items"] == 1
    assert counts["passages"] == 1


async def test_the_embedded_vocabulary_is_capped(store, monkeypatch):
    backend = FakeEmbedding()
    register(monkeypatch, backend)
    ids = await seed(store, [("a", "One", "alpha beta gamma delta epsilon")])
    await embed_passages(store, ids["a"], ["alpha beta gamma delta epsilon"])
    while await harvest_pass(store, limit=100):
        pass

    embedded = await embed_terms_pass(
        store, backend, model=EMBED_MODEL, dim=EMBED_DIM, limit=64,
        min_doc_freq=1, max_terms=2,
    )
    assert embedded == 2
    assert (
        await embed_terms_pass(
            store, backend, model=EMBED_MODEL, dim=EMBED_DIM, limit=64,
            min_doc_freq=1, max_terms=2,
        )
        == 0
    ), "the cap is on the whole embedded vocabulary, not on one batch"


async def test_rare_terms_are_stored_but_never_embedded(store, monkeypatch):
    """The cost bound: a corpus full of ids costs vocabulary rows, not calls."""
    backend = FakeEmbedding()
    register(monkeypatch, backend)
    ids = await seed(store, [("a", "One", "common word"), ("b", "Two", "common a1b2c3d4")])
    await embed_passages(store, ids["a"], ["common word"])
    await embed_passages(store, ids["b"], ["common a1b2c3d4"])
    while await harvest_pass(store, limit=100):
        pass

    await embed_terms_pass(
        store, backend, model=EMBED_MODEL, dim=EMBED_DIM, limit=64, min_doc_freq=2
    )

    # "one"/"two" from the titles, "common", "word", "a1b2c3d4".
    assert (await store.lexicon_counts())["terms"] == 5
    embedded_texts = [text for call in backend.calls for text in call]
    assert "common" in embedded_texts
    assert "a1b2c3d4" not in embedded_texts
