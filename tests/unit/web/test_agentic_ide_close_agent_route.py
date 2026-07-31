from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.ui.web import agentic_ide_routes as routes


class _Bus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_close_by_agent_returns_state_and_refreshes_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(id="workspace", folder="/project")
    registry = SimpleNamespace(
        session=session,
        state=lambda: {"active": True},
    )
    closed = [SimpleNamespace(name="Cody"), SimpleNamespace(name="Cole")]

    async def _close(_registry: object, agent: str) -> list[object]:
        assert _registry is registry
        assert agent == "codex"
        return closed

    bus = _Bus()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bus=bus)))
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    monkeypatch.setattr(routes, "close_agent_terminals", _close)

    result = await routes.close_terminals_by_agent(request, "CoDeX")

    assert result == {
        "ok": True,
        "agent": "codex",
        "closed": ["Cody", "Cole"],
        "state": {"active": True},
    }
    assert len(bus.events) == 1
    assert type(bus.events[0]).__name__ == "AgenticIdeTerminalsClosed"


def test_close_by_agent_route_is_cli_visible_and_dangerous() -> None:
    route = next(
        route
        for route in routes.router.routes
        if getattr(route, "path", "") == "/api/agentic-ide/terminals/agent/{agent}"
    )
    assert route.methods == {"DELETE"}
    assert route.openapi_extra == {"x-jarvis-dangerous": True}
