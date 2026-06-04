"""Route-Tests fuer ``/api/board/personal/*``.

Isolierter Setup: wir haengen nur den ``board_router`` in eine frische
``FastAPI``-Instanz und wiring manuell ``app.state.board_store`` +
``app.state.board_aggregator``. Das vermeidet die komplexe ``WebServer``-
Initialisierung (Skill-Registry, Conductor, MCP) im Route-Test.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.board.aggregator import BoardAggregator
from jarvis.board.store import BoardStore
from jarvis.ui.web.board_routes import router as board_router


def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1e9)


@pytest.fixture
def wired_client(tmp_path: Path) -> TestClient:
    jsonl_dir = tmp_path / "flight_recorder"
    jsonl_dir.mkdir()
    # minimaler Datenstand: 2 Tage mit unterschiedlichen Signalen
    today = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    events = [
        {
            "ts_ns": _ns(yesterday),
            "trace_id": "a" * 32, "event": "TaskCompleted", "layer": "tasks",
            "payload": {"task_id": "y1", "duration_ms": 100},
        },
        {
            "ts_ns": _ns(yesterday + timedelta(minutes=1)),
            "trace_id": "a" * 32, "event": "ActionExecuted", "layer": "orchestrator",
            "payload": {"tool_name": "bash", "success": True, "duration_ms": 50},
        },
        {
            "ts_ns": _ns(yesterday + timedelta(minutes=2)),
            "trace_id": "b" * 32, "event": "ActionExecuted", "layer": "orchestrator",
            "payload": {"tool_name": "search_web", "success": True, "duration_ms": 200},
        },
        {
            "ts_ns": _ns(today),
            "trace_id": "c" * 32, "event": "TranscriptFinal", "layer": "speech.stt",
            "payload": {"transcript": {"text": "<redacted>"}},
        },
        {
            "ts_ns": _ns(today + timedelta(seconds=10)),
            "trace_id": "c" * 32, "event": "OpenClawTaskCompleted", "layer": "agents",
            "payload": {"success": True, "duration_s": 600.0, "summary": "<redacted>"},
        },
    ]
    (jsonl_dir / "d.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8",
    )

    db_path = tmp_path / "board" / "personal.db"
    aggregator = BoardAggregator(jsonl_dir=jsonl_dir, db_path=db_path)
    aggregator.run()

    app = FastAPI()
    app.include_router(board_router)
    app.state.board_store = BoardStore(db_path=db_path)
    app.state.board_aggregator = aggregator

    with TestClient(app) as client:
        yield client


def test_summary_returns_totals_and_window(wired_client: TestClient) -> None:
    resp = wired_client.get("/api/board/personal/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_days"] == 30
    assert body["totals"]["tasks_completed"] >= 2
    assert body["totals"]["voice_commands"] >= 1
    assert body["totals"]["hours_saved"] > 0.0
    assert body["totals"]["activity_events"] >= 1
    assert body["totals"]["conversation_hours"] >= 0.0
    assert isinstance(body["streak_days"], int)


def test_heatmap_fills_every_day(wired_client: TestClient) -> None:
    resp = wired_client.get("/api/board/personal/heatmap?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 30
    assert len(body["cells"]) == 30
    # mindestens eine Zelle muss Aktivitaet haben, auch wenn kein Task lief
    assert any(c["activity_events"] >= 1 for c in body["cells"])


def test_tools_histogram_contains_used_tools(wired_client: TestClient) -> None:
    resp = wired_client.get("/api/board/personal/tools?window_days=30")
    assert resp.status_code == 200
    body = resp.json()
    tools = [entry["tool"] for entry in body["histogram"]]
    assert "bash" in tools
    assert "search_web" in tools
    assert body["total_unique"] >= 2


def test_records_are_set(wired_client: TestClient) -> None:
    resp = wired_client.get("/api/board/personal/records")
    assert resp.status_code == 200
    body = resp.json()
    metrics = {r["metric"] for r in body["records"]}
    assert "most_tasks_in_a_day" in metrics


def test_refresh_reruns_aggregator(wired_client: TestClient) -> None:
    resp = wired_client.post("/api/board/personal/refresh")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "triggered": True}


def test_503_when_store_missing(tmp_path: Path) -> None:
    """Ohne konfigurierten Store muss der Endpoint sauber 503 liefern."""
    app = FastAPI()
    app.include_router(board_router)
    app.state.board_store = None
    app.state.board_aggregator = None
    with TestClient(app) as client:
        resp = client.get("/api/board/personal/summary")
        assert resp.status_code == 503
