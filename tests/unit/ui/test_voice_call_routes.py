"""GET /api/voice/state + POST /api/voice/call + /hangup — the voice orb's click.

The click must take the SAME path as the call hotkey (``request_voice_session``
/ ``request_voice_hangup``), report a pipeline refusal as an answer rather than
an error, and degrade honestly on a headless install (503 / available=false)
instead of 500-ing.
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import voice_call_routes
from jarvis.ui.web.voice_call_routes import router


class _Pipeline:
    """The two public arming methods, and the state the GET reads."""

    def __init__(
        self,
        armed: bool = True,
        stopped: bool = True,
        *,
        state: str = "IDLE",
        supervisor_state: str = "IDLE",
    ) -> None:
        self._armed = armed
        self._stopped = stopped
        self.calls: list[str] = []
        self._state = SimpleNamespace(name=state)
        self._supervisor = SimpleNamespace(state=supervisor_state)

    def request_voice_session(self) -> bool:
        self.calls.append("call")
        return self._armed

    def request_voice_hangup(self) -> bool:
        self.calls.append("hangup")
        return self._stopped


def _client(monkeypatch, pipeline) -> TestClient:
    monkeypatch.setattr(voice_call_routes, "_pipeline", lambda: pipeline)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_call_arms_a_wake_style_session(monkeypatch):
    pipeline = _Pipeline()
    client = _client(monkeypatch, pipeline)
    answer = client.post("/api/voice/call")
    assert answer.status_code == 200
    assert answer.json() == {"armed": True}
    assert pipeline.calls == ["call"]


def test_call_reports_a_refusal_as_an_answer(monkeypatch):
    # A session already running (or a hidden window) is a refusal, not an error.
    client = _client(monkeypatch, _Pipeline(armed=False))
    answer = client.post("/api/voice/call")
    assert answer.status_code == 200
    assert answer.json() == {"armed": False}


def test_hangup_takes_the_hangup_contract(monkeypatch):
    pipeline = _Pipeline()
    client = _client(monkeypatch, pipeline)
    answer = client.post("/api/voice/hangup")
    assert answer.status_code == 200
    assert answer.json() == {"stopped": True}
    assert pipeline.calls == ["hangup"]


def test_headless_answers_honestly_instead_of_500(monkeypatch):
    client = _client(monkeypatch, None)
    assert client.post("/api/voice/call").status_code == 503
    assert client.post("/api/voice/hangup").status_code == 503
    answer = client.get("/api/voice/state")
    assert answer.status_code == 200
    assert answer.json() == {
        "available": False,
        "state": "unavailable",
        "voice_state": "idle",
    }


def test_state_names_the_pipeline_state(monkeypatch):
    client = _client(monkeypatch, _Pipeline())
    assert client.get("/api/voice/state").json() == {
        "available": True,
        "state": "idle",
        "voice_state": "idle",
    }


def test_state_mirrors_the_supervisor_while_a_session_runs(monkeypatch):
    # The fine-grained state every voice surface renders: the REST mirror of
    # the one-shot SystemStateChanged event, so a window that missed one can
    # reconcile instead of showing the last thing it heard forever.
    client = _client(
        monkeypatch,
        _Pipeline(state="ACTIVE", supervisor_state="LISTENING"),
    )
    assert client.get("/api/voice/state").json() == {
        "available": True,
        "state": "active",
        "voice_state": "listening",
    }


def test_state_says_unknown_when_a_running_session_has_no_supervisor(monkeypatch):
    # Never a guess: the client leaves a live call alone on anything but idle.
    pipeline = _Pipeline(state="ACTIVE")
    del pipeline._supervisor
    client = _client(monkeypatch, pipeline)
    assert client.get("/api/voice/state").json()["voice_state"] == "unknown"


def test_state_clamps_a_left_over_supervisor_state_to_idle(monkeypatch):
    # No session runs, so LISTENING is unearned detail whatever the supervisor
    # still holds — and it is precisely the value that used to freeze the bar.
    client = _client(
        monkeypatch,
        _Pipeline(state="IDLE", supervisor_state="LISTENING"),
    )
    assert client.get("/api/voice/state").json() == {
        "available": True,
        "state": "idle",
        "voice_state": "idle",
    }
