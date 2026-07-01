"""Action registry: lookup, validation, arg transforms, composite runners.

Pins the **vocabulary** that the LLM system prompt names and that the
Computer-Use loop can execute. If the brain and the loop drift apart here,
the brain plans actions the executor doesn't know — and the
user hears "unknown action".

These tests make sure that:
  1. All actions the user explicitly required are registered.
  2. ActionSpec validation catches inconsistencies (both tool AND
     composite, or neither).
  3. Argument transformers produce the correct tool args.
  4. Composite runners execute all required tools correctly.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.protocols import ToolResult
from jarvis.harness.action_registry import (
    DEFAULT_REGISTRY,
    ActionRegistry,
    ActionSpec,
    _composite_open_new_tab,
    _composite_run_terminal_command,
    _transform_open_terminal,
    _transform_press_key,
    _transform_press_shortcut,
    build_default_registry,
)


# ---------------------------------------------------------------------
# Vocabulary coverage
# ---------------------------------------------------------------------

# Actions the user explicitly required in their architecture brief.
# If this test fails, the system vocabulary is incomplete against the
# user contract — please add the action instead of deleting the test.
REQUIRED_ACTIONS: frozenset[str] = frozenset({
    "open_terminal",
    "open_new_tab",
    "type_text",
    "press_key",
    "press_shortcut",
    "click",
    "move_mouse",
    "switch_window",
    "wait_for_ui_state",
    "read_visible_ui_state",
    "run_terminal_command_through_ui",
})


def test_default_registry_covers_user_required_actions() -> None:
    registered = set(DEFAULT_REGISTRY.names())
    missing = REQUIRED_ACTIONS - registered
    assert not missing, f"Missing actions in the default registry: {missing!r}"


def test_describe_for_prompt_lists_every_action() -> None:
    desc = DEFAULT_REGISTRY.describe_for_prompt()
    for name in DEFAULT_REGISTRY.names():
        assert name in desc, f"Action {name!r} is missing from the prompt description text"


# ---------------------------------------------------------------------
# ActionSpec.validate
# ---------------------------------------------------------------------

def test_actionspec_rejects_both_tool_and_composite() -> None:
    spec = ActionSpec(
        name="x", tool_name="t", description="",
        composite=lambda *a, **kw: None,
    )
    with pytest.raises(ValueError, match="entweder tool_name ODER composite"):
        spec.validate()


def test_actionspec_rejects_neither_tool_nor_composite() -> None:
    spec = ActionSpec(name="x", tool_name=None, description="")
    with pytest.raises(ValueError, match="entweder tool_name ODER composite"):
        spec.validate()


def test_registry_register_runs_validation() -> None:
    reg = ActionRegistry()
    bad = ActionSpec(name="bad", tool_name=None, description="")
    with pytest.raises(ValueError):
        reg.register(bad)


def test_registry_lookup_returns_none_for_unknown() -> None:
    assert DEFAULT_REGISTRY.get("totally-not-an-action") is None
    assert not DEFAULT_REGISTRY.has("totally-not-an-action")


# ---------------------------------------------------------------------
# Argument-Transformer
# ---------------------------------------------------------------------

def test_transform_press_key_string_to_keys_list() -> None:
    result = _transform_press_key({"key": "enter"})
    assert result == {"keys": ["enter"]}


def test_transform_press_key_list_passthrough() -> None:
    result = _transform_press_key({"keys": ["enter"]})
    assert result == {"keys": ["enter"]}


def test_transform_press_key_missing_arg_raises() -> None:
    with pytest.raises(ValueError, match="braucht 'key'"):
        _transform_press_key({})


def test_transform_press_shortcut_combo_string() -> None:
    result = _transform_press_shortcut({"combo": "ctrl+shift+t"})
    assert result == {"keys": ["ctrl", "shift", "t"]}


def test_transform_press_shortcut_keys_list_wins() -> None:
    """If both are given, the explicit list wins."""
    result = _transform_press_shortcut({
        "combo": "alt+f4", "keys": ["ctrl", "c"],
    })
    assert result == {"keys": ["ctrl", "c"]}


def test_transform_press_shortcut_lowercases() -> None:
    """ctrl+T becomes ['ctrl', 't'] — the hotkey tool resolve does ord(upper)."""
    result = _transform_press_shortcut({"combo": "Ctrl+T"})
    assert result == {"keys": ["ctrl", "t"]}


def test_transform_press_shortcut_missing_args_raises() -> None:
    with pytest.raises(ValueError):
        _transform_press_shortcut({})


def test_transform_open_terminal_default_is_wt() -> None:
    result = _transform_open_terminal({})
    assert result == {"app_name": "wt", "arguments": ""}


def test_transform_open_terminal_profile_aliases() -> None:
    assert _transform_open_terminal({"profile": "powershell"})["app_name"] == "powershell"
    assert _transform_open_terminal({"profile": "cmd"})["app_name"] == "cmd"
    assert _transform_open_terminal({"profile": "pwsh"})["app_name"] == "pwsh"


def test_transform_open_terminal_passthrough_for_custom_path() -> None:
    """An unknown profile name is passed through — the user can use their own paths."""
    result = _transform_open_terminal({"profile": "/usr/local/bin/iterm2"})
    assert result["app_name"] == "/usr/local/bin/iterm2"


# ---------------------------------------------------------------------
# Composite-Runners — ohne echte Tools, mit Fake-Executor
# ---------------------------------------------------------------------

class _FakeExecutor:
    """Stores all execute calls for assertions."""

    def __init__(self, success: bool = True, error: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.success = success
        self.error = error

    async def execute(
        self,
        tool: Any,
        args: dict[str, Any],
        *,
        user_utterance: str = "",
        trace_id: Any = None,
    ) -> ToolResult:
        self.calls.append({
            "tool": getattr(tool, "name", str(tool)),
            "args": args,
            "user_utterance": user_utterance,
        })
        return ToolResult(
            success=self.success,
            output="fake-output" if self.success else None,
            error=self.error,
        )


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.mark.asyncio
async def test_composite_open_new_tab_calls_hotkey_with_ctrl_t() -> None:
    """open_new_tab must send exactly ctrl+T — that's the universal tab shortcut."""
    executor = _FakeExecutor()
    tools = {"hotkey": _FakeTool("hotkey")}

    result = await _composite_open_new_tab({}, executor, tools, trace_id=uuid4())

    assert result.success is True
    assert len(executor.calls) == 1
    assert executor.calls[0]["tool"] == "hotkey"
    assert executor.calls[0]["args"] == {"keys": ["ctrl", "t"]}


