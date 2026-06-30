"""Integration tests for POST /api/sessions/{id}/open-with (open-in-editor).

Pins the security/gating contract: desktop-only (404 when disabled), the closed
opener-id set (400 on anything else, 409 when the opener isn't installed), and
that the transcript is materialized to a temp file and handed to the right
``open_path`` launcher (open_file for "default", open_file_with for an editor).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jarvis.platform.open_path as open_path_mod
import jarvis.ui.web.outputs_routes as outputs_routes
from jarvis.sessions.models import VoiceSessionRow, VoiceTurnRow
from jarvis.ui.web.sessions_routes import router as sessions_router


class _FakeStore:
    def __init__(self, session: VoiceSessionRow | None) -> None:
        self._session = session
        self._turns = [
            VoiceTurnRow(
                id="t0", session_id="sess-1", idx=0, started_ms=1_717_780_000_000,
                user_text="Hallo", jarvis_text="Hi.",
            )
        ]

    def get_session(self, sid: str) -> VoiceSessionRow | None:
        if self._session and self._session.id == sid:
            return self._session
        return None

    def get_turns(self, sid: str) -> list[VoiceTurnRow]:
        return self._turns

    def get_events(self, sid: str) -> list:
        return []


def _client(native: bool, *, session: VoiceSessionRow | None) -> TestClient:
    app = FastAPI()
    app.state.native_file_actions = native
    app.state.session_store = _FakeStore(session)
    app.include_router(sessions_router)
    return TestClient(app)


@pytest.fixture
def session() -> VoiceSessionRow:
    return VoiceSessionRow(id="sess-1", started_ms=1_717_780_000_000)


@pytest.fixture(autouse=True)
def _temp_transcripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def test_open_with_disabled_returns_404(session: VoiceSessionRow) -> None:
    client = _client(native=False, session=session)
    res = client.post(
        "/api/sessions/sess-1/open-with?format=markdown", json={"opener": "default"}
    )
    assert res.status_code == 404


def test_open_with_rejects_unknown_opener(session: VoiceSessionRow) -> None:
    client = _client(native=True, session=session)
    res = client.post(
        "/api/sessions/sess-1/open-with",
        json={"opener": "/usr/bin/evil"},
    )
    assert res.status_code == 400


def test_open_with_default_uses_open_file(
    session: VoiceSessionRow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(
        open_path_mod, "open_file", lambda p: (calls.append(p) or True)
    )
    client = _client(native=True, session=session)
    res = client.post(
        "/api/sessions/sess-1/open-with?format=markdown", json={"opener": "default"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["opened"] is True
    assert body["opener"] == "default"
    # Transcript was materialized under the temp dir and handed to open_file.
    assert len(calls) == 1
    assert calls[0].parent == tmp_path / "jarvis-transcripts"
    assert calls[0].exists()
    assert calls[0].suffix == ".md"


def test_open_with_editor_uses_open_file_with(
    session: VoiceSessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        outputs_routes, "_resolve_opener", lambda oid: ("executable", "/usr/bin/code")
    )
    captured: dict = {}
    monkeypatch.setattr(
        open_path_mod,
        "open_file_with",
        lambda p, kind, value: (
            captured.update({"path": p, "kind": kind, "value": value}) or True
        ),
    )
    client = _client(native=True, session=session)
    res = client.post(
        "/api/sessions/sess-1/open-with?format=plain", json={"opener": "code"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["opened"] is True
    assert captured["kind"] == "executable"
    assert captured["value"] == "/usr/bin/code"
    assert captured["path"].suffix == ".txt"


def test_open_with_unavailable_opener_returns_409(
    session: VoiceSessionRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Known id, but not installed on this host → resolver yields None.
    monkeypatch.setattr(outputs_routes, "_resolve_opener", lambda oid: None)
    client = _client(native=True, session=session)
    res = client.post(
        "/api/sessions/sess-1/open-with", json={"opener": "cursor"}
    )
    assert res.status_code == 409
