"""The pane socket must never fail silently.

A terminal that will not start shows up in the UI as a red badge whose reason
lives in a tooltip. When a whole grid of panes fails at once — a restart caught
mid-flight, a handshake the engine would not authorize — nobody is hovering,
and until these lines existed the backend wrote nothing at all: no log entry
for a refused socket, none for a refused attach. The incident could then only
be reconstructed by *absence* (a session started, no PTY ever spawned).

So the contract pinned here is diagnostic, not cosmetic: every socket that is
turned away leaves a record naming the pane and the reason.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry, SessionError
from jarvis.ui.web import agentic_ide_routes as routes
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the resume snapshot and recents out of the developer's own profile."""
    from jarvis.agentic_ide import recents, resume_store

    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(resume_store, "_store_path", lambda: store / "last.json")
    monkeypatch.setattr(recents, "remember", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _agents_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both coding CLIs count as on PATH — never ask what this machine has (AP-23)."""
    monkeypatch.setattr(session_mod, "agent_argv", lambda agent: ("/usr/bin/" + agent,))


class FakeWebSocket:
    """Enough of a Starlette WebSocket for the pane endpoint."""

    def __init__(self, **query: str) -> None:
        self.scope: dict[str, Any] = {"type": "websocket"}
        self.query_params = dict(query)
        self.accepted = False
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    instance = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(routes, "get_registry", lambda: instance)
    return instance


async def test_refused_handshake_is_recorded_not_only_closed(
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unauthorized socket names the pane in the log.

    This is the WebKit case (BUG-065): the engine withholds the session cookie
    from the handshake, so a healthy session is turned away. The client retries
    with a one-time ticket — but if that ever stops working, the only evidence
    that a pane was refused rather than broken is this line.
    """
    monkeypatch.setattr(routes, "credentials_valid", lambda scope: False)
    ws = FakeWebSocket(cols="80", rows="24")

    with caplog.at_level(logging.WARNING, logger=routes.__name__):
        await routes.agentic_pty(ws, "Mika")

    assert ws.closed == (4401, "unauthorized")
    assert any("Mika" in record.getMessage() for record in caplog.records)


async def test_attach_before_the_workspace_is_open_asks_the_pane_to_wait(
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """"Not yet" and "not here" are different answers, and this is the first.

    No workspace is open, which is exactly what a full grid of panes runs into
    for a second or two after the backend restarts. Answering 4404 here made
    every one of them give up permanently and the workspace came back frozen, so
    this case has a code of its own (4503) that means "come back". The record in
    the log still names the pane either way.
    """
    monkeypatch.setattr(routes, "credentials_valid", lambda scope: True)
    ws = FakeWebSocket(cols="80", rows="24")

    with caplog.at_level(logging.INFO, logger=routes.__name__):
        await routes.agentic_pty(ws, "Mika")

    assert ws.closed == (4503, "not ready")
    assert ws.sent and ws.sent[0]["t"] == "error"
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Mika" in logged


async def test_failed_attach_is_recorded_with_pane_and_reason(
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refused attach says which pane and why — in the log, not only on screen.

    This is the terminal failure, not the "try again" one above: a spawn that
    will not come back. It is the case where nobody is hovering over the red
    badge, because it tends to hit every pane at once.

    The code is 4500 and deliberately NOT 4404. 4404 means "this pane is not in
    the open workspace", and the client acts on exactly that: it overwrites the
    reason sent one frame earlier with its own "no longer part of the open
    workspace" line and asks the view to re-read the grid. A pane whose agent
    refused to start over a missing API key was then told to go looking for a
    missing pane — the one sentence naming the actual fix having been painted
    over by a sentence about a different problem entirely.
    """
    monkeypatch.setattr(routes, "credentials_valid", lambda scope: True)

    async def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise SessionError("the session could not be started")

    monkeypatch.setattr(registry, "attach", _refuse)
    ws = FakeWebSocket(cols="80", rows="24")

    with caplog.at_level(logging.WARNING, logger=routes.__name__):
        await routes.agentic_pty(ws, "Mika")

    assert ws.closed == (4500, "attach failed")
    assert ws.sent and ws.sent[0]["t"] == "error"
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Mika" in logged
    # The reason itself, not just the fact that something failed.
    assert "session" in logged.lower()
