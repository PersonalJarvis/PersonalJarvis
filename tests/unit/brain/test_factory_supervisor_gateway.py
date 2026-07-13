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
