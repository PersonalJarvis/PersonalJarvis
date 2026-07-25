"""Hybrid-search unit tests — real UltraStore + real FTS, fully offline.

Covers: keyword-only degradation (no / dead / unknown embedding backend and
store-side vector degradation), RRF consensus beating a single-list rank, the
recency tiebreak, per-source capping, area-filter passthrough to BOTH legs,
rerank reordering + RerankError fallback, empty-query/no-hit short-circuits,
and the ``search_status`` capability report. The embedding backend is a
hand-written fake with deterministic vectors; rerank is monkeypatched. No
network, no credentials, no models.
"""

from __future__ import annotations

import importlib.util
import logging
from types import SimpleNamespace

import pytest

import jarvis.ultrawiki.embeddings as embeddings_mod
import jarvis.ultrawiki.rerank as rerank_mod
from jarvis.ultrawiki.embeddings import EmbeddingError
from jarvis.ultrawiki.rerank import RerankError
from jarvis.ultrawiki.search import hybrid_search, search_status
from jarvis.ultrawiki.store import UltraStore
from jarvis.ultrawiki.types import (
    ConsentState,
    DocType,
    ItemState,
    RawItem,
    SearchResult,
)

HAS_SQLITE_VEC = importlib.util.find_spec("sqlite_vec") is not None

EMBED_MODEL = "fake-model"
EMBED_DIM = 3


# ---------------------------------------------------------------------------
# Fakes + helpers
# ---------------------------------------------------------------------------


