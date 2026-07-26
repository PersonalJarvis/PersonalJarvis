"""The completeness verdict — "is everything really in there?".

A total cannot answer that question: "4 689 items" is equally consistent with
*all of it* and with *as much as we managed before something quietly stopped*.
The verdict compares two independently produced numbers — what the last
finished run READ against what the store HOLDS — so agreement is evidence and
disagreement prints the gap.

These pin the four outcomes, because "not complete" has genuinely different
causes and a user acts differently on each.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.ultrawiki_routes import reconcile_sources


class _Service:
    """Only the one method the route uses."""

    def __init__(self, sources: list[dict[str, Any]]) -> None:
        self._sources = sources

    async def status(self) -> dict[str, Any]:
        return {"sources": self._sources}


@pytest.fixture
def client_for():
    """A tiny app around the route — no store, no pipeline, no network."""

    def _build(sources: list[dict[str, Any]]) -> TestClient:
        app = FastAPI()
        app.state.ultrawiki = _Service(sources)
        app.get("/reconcile")(reconcile_sources)
        return TestClient(app)

    return _build


def _source(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "normal-wiki",
        "label": "Built-in Wiki",
        "counts": {"total": 60},
        "last_outcome": {
            "status": "done",
            "new": 60,
            "changed": 0,
            "unchanged": 0,
            "tombstoned": 0,
            "finished_at": "2026-07-25T18:08:31Z",
        },
        "last_error": "",
    }
    base.update(overrides)
    return base


def test_a_fully_landed_source_says_so_with_its_numbers(client_for):
    body = client_for([_source()]).get("/reconcile").json()
    row = body["sources"][0]
    assert row["verdict"] == "complete"
    assert row["read"] == 60
    assert row["stored"] == 60
    assert "All 60 item(s)" in row["detail"]
    assert body["all_complete"] is True
    assert body["total_stored"] == 60


def test_read_counts_every_item_the_run_saw_not_just_the_new_ones(client_for):
    """A re-import is mostly `unchanged`, and those items ARE in the database.

    Counting only `new` would report a healthy second sync as having lost
    everything — the most alarming possible way to be wrong.
    """
    body = (
        client_for(
            [
                _source(
                    counts={"total": 60},
                    last_outcome={
                        "status": "done",
                        "new": 2,
                        "changed": 3,
                        "unchanged": 55,
                        "tombstoned": 0,
                        "finished_at": "2026-07-25T19:00:00Z",
                    },
                )
            ]
        )
        .get("/reconcile")
        .json()
    )
    row = body["sources"][0]
    assert row["read"] == 60
    assert row["verdict"] == "complete"


def test_a_source_that_stored_fewer_than_it_read_prints_the_gap(client_for):
    body = (
        client_for(
            [
                _source(
                    counts={"total": 40},
                    last_outcome={
                        "status": "done",
                        "new": 60,
                        "changed": 0,
                        "unchanged": 0,
                        "tombstoned": 0,
                        "finished_at": "2026-07-25T18:08:31Z",
                    },
                )
            ]
        )
        .get("/reconcile")
        .json()
    )
    row = body["sources"][0]
    assert row["verdict"] == "short"
    # The number, not a shrug: 20 items are unaccounted for.
    assert "20 are missing" in row["detail"]
    assert body["all_complete"] is False


def test_a_run_that_ended_early_is_not_reported_as_complete(client_for):
    """A cancelled or failed run may simply not have reached the rest.

    Its stored count can look right while half the source was never read, so
    the verdict must follow the run's own status, not the arithmetic.
    """
    body = (
        client_for(
            [
                _source(
                    counts={"total": 60},
                    last_outcome={
                        "status": "cancelled",
                        "new": 60,
                        "changed": 0,
                        "unchanged": 0,
                        "tombstoned": 0,
                        "finished_at": "2026-07-25T18:08:31Z",
                    },
                )
            ]
        )
        .get("/reconcile")
        .json()
    )
    row = body["sources"][0]
    assert row["verdict"] == "incomplete"
    assert "cancelled" in row["detail"]


def test_a_never_imported_source_is_its_own_verdict(client_for):
    """The exact state that left this knowledge base empty for days."""
    body = client_for([_source(counts={}, last_outcome=None)]).get("/reconcile").json()
    row = body["sources"][0]
    assert row["verdict"] == "never_imported"
    assert row["read"] == 0
    assert row["stored"] == 0
    assert body["all_complete"] is False


def test_the_summary_counts_only_the_sources_that_are_complete(client_for):
    body = (
        client_for(
            [
                _source(id="a", label="A"),
                _source(id="b", label="B", counts={}, last_outcome=None),
            ]
        )
        .get("/reconcile")
        .json()
    )
    assert body["complete"] == 1
    assert body["total_sources"] == 2
    assert body["all_complete"] is False


def test_no_sources_is_not_reported_as_all_complete(client_for):
    """An empty install must not claim a clean bill of health."""
    body = client_for([]).get("/reconcile").json()
    assert body["sources"] == []
    assert body["all_complete"] is False
    assert body["total_stored"] == 0
