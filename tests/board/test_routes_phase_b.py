"""Route-Tests fuer Phase-B-Endpoints: Achievements + Bio."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.board.evaluator import AchievementEvaluator
from jarvis.board.profile import BioGenerator, BioStore, make_resolver_from_brain
from jarvis.board.store import BoardStore
from jarvis.core.events import ActionExecuted
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.ui.web.board_routes import board_router


class _FakeBrain:
    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="Auf Daten basiert: 5 Tools genutzt, meistens bash. Kein Muster darueber hinaus.")
        yield BrainDelta(finish_reason="stop", usage={"input_tokens": 100, "output_tokens": 50})


@pytest.fixture
def wired_app(tmp_path: Path) -> TestClient:
    db = tmp_path / "personal.db"
    # Evaluator mit ein paar Events vorbefuellen.
    ev = AchievementEvaluator(db)
    ev.attach()
    for tool in ("bash", "search_web", "write_file", "read_file", "grep_repo"):
        ev.evaluate_sync(
            ActionExecuted(
                trace_id=uuid4(), tool_name=tool, success=True, duration_ms=5,
            ),
        )

    bio_store = BioStore(db)
    store = BoardStore(db)
    gen = BioGenerator(
        brain_resolver=make_resolver_from_brain(_FakeBrain()),
        store=store,
        bio_store=bio_store,
        jsonl_dir=tmp_path / "flight_recorder",
    )

    app = FastAPI()
    app.include_router(board_router)
    app.state.achievement_evaluator = ev
    app.state.bio_store = bio_store
    app.state.bio_generator = gen
    with TestClient(app) as client:
        yield client


def test_achievements_list_reports_unlocked_count(wired_app: TestClient) -> None:
    resp = wired_app.get("/api/board/achievements")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 10  # 10 im Katalog
    # tool_dabbler muss unlocked sein (5 tools).
    unlocked_ids = {i["id"] for i in body["items"] if i["unlocked_at"]}
    assert "tool_dabbler" in unlocked_ids


def test_bio_empty_when_never_generated(wired_app: TestClient) -> None:
    resp = wired_app.get("/api/board/bio")
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] is None
    assert body["generated_at"] is None
    assert body["staleness_days"] is None


def test_bio_regenerate_writes_and_retrievable(wired_app: TestClient) -> None:
    resp = wired_app.post(
        "/api/board/bio/regenerate",
        json={"memory_text": "", "soul_text": ""},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["text"]

    resp2 = wired_app.get("/api/board/bio")
    assert resp2.status_code == 200
    got = resp2.json()
    assert got["text"] == body["text"]
    assert got["staleness_days"] == 0


def test_bio_regenerate_graceful_on_brain_outage(tmp_path: Path) -> None:
    class _Fail:
        async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
            raise RuntimeError("429")
            yield  # pragma: no cover — macht Methode zum AsyncGenerator

    db = tmp_path / "personal.db"
    # Leere DB fuer minimalen Setup.
    ev = AchievementEvaluator(db)
    ev.attach()
    bio_store = BioStore(db)
    store = BoardStore(db)
    gen = BioGenerator(
        brain_resolver=make_resolver_from_brain(_Fail()), store=store, bio_store=bio_store,
        jsonl_dir=tmp_path / "flight_recorder",
    )
    app = FastAPI()
    app.include_router(board_router)
    app.state.achievement_evaluator = ev
    app.state.bio_store = bio_store
    app.state.bio_generator = gen
    with TestClient(app) as client:
        resp = client.post("/api/board/bio/regenerate", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["reason"]


def test_regenerate_503_when_generator_missing(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(board_router)
    app.state.bio_generator = None
    with TestClient(app) as client:
        resp = client.post("/api/board/bio/regenerate", json={})
        assert resp.status_code == 503
