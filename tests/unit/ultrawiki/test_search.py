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

import asyncio
import importlib.util
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import jarvis.ultrawiki.embeddings as embeddings_mod
import jarvis.ultrawiki.rerank as rerank_mod
from jarvis.ultrawiki.embeddings import EmbeddingError
from jarvis.ultrawiki.rerank import RerankError
from jarvis.ultrawiki.search import (
    RRF_K,
    hybrid_search,
    ranking_settings,
    search_status,
)
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
    # The ranking knobs are spelled out (not left to getattr defaults) so a
    # test states the ranking it exercises and stays honest if a product
    # default moves.
    values = {
        "enabled": True,
        "embedding_provider": "",
        "embedding_model": "",
        "rerank_provider": "",
        "rerank_model": "",
        "rerank_min_score": 4.0,
        "rrf_keyword_weight": 1.0,
        "rrf_vector_weight": 1.0,
        "rrf_event_weight": 1.0,
        "recency_half_life_days": 180.0,
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
    title: str | None = None,
    snippet: str | None = None,
    recorded_utc: str = "",
) -> SearchResult:
    return SearchResult(
        item_id=item_id,
        source_id=source_id,
        title=f"Item {item_id}" if title is None else title,
        snippet=f"snippet {item_id}" if snippet is None else snippet,
        permalink=f"app://item/{item_id}",
        timestamp_utc=timestamp_utc,
        score=score,
        matched_by=matched_by,
        recorded_utc=recorded_utc,
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
    # Pure RRF arithmetic under test: the age decay is a separate stage with
    # its own tests, and leaving it on would make the asserted sums depend on
    # the day the suite runs.
    cfg = make_cfg(
        embedding_provider="fake",
        embedding_model=EMBED_MODEL,
        recency_half_life_days=0,
    )

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
    # Decay off: the assertion below compares fused scores across two separate
    # searches, and a live half-life would move them between the two calls.
    baseline = await hybrid_search(store, make_cfg(recency_half_life_days=0), "alpha")
    assert [hit.item_id for hit in baseline] == [id_0, id_1, id_2]

    fake = FakeReranker(pairs=[(2, 9.0), (1, 5.0), (0, 1.0)])
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: fake)
    cfg = make_cfg(rerank_provider="voyage", recency_half_life_days=0)

    results = await hybrid_search(store, cfg, "alpha")

    assert [hit.item_id for hit in results] == [id_2, id_1, id_0]
    # Only the ORDER changes — every hit keeps its fused score...
    assert {hit.item_id: hit.score for hit in results} == {
        hit.item_id: hit.score for hit in baseline
    }
    # ...and gains the ABSOLUTE grade the fused score could never express.
    assert [hit.rerank_score for hit in results] == [9.0, 5.0, 1.0]
    assert all(hit.rerank_score is None for hit in baseline)
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


def test_search_status_reports_the_live_ranking_knobs():
    status = search_status(
        make_cfg(
            rrf_keyword_weight=2.0,
            rrf_vector_weight=0.5,
            rrf_event_weight=0.25,
            recency_half_life_days=30,
            rerank_min_score=6,
            rerank_timeout_s=8,
        )
    )

    assert status["ranking"] == {
        "keyword_weight": 2.0,
        "vector_weight": 0.5,
        "event_weight": 0.25,
        "recency_half_life_days": 30.0,
        "rerank_min_score": 6.0,
        "rerank_timeout_s": 8.0,
    }


def test_ranking_settings_fall_back_on_junk_values():
    """A hand-edited jarvis.toml must never take the ranking down."""
    knobs = ranking_settings(
        make_cfg(rrf_keyword_weight="nonsense", recency_half_life_days=-5)
    )

    assert knobs["keyword_weight"] == 1.0
    assert knobs["recency_half_life_days"] == 0.0  # negative clamps to "off"


# ---------------------------------------------------------------------------
# Per-leg weights + age decay
# ---------------------------------------------------------------------------


