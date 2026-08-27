"""The endpoint behind hiding a chat in the session list."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide.session import SessionError
from jarvis.ui.web import agentic_ide_routes as routes


def _terminal(name: str, *, archived: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        archived=archived,
        to_dict=lambda: {"name": name, "archived": archived},
    )


class FakeRegistry:
    def __init__(self, *, result=None, error: SessionError | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, bool, str | None]] = []

    async def set_terminal_archived(
        self, wanted: str, archived: bool, *, workspace_id: str | None = None
    ):
        self.calls.append((wanted, archived, workspace_id))
        if self._error is not None:
            raise self._error
        return self._result


async def test_archiving_answers_with_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    session = SimpleNamespace(id="ide_test")
    registry = FakeRegistry(result=(session, _terminal("T1", archived=True)))
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    result = await routes.archive_terminal(
        "T1", routes.ArchiveTerminalRequest(archived=True, workspace_id="ide_test")
    )

    assert registry.calls == [("T1", True, "ide_test")]
    assert result["ok"] is True
    assert result["archived"] is True
    assert result["name"] == "T1"
    assert result["workspace_id"] == "ide_test"


async def test_an_unknown_pane_is_a_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = FakeRegistry(error=SessionError("No terminal called 'T7'. Running: T1."))
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    with pytest.raises(HTTPException) as caught:
        await routes.archive_terminal("T7", routes.ArchiveTerminalRequest(archived=True))

    assert caught.value.status_code == 404
    assert "Running: T1" in caught.value.detail
