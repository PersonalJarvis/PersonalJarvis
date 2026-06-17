from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.runs.routes import router as runs_router
from jarvis.sessions.store import SessionStore


def _app(tmp_path, with_store=True):
    app = FastAPI()
    app.include_router(runs_router)
    if with_store:
        store = SessionStore(tmp_path / "chats.db")
        store.open()
        store.upsert_session(session_id="s1", started_ms=1000, wake_keyword="hey jarvis")
        store.finalize_session(session_id="s1", ended_ms=2000, hangup_reason="idle_timeout",
                               turn_count=0, total_cost_usd=0.0, total_tokens_in=0,
                               total_tokens_out=0, providers_used=[])
        app.state.session_store = store
    else:
        app.state.session_store = None
    return app


def test_list_runs_ok(tmp_path):
    client = TestClient(_app(tmp_path))
    res = client.get("/api/runs")
    assert res.status_code == 200
    body = res.json()
    assert body and body[0]["session_id"] == "s1"


def test_detail_404_for_unknown(tmp_path):
    client = TestClient(_app(tmp_path))
    assert client.get("/api/runs/nope").status_code == 404


def test_503_when_store_absent(tmp_path):
    client = TestClient(_app(tmp_path, with_store=False))
    assert client.get("/api/runs").status_code == 503