async def test_zero_weight_silences_a_leg_without_removing_its_hits(monkeypatch):
    keyword_hit = make_result(1)
    vector_hit = make_result(2, matched_by=("vector",))
    fake_store = FakeStore(keyword_hits=[keyword_hit], vector_hits=[vector_hit])
    register_fake_embedding(monkeypatch, FakeEmbedding())
    cfg = make_cfg(
        embedding_provider="fake",
        embedding_model=EMBED_MODEL,
        rrf_keyword_weight=0.0,
        recency_half_life_days=0,
    )

    results = await hybrid_search(fake_store, cfg, "alpha")

    # The vector hit outranks the silenced keyword hit, which still appears.
    assert [hit.item_id for hit in results] == [2, 1]
    by_id = {hit.item_id: hit for hit in results}
    assert by_id[1].score == pytest.approx(0.0, abs=1e-5)
    assert by_id[2].score > 0.0


async def test_age_decay_lets_a_fresh_item_beat_a_stale_one(monkeypatch):
    """Equal leg rank, very different age — the article's age decay."""
    now = datetime.now(UTC)
    stale = make_result(
        1, timestamp_utc=(now - timedelta(days=720)).isoformat().replace("+00:00", "Z")
    )
    fresh = make_result(
        2,
        timestamp_utc=(now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        matched_by=("vector",),
    )
    fake_store = FakeStore(keyword_hits=[stale], vector_hits=[fresh])
    register_fake_embedding(monkeypatch, FakeEmbedding())

    decayed = await hybrid_search(
        fake_store,
        make_cfg(
            embedding_provider="fake",
            embedding_model=EMBED_MODEL,
            recency_half_life_days=180,
        ),
        "alpha",
    )
    assert [hit.item_id for hit in decayed] == [2, 1]
    # Two years at a 180-day half-life is roughly a sixteenth of the score.
    by_id = {hit.item_id: hit for hit in decayed}
    assert by_id[1].score < by_id[2].score / 10


async def test_half_life_zero_keeps_the_pure_fusion_order(monkeypatch):
    """The opt-out reproduces the old behaviour exactly: with the decay off,
    an ancient rank-1 hit still ties a fresh rank-1 hit (epsilon aside)."""
    now = datetime.now(UTC)
    stale = make_result(
        1, timestamp_utc=(now - timedelta(days=720)).isoformat().replace("+00:00", "Z")
    )
    fresh = make_result(
        2,
        timestamp_utc=(now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        matched_by=("vector",),
    )
    fake_store = FakeStore(keyword_hits=[stale], vector_hits=[fresh])
    register_fake_embedding(monkeypatch, FakeEmbedding())

    results = await hybrid_search(
        fake_store,
        make_cfg(
            embedding_provider="fake",
            embedding_model=EMBED_MODEL,
            recency_half_life_days=0,
        ),
        "alpha",
    )

    by_id = {hit.item_id: hit for hit in results}
    assert by_id[1].score == pytest.approx(by_id[2].score, abs=1e-5)
    assert [hit.item_id for hit in results] == [2, 1]  # epsilon tiebreak only


async def test_unparsable_timestamp_is_never_punished_by_the_decay(monkeypatch):
    broken = make_result(1, timestamp_utc="not-a-date")
    fake_store = FakeStore(keyword_hits=[broken])
    cfg = make_cfg(recency_half_life_days=30)

    results = await hybrid_search(fake_store, cfg, "alpha")

    assert results[0].score == pytest.approx(1 / (RRF_K + 1), abs=1e-5)


# ---------------------------------------------------------------------------
# Term rarity (IDF)
# ---------------------------------------------------------------------------


class SignalStore(FakeStore):
    """A store that answers the ranking-signal probes."""

    def __init__(self, *args, corpus: int = 100, frequencies=None, neighbors=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._corpus = corpus
        self._frequencies = frequencies or {}
        self._neighbors = neighbors or {}
        self.neighbor_calls: list[int] = []

    async def live_item_count(self) -> int:
        return self._corpus

    async def term_document_frequency(self, terms):
        return {term: self._frequencies.get(term, 0) for term in terms}

    async def neighbors_for(self, item_id: int, *, limit: int = 2) -> list[str]:
        self.neighbor_calls.append(item_id)
        return list(self._neighbors.get(item_id, []))


async def test_rare_term_hit_outranks_higher_ranked_filler():
    """The article's "separate signal from filler": a short pleasantry that
    ranked first loses to the candidate actually carrying the rare words."""
    filler = make_result(1, snippet="sounds good, thanks!")
    substantive = make_result(
        2, snippet="the bugatti chiron service interval is every two years"
    )
    store = SignalStore(
        keyword_hits=[filler, substantive],  # filler ranks FIRST before signals
        frequencies={"bugatti": 1, "chiron": 1, "the": 90},
    )

    results = await hybrid_search(store, make_cfg(recency_half_life_days=0), "bugatti chiron")

    assert [hit.item_id for hit in results] == [2, 1]


async def test_filler_is_pushed_down_never_dropped():
    """AP-27 discipline: content-based signals may reorder, never delete —
    the rerank stage is where real filtering belongs."""
    filler = make_result(1, snippet="ok")
    store = SignalStore(keyword_hits=[filler], frequencies={"bugatti": 1})

    results = await hybrid_search(store, make_cfg(recency_half_life_days=0), "bugatti")

    assert [hit.item_id for hit in results] == [1]
    assert results[0].score > 0.0


async def test_common_query_terms_leave_the_ranking_untouched():
    """Every query term is common => no rare vocabulary => neutral factor."""
    first = make_result(1, snippet="alpha one")
    second = make_result(2, snippet="alpha two")
    store = SignalStore(
        keyword_hits=[first, second], corpus=100, frequencies={"alpha": 80}
    )

    results = await hybrid_search(store, make_cfg(recency_half_life_days=0), "alpha")

    assert [hit.item_id for hit in results] == [1, 2]
    assert results[0].score == pytest.approx(1 / (RRF_K + 1), abs=1e-5)


async def test_store_without_ranking_signals_degrades_neutrally():
    """Third-party stores and fakes predate these methods — neutral, no crash."""
    plain = FakeStore(keyword_hits=[make_result(1, snippet="ok")])

    results = await hybrid_search(plain, make_cfg(recency_half_life_days=0), "bugatti")

    assert results[0].score == pytest.approx(1 / (RRF_K + 1), abs=1e-5)


async def test_failing_signal_probe_never_fails_the_search():
    class ExplodingStore(SignalStore):
        async def term_document_frequency(self, terms):
            raise RuntimeError("index corrupted")

    store = ExplodingStore(keyword_hits=[make_result(1)])

    results = await hybrid_search(store, make_cfg(recency_half_life_days=0), "bugatti")

    assert [hit.item_id for hit in results] == [1]


# ---------------------------------------------------------------------------
# Relevance floor (the "Bugatti case" guard)
# ---------------------------------------------------------------------------


async def test_explicit_search_keeps_weakly_graded_hits(store, monkeypatch):
    """The Ask view / REST route / CLI: the user asked and sees the evidence."""
    id_0, id_1, id_2 = await seed_three_ranked_items(store)
    fake = FakeReranker(pairs=[(0, 9.0), (1, 2.0), (2, 0.0)])
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: fake)
    cfg = make_cfg(rerank_provider="voyage", rerank_min_score=4.0)

    results = await hybrid_search(store, cfg, "alpha")

    assert [hit.item_id for hit in results] == [id_0, id_1, id_2]


async def test_unsolicited_surface_drops_hits_below_the_floor(store, monkeypatch):
    """Context injection / volunteered voice answers: below the floor, gone."""
    id_0, _id_1, _id_2 = await seed_three_ranked_items(store)
    fake = FakeReranker(pairs=[(0, 9.0), (1, 2.0), (2, 0.0)])
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: fake)
    cfg = make_cfg(rerank_provider="voyage", rerank_min_score=4.0)

    results = await hybrid_search(store, cfg, "alpha", enforce_floor=True)

    assert [hit.item_id for hit in results] == [id_0]


async def test_floor_can_return_nothing_at_all(store, monkeypatch):
    """RRF alone can never say "nothing here is relevant" — the grade can."""
    await seed_three_ranked_items(store)
    fake = FakeReranker(pairs=[(0, 1.0), (1, 0.0), (2, 0.0)])
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: fake)
    cfg = make_cfg(rerank_provider="voyage", rerank_min_score=4.0)

    assert await hybrid_search(store, cfg, "alpha", enforce_floor=True) == []


