"""Benchmarks + freshness: canned snippets in, honest labels out, offline safe."""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain.ollama_pull import CURATED_REVIEWED_ON, RECOMMENDED_MODELS
from jarvis.core import config as cfg_mod
from jarvis.local_models import benchmarks as bm

pytestmark = pytest.mark.asyncio

TODAY = CURATED_REVIEWED_ON + _dt.timedelta(days=1)
NOW = _dt.datetime(TODAY.year, TODAY.month, TODAY.day, 12, 0, tzinfo=_dt.UTC)

SNIPPETS: dict[str, list[dict[str, Any]]] = {
    "qwen3.5 Artificial Analysis intelligence index": [
        {
            "title": "Qwen 3.5 27B — Artificial Analysis",
            "url": "https://artificialanalysis.ai/models/qwen3-5-27b",
            "snippet": "Qwen3.5 27B scores an Intelligence Index of 48 across our evaluations.",
        },
        {
            "title": "Unrelated blog",
            "url": "https://example.com/blog",
            "snippet": "Gemma 4 has an intelligence index of 99 (this is not about qwen).",
        },
    ],
    "qwen3.5 LMArena text arena score": [
        {
            "title": "LMArena leaderboard",
            "url": "https://lmarena.ai/leaderboard/text",
            "snippet": "qwen3.5-27b-instruct: Arena score 1312, rank 18.",
        }
    ],
    "qwen3.5 Open LLM Leaderboard average score": [],
    "gemma4 Artificial Analysis intelligence index": [
        {
            "title": "Gemma 4 — page without numbers",
            "url": "https://artificialanalysis.ai/models/gemma-4",
            "snippet": "Gemma 4 is Google's open model family.",
        }
    ],
    "gemma4 LMArena text arena score": [],
    "gemma4 Open LLM Leaderboard average score": [],
}


class _Search:
    def __init__(self, table: dict[str, list[dict[str, Any]]] | None = None, *, fail: bool = False):
        self.table = table if table is not None else SNIPPETS
        self.fail = fail
        self.queries: list[str] = []

    async def __call__(self, query: str) -> list[dict[str, Any]]:
        self.queries.append(query)
        if self.fail:
            raise ConnectionError("offline")
        return self.table.get(query, [])


def _library(entries: dict[str, dict[str, str]], *, fail: bool = False):
    async def _fn(family: str) -> dict[str, Any]:
        if fail:
            raise ConnectionError("offline")
        entry = entries.get(family)
        return {"models": [dict(name=family, **entry)] if entry else [], "error": None}

    return _fn


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)
    return tmp_path


# ------------------------------------------------------------ pure helpers


def test_family_and_pulls_and_updated_parsers() -> None:
    assert bm.family_of("qwen3.5:4b") == "qwen3.5"
    assert bm.family_of("gemma4:26b-a4b-it-qat") == "gemma4"
    assert bm.family_of("embeddinggemma") == "embeddinggemma"
    assert bm.parse_pulls("17.2M") == 17_200_000
    assert bm.parse_pulls("845.3K") == 845_300
    assert bm.parse_pulls("1,204") == 1204
    assert bm.parse_pulls("") == 0 and bm.parse_pulls("lots") == 0
    assert bm.parse_updated_days("2 weeks ago") == 14
    assert bm.parse_updated_days("a month ago") == 30
    assert bm.parse_updated_days("yesterday") == 1
    assert bm.parse_updated_days("3 hours ago") == 0
    assert bm.parse_updated_days("") is None and bm.parse_updated_days("soon") is None


def test_extract_rows_is_tolerant_and_keyed_by_source_and_metric() -> None:
    results = (
        SNIPPETS["qwen3.5 Artificial Analysis intelligence index"]
        + SNIPPETS["qwen3.5 LMArena text arena score"]
    )
    rows = bm.extract_rows("qwen3.5", results, source="artificial_analysis", seen_at="t")
    by_key = {(r.source, r.metric): r for r in rows}
    assert by_key[("artificial_analysis", "intelligence_index")].value == 48.0
    # The LMArena snippet is filed under LMArena by its host, whatever query found it.
    assert by_key[("lmarena", "arena_score")].value == 1312.0
    assert by_key[("lmarena", "arena_score")].url == "https://lmarena.ai/leaderboard/text"
    # The unrelated snippet (no family mention) contributed nothing.
    assert all(r.value != 99.0 for r in rows)
    assert bm.extract_rows("qwen3.5", [None, "junk", {}], source="x", seen_at="t") == []  # type: ignore[list-item]


def test_curated_families_come_from_the_shortlist() -> None:
    families = bm.curated_families()
    assert families[0] == bm.family_of(RECOMMENDED_MODELS[0].id)
    assert len(families) == len(set(families))
    assert bm.curated_families(("qwen3.5:4b", "qwen3.5:27b")) == ("qwen3.5",)


# ------------------------------------------------------------ labelling


def _table(
    rows: tuple[bm.BenchmarkRow, ...] = (), library: dict[str, bm.LibraryFacts] | None = None
) -> bm.BenchmarkTable:
    return bm.BenchmarkTable(
        fetched_at=NOW.isoformat(), rows=rows, library=library or {}, source="live"
    )


