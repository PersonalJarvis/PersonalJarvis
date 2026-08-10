"""The batch endpoint behind "open five more terminals".

Two things it must never do: report a full success when the pane cap cut
the request short, and leave connected clients staring at a stale grid. The
second one is why the route publishes an event at all — the workspace view
fetches its state once when it mounts, so panes it did not create itself are
invisible to it until something tells it to look again.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide.session import SessionError
from jarvis.ui.web import agentic_ide_routes as routes


class FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


def _request(bus: object | None) -> SimpleNamespace:
    """The one thing the route needs from a Request: ``app.state.bus``."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=bus)))


def _terminal(name: str, agent: str = "claude") -> SimpleNamespace:
    return SimpleNamespace(
        name=name, agent=agent, to_dict=lambda: {"name": name, "agent": agent}
    )


class FakeRegistry:
    """Registry stand-in: records the call, returns a scripted outcome."""

    def __init__(self, created: list[object], capped: bool, folder: Path) -> None:
        self._created = created
        self._capped = capped
        self.calls: list[tuple[int, str | None, str | None]] = []
        self.session = SimpleNamespace(id="ide_test", folder=str(folder))

    async def add_terminals(
        self, count: int, *, agent: str | None = None, account: str | None = None
    ):
        self.calls.append((count, agent, account))
        return self._created, self._capped

    def state(self) -> dict:
        return {"active": True}


async def test_a_batch_opens_the_panes_and_tells_the_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = [_terminal("Juno"), _terminal("Milo")]
    registry = FakeRegistry(created, capped=False, folder=tmp_path)
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    bus = FakeBus()

    result = await routes.add_terminals(
        _request(bus), routes.AddTerminalsRequest(count=2, agent="claude")
    )

    assert result["ok"] is True
    assert result["requested"] == 2
    assert result["capped"] is False
    assert [t["name"] for t in result["terminals"]] == ["Juno", "Milo"]
    # No account named — the registry then opens every pane of the batch on the
    # workspace's active subscription (see Registry.add_terminal).
    assert registry.calls == [(2, "claude", None)]

    # Exactly one event for the batch, naming every pane — five panes must not
    # make the open view refetch five times.
    assert len(bus.published) == 1
    event = bus.published[0]
    assert event.names == ("Juno", "Milo")
    assert event.folder == str(tmp_path)


async def test_the_cap_is_reported_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five asked for, one opened: ``capped`` is what keeps the reply honest."""
    registry = FakeRegistry([_terminal("Zara")], capped=True, folder=tmp_path)
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    result = await routes.add_terminals(
        _request(FakeBus()), routes.AddTerminalsRequest(count=5)
    )

    assert result["capped"] is True
    assert result["requested"] == 5
    assert len(result["terminals"]) == 1


async def test_a_refusal_becomes_a_conflict_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full workspace / missing CLI answers 409 carrying the sentence to say."""

    class Refusing:
        session = None

        async def add_terminals(
            self, count: int, *, agent: str | None = None, account: str | None = None
        ):
            raise SessionError("This workspace already has the maximum of 12 terminals.")

        def state(self) -> dict:
            return {}

    monkeypatch.setattr(routes, "get_registry", lambda: Refusing())

    with pytest.raises(routes.HTTPException) as caught:
        await routes.add_terminals(
            _request(None), routes.AddTerminalsRequest(count=3)
        )
    assert caught.value.status_code == 409
    assert "maximum" in caught.value.detail


async def test_no_bus_still_opens_the_panes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless / test hosts have no bus attached; the work must happen anyway."""
    registry = FakeRegistry([_terminal("Juno")], capped=False, folder=tmp_path)
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    result = await routes.add_terminals(
        _request(None), routes.AddTerminalsRequest(count=1)
    )
    assert result["ok"] is True
    assert len(result["terminals"]) == 1


class FakeClosingRegistry:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.session = SimpleNamespace(id="ide_test", folder="ws-folder")

    async def close_terminals(self, names: list[str]):
        self.calls.append(names)
        return (
            [_terminal("Juno")],
            [{"name": "Missing", "detail": "No terminal called 'Missing'."}],
        )

    def state(self) -> dict:
        return {"active": True, "session": {"terminals": [{"name": "Milo"}]}}


async def test_close_batch_returns_closed_failed_and_canonical_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = FakeClosingRegistry()
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    bus = FakeBus()

    result = await routes.close_terminals(
        _request(bus), routes.CloseTerminalsRequest(names=["Juno", "Missing"])
    )

    assert registry.calls == [["Juno", "Missing"]]
    assert result == {
        "ok": False,
        "closed": ["Juno"],
        "failed": [{"name": "Missing", "detail": "No terminal called 'Missing'."}],
        "state": {"active": True, "session": {"terminals": [{"name": "Milo"}]}},
    }
    # A close made by voice or the CLI must reach every OTHER open view — the
    # grid refetches on this event, exactly like the add-terminals batch above.
    assert len(bus.published) == 1
    assert type(bus.published[0]).__name__ == "AgenticIdeTerminalsClosed"
    assert bus.published[0].names == ("Juno",)


async def test_close_batch_without_a_bus_still_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = FakeClosingRegistry()
    monkeypatch.setattr(routes, "get_registry", lambda: registry)

    result = await routes.close_terminals(
        _request(None), routes.CloseTerminalsRequest(names=["Juno", "Missing"])
    )
    assert result["closed"] == ["Juno"]


def test_close_batch_route_is_marked_dangerous() -> None:
    route = next(
        route
        for route in routes.router.routes
        if getattr(route, "path", "") == "/api/agentic-ide/terminals/close-batch"
    )
    assert route.openapi_extra == {"x-jarvis-dangerous": True}