async def test_floor_zero_disables_the_gate(store, monkeypatch):
    await seed_three_ranked_items(store)
    fake = FakeReranker(pairs=[(0, 1.0), (1, 0.0), (2, 0.0)])
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: fake)
    cfg = make_cfg(rerank_provider="voyage", rerank_min_score=0)

    assert len(await hybrid_search(store, cfg, "alpha", enforce_floor=True)) == 3


async def test_ungraded_hits_pass_the_floor_visibly_rather_than_silently(
    store, monkeypatch, caplog
):
    """A rerank outage must not turn the floor into a silent pass-all: the
    hits come through UNGRADED (rerank_score None) so the caller's own
    deterministic gate can still refuse them."""
    await seed_three_ranked_items(store)
    fake = FakeReranker(error=RerankError("fake: HTTP 500"))
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: fake)
    cfg = make_cfg(rerank_provider="voyage", rerank_min_score=4.0)

    with caplog.at_level(logging.WARNING, logger="jarvis.ultrawiki.search"):
        results = await hybrid_search(store, cfg, "alpha", enforce_floor=True)

    assert len(results) == 3
    assert all(hit.rerank_score is None for hit in results)
    assert any("rerank failed" in rec.message for rec in caplog.records)


async def test_rerank_stage_budget_bust_keeps_the_fusion_order(
    store, monkeypatch, caplog
):
    """The 45s-per-provider trap: a hung rerank chain must never hold the
    search hostage — the stage has ONE wall-clock budget, then fusion order
    stands."""
    import asyncio

    id_0, id_1, id_2 = await seed_three_ranked_items(store)

    class HungReranker(FakeReranker):
        async def rerank(self, query, documents, top_k):
            await asyncio.sleep(5.0)
            return [(2, 9.0)]

    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: HungReranker())
    cfg = make_cfg(rerank_provider="voyage", rerank_timeout_s=0.05)

    with caplog.at_level(logging.WARNING, logger="jarvis.ultrawiki.search"):
        results = await hybrid_search(store, cfg, "alpha")

    assert [hit.item_id for hit in results] == [id_0, id_1, id_2]  # fusion order
    assert all(hit.rerank_score is None for hit in results)
    assert any("exceeded its" in rec.message for rec in caplog.records)


