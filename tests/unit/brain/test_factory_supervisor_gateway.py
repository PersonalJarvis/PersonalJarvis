"""Factory registration guards for the shared supervisor-tool gateway."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.factory import _register_runtime_manager
from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.core.protocols import ToolResult


class _Tool:
    name = "health"
    description = "Read the current health status."
    schema = {"type": "object", "properties": {}}
    risk_tier = "safe"

    async def execute(self, _arguments, _context):
        return ToolResult(success=True, output="ok")


@pytest.fixture(autouse=True)
def _clean_runtime_refs():
    runtime_refs._reset_for_tests()
    yield
    runtime_refs._reset_for_tests()


def test_factory_registration_publishes_manager_and_public_gateway() -> None:
    manager = SimpleNamespace(
        _tools={"health": _Tool()},
        _tool_executor=SimpleNamespace(),
    )

    _register_runtime_manager(manager)

    assert runtime_refs.get_brain_manager() is manager
    gateway = runtime_refs.get_supervisor_tool_gateway()
    assert isinstance(gateway, BrainSupervisorToolGateway)
    assert [descriptor.name for descriptor in gateway.catalog()] == ["health"]


def test_catalog_carries_the_per_args_risk_tier_hook() -> None:
    class _Music:
        name = "youtube_music"
        description = "Play and read YouTube Music."
        schema = {"type": "object", "properties": {}}
        risk_tier = "monitor"

        async def execute(self, _arguments, _context):
            return ToolResult(success=True, output="ok")

        def risk_tier_for_args(self, args):
            action = str((args or {}).get("action") or "now_playing")
            return "safe" if action == "now_playing" else "monitor"

    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools={"youtube_music": _Music()}, _tool_executor=None)
    )
    descriptor = gateway.catalog()[0]
    assert descriptor.risk_tier == "monitor"
    assert descriptor.risk_tier_for_args({"action": "now_playing"}) == "safe"
    assert descriptor.risk_tier_for_args({"action": "play"}) == "monitor"


def test_catalog_carries_the_per_args_impact_hook() -> None:
    """Without this the realtime shape guard cannot tell a read from a write.

    The guard's unit tests build descriptors by hand; only this one proves the
    hook survives the trip through the live catalog, which is where the
    2026-08-20 15:35 refusal happened.
    """
    from jarvis.plugins.tool.run_shell import RunShellTool

    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools={"run_shell": RunShellTool()}, _tool_executor=None)
    )
    descriptor = gateway.catalog()[0]
    assert descriptor.name == "run_shell"
    assert descriptor.describe_args({"command": "systeminfo"})["level"] == "read"
    assert (
        descriptor.describe_args({"command": "Remove-Item C:/x -Recurse"})["level"]
        == "destructive"
    )
