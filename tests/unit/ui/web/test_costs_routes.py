"""Tests for the Spend & Tokens REST surface (``/api/costs``).

The read model itself is covered in ``tests/unit/costs``; what matters here is
the contract the section depends on:

- the filters actually narrow the report, and every dimension is filterable,
- the filter OPTIONS stay complete while a filter is on (a picker that removes
  its own options is a dead end),
- an install with no databases yet still answers 200 with zeroes.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_SCHEMA = """
CREATE TABLE voice_turns (
    id TEXT PRIMARY KEY, session_id TEXT, started_ms INTEGER, tier TEXT,
    provider TEXT, model TEXT, tokens_in INTEGER, tokens_out INTEGER,
    cost_usd REAL, user_text TEXT
);
"""

NOW_MS = int(time.time() * 1000)


def _seed(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / "sessions.db")
    conn.executescript(_SCHEMA)
    rows = [
        ("t1", "sess-a", NOW_MS - 3_600_000, "realtime", "gemini-live",
         "gemini-3.1-flash-live-preview", 40_000, 200, 0.40, "what is on my calendar"),
        ("t2", "sess-b", NOW_MS - 7_200_000, "deep", "anthropic",
         "claude-opus-4-7-20251022", 2_000, 400, 0.09, "summarise the meeting"),
    ]
    conn.executemany(
        "INSERT INTO voice_turns (id, session_id, started_ms, tier, provider, model, "
        "tokens_in, tokens_out, cost_usd, user_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _client(data_dir: Path) -> TestClient:
    from jarvis.ui.web.costs_routes import router

    app = FastAPI()
    app.include_router(router)
    # The routes resolve their stores from the instance's data dir, so a test
    # sandbox is a config object with nothing else on it.
    app.state.config = SimpleNamespace(memory=SimpleNamespace(data_dir=str(data_dir)))
    return TestClient(app)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    _seed(tmp_path / "data")
    return _client(tmp_path / "data")


def test_summary_totals_every_source(client: TestClient) -> None:
    body = client.get("/api/costs/summary?days=30").json()
    assert body["totals"]["cost_usd"] == pytest.approx(0.49)
    assert body["totals"]["entries"] == 2
    assert {row["key"] for row in body["by_provider"]} == {"gemini-live", "anthropic"}
    assert {row["key"] for row in body["by_role"]} == {"realtime", "pipeline"}
    assert body["sources_present"] == ["sessions.db"]


def test_filters_narrow_the_totals(client: TestClient) -> None:
    body = client.get("/api/costs/summary?days=30&provider=anthropic").json()
    assert body["totals"]["entries"] == 1
    assert body["totals"]["cost_usd"] == pytest.approx(0.09)


def test_facets_stay_complete_while_filtering(client: TestClient) -> None:
    """Picking a provider must not delete the other providers from the picker."""
    body = client.get("/api/costs/summary?days=30&provider=anthropic").json()
    assert set(body["facets"]["providers"]) == {"gemini-live", "anthropic"}


def test_role_filter_matches_the_read_model(client: TestClient) -> None:
    body = client.get("/api/costs/summary?days=30&role=realtime").json()
    assert [row["key"] for row in body["by_provider"]] == ["gemini-live"]


def test_ref_filter_isolates_one_session(client: TestClient) -> None:
    body = client.get("/api/costs/summary?days=30&ref=sess-b").json()
    assert body["totals"]["entries"] == 1
    assert body["top_refs"][0]["key"] == "sess-b"


def test_comma_separated_filters_are_accepted(client: TestClient) -> None:
    body = client.get("/api/costs/summary?days=30&provider=anthropic,gemini-live").json()
    assert body["totals"]["entries"] == 2


def test_entries_sort_and_paginate(client: TestClient) -> None:
    page = client.get("/api/costs/entries?days=30&sort=cost&limit=1").json()
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert page["items"][0]["provider"] == "gemini-live"

    second = client.get("/api/costs/entries?days=30&sort=cost&limit=1&offset=1").json()
    assert second["items"][0]["provider"] == "anthropic"


def test_pricing_lists_every_seen_model(client: TestClient) -> None:
    body = client.get("/api/costs/pricing?days=30").json()
    models = {row["model"] for row in body["rates"]}
    assert models == {"gemini-3.1-flash-live-preview", "claude-opus-4-7-20251022"}
    live = next(r for r in body["rates"] if r["model"] == "gemini-3.1-flash-live-preview")
    # The realtime model's audio rates are the whole point of the rate card.
    assert live["audio_input_usd_per_mtok"] is not None
    assert live["known"] is True


def test_empty_install_answers_with_zeroes(tmp_path: Path) -> None:
    """No databases yet — the section must open, not 500."""
    client = _client(tmp_path / "nothing")
    body = client.get("/api/costs/summary?days=30").json()
    assert body["totals"]["cost_usd"] == 0.0
    assert body["by_provider"] == []
    assert body["sources_present"] == []


def test_currency_is_labelled_as_a_conversion(client: TestClient) -> None:
    body = client.get("/api/costs/summary?days=30").json()
    assert body["currency"]["eur_per_usd"] > 0
    assert body["currency"]["source"] in {"config", "default"}
