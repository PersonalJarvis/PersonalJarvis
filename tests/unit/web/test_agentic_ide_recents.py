"""Recent folders belong to explicit Agentic-IDE open actions only."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.ui.web import agentic_ide_routes as routes


async def test_open_route_remembers_the_user_selected_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SimpleNamespace(
        folder=str(tmp_path),
        terminals=[
            SimpleNamespace(agent="codex"),
            SimpleNamespace(agent="codex"),
            SimpleNamespace(agent="claude"),
        ],
        to_dict=lambda: {"folder": str(tmp_path)},
    )

    class FakeRegistry:
        async def start(self, folder: str, terminals: list[dict]) -> object:
            assert folder == str(tmp_path)
            assert len(terminals) == 3
            return session

        def state(self) -> dict:
            # Opening answers with the workspace bar too, so the client that
            # just added a workspace does not need a second round-trip to
            # discover it is there.
            return {"active": True, "workspaces": [], "active_id": "ide_fake"}

    remembered: list[tuple[str, int, dict[str, int]]] = []
    monkeypatch.setattr(routes, "get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(
        routes.recents,
        "remember",
        lambda path, *, terminals, agents: remembered.append((path, terminals, agents)),
    )

    # ``app.state.bus`` is all the route wants from the Request: opening a
    # workspace announces itself so other windows redraw. No bus, no
    # announcement — this test is about the recent-folder history.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=None)))

    response = await routes.start_session(
        request,
        routes.StartSessionRequest(
            folder=str(tmp_path),
            terminals=[
                routes.TerminalRequest(agent="codex"),
                routes.TerminalRequest(agent="codex"),
                routes.TerminalRequest(agent="claude"),
            ],
        ),
    )

    assert response["ok"] is True
    assert remembered == [(str(tmp_path), 3, {"codex": 2, "claude": 1})]
