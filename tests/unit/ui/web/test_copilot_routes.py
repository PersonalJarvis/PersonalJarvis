from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import copilot_routes


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(copilot_routes, "user_data_dir", lambda: tmp_path)
    from jarvis.copilot import workflow

    monkeypatch.setattr(workflow, "user_data_dir", lambda: tmp_path)
    deck = tmp_path / "materials" / "d1"
    deck.mkdir(parents=True)
    (deck / "deck.json").write_text('{"title": "T", "order": "o", "slides": [{}, {}]}', "utf-8")
    (deck / "quality.txt").write_text("OK  a\nNG  b\n", "utf-8")
    (deck / "deck.pdf").write_bytes(b"%PDF")
    app = FastAPI()
    app.include_router(copilot_routes.router)
    return TestClient(app)


def test_overview_reads_what_the_features_wrote(client: TestClient) -> None:
    body = client.get("/api/copilot/overview").json()
    m = body["materials"][0]
    assert (m["title"], m["slides"], m["passed"], m["total"]) == ("T", 2, 1, 2)
    assert m["files"] == ["deck.pdf"]
    assert body["observing"] is False and body["lesson"] is None


def test_open_refuses_paths_outside_the_copilot_folders(client: TestClient) -> None:
    res = client.post("/api/copilot/open", json={"area": "materials", "path": "../../etc"})
    assert res.status_code == 400
    assert client.post("/api/copilot/open", json={"area": "home", "path": "x"}).status_code == 400


def test_run_needs_the_assistant(client: TestClient) -> None:
    assert client.post("/api/copilot/run", json={"text": "summarize"}).status_code == 503
