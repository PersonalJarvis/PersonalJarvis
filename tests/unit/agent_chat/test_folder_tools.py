"""The folder tools as Jarvis tools: tiers, plan mode, and the executor path."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.agent_chat import folder_tools as ft
from jarvis.agent_chat.tools import READ_ONLY_TOOLS, TOOL_SPECS
from jarvis.core.protocols import ExecutionContext, ToolResult


def _ctx() -> ExecutionContext:
    return ExecutionContext(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)


def test_every_folder_tool_has_a_tier_and_reads_are_safe():
    names = {str(spec["name"]) for spec in TOOL_SPECS}
    assert set(ft.FOLDER_RISK_TIERS) == names
    for name in READ_ONLY_TOOLS:
        assert ft.FOLDER_RISK_TIERS[name] == "safe"
    for name in ("Write", "Edit", "RunCommand"):
        assert ft.FOLDER_RISK_TIERS[name] == "ask"


def test_folder_tools_are_tool_objects_scoped_to_the_folder(tmp_path: Path):
    tools = ft.folder_tools(tmp_path)
    assert set(tools) == {str(spec["name"]) for spec in TOOL_SPECS}
    read = tools["Read"]
    assert read.name == "Read" and read.risk_tier == "safe"
    assert isinstance(read.schema, dict) and read.schema.get("type") == "object"
    assert read.description
    assert read.cwd == tmp_path  # type: ignore[attr-defined]


async def test_execute_returns_a_tool_result_through_the_folder_code(tmp_path: Path):
    (tmp_path / "hello.txt").write_text("hi there\n", encoding="utf-8")
    tools = ft.folder_tools(tmp_path)
    result = await tools["Read"].execute({"file_path": "hello.txt"}, _ctx())
    assert isinstance(result, ToolResult)
    assert result.success and "hi there" in str(result.output)

    missing = await tools["Read"].execute({"file_path": "nope.txt"}, _ctx())
    assert not missing.success and missing.error

    written = await tools["Write"].execute({"file_path": "out.txt", "content": "x"}, _ctx())
    assert written.success and (tmp_path / "out.txt").read_text(encoding="utf-8") == "x"


def test_describe_args_is_the_card_summary(tmp_path: Path):
    tools = ft.folder_tools(tmp_path)
    assert tools["Write"].describe_args({"file_path": "a.txt", "content": "x"}) == {  # type: ignore[attr-defined]
        "summary": "a.txt"
    }
    assert tools["RunCommand"].describe_args({"command": "ls -la\nrm x"}) == {  # type: ignore[attr-defined]
        "summary": "ls -la"
    }
    assert tools["Ls"].describe_args({}) == {"summary": "."}  # type: ignore[attr-defined]


def test_plan_stance_offers_only_the_reading_hands(tmp_path: Path):
    assert set(ft.folder_tools(tmp_path, stance="plan")) == set(READ_ONLY_TOOLS)
    assert set(ft.folder_tools(tmp_path, stance="accept-edits")) == set(ft.FOLDER_RISK_TIERS)


def test_plan_filter_keeps_safe_tools_only(tmp_path: Path):
    class _Jarvis:
        def __init__(self, name: str, tier: str) -> None:
            self.name = name
            self.risk_tier = tier
            self.read_only = tier == "safe"

    surface = {
        **ft.folder_tools(tmp_path),
        "wiki-recall": _Jarvis("wiki-recall", "safe"),
        "run-shell": _Jarvis("run-shell", "ask"),
        "open-app": _Jarvis("open-app", "monitor"),
        "no-tier": object(),
    }
    kept = ft.plan_filter(surface)  # type: ignore[arg-type]
    assert set(kept) == set(READ_ONLY_TOOLS) | {"wiki-recall"}


@pytest.mark.parametrize("name,args", [
    ("Read", {"file_path": "hello.txt"}),
    ("Ls", {"path": "."}),
    ("Glob", {"pattern": "*.txt"}),
    ("Grep", {"pattern": "hello", "path": "."}),
    ("Write", {"file_path": "marker", "content": "x"}),
    ("Edit", {"file_path": "hello.txt", "old_string": "hello", "new_string": "changed"}),
    ("RunCommand", {"command": "echo changed > marker"}),
])
async def test_plan_discovery_matches_real_execution(tmp_path, name, args):
    from jarvis.core.bus import EventBus
    from jarvis.core.config import SafetyConfig
    from jarvis.safety.approval import ApprovalWorkflow
    from jarvis.safety.risk_tier import RiskTierEvaluator
    from jarvis.safety.tool_executor import ToolExecutor

    (tmp_path / "hello.txt").write_text("hello", encoding="utf-8")
    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
    tools = ft.folder_tools(tmp_path)
    result = await executor.execute(tools[name], args, config_snapshot={"chat_read_only": True})
    assert result.success == (name in ft.plan_filter(tools))
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / "marker").exists()


@pytest.mark.parametrize("command", [
    "echo $(touch marker)", "git config --file marker audit.plan escaped",
    "echo $(New-Item marker)", "echo innocent", "touch marker",
])
async def test_plan_shell_cannot_write_even_when_described_as_read(tmp_path, command):
    from jarvis.core.bus import EventBus
    from jarvis.core.config import SafetyConfig
    from jarvis.plugins.tool.run_shell import RunShellTool
    from jarvis.safety.approval import ApprovalWorkflow
    from jarvis.safety.risk_tier import RiskTierEvaluator
    from jarvis.safety.tool_executor import ToolExecutor

    shell = RunShellTool()
    assert shell.name not in ft.plan_filter({shell.name: shell})
    bus = EventBus()
    executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
    result = await executor.execute(shell, {"command": command, "cwd": str(tmp_path)},
                                    config_snapshot={"chat_read_only": True})
    assert result.error == "Plan mode permits reads only"
    assert not list(tmp_path.iterdir())


def test_safe_tier_or_display_text_alone_never_grants_plan_authority():
    from types import SimpleNamespace

    from jarvis.core.tool_read_only import allows_read

    tool = SimpleNamespace(risk_tier="safe", describe_args=lambda args: {"level": "read"})
    assert not allows_read(tool)
    assert not allows_read(tool, {})


def test_workspace_plan_capability_only_allows_observation():
    from jarvis.brain.workspace_tool import WorkspaceOrchestrationTool
    from jarvis.core.tool_read_only import allows_read

    tool = WorkspaceOrchestrationTool(None)
    assert allows_read(tool)
    for action in ("inspect", "resolve", "context", "observe"):
        assert allows_read(tool, {"action": action})
    for action in ("create", "send", "keys", "stop", "close", "unknown", None):
        assert not allows_read(tool, {"action": action})