@pytest.mark.asyncio
async def test_composite_open_new_tab_fails_clearly_when_hotkey_missing() -> None:
    executor = _FakeExecutor()
    tools: dict[str, Any] = {}  # No hotkey tool

    result = await _composite_open_new_tab({}, executor, tools, trace_id=uuid4())

    assert result.success is False
    assert result.error and "hotkey" in result.error.lower()


@pytest.mark.asyncio
async def test_composite_run_terminal_command_types_then_presses_enter() -> None:
    """run_terminal_command_through_ui = type_text(command) + hotkey(enter)."""
    executor = _FakeExecutor()
    tools = {
        "type_text": _FakeTool("type_text"),
        "hotkey": _FakeTool("hotkey"),
    }

    result = await _composite_run_terminal_command(
        {"command": "git status"}, executor, tools, trace_id=uuid4(),
    )

    assert result.success is True
    assert len(executor.calls) == 2
    assert executor.calls[0]["tool"] == "type_text"
    assert executor.calls[0]["args"] == {"text": "git status"}
    assert executor.calls[1]["tool"] == "hotkey"
    assert executor.calls[1]["args"] == {"keys": ["enter"]}


@pytest.mark.asyncio
async def test_composite_run_terminal_command_short_circuits_on_type_failure() -> None:
    """If type_text fails, Enter must NOT be pressed — otherwise empty commands."""
    executor = _FakeExecutor(success=False, error="Typing failed")
    tools = {
        "type_text": _FakeTool("type_text"),
        "hotkey": _FakeTool("hotkey"),
    }

    result = await _composite_run_terminal_command(
        {"command": "rm -rf /"}, executor, tools, trace_id=uuid4(),
    )

    assert result.success is False
    # Only one call (type_text), no Enter hotkey.
    assert len(executor.calls) == 1
    assert executor.calls[0]["tool"] == "type_text"


@pytest.mark.asyncio
async def test_composite_run_terminal_command_rejects_empty_command() -> None:
    executor = _FakeExecutor()
    tools = {"type_text": _FakeTool("type_text"), "hotkey": _FakeTool("hotkey")}

    result = await _composite_run_terminal_command(
        {}, executor, tools, trace_id=uuid4(),
    )

    assert result.success is False
    assert "command" in (result.error or "").lower()
    assert len(executor.calls) == 0


# ---------------------------------------------------------------------
# Default registry build is deterministic and idempotent
# ---------------------------------------------------------------------

def test_build_default_registry_is_deterministic() -> None:
    reg1 = build_default_registry()
    reg2 = build_default_registry()
    assert reg1.names() == reg2.names()