async def test_voice_path_can_skip_the_rerank_stage(store, monkeypatch):
    """doc 03 latency budget: realtime voice degrades stages, never blocks."""
    id_0, id_1, id_2 = await seed_three_ranked_items(store)
    fake = FakeReranker(pairs=[(2, 9.0), (1, 5.0), (0, 1.0)])
    monkeypatch.setattr(rerank_mod, "resolve_reranker", lambda cfg: fake)
    cfg = make_cfg(rerank_provider="voyage")

    results = await hybrid_search(store, cfg, "alpha", rerank=False)

    assert [hit.item_id for hit in results] == [id_0, id_1, id_2]
    assert fake.calls == []  # no model call on the voice path


# ---------------------------------------------------------------------------
# Context expansion
# ---------------------------------------------------------------------------


async def test_context_expansion_rehydrates_the_winners():
    store = SignalStore(
        keyword_hits=[make_result(1), make_result(2)],
        neighbors={1: ["the message before", "the message after"]},
    )

    results = await hybrid_search(store, make_cfg(recency_half_life_days=0), "alpha")

    by_id = {hit.item_id: hit for hit in results}
    assert by_id[1].context == ("the message before", "the message after")
    assert by_id[2].context == ()
    assert sorted(store.neighbor_calls) == [1, 2]


