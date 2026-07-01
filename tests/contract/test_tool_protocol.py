"""Contract tests: all 5 Phase-2 tools structurally satisfy the tool protocol."""
from __future__ import annotations

import inspect
from importlib.metadata import entry_points

import pytest

PHASE2_TOOLS = ["open-app", "type-text", "run-shell", "search-web", "remember"]


def _load_tools():
    tools = {}
    for ep in entry_points(group="jarvis.tool"):
        if ep.name in PHASE2_TOOLS:
            tools[ep.name] = ep.load()
    return tools


@pytest.fixture(scope="module")
def tool_classes():
    return _load_tools()


def test_all_phase2_tools_loaded(tool_classes):
    missing = set(PHASE2_TOOLS) - set(tool_classes.keys())
    assert not missing, f"Tools fehlen: {missing}"


@pytest.mark.parametrize("ep_name", PHASE2_TOOLS)
def test_tool_has_required_attributes(tool_classes, ep_name):
    cls = tool_classes[ep_name]
    inst = cls()
    assert isinstance(inst.name, str) and inst.name
    assert isinstance(inst.schema, dict)
    assert inst.schema.get("type") == "object"
    assert isinstance(inst.description, str)
    assert inst.risk_tier in ("safe", "monitor", "ask", "block")
    assert hasattr(inst, "execute")
    assert inspect.iscoroutinefunction(inst.execute)


@pytest.mark.parametrize("ep_name,expected_tier", [
    ("open-app", "monitor"),
    ("type-text", "safe"),
    ("run-shell", "monitor"),
    ("search-web", "safe"),
    ("remember", "safe"),
])
def test_risk_tier_matches_plan(tool_classes, ep_name, expected_tier):
    inst = tool_classes[ep_name]()
    assert inst.risk_tier == expected_tier
