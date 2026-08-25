"""The folder tools as Jarvis tools: tiers, plan mode, and the executor path."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

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

    surface = {
        **ft.folder_tools(tmp_path),
        "wiki-recall": _Jarvis("wiki-recall", "safe"),
        "run-shell": _Jarvis("run-shell", "ask"),
        "open-app": _Jarvis("open-app", "monitor"),
        "no-tier": object(),
    }
    kept = ft.plan_filter(surface)  # type: ignore[arg-type]
    assert set(kept) == set(READ_ONLY_TOOLS) | {"wiki-recall"}
