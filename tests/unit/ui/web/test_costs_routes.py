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
from datetime import datetime, timedelta
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


# ---------------------------------------------------------------------------
# The daily ledger — one row per day, and the drill-down behind it
# ---------------------------------------------------------------------------

def _seed_two_days(data_dir: Path) -> None:
    """Two calendar days, two models on the busier one.

    Both days are in the PAST and both are anchored to local noon. Seeding
    "today at noon" would put half the rows in the future whenever the suite
    runs in the morning, and seeding "now minus a day" would straddle
    midnight — either way the test would pass or fail by the hour it ran.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    busy = int((noon - timedelta(days=1)).timestamp() * 1000)
    quiet = int((noon - timedelta(days=2)).timestamp() * 1000)
    conn = sqlite3.connect(data_dir / "sessions.db")
    conn.executescript(_SCHEMA)
    rows = [
        # The busy day: the expensive model ran early, the cheap one an hour
        # later. A ledger that sorted by "newest" would put the cheap one on
        # top and read as if the day had been spent on it.
        ("d1", "sess-a", busy, "deep", "anthropic",
         "claude-opus-4-7-20251022", 200_000, 20_000, 6.00, "the long morning run"),
        ("d2", "sess-b", busy + 3_600_000, "fast", "anthropic",
         "claude-haiku-4-5-20251001", 5_000, 500, 0.01, "a quick question"),
        ("d3", "sess-c", quiet, "realtime", "gemini-live",
         "gemini-3.1-flash-live-preview", 40_000, 200, 0.40, "the day before"),
    ]
    conn.executemany(
        "INSERT INTO voice_turns (id, session_id, started_ms, tier, provider, model, "
        "tokens_in, tokens_out, cost_usd, user_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def two_day_client(tmp_path: Path) -> TestClient:
    _seed_two_days(tmp_path / "data")
    return _client(tmp_path / "data")


def test_daily_is_one_row_per_day_newest_first(two_day_client: TestClient) -> None:
    body = two_day_client.get("/api/costs/daily?days=30").json()
    dates = [d["date"] for d in body["days"]]
    assert len(dates) == 2
    assert dates == sorted(dates, reverse=True)


def test_daily_names_every_model_the_day_used(two_day_client: TestClient) -> None:
    """The regression this replaced: a day read as if one model did it all.

    Three sessions on one day used to be three rows, each labelled with its
    own model, ordered by the timestamp of their FIRST call — so the day's
    biggest spender sank below a later, smaller one.
    """
    body = two_day_client.get("/api/costs/daily?days=30").json()
    busy = body["days"][0]
    models = [m["key"] for m in busy["by_model"]]
    assert models == ["claude-opus-4-7-20251022", "claude-haiku-4-5-20251001"]
    assert busy["by_model"][0]["cost_share"] > 0.9


def test_daily_totals_add_up_to_the_range(two_day_client: TestClient) -> None:
    daily = two_day_client.get("/api/costs/daily?days=30").json()
    summary = two_day_client.get("/api/costs/summary?days=30").json()
    assert sum(d["totals"]["entries"] for d in daily["days"]) == summary["totals"]["entries"]
    assert (
        pytest.approx(sum(d["totals"]["cost_usd"] for d in daily["days"]), rel=1e-6)
        == summary["totals"]["cost_usd"]
    )


def test_a_day_row_opens_into_exactly_that_day(two_day_client: TestClient) -> None:
    """The drill-down contract: a row's bounds reproduce the row."""
    row = two_day_client.get("/api/costs/daily?days=30").json()["days"][0]
    detail = two_day_client.get(
        f"/api/costs/summary?since_ms={row['since_ms']}&until_ms={row['until_ms']}"
    ).json()
    assert detail["totals"]["entries"] == row["totals"]["entries"]
    assert pytest.approx(detail["totals"]["cost_usd"]) == row["totals"]["cost_usd"]
    # A one-day window is short enough to be drawn hour by hour.
    assert detail["bucket"] == "hour"


def test_daily_narrows_with_the_same_filters(two_day_client: TestClient) -> None:
    body = two_day_client.get("/api/costs/daily?days=30&provider=gemini-live").json()
    assert len(body["days"]) == 1
    assert [m["key"] for m in body["days"][0]["by_model"]] == [
        "gemini-3.1-flash-live-preview"
    ]


def test_daily_on_an_empty_install_is_an_empty_ledger(tmp_path: Path) -> None:
    body = _client(tmp_path / "nothing").get("/api/costs/daily?days=30").json()
    assert body["days"] == []
    assert body["currency"]["eur_per_usd"] > 0
