"""A pane socket must never be answered by a workspace it did not ask for.

Call-signs are positional: every workspace numbers its panes T1, T2, T3. The
same name is therefore a different terminal — different folder, different
agent, other work — in every open tab. A socket still carrying the id of a
workspace that has since been closed cannot be served by "whatever is at the
front now": it would attach to a stranger's pane, and because attaching makes
you that pane's owner it would take the pane away from the window the user is
actually looking at.

That is not theory. Measured 2026-07-28: an app restart left a page open whose
grid belonged to a six-pane workspace, a fresh four-pane workspace was opened,
and the leftover page's sockets bound to it one by one. Prompts typed into T2
and T3 by voice went to a screen nobody was watching, the visible panes sat
unchanged, and the only cure was reloading the page.

The answer is a verdict of its own — "that workspace is closed" (4409) — which
tells the client to re-read the state rather than retry or wait.

Written synchronously and driven through ``client.portal``: the registry's
per-pane locks and the app's PTY callbacks live on the SERVER loop, and setting
a workspace up on the test's own loop would leave them attached to a loop the
route never runs on (the same trap documented in ``test_wiki_ws``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry
from tests.fakes.fake_pty_manager import FakePtyManager

CLOSE_STALE_WORKSPACE = 4409
CLOSE_NOT_READY = 4503


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    from jarvis.ui.web import agentic_ide_routes

    monkeypatch.setattr(agentic_ide_routes, "get_registry", lambda: reg)
    # The handshake credential is a different question with its own tests; here
    # every socket is authorized so the workspace decision is the only thing
    # under test.
    monkeypatch.setattr(agentic_ide_routes, "credentials_valid", lambda _scope: True)
    return reg


@pytest.fixture
def client(registry: Registry) -> Iterator[TestClient]:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as ready:
        yield ready


def _open(client: TestClient, registry: Registry, folder: Path, count: int = 2) -> str:
    """Open a workspace ON THE SERVER LOOP and return its id."""
    folder.mkdir(parents=True, exist_ok=True)
    session = client.portal.call(
        registry.start, str(folder), [{"agent": "claude"} for _ in range(count)]
    )
    return session.id


def _refusal(client: TestClient, url: str) -> tuple[int, list[dict]]:
    """Connect, read whatever the server says, and return (close code, frames).

    The handshake is ACCEPTED before a pane is refused — the reason travels as a
    frame first, so that a client can show a sentence rather than a bare number
    — which means the refusal only surfaces on the next read.
    """
    frames: list[dict] = []
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(url) as socket:
            while True:
                frames.append(socket.receive_json())
    return closed.value.code, frames


def test_a_pane_of_a_closed_workspace_never_lands_in_the_open_one(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    gone = _open(client, registry, tmp_path / "old")
    client.portal.call(registry.end, gone)
    _open(client, registry, tmp_path / "new")

    code, frames = _refusal(client, f"/api/agentic-ide/pty/T1?workspace={gone}")

    assert code == CLOSE_STALE_WORKSPACE, (
        "a pane whose workspace is closed was served by the workspace at the "
        "front — that is how a leftover window steals a live pane"
    )
    assert frames and frames[0]["t"] == "error", "the refusal has to say why"
    # And the pane it reached for is untouched, which is the point of refusing:
    # nothing was started, and nobody's screen changed hands.
    assert registry.session is not None
    assert registry.session.terminals[0].viewer_output is None


def test_a_pane_is_still_told_to_wait_while_nothing_is_open(
    client: TestClient, registry: Registry
) -> None:
    """The restart case keeps its patient answer (BUG-113).

    With no workspace open at all, an unknown id means "not yet" — the app is
    still coming up — and the pane must keep waiting rather than give up.
    """
    code, _frames = _refusal(client, "/api/agentic-ide/pty/T1?workspace=ide_longgone")

    assert code == CLOSE_NOT_READY


def test_a_client_that_names_no_workspace_still_gets_the_front_one(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """The single-workspace case, and every older client, must keep working."""
    _open(client, registry, tmp_path / "only", count=1)

    with client.websocket_connect("/api/agentic-ide/pty/T1") as socket:
        ready = socket.receive_json()

    assert ready["t"] == "ready"
    assert ready["name"] == "T1"