class FakeEmbedding:
    """Deterministic offline embedding backend (protocol-shaped)."""

    name = "fake"

    def __init__(
        self,
        vectors_by_text: dict[str, list[float]] | None = None,
        *,
        default: list[float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._vectors = vectors_by_text or {}
        self._default = default if default is not None else [1.0, 0.0, 0.0]
        self._error = error
        self.calls: list[tuple[list[str], str]] = []

    def ready(self) -> tuple[bool, str]:
        return True, ""

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        self.calls.append((list(texts), model))
        if self._error is not None:
            raise self._error
        return [list(self._vectors.get(text, self._default)) for text in texts]


class FakeReranker:
    """Scripted reranker: returns fixed (index, score) pairs or raises."""

    name = "fake"

    def __init__(
        self,
        pairs: list[tuple[int, float]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._pairs = pairs or []
        self._error = error
        self.calls: list[tuple[str, list[str], int]] = []

    def ready(self) -> tuple[bool, str]:
        return True, ""

    async def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        self.calls.append((query, list(documents), top_k))
        if self._error is not None:
            raise self._error
        return list(self._pairs)


class FakeStore:
    """Scripted store legs, recording the arguments each leg received."""

    def __init__(
        self,
        keyword_hits: list[SearchResult] | None = None,
        vector_hits: list[SearchResult] | None = None,
    ) -> None:
        self._keyword_hits = keyword_hits or []
        self._vector_hits = vector_hits or []
        self.keyword_calls: list[tuple[str, int, str | None]] = []
        self.vector_calls: list[tuple[list[float], int, str | None]] = []

    async def keyword_search(
        self, query: str, k: int = 10, *, area_id: str | None = None
    ) -> list[SearchResult]:
        self.keyword_calls.append((query, k, area_id))
        return list(self._keyword_hits)

    async def vector_search(
        self, query_vector, k: int = 10, *, area_id: str | None = None
    ) -> tuple[list[SearchResult], str]:
        self.vector_calls.append((list(query_vector), k, area_id))
        return list(self._vector_hits), ""


def make_cfg(**overrides) -> SimpleNamespace:
    values = {
        "enabled": True,
        "embedding_provider": "",
        "embedding_model": "",
        "rerank_provider": "",
        "ollama_endpoint": "http://localhost:11434",
    }
    values.update(overrides)
    return SimpleNamespace(ultrawiki=SimpleNamespace(**values))


def make_result(
    item_id: int,
    *,
    source_id: str = "src1",
    timestamp_utc: str = "2026-01-02T10:00:00Z",
    matched_by: tuple[str, ...] = ("keyword",),
    score: float = 0.5,
) -> SearchResult:
    return SearchResult(
        item_id=item_id,
        source_id=source_id,
        title=f"Item {item_id}",
        snippet=f"snippet {item_id}",
        permalink=f"app://item/{item_id}",
        timestamp_utc=timestamp_utc,
        score=score,
        matched_by=matched_by,
    )


def raw_item(
    external_id: str,
    *,
    body: str,
    title: str = "",
    timestamp_utc: str = "2026-01-02T10:00:00Z",
) -> RawItem:
    return RawItem(
        external_id=external_id,
        body=body,
        permalink=f"app://{external_id}",
        timestamp_utc=timestamp_utc,
        title=title,
    )


@pytest.fixture
async def store(tmp_path):
    instance = UltraStore(tmp_path / "ultrawiki.db")
    yield instance
    await instance.close()


async def add_source(
    store: UltraStore, source_id: str = "src1", areas: list[str] | None = None
) -> str:
    await store.upsert_source(source_id, connector="local-folder", label="Test source", areas=areas)
    await store.set_consent(source_id, ConsentState.APPROVED)
    return source_id


async def index_all_keyword(store: UltraStore) -> None:
    claimed = await store.claim_batch(ItemState.KEYWORD_INDEXED, limit=500)
    for item in claimed:
        await store.mark_stage_done(
            item["id"],
            ItemState.KEYWORD_INDEXED,
            fts_title=item["title"],
            fts_body=item["body_raw"],
        )


async def item_id_of(store: UltraStore, source_id: str, external_id: str) -> int:
    row = await store.get_item_by_external_id(source_id, external_id)
    assert row is not None
    return int(row["id"])


async def embed_item(
    store: UltraStore, item_id: int, vector: list[float], *, text: str = "summary"
) -> None:
    doc_id = await store.add_document(item_id, DocType.SUMMARY, text)
    await store.store_embedding(doc_id, model=EMBED_MODEL, dim=len(vector), vector=vector)


def register_fake_embedding(monkeypatch, fake: FakeEmbedding) -> None:
    monkeypatch.setitem(embeddings_mod.EMBEDDING_BACKENDS, "fake", lambda cfg: fake)


# ---------------------------------------------------------------------------
# Empty query / no hits
# ---------------------------------------------------------------------------


async def test_empty_query_and_no_hits_return_empty(store):
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    cfg = make_cfg()

    assert await hybrid_search(store, cfg, "") == []
    assert await hybrid_search(store, cfg, "   ") == []
    assert await hybrid_search(store, cfg, "zzzunmatchedtoken") == []


# ---------------------------------------------------------------------------
# Keyword-only degradation
# ---------------------------------------------------------------------------


async def test_keyword_only_when_no_embedding_configured(store):
    await add_source(store)
    await store.upsert_items(
        "src1",
        [
            raw_item("a", body="alpha first"),
            raw_item("b", body="alpha second"),
        ],
    )
    await index_all_keyword(store)

    results = await hybrid_search(store, make_cfg(), "alpha")

    assert len(results) == 2
    assert all(hit.matched_by == ("keyword",) for hit in results)


async def test_keyword_only_when_embedding_backend_is_dead(store, monkeypatch):
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha text")])
    await index_all_keyword(store)
    register_fake_embedding(
        monkeypatch, FakeEmbedding(error=EmbeddingError("fake: backend is down"))
    )
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    results = await hybrid_search(store, cfg, "alpha")

    assert [hit.matched_by for hit in results] == [("keyword",)]


async def test_keyword_only_when_embedding_provider_is_unknown(store):
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha text")])
    await index_all_keyword(store)
    cfg = make_cfg(embedding_provider="does-not-exist")

    results = await hybrid_search(store, cfg, "alpha")

    assert [hit.matched_by for hit in results] == [("keyword",)]


async def test_keyword_only_when_store_vector_leg_is_degraded(store, monkeypatch):
    """Backend healthy but the store holds no embeddings — an honest skip."""
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha text")])
    await index_all_keyword(store)
    register_fake_embedding(monkeypatch, FakeEmbedding())
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    results = await hybrid_search(store, cfg, "alpha")

    assert [hit.matched_by for hit in results] == [("keyword",)]


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_SQLITE_VEC, reason="sqlite-vec extension not installed")
async def test_rrf_consensus_beats_single_list_rank(store, monkeypatch):
    """An item ranked 2nd in BOTH legs outscores each leg's own #1."""
    await add_source(store)
    await store.upsert_items(
        "src1",
        [
            # Keyword leg (query "alpha"): b (tf=3) ranks 1st, a (tf=1) 2nd.
            raw_item("a", body="alpha one two three", timestamp_utc="2026-01-01T10:00:00Z"),
            raw_item("b", body="alpha alpha alpha zero", timestamp_utc="2026-01-01T11:00:00Z"),
            # c does not contain "alpha" at all — vector leg only.
            raw_item("c", body="unrelated body text here", timestamp_utc="2026-01-01T12:00:00Z"),
        ],
    )
    await index_all_keyword(store)
    id_a = await item_id_of(store, "src1", "a")
    id_b = await item_id_of(store, "src1", "b")
    id_c = await item_id_of(store, "src1", "c")
    # Vector leg (query -> [1, 0, 0]): c is an exact match (rank 1), a is
    # close (rank 2), b has no embedding at all.
    await embed_item(store, id_c, [1.0, 0.0, 0.0])
    await embed_item(store, id_a, [0.9, 0.1, 0.0])
    register_fake_embedding(monkeypatch, FakeEmbedding({"alpha": [1.0, 0.0, 0.0]}))
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    results = await hybrid_search(store, cfg, "alpha")

    assert [hit.item_id for hit in results] == [id_a, id_c, id_b]
    by_id = {hit.item_id: hit for hit in results}
    # Consensus winner carries both legs; single-list hits carry theirs only.
    assert by_id[id_a].matched_by == ("keyword", "vector")
    assert by_id[id_b].matched_by == ("keyword",)
    assert by_id[id_c].matched_by == ("vector",)
    # RRF arithmetic: a = 1/62 + 1/62; b and c = 1/61 each (recency-tiebroken).
    assert by_id[id_a].score > by_id[id_c].score > by_id[id_b].score
    assert by_id[id_a].score == pytest.approx(2 / 62, abs=1e-5)


async def test_recency_breaks_rrf_ties_toward_newer_items(monkeypatch):
    """Equal RRF sums (rank 1 in exactly one leg each) — newer wins."""
    older = make_result(1, timestamp_utc="2026-01-01T10:00:00Z")
    newer = make_result(2, timestamp_utc="2026-06-01T10:00:00Z", matched_by=("vector",))
    fake_store = FakeStore(keyword_hits=[older], vector_hits=[newer])
    register_fake_embedding(monkeypatch, FakeEmbedding())
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    results = await hybrid_search(fake_store, cfg, "alpha")

    assert [hit.item_id for hit in results] == [2, 1]


async def test_area_filter_passes_through_to_both_legs(monkeypatch):
    fake_store = FakeStore(
        keyword_hits=[make_result(1)],
        vector_hits=[make_result(2, matched_by=("vector",))],
    )
    register_fake_embedding(monkeypatch, FakeEmbedding())
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    results = await hybrid_search(fake_store, cfg, "alpha", area_id="area-work")

    assert len(results) == 2
    assert fake_store.keyword_calls == [("alpha", 20, "area-work")]
    assert len(fake_store.vector_calls) == 1
    assert fake_store.vector_calls[0][2] == "area-work"


async def test_area_filter_passthrough_real_store(store):
    await add_source(store, "s-work", areas=["work"])
    await add_source(store, "s-home", areas=["home"])
    await store.upsert_items("s-work", [raw_item("w1", body="alpha work note")])
    await store.upsert_items("s-home", [raw_item("h1", body="alpha home note")])
    await index_all_keyword(store)
    cfg = make_cfg()

    scoped = await hybrid_search(store, cfg, "alpha", area_id="work")
    assert {hit.source_id for hit in scoped} == {"s-work"}

    unscoped = await hybrid_search(store, cfg, "alpha")
    assert {hit.source_id for hit in unscoped} == {"s-work", "s-home"}


async def test_per_source_cap_enforced_before_final_cut(store):
    await add_source(store, "s-busy")
    await add_source(store, "s-quiet")
    await store.upsert_items(
        "s-busy",
        [raw_item(f"busy-{i}", body=f"alpha busy note {i}") for i in range(8)],
    )
    await store.upsert_items("s-quiet", [raw_item("q1", body="alpha quiet note")])
    await index_all_keyword(store)

    results = await hybrid_search(store, make_cfg(), "alpha", k=10)

    per_source: dict[str, int] = {}
    for hit in results:
        per_source[hit.source_id] = per_source.get(hit.source_id, 0) + 1
    # The busy source is capped at 5, so the quiet source still surfaces.
    assert per_source == {"s-busy": 5, "s-quiet": 1}


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------


async def seed_three_ranked_items(store: UltraStore) -> list[int]:
    """Three keyword items with strictly decreasing term frequency."""
    await add_source(store)
    await store.upsert_items(
        "src1",
        [
            raw_item("r0", body="alpha alpha alpha zero"),
            raw_item("r1", body="alpha alpha one two"),
            raw_item("r2", body="alpha one two three"),
        ],
    )
    await index_all_keyword(store)
    return [await item_id_of(store, "src1", ext) for ext in ("r0", "r1", "r2")]


async def test_rerank_reorders_when_configured_and_ready(store, monkeypatch):
    id_0, id_1, id_2 = await seed_three_ranked_items(store)

    baseline = await hybrid_search(store, make_cfg(), "alpha")
    assert [hit.item_id for hit in baseline] == [id_0, id_1, id_2]

    fake = FakeReranker(pairs=[(2, 0.9), (1, 0.5), (0, 0.1)])
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: fake)
    cfg = make_cfg(rerank_provider="voyage")

    results = await hybrid_search(store, cfg, "alpha")

    assert [hit.item_id for hit in results] == [id_2, id_1, id_0]
    # Only the ORDER changes — every hit keeps its fused score.
    assert {hit.item_id: hit.score for hit in results} == {
        hit.item_id: hit.score for hit in baseline
    }
    # The reranker saw one call with all three snippets.
    assert len(fake.calls) == 1
    assert len(fake.calls[0][1]) == 3
    # The k cut applies AFTER the rerank order.
    top_two = await hybrid_search(store, cfg, "alpha", k=2)
    assert [hit.item_id for hit in top_two] == [id_2, id_1]


async def test_rerank_error_falls_back_to_fusion_order(store, monkeypatch, caplog):
    id_0, id_1, id_2 = await seed_three_ranked_items(store)
    fake = FakeReranker(error=RerankError("fake: HTTP 500"))
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: fake)
    cfg = make_cfg(rerank_provider="voyage")

    with caplog.at_level(logging.WARNING, logger="jarvis.ultrawiki.search"):
        results = await hybrid_search(store, cfg, "alpha")

    assert [hit.item_id for hit in results] == [id_0, id_1, id_2]
    warnings = [rec for rec in caplog.records if "rerank failed" in rec.message]
    assert len(warnings) == 1


async def test_rerank_skipped_when_provider_not_ready(store, monkeypatch):
    id_0, id_1, id_2 = await seed_three_ranked_items(store)
    # resolve_reranker returning None == unconfigured/unknown/not ready.
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: None)
    cfg = make_cfg(rerank_provider="voyage")

    results = await hybrid_search(store, cfg, "alpha")

    assert [hit.item_id for hit in results] == [id_0, id_1, id_2]