async def test_context_expansion_only_runs_for_the_returned_top_k():
    store = SignalStore(
        keyword_hits=[make_result(i) for i in range(1, 6)],
        neighbors={i: [f"ctx {i}"] for i in range(1, 6)},
    )

    results = await hybrid_search(
        store, make_cfg(recency_half_life_days=0), "alpha", k=2
    )

    assert len(results) == 2
    assert store.neighbor_calls == [1, 2]  # no wasted lookups below the cut


async def test_context_expansion_failure_leaves_bare_snippets():
    class ExplodingStore(SignalStore):
        async def neighbors_for(self, item_id: int, *, limit: int = 2) -> list[str]:
            raise RuntimeError("neighbour lookup exploded")

    store = ExplodingStore(keyword_hits=[make_result(1)])

    results = await hybrid_search(store, make_cfg(recency_half_life_days=0), "alpha")

    assert results[0].context == ()


async def test_context_expansion_can_be_switched_off():
    store = SignalStore(keyword_hits=[make_result(1)], neighbors={1: ["ctx"]})

    results = await hybrid_search(
        store, make_cfg(recency_half_life_days=0), "alpha", expand_context=False
    )

    assert results[0].context == ()
    assert store.neighbor_calls == []


# ---------------------------------------------------------------------------
# Stage timing instrumentation
# ---------------------------------------------------------------------------


async def test_timings_report_every_stage_that_ran(store, monkeypatch):
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    register_fake_embedding(monkeypatch, FakeEmbedding())
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)
    timings: dict[str, float] = {}

    results = await hybrid_search(store, cfg, "alpha", timings=timings)

    assert results
    ran = {"keyword_ms", "vector_ms", "vector_embed_ms", "vector_ann_ms",
           "signals_ms", "context_ms", "total_ms"}
    assert ran <= set(timings)
    assert "rerank_ms" not in timings  # no rerank provider configured
    assert all(value >= 0.0 for value in timings.values())
    # The stage costs can never exceed the honest total.
    assert timings["total_ms"] >= timings["keyword_ms"]


async def test_timings_record_a_failing_embed_stage(store, monkeypatch):
    """The point of measuring: a DEGRADED stage still reports the time it
    burned before giving up, so a slow failure is visible in the breakdown."""
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    register_fake_embedding(
        monkeypatch, FakeEmbedding(error=EmbeddingError("fake: HTTP 500"))
    )
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)
    timings: dict[str, float] = {}

    results = await hybrid_search(store, cfg, "alpha", timings=timings)

    assert results  # keyword leg still answered
    assert "vector_embed_ms" in timings  # the failed embed reported its cost
    assert "vector_ann_ms" not in timings  # the ANN query never ran


async def test_slow_search_logs_its_breakdown_at_info(store, monkeypatch, caplog):
    import jarvis.ultrawiki.search as search_mod

    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    monkeypatch.setattr(search_mod, "_SLOW_SEARCH_MS", 0.0)

    with caplog.at_level(logging.INFO, logger="jarvis.ultrawiki.search"):
        await hybrid_search(store, make_cfg(), "alpha")

    assert any("hybrid search took" in rec.message for rec in caplog.records)
    # Privacy: durations and counts only — the query text never reaches a log.
    assert not any("alpha" in rec.getMessage() for rec in caplog.records)


