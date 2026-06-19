"""Unit tests: BrainToolsChanged is published by mcp_routes endpoints.

Validates Workstream 2 of the MCP-tools-in-brain fix: every state-changing
endpoint dispatches BrainToolsChanged so the live BrainManager reloads its
tool set without a restart.

Only the ``enable_mcp`` endpoint (already-active fast path) is exercised here
because it is the simplest to fake: no ``start_enabled`` call is needed, and
the happy-path ``_publish_brain_tools_changed`` call is unambiguous.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch  # noqa: F401 — used below

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeBus:
    """Minimal EventBus stand-in that records published events."""

    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:  # noqa: D401
        self.published.append(event)


def _make_request(name: str, bus: _FakeBus) -> SimpleNamespace:
    """Build a minimal fake FastAPI Request sufficient for enable_mcp."""
    # registry: has the spec + reports the server as already active
    fake_spec = SimpleNamespace(name=name)
    fake_client = SimpleNamespace(_tools_cache=[], is_healthy=True)

    fake_registry = SimpleNamespace(
        get_spec=lambda n: fake_spec if n == name else None,
        active_clients=lambda: {name: fake_client},
        last_error=lambda n: None,
    )

    app_state = SimpleNamespace(
        mcp_registry=fake_registry,
        tool_registry={},
        bus=bus,
        cfg=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=app_state))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_enable_mcp_publishes_brain_tools_changed() -> None:
    """enable_mcp (already-active path) must publish BrainToolsChanged."""
    # Import here so structlog / other heavy deps are never touched at
    # collection time; the module is self-contained enough to import cleanly.
    from jarvis.core.events import BrainToolsChanged
    from jarvis.ui.web import mcp_routes

    bus = _FakeBus()
    request = _make_request("test-server", bus)

    # Patch mcp_state.enable so it doesn't touch disk, and _sync_tools_for_server
    # so it doesn't call client.list_tools() (adapter import chain).
    with (
        patch.object(mcp_routes.mcp_state, "enable", return_value=None),
        patch.object(
            mcp_routes,
            "_sync_tools_for_server",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await mcp_routes.enable_mcp("test-server", request)

    assert result["ok"] is True
    assert result["enabled"] is True

    # Exactly one BrainToolsChanged must have been published.
    changed = [e for e in bus.published if isinstance(e, BrainToolsChanged)]
    assert len(changed) == 1, f"Expected 1 BrainToolsChanged, got {len(changed)}"
    assert changed[0].reason.startswith("mcp_enabled:"), (
        f"Unexpected reason: {changed[0].reason!r}"
    )
    assert "test-server" in changed[0].reason


async def test_enable_mcp_no_bus_does_not_raise() -> None:
    """_publish_brain_tools_changed must be a no-op when bus is absent."""
    from jarvis.ui.web import mcp_routes

    # No bus on app.state
    fake_spec = SimpleNamespace(name="x")
    fake_client = SimpleNamespace(_tools_cache=[], is_healthy=True)
    fake_registry = SimpleNamespace(
        get_spec=lambda n: fake_spec,
        active_clients=lambda: {"x": fake_client},
        last_error=lambda n: None,
    )
    app_state = SimpleNamespace(
        mcp_registry=fake_registry,
        tool_registry={},
        cfg=None,
        # deliberately no ``bus`` attribute
    )
    request = SimpleNamespace(app=SimpleNamespace(state=app_state))

    with (
        patch.object(mcp_routes.mcp_state, "enable", return_value=None),
        patch.object(
            mcp_routes,
            "_sync_tools_for_server",
            new=AsyncMock(return_value=None),
        ),
    ):
        # Must not raise even though bus is missing
        result = await mcp_routes.enable_mcp("x", request)

    assert result["ok"] is True