# ---------------------------------------------------------------------------
# search_status
# ---------------------------------------------------------------------------


def test_search_status_unconfigured():
    status = search_status(make_cfg())

    assert status["keyword"] == {"available": True}
    assert status["vector"]["available"] is False
    assert status["vector"]["reason"]  # honest, non-empty English reason
    assert status["rerank"] == {"available": False, "provider": "off"}


def test_search_status_tolerates_missing_ultrawiki_section():
    status = search_status(SimpleNamespace())

    assert status["keyword"] == {"available": True}
    assert status["vector"]["available"] is False
    assert status["rerank"]["provider"] == "off"


def test_search_status_reports_configured_legs(monkeypatch):
    register_fake_embedding(monkeypatch, FakeEmbedding())
    monkeypatch.setitem(rerank_mod.RERANK_BACKENDS, "fake", lambda cfg: FakeReranker())
    cfg = make_cfg(
        embedding_provider="fake",
        embedding_model=EMBED_MODEL,
        rerank_provider="fake",
    )

    status = search_status(cfg)

    assert status["vector"] == {
        "available": True,
        "backend": "fake",
        "model": EMBED_MODEL,
    }
    assert status["rerank"] == {"available": True, "provider": "fake"}


def test_search_status_unknown_embedding_provider():
    status = search_status(make_cfg(embedding_provider="does-not-exist"))

    assert status["vector"]["available"] is False
    assert "does-not-exist" in status["vector"]["reason"]
