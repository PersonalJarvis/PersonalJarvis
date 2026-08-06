"""The endpoint behind renaming a pane.

The route itself is thin — the rules live in the registry — so what is pinned
here is the part a caller depends on: the answer says which pane was renamed
and what it used to be called, and the two ways it can fail are told apart by
status code. A duplicate call-sign is something the caller fixes by typing a
different name (409); a call-sign nobody answers to is a different mistake
entirely (404), and a UI that cannot distinguish them can only say "it didn't
work".
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide.session import SessionError
from jarvis.ui.web import agentic_ide_routes as routes


def _terminal(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, to_dict=lambda: {"name": name, "key": "t1"})


class FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


def _request(bus: object | None) -> SimpleNamespace:
    """The one thing the route needs from a Request: ``app.state.bus``."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=bus)))


class FakeRegistry:
    """Registry stand-in: records the call, returns or raises what was scripted."""

    def __init__(self, *, result=None, error: SessionError | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, str]] = []

    async def rename_terminal(self, wanted: str, name: str):
        self.calls.append((wanted, name))
        if self._error is not None:
            raise self._error
        return self._result

    def state(self) -> dict:
        return {"active": True}


async def test_a_rename_answers_with_both_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both, because the caller has UI filed under the OLD one to move across."""
    session = SimpleNamespace(id="ide_test", folder="ws-folder", name="alpha")
    registry = FakeRegistry(result=(session, _terminal("Frontend")))
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    bus = FakeBus()

    result = await routes.rename_terminal(
        _request(bus), "T1", routes.RenameTerminalRequest(name="Frontend")
    )

    assert registry.calls == [("T1", "Frontend")]
    # Every OTHER open view learns of the new call-sign through this trigger —
    # without it a CLI/voice rename left the grid addressing a dead name.
    assert len(bus.published) == 1
    assert type(bus.published[0]).__name__ == "AgenticIdeWorkspaceChanged"
    assert bus.published[0].reason == "renamed"
    assert result["ok"] is True
    assert result["renamed"] == "Frontend"
    assert result["previous"] == "T1"
    assert result["workspace_id"] == "ide_test"
    assert result["terminal"]["name"] == "Frontend"
    # The whole state travels back, so the grid redraws from this one answer
    # instead of polling for the change it just made.
    assert result["state"] == {"active": True}


async def test_a_name_another_pane_carries_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = FakeRegistry(
        error=SessionError("Another terminal in this workspace is already called 'Api'.")
    )
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    with pytest.raises(HTTPException) as caught:
        await routes.rename_terminal(
            _request(None), "T2", routes.RenameTerminalRequest(name="Api")
        )

    assert caught.value.status_code == 409
    assert "already called" in caught.value.detail


async def test_an_unknown_pane_is_a_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = FakeRegistry(error=SessionError("No terminal called 'T7'. Running: T1."))
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    with pytest.raises(HTTPException) as caught:
        await routes.rename_terminal(
            _request(None), "T7", routes.RenameTerminalRequest(name="Api")
        )

    assert caught.value.status_code == 404
    # The panes that DO exist travel with it — the answer to the question asked.
    assert "Running: T1" in caught.value.detail


async def test_an_invalid_call_sign_is_unprocessable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = FakeRegistry(
        error=SessionError("Give the terminal a name with letters or numbers in it.")
    )
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    with pytest.raises(HTTPException) as caught:
        await routes.rename_terminal(
            _request(None), "T1", routes.RenameTerminalRequest(name="---")
        )

    assert caught.value.status_code == 422
