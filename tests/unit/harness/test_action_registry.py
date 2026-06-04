"""Action-Registry: Lookup, Validierung, Arg-Transform, Composite-Runners.

Pinnt das **Vokabular**, das der LLM-System-Prompt nennt und das der
Computer-Use-Loop ausfuehren kann. Wenn Brain und Loop hier auseinander-
laufen, plant der Brain Actions, die der Executor nicht kennt — und der
User hoert "Unbekannte Action".

Diese Tests stellen sicher dass:
  1. Alle vom User explizit verlangten Actions registriert sind.
  2. ActionSpec-Validierung Inkonsistenzen aufdeckt (Tool UND Composite,
     oder weder noch).
  3. Argument-Transformer die richtigen Tool-Args produzieren.
  4. Composite-Runners alle benoetigten Tools korrekt ausfuehren.
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
# Vokabular-Coverage
# ---------------------------------------------------------------------

# Vom User explizit verlangte Actions in seinem Architektur-Auftrag.
# Wenn dieser Test failed, ist das System-Vokabular gegenueber dem User-
# Vertrag unvollstaendig — bitte Action ergaenzen statt Test loeschen.
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
    assert not missing, f"Fehlende Actions in der Default-Registry: {missing!r}"


def test_describe_for_prompt_lists_every_action() -> None:
    desc = DEFAULT_REGISTRY.describe_for_prompt()
    for name in DEFAULT_REGISTRY.names():
        assert name in desc, f"Action {name!r} fehlt im Prompt-Beschreibungstext"


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
    """Wenn beide angegeben sind, gewinnt die explizite Liste."""
    result = _transform_press_shortcut({
        "combo": "alt+f4", "keys": ["ctrl", "c"],
    })
    assert result == {"keys": ["ctrl", "c"]}


def test_transform_press_shortcut_lowercases() -> None:
    """ctrl+T wird zu ['ctrl', 't'] — der hotkey-Tool resolve macht ord(upper)."""
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
    """Unbekannter Profile-Name wird durchgereicht — User kann eigene Pfade nutzen."""
    result = _transform_open_terminal({"profile": "/usr/local/bin/iterm2"})
    assert result["app_name"] == "/usr/local/bin/iterm2"


# ---------------------------------------------------------------------
# Composite-Runners — ohne echte Tools, mit Fake-Executor
# ---------------------------------------------------------------------

class _FakeExecutor:
    """Speichert alle execute-Calls fuer Assertions."""

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
    """open_new_tab muss exakt Strg+T senden — das ist der universelle Tab-Shortcut."""
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
    tools: dict[str, Any] = {}  # Kein hotkey-Tool

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
    """Wenn type_text failed, darf Enter NICHT gedrueckt werden — sonst leere Befehle."""
    executor = _FakeExecutor(success=False, error="Tippen fehlgeschlagen")
    tools = {
        "type_text": _FakeTool("type_text"),
        "hotkey": _FakeTool("hotkey"),
    }

    result = await _composite_run_terminal_command(
        {"command": "rm -rf /"}, executor, tools, trace_id=uuid4(),
    )

    assert result.success is False
    # Nur ein Aufruf (type_text), kein Enter-Hotkey.
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
# Default-Registry-Build ist deterministisch und idempotent
# ---------------------------------------------------------------------

def test_build_default_registry_is_deterministic() -> None:
    reg1 = build_default_registry()
    reg2 = build_default_registry()
    assert reg1.names() == reg2.names()