async def test_fast_search_stays_quiet_at_info_level(store, caplog):
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)

    with caplog.at_level(logging.INFO, logger="jarvis.ultrawiki.search"):
        await hybrid_search(store, make_cfg(), "alpha")

    assert not any("hybrid search took" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Vector-leg budget
# ---------------------------------------------------------------------------


async def test_slow_vector_leg_degrades_to_keyword_within_budget(
    store, monkeypatch, caplog
):
    """A cold or throttled embedding provider must cost the caller its
    vector hits, never its whole answer — the injector's hard overall budget
    depends on this."""
    import asyncio

    class SlowEmbedding(FakeEmbedding):
        async def embed(self, texts, *, model):
            await asyncio.sleep(5.0)
            return await super().embed(texts, model=model)

    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    register_fake_embedding(monkeypatch, SlowEmbedding())
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    with caplog.at_level(logging.INFO, logger="jarvis.ultrawiki.search"):
        results = await hybrid_search(
            store, cfg, "alpha", vector_timeout_s=0.05
        )

    assert results  # keyword leg still answered
    assert all(hit.matched_by == ("keyword",) for hit in results)
    assert any("continuing keyword-only" in rec.message for rec in caplog.records)


async def test_unset_vector_budget_leaves_the_leg_unbounded(store, monkeypatch):
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    fake = FakeEmbedding()
    register_fake_embedding(monkeypatch, fake)
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    results = await hybrid_search(store, cfg, "alpha", vector_timeout_s=0)

    assert results
    assert len(fake.calls) == 1  # the leg ran normally


# ---------------------------------------------------------------------------
# Query-embedding cache
# ---------------------------------------------------------------------------


async def test_repeated_query_embeds_only_once(store, monkeypatch):
    """The injector and the search tool run the SAME query within one turn;
    the second run must not pay the embedding round trip again."""
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    fake = FakeEmbedding()
    register_fake_embedding(monkeypatch, fake)
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    await hybrid_search(store, cfg, "alpha content")
    await hybrid_search(store, cfg, "  Alpha   CONTENT ")  # same after normalizing

    assert len(fake.calls) == 1


async def test_concurrent_identical_searches_share_embedding_and_ann(monkeypatch):
    """One UI/brain turn must not multiply the two expensive vector steps."""
    gate = asyncio.Event()

    class SlowEmbedding(FakeEmbedding):
        async def embed(self, texts, *, model):
            await gate.wait()
            return await super().embed(texts, model=model)

    class SlowStore(FakeStore):
        async def vector_search(self, query_vector, k=10, *, area_id=None):
            await asyncio.sleep(0.02)
            return await super().vector_search(
                query_vector, k=k, area_id=area_id
            )

    fake = SlowEmbedding()
    register_fake_embedding(monkeypatch, fake)
    store = SlowStore(
        keyword_hits=[make_result(1, matched_by=("keyword",))],
        vector_hits=[make_result(1, matched_by=("vector",))],
    )
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    first = asyncio.create_task(hybrid_search(store, cfg, "alpha"))
    second = asyncio.create_task(hybrid_search(store, cfg, "alpha"))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)

    assert len(fake.calls) == 1
    assert len(store.vector_calls) == 1


async def test_cache_is_scoped_by_model_and_query(store, monkeypatch):
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    fake = FakeEmbedding()
    register_fake_embedding(monkeypatch, fake)

    await hybrid_search(
        store, make_cfg(embedding_provider="fake", embedding_model="model-a"), "alpha"
    )
    await hybrid_search(
        store, make_cfg(embedding_provider="fake", embedding_model="model-b"), "alpha"
    )
    await hybrid_search(
        store, make_cfg(embedding_provider="fake", embedding_model="model-a"), "beta"
    )

    assert len(fake.calls) == 3  # no cross-model or cross-query reuse