def test_label_proven_needs_a_row_or_100k_pulls() -> None:
    row = bm.BenchmarkRow("qwen3.5", "lmarena", "arena_score", 1312.0, "u", "t")
    assert bm.label_for("qwen3.5:4b", _table((row,)), TODAY) == "proven"
    popular = {"gemma4": bm.LibraryFacts("gemma4", pulls=2_000_000, updated_days=120)}
    assert bm.label_for("gemma4:12b-it-qat", _table((), popular), TODAY) == "proven"
    quiet = {"gemma4": bm.LibraryFacts("gemma4", pulls=5_000, updated_days=120)}
    assert bm.label_for("gemma4:12b-it-qat", _table((), quiet), TODAY) == "new_little_tested"
    assert bm.label_for("gemma4:12b-it-qat", None, TODAY) == "new_little_tested"


def test_label_new_when_the_library_updated_it_recently_even_with_rows() -> None:
    row = bm.BenchmarkRow("qwen3.5", "lmarena", "arena_score", 1312.0, "u", "t")
    fresh = {"qwen3.5": bm.LibraryFacts("qwen3.5", pulls=9_000_000, updated_days=3)}
    assert bm.label_for("qwen3.5:4b", _table((row,), fresh), TODAY) == "new_little_tested"
    settled = {"qwen3.5": bm.LibraryFacts("qwen3.5", pulls=9_000_000, updated_days=60)}
    assert bm.label_for("qwen3.5:4b", _table((row,), settled), TODAY) == "proven"


def test_label_stale_when_the_review_is_older_than_a_year() -> None:
    row = bm.BenchmarkRow("qwen3.5", "lmarena", "arena_score", 1312.0, "u", "t")
    a_year_later = CURATED_REVIEWED_ON + _dt.timedelta(days=366)
    assert bm.label_for(RECOMMENDED_MODELS[0], _table((row,)), a_year_later) == "stale"
    assert bm.label_for(RECOMMENDED_MODELS[0], _table((row,)), TODAY) in bm.LABELS


# ------------------------------------------------------------ refresh + cache


async def test_refresh_runs_three_queries_per_family_and_caches(tmp_path: Path) -> None:
    search = _Search()
    library = _library({"qwen3.5": {"pulls": "17.2M", "updated": "3 months ago"}})
    table = await bm.refresh_benchmarks(
        search, families=("qwen3.5", "gemma4"), library_fn=library, now=NOW
    )
    assert table.source == "live" and table.note == ""
    assert sorted(search.queries) == sorted(SNIPPETS)
    assert {(r.source, r.metric, r.value) for r in table.rows_for("qwen3.5")} == {
        ("artificial_analysis", "intelligence_index", 48.0),
        ("lmarena", "arena_score", 1312.0),
    }
    assert table.rows_for("gemma4") == ()
    assert table.library["qwen3.5"] == bm.LibraryFacts("qwen3.5", 17_200_000, 90)
    assert "gemma4" not in table.library

    written = json.loads((tmp_path / "state" / "local_models_benchmarks.json").read_text("utf-8"))
    assert written["fetched_at"] == NOW.isoformat(timespec="seconds")
    assert len(written["rows"]) == 2

    cached = bm.load_cached(now=NOW + _dt.timedelta(days=6))
    assert cached is not None and cached.source == "cache"
    assert cached.rows == table.rows and cached.library == table.library
    assert bm.load_cached(now=NOW + _dt.timedelta(days=7)) is None, "TTL is seven days"
    assert bm.load_cached(max_age_days=None, now=NOW + _dt.timedelta(days=400)) is not None


async def test_one_failing_query_loses_only_its_rows() -> None:
    class _Flaky(_Search):
        async def __call__(self, query: str) -> list[dict[str, Any]]:
            if "LMArena" in query:
                raise TimeoutError("slow")
            return await super().__call__(query)

    table = await bm.refresh_benchmarks(
        _Flaky(), families=("qwen3.5",), library_fn=_library({}), now=NOW, persist=False
    )
    assert [r.metric for r in table.rows] == ["intelligence_index"]


async def test_offline_prefers_the_cache_then_the_curated_note(tmp_path: Path) -> None:
    # No cache at all → curated-only, with the note.
    table = await bm.refresh_benchmarks(
        _Search(fail=True), families=("qwen3.5",), library_fn=_library({}, fail=True), now=NOW
    )
    assert table.source == "curated" and table.rows == () and "curated" in table.note
    assert not (tmp_path / "state" / "local_models_benchmarks.json").exists()

    # A cache (however old) beats curated-only.
    await bm.refresh_benchmarks(
        _Search(), families=("qwen3.5",), library_fn=_library({}), now=NOW - _dt.timedelta(days=30)
    )
    table = await bm.refresh_benchmarks(
        _Search(fail=True), families=("qwen3.5",), library_fn=_library({}, fail=True), now=NOW
    )
    assert table.source == "cache" and len(table.rows) == 2 and "cached" in table.note


async def test_sync_search_fn_is_accepted() -> None:
    def search(query: str) -> list[dict[str, Any]]:
        return SNIPPETS.get(query, [])

    table = await bm.refresh_benchmarks(
        search, families=("qwen3.5",), library_fn=_library({}), now=NOW, persist=False
    )
    assert len(table.rows) == 2


def test_from_payload_drops_malformed_entries() -> None:
    table = bm.BenchmarkTable.from_payload(
        {
            "fetched_at": "2026-08-25T12:00:00+00:00",
            "rows": [{"family": "a", "value": "x"}, {"family": "b", "value": 1}],
            "library": {"a": {"pulls": "nope"}, "b": {"pulls": 5, "updated_days": None}},
        }
    )
    assert [r.family for r in table.rows] == ["b"]
    assert table.library == {"b": bm.LibraryFacts("b", 5, None)}
