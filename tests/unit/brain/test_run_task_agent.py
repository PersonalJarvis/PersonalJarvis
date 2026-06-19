"""BrainManager.run_task — the isolated agentic turn used by scheduled Tasks.

A scheduled "agent" task runs ONE brain turn restricted to a per-task tool
allowlist. Two invariants matter and are easy to get wrong:
  1. Only the allowlisted tools are visible to that turn (a task must not be
     able to reach tools it wasn't granted).
  2. The turn is isolated — it must NOT pollute the live voice session's
     history (`_history`) or sticky model level.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ToolResult


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.schema: dict[str, Any] = {}


class _NullExecutor:
    async def execute(self, *a: Any, **kw: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")


def _manager(tools: dict[str, Any] | None = None) -> BrainManager:
    return BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools=tools or {},
        tool_executor=_NullExecutor(),  # type: ignore[arg-type]
    )


def test_select_task_tools_filters_to_allowlist() -> None:
    mgr = _manager({
        "gmail": _FakeTool("gmail"),
        "github": _FakeTool("github"),
        "search_web": _FakeTool("search_web"),
    })
    sel = mgr._select_task_tools(("gmail", "search_web"))
    assert set(sel.keys()) == {"gmail", "search_web"}


def test_select_task_tools_skips_unknown_grants() -> None:
    mgr = _manager({"gmail": _FakeTool("gmail")})
    sel = mgr._select_task_tools(("gmail", "not-connected-plugin"))
    assert set(sel.keys()) == {"gmail"}


def test_select_task_tools_empty_allowlist_yields_no_tools() -> None:
    mgr = _manager({"gmail": _FakeTool("gmail")})
    assert mgr._select_task_tools(()) == {}


async def test_run_task_filters_tools_and_isolates_history(monkeypatch) -> None:
    mgr = _manager({"gmail": _FakeTool("gmail"), "github": _FakeTool("github")})
    captured: dict[str, Any] = {}

    class _FakeDispatcher:
        async def dispatch(self, text: str, **kw: Any) -> Any:
            captured["text"] = text
            captured["history"] = kw.get("history")
            return SimpleNamespace(text="briefing result")

    def _fake_build(brain: Any, *, tools_override: dict[str, Any] | None = None) -> Any:
        captured["tools"] = set((tools_override or {}).keys())
        return _FakeDispatcher()

    monkeypatch.setattr(mgr, "_get_brain", lambda *a, **k: object())
    monkeypatch.setattr(mgr, "_build_dispatcher", _fake_build)

    out = await mgr.run_task(
        prompt="Make my briefing", allowed_tools=("gmail",), model_tier="fast",
    )

    assert out == "briefing result"
    assert captured["tools"] == {"gmail"}      # only the granted tool
    assert captured["text"] == "Make my briefing"
    assert captured["history"] == []           # isolated — no voice history
    assert len(mgr._history) == 0              # voice session untouched


async def test_run_task_passes_trace_id_through(monkeypatch) -> None:
    """The caller-supplied trace_id must reach dispatch so an auto-approver
    armed on that id can match the turn's ActionProposed events."""
    from uuid import uuid4

    mgr = _manager({"gmail": _FakeTool("gmail")})
    captured: dict[str, Any] = {}

    class _FakeDispatcher:
        async def dispatch(self, text: str, **kw: Any) -> Any:
            captured["trace_id"] = kw.get("trace_id")
            return SimpleNamespace(text="x")

    monkeypatch.setattr(mgr, "_get_brain", lambda *a, **k: object())
    monkeypatch.setattr(mgr, "_build_dispatcher", lambda *a, **k: _FakeDispatcher())

    tid = uuid4()
    await mgr.run_task(prompt="p", allowed_tools=(), model_tier="fast", trace_id=tid)
    assert captured["trace_id"] == tid


async def test_run_task_deep_tier_requests_deep_model(monkeypatch) -> None:
    mgr = _manager({"gmail": _FakeTool("gmail")})
    asked: dict[str, Any] = {}

    def _spy_get_brain(name: str, model: str | None = None) -> Any:
        asked["model"] = model
        return object()

    class _FakeDispatcher:
        async def dispatch(self, text: str, **kw: Any) -> Any:
            return SimpleNamespace(text="x")

    monkeypatch.setattr(mgr, "_get_brain", _spy_get_brain)
    monkeypatch.setattr(mgr, "_build_dispatcher", lambda *a, **k: _FakeDispatcher())

    await mgr.run_task(prompt="p", allowed_tools=(), model_tier="deep")
    # deep tier must resolve to the deep model of the active provider
    assert asked["model"] == mgr._deep_model(mgr._active_name)