async def test_failed_embeddings_are_never_cached(store, monkeypatch):
    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    fake = FakeEmbedding(error=EmbeddingError("fake: HTTP 500"))
    register_fake_embedding(monkeypatch, fake)
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    await hybrid_search(store, cfg, "alpha")
    await hybrid_search(store, cfg, "alpha")

    assert len(fake.calls) == 2  # a failure is retried, not remembered


async def test_cache_eviction_keeps_the_size_bounded(store, monkeypatch):
    import jarvis.ultrawiki.search as search_mod

    await add_source(store)
    await store.upsert_items("src1", [raw_item("a", body="alpha content")])
    await index_all_keyword(store)
    fake = FakeEmbedding()
    register_fake_embedding(monkeypatch, fake)
    monkeypatch.setattr(search_mod, "_QUERY_VECTOR_CACHE_MAX", 2)
    cfg = make_cfg(embedding_provider="fake", embedding_model=EMBED_MODEL)

    for query in ("one", "two", "three"):
        await hybrid_search(store, cfg, query)

    assert len(search_mod._QUERY_VECTOR_CACHE) == 2
    # "one" was evicted (oldest); asking again re-embeds.
    await hybrid_search(store, cfg, "one")
    assert len(fake.calls) == 4


# ---------------------------------------------------------------------------
# Fusion and the event leg (P5 review, finding 7)
# ---------------------------------------------------------------------------


def test_fusion_decays_the_record_not_the_memory():
    """The age factor answers "how stale is this note", not "how long ago did
    this happen". An event card carries the event's date in ``timestamp_utc``,
    so decaying by that demotes a note written yesterday about a dinner three
    years ago by ~65x for having remembered something old."""
    from jarvis.ultrawiki.search import _fuse

    fresh = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    old_event = make_result(
        1,
        timestamp_utc="2023-03-14T19:30:00Z",
        matched_by=("event",),
        recorded_utc=fresh,
    )
    plain = make_result(2, timestamp_utc=fresh)

    fused = _fuse([plain], [], cfg=make_cfg(), event_hits=[old_event])
    by_item = {hit.item_id: hit for hit in fused}
    # Same single-leg RRF contribution on both, so any gap is pure decay.
    assert by_item[1].score == pytest.approx(by_item[2].score, rel=0.02)
    # The date the user asked about still survives for display.
    assert by_item[1].timestamp_utc == "2023-03-14T19:30:00Z"


def test_a_multi_event_item_votes_once_in_the_event_leg():
    """An itinerary can produce five events. Five rows for one item would
    stack five RRF contributions where every other item gets one."""
    from jarvis.ultrawiki.search import _fuse

    itinerary = [
        make_result(1, matched_by=("event",), title=f"leg {n}") for n in range(5)
    ]
    single = [make_result(2, matched_by=("event",))]

    fused = _fuse(
        [], [], cfg=make_cfg(recency_half_life_days=0.0), event_hits=[*itinerary, *single]
    )
    by_item = {hit.item_id: hit for hit in fused}
    # Item 1 keeps rank 1 and item 2 gets the NEXT dense rank, not rank 6 —
    # a many-event item must not push the items behind it down either.
    assert by_item[1].score == pytest.approx(1.0 / (RRF_K + 1), abs=1e-5)
    assert by_item[2].score == pytest.approx(1.0 / (RRF_K + 2), abs=1e-5)
    assert by_item[1].matched_by == ("event",)


def test_an_item_matched_by_two_legs_still_counts_both():
    from jarvis.ultrawiki.search import _fuse

    fused = _fuse(
        [make_result(1)],
        [make_result(1, matched_by=("vector",))],
        cfg=make_cfg(recency_half_life_days=0.0),
        event_hits=[make_result(1, matched_by=("event",))],
    )
    assert len(fused) == 1
    assert set(fused[0].matched_by) == {"event", "keyword", "vector"}
    assert fused[0].score == pytest.approx(3.0 / (RRF_K + 1), abs=1e-3)
