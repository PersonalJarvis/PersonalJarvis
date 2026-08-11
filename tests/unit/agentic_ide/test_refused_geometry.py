"""A viewer must learn the size its agent is REALLY in, or the pane corrupts.

A resize is a request, not an instruction. ``Registry.resize`` turns one down
when the tile is under the viewer floor, ignores one from a viewer that no
longer holds the pane, and lifts a pane stuck below the floor back onto it. The
pane meanwhile reflowed its own xterm the moment it measured the tile — so a
refusal used to leave the two grids permanently disagreeing.

That is not a cosmetic drift. A TUI addresses rows by RELATIVE cursor moves, so
once the two grids disagree the agent's next repaint finishes into rows holding
other text and the screens interleave character by character. Reported on macOS
2026-08-11 as "the content is mirrored": it reads as a renderer fault and is
really two different screens in one grid. A Windows window wide enough that no
resize is ever refused never shows it, which is what made it look platform-
specific.

So: the server reports the granted geometry whenever it differs from the
requested one, and stays silent when the request was honoured — the normal case,
and the reason a dragged seam adds no chatter to the wire.

Driven through ``client.portal`` for the reason spelled out in
``test_stale_workspace_socket``: the registry's locks and the PTY callbacks live
on the SERVER loop.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import (
    MIN_VIEWER_COLS,
    MIN_VIEWER_ROWS,
    Registry,
)
from tests.fakes.fake_pty_manager import FakePtyManager


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
    monkeypatch.setattr(agentic_ide_routes, "credentials_valid", lambda _scope: True)
    return reg


@pytest.fixture
def client(registry: Registry) -> Iterator[TestClient]:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as ready:
        yield ready


def _open(client: TestClient, registry: Registry, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    client.portal.call(registry.start, str(folder), [{"agent": "claude"}])


def _next_size(socket, limit: int = 12) -> dict | None:
    """The next ``size`` frame, or ``None`` if the pane says other things first.

    Output and state frames share this socket, so a size report is looked for
    among them rather than assumed to be next in line.
    """
    for _ in range(limit):
        frame = socket.receive_json()
        if frame.get("t") == "size":
            return frame
    return None


def test_a_refused_resize_tells_the_viewer_what_the_agent_really_got(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """The failure this exists for: a tile under the floor is turned down."""
    _open(client, registry, tmp_path / "ws")

    with client.websocket_connect("/api/agentic-ide/pty/T1?cols=120&rows=40") as socket:
        assert socket.receive_json()["t"] == "ready"
        # Under MIN_VIEWER_COLS: the PTY keeps the geometry it is working in,
        # and the pane has already reflowed itself to the narrow tile.
        socket.send_json({"t": "r", "cols": 4, "rows": 2})
        reported = _next_size(socket)

    assert reported is not None, (
        "a refused resize left the viewer believing it had been granted — that "
        "is the disagreement that interleaves two screens in one grid"
    )
    assert (reported["cols"], reported["rows"]) == (120, 40)


def test_a_granted_resize_says_nothing(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """Silence is the contract for the normal path, not an accident.

    Proven without waiting on a frame that must never come: a granted resize is
    followed by a refused one, and the first size report to arrive describes the
    REFUSED size. Had the granted resize also reported, that frame would be
    sitting in front of it.
    """
    _open(client, registry, tmp_path / "ws")

    with client.websocket_connect("/api/agentic-ide/pty/T1?cols=120&rows=40") as socket:
        assert socket.receive_json()["t"] == "ready"
        socket.send_json({"t": "r", "cols": 90, "rows": 30})
        # Small but POSITIVE: `_safe_int` reads anything <= 0 as "unset" and
        # substitutes the handshake size, so a literal zero never reaches the
        # registry over this wire and would be granted rather than refused.
        socket.send_json({"t": "r", "cols": 4, "rows": 2})
        reported = _next_size(socket)

    assert reported is not None
    assert (reported["cols"], reported["rows"]) == (90, 30), (
        "the granted 90x30 announced itself; only disagreements belong on the wire"
    )


def test_a_pane_lifted_off_the_floor_reports_the_size_it_was_lifted_to(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """A clamp is a disagreement too — the viewer asked for less than it got."""
    _open(client, registry, tmp_path / "ws")
    assert registry.session is not None
    term = registry.session.terminals[0]

    with client.websocket_connect("/api/agentic-ide/pty/T1?cols=120&rows=40") as socket:
        assert socket.receive_json()["t"] == "ready"
        # However it got there (an older client, a session predating the guard),
        # this pane's agent is in a terminal it cannot draw in.
        term.pty_cols, term.pty_rows = 3, 2
        socket.send_json({"t": "r", "cols": 4, "rows": 2})
        reported = _next_size(socket)

    assert reported is not None, "a lifted pane must say where it was lifted to"
    assert (reported["cols"], reported["rows"]) == (MIN_VIEWER_COLS, MIN_VIEWER_ROWS)
