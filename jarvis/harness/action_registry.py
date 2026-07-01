"""Action registry: central broker between LLM vocabulary and the tool layer.

Background (2026-04-25): Before the registry, action mapping in
``computer_use_loop.py`` was a hard-coded 4-entry dict (open_app,
type_text, hotkey, click). The LLM system-prompt sometimes used different
names (key_press, scroll, wait), which led to "unknown action" errors.
Generic actions such as "open_terminal" or "open_new_tab" could not be
expressed at all.

This registry is the single source of truth for which action names the
system recognises and which tool + argument combination they map to. The
LLM system-prompt is generated from the registry, the CU-loop consumes
the registry, and tests pin the vocabulary.

Three action types:
  1. **Direct**: 1:1 mapping to a tool (e.g. `type_text` -> TypeTextTool).
  2. **Aliased**: action name differs from the tool name
     (e.g. `press_key` -> hotkey tool, `press_shortcut` -> hotkey tool).
  3. **Composite**: one action word corresponds to a sequence of tool calls
     (e.g. `open_terminal` -> open_app('wt') + wait_for_ui_state,
     `run_terminal_command_through_ui` -> hotkey + type + enter).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)


ArgTransform = Callable[[dict[str, Any]], dict[str, Any]]
"""Function that transforms raw action args into tool args.

Example: ``press_shortcut(combo='ctrl+t')`` -> ``hotkey(keys=['ctrl', 't'])``.
"""

CompositeRunner = Callable[..., Any]
"""Async function that orchestrates multiple tool calls.

Signature: ``async def runner(args, executor, tools, trace_id) -> ToolResult``.
The runner may invoke other tools from ``tools`` itself — typical for
``run_terminal_command_through_ui`` (open_app + wait + type + enter).
"""


@dataclass(frozen=True)
class ActionSpec:
    """Specification of an action in the LLM vocabulary."""

    name: str
    """Action name as it appears in a plan step (e.g. 'press_shortcut')."""

    tool_name: str | None
    """Tool name for direct/aliased actions. None for composite actions."""

    description: str
    """Short description — listed in the system-prompt for the LLM."""

    arg_schema: dict[str, Any] = field(default_factory=dict)
    """JSON-schema-like dict: what the LLM should write into `args`.

    Not strictly validated at runtime (the tool does that itself), but used
    by the prompt generator and tests.
    """

    arg_transform: ArgTransform | None = None
    """Optional transformer from action args to tool args.

    None means: pass through 1:1.
    """

    composite: CompositeRunner | None = None
    """Set when the action orchestrates multiple tools. tool_name must be None."""

    risk_hint: str = "monitor"
    """Defensive risk class for the system-prompt hint. The real risk tier is
    determined by the tool itself — this is only for brain hints."""

    def validate(self) -> None:
        """Consistency check: either tool_name OR composite, not both and not neither."""
        has_tool = self.tool_name is not None
        has_comp = self.composite is not None
        if has_tool == has_comp:
            raise ValueError(
                f"ActionSpec '{self.name}' must have either tool_name OR "
                f"composite — not both and not neither."
            )


class ActionRegistry:
    """Mutable registry with lookup, listing, and validation API.

    Typically populated at module load time (see ``build_default_registry()``
    below) and can be extended at runtime if skills or plugins want to
    register additional actions.
    """

    def __init__(self) -> None:
        self._actions: dict[str, ActionSpec] = {}

    def register(self, spec: ActionSpec) -> None:
        spec.validate()
        if spec.name in self._actions:
            log.debug("Action '%s' is being overwritten", spec.name)
        self._actions[spec.name] = spec

    def get(self, name: str) -> ActionSpec | None:
        return self._actions.get(name)

    def has(self, name: str) -> bool:
        return name in self._actions

    def names(self) -> list[str]:
        return sorted(self._actions.keys())

    def all(self) -> list[ActionSpec]:
        return [self._actions[k] for k in sorted(self._actions.keys())]

    def describe_for_prompt(self) -> str:
        """Returns a human-readable action vocabulary for the system-prompt.

        Used by ``brain/factory.py`` and ``computer_use_loop.py`` to ensure
        the LLM prompt knows exactly the action names AND the exact argument
        names that the CU-loop can execute.

        IMPORTANT: The ``args=`` section is not optional. Without it the planner
        guesses argument names (``open_app{name:..}`` instead of
        ``open_app{app_name:..}``), and every step fails with "<param> missing".
        The ``arg_schema`` of each ``ActionSpec`` is the single source of truth;
        it is rendered 1:1 into the prompt so that planner vocabulary and action
        handlers never drift.
        """
        lines: list[str] = [
            "Verfuegbare Actions im Computer-Use-Loop "
            "(nutze EXAKT die gelisteten args-Namen):",
        ]
        for spec in self.all():
            schema = getattr(spec, "arg_schema", None) or {}
            if schema:
                arg_str = ", ".join(f"{k}: {v}" for k, v in schema.items())
            else:
                arg_str = ""
            lines.append(f"  - {spec.name}: {spec.description} | args={{{arg_str}}}")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Argument-Transformer (Aliased Actions)
# ----------------------------------------------------------------------


def _transform_press_key(args: dict[str, Any]) -> dict[str, Any]:
    """``press_key(key='enter')`` -> ``hotkey(keys=['enter'])``."""
    key = args.get("key") or args.get("keys")
    if isinstance(key, str):
        return {"keys": [key]}
    if isinstance(key, list):
        return {"keys": [str(k) for k in key]}
    raise ValueError("press_key braucht 'key' (string) oder 'keys' (list)")


def _transform_press_shortcut(args: dict[str, Any]) -> dict[str, Any]:
    """``press_shortcut(combo='ctrl+shift+t')`` -> ``hotkey(keys=['ctrl','shift','t'])``.

    Also accepts ``keys`` as a list (passed through 1:1).
    """
    if "keys" in args and isinstance(args["keys"], list):
        return {"keys": [str(k) for k in args["keys"]]}
    combo = args.get("combo") or args.get("shortcut")
    if not isinstance(combo, str):
        raise ValueError(
            "press_shortcut braucht 'combo' (z.B. 'ctrl+t') oder 'keys' (Liste)"
        )
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"Leere combo: {combo!r}")
    return {"keys": parts}


def _transform_open_terminal(args: dict[str, Any]) -> dict[str, Any]:
    """``open_terminal(profile='powershell')`` -> ``open_app(app_name='wt')``.

    Default is Windows Terminal (``wt``). Fallback profiles via argument:
    ``cmd``, ``powershell``, ``pwsh``.
    """
    profile = (args.get("profile") or args.get("app") or "wt").strip().lower()
    if profile in ("wt", "windowsterminal", "terminal"):
        app = "wt"
    elif profile in ("cmd", "command"):
        app = "cmd"
    elif profile in ("powershell", "ps"):
        app = "powershell"
    elif profile == "pwsh":
        app = "pwsh"
    else:
        # Path/name provided by the user — pass through as-is.
        app = profile
    return {"app_name": app, "arguments": args.get("arguments", "")}


# ----------------------------------------------------------------------
# Composite Runners
# ----------------------------------------------------------------------


async def _composite_open_new_tab(
    args: dict[str, Any],
    executor: Any,
    tools: dict[str, Any],
    trace_id: Any,
) -> ToolResult:
    """Sends Ctrl+T to the active window — the standard shortcut for 'new tab'.

    Works in every browser, Windows Terminal, and many editors. If an app
    maps this differently, the LLM can use ``press_shortcut`` directly instead.
    """
    hotkey_tool = tools.get("hotkey")
    if hotkey_tool is None:
        return ToolResult(
            success=False, output=None,
            error="hotkey tool not available — open_new_tab cannot be executed",
        )
    return await executor.execute(
        hotkey_tool,
        {"keys": ["ctrl", "t"]},
        user_utterance="open_new_tab",
        trace_id=trace_id,
    )


async def _composite_run_terminal_command(
    args: dict[str, Any],
    executor: Any,
    tools: dict[str, Any],
    trace_id: Any,
) -> ToolResult:
    """Types a command into the currently active terminal and submits it with Enter.

    Args:
        command: the command as a string (e.g. "ls -la" or "git status")

    Requires: the terminal window must already be open AND focused.
    The caller is responsible for calling ``open_terminal`` and, if needed,
    ``wait_for_ui_state`` beforehand.
    """
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolResult(
            success=False, output=None,
            error="run_terminal_command_through_ui needs 'command' (string)",
        )
    type_tool = tools.get("type_text")
    hotkey_tool = tools.get("hotkey")
    if type_tool is None or hotkey_tool is None:
        return ToolResult(
            success=False, output=None,
            error="type_text or hotkey tool missing — composite cannot run",
        )
    type_result = await executor.execute(
        type_tool, {"text": command},
        user_utterance="run_terminal_command", trace_id=trace_id,
    )
    if not getattr(type_result, "success", False):
        return type_result
    return await executor.execute(
        hotkey_tool, {"keys": ["enter"]},
        user_utterance="run_terminal_command", trace_id=trace_id,
    )


# ----------------------------------------------------------------------
# Default registry construction
# ----------------------------------------------------------------------


def build_default_registry() -> ActionRegistry:
    """Builds the default registry with all user-defined actions.

    Changes to this vocabulary should keep two places in sync:
    this registry and ``brain/factory.py`` (system-prompt). Ideally the
    prompt calls ``registry.describe_for_prompt()`` directly — then drift
    is architecturally impossible.
    """
    reg = ActionRegistry()

    # ---- Direct (1:1 tool mapping) -----------------------------------
    reg.register(ActionSpec(
        name="open_app",
        tool_name="open_app",
        description="Opens a Windows application by name or path.",
        arg_schema={"app_name": "string", "arguments": "string?"},
        risk_hint="monitor",
    ))
    reg.register(ActionSpec(
        name="type_text",
        tool_name="type_text",
        description="Types text into the active window.",
        arg_schema={"text": "string", "delay_s": "number?"},
        risk_hint="safe",
    ))
    reg.register(ActionSpec(
        name="click",
        tool_name="click",
        description="Clicks the mouse at a screen coordinate.",
        arg_schema={"x": "int", "y": "int", "button": "left|right|middle?", "double": "bool?"},
        risk_hint="monitor",
    ))
    reg.register(ActionSpec(
        name="move_mouse",
        tool_name="move_mouse",
        description="Moves the mouse cursor without clicking.",
        arg_schema={"x": "int", "y": "int"},
        risk_hint="safe",
    ))
    reg.register(ActionSpec(
        name="click_element",
        tool_name="click_element",
        description=(
            "Clicks a UIA element by name/role (instead of by coordinate). "
            "More robust than click — no pixel guessing needed."
        ),
        arg_schema={
            "name": "string",
            "role": "string?",
            "automation_id": "string?",
            "button": "left|right|middle?",
            "double": "bool?",
            "nth": "int?",
        },
        risk_hint="monitor",
    ))
    reg.register(ActionSpec(
        name="scroll",
        tool_name="scroll",
        description=(
            "Scrolls the mouse wheel in a direction to bring hidden elements "
            "into view (lists, chats, file pickers)."
        ),
        arg_schema={
            "direction": "up|down|left|right",
            "amount": "int?",
            "x": "int?",
            "y": "int?",
        },
        risk_hint="safe",
    ))
    reg.register(ActionSpec(
        name="wait_for_element",
        tool_name="wait_for_element",
        description=(
            "Waits (polling UIA) until an element with a matching role/name appears "
            "and returns its midpoint — for app-start/load waits."
        ),
        arg_schema={
            "name_contains": "string?",
            "role": "string?",
            "automation_id": "string?",
            "enabled_required": "bool?",
            "timeout_s": "number?",
        },
        risk_hint="safe",
    ))
    reg.register(ActionSpec(
        name="switch_window",
        tool_name="switch_window",
        description="Switches to a window whose title matches a substring.",
        arg_schema={"title_contains": "string"},
        risk_hint="monitor",
    ))
    reg.register(ActionSpec(
        name="wait_for_ui_state",
        tool_name="wait_for_ui_state",
        description=(
            "Waits until the UI state changes (e.g. a new window becomes visible, "
            "text appears in an element). Polls the vision/UIA snapshot."
        ),
        arg_schema={
            "title_contains": "string?",
            "text_contains": "string?",
            "timeout_s": "number?",
        },
        risk_hint="safe",
    ))
    reg.register(ActionSpec(
        name="read_visible_ui_state",
        tool_name="read_visible_ui_state",
        description=(
            "Reads the current UI state (window title, visible text) "
            "as structured feedback for the agent."
        ),
        arg_schema={"include_screenshot": "bool?"},
        risk_hint="safe",
    ))

    # ---- Aliased (tool mapping with arg transform) --------------------
    reg.register(ActionSpec(
        name="press_key",
        tool_name="hotkey",
        description="Presses a single key (e.g. enter, esc, tab, f5).",
        arg_schema={"key": "string"},
        arg_transform=_transform_press_key,
        risk_hint="monitor",
    ))
    reg.register(ActionSpec(
        name="press_shortcut",
        tool_name="hotkey",
        description=(
            "Sends a key combination such as 'ctrl+t' (new tab), "
            "'alt+tab' (window switch), 'ctrl+shift+t' (restore tab)."
        ),
        arg_schema={"combo": "string (e.g. 'ctrl+t')"},
        arg_transform=_transform_press_shortcut,
        risk_hint="monitor",
    ))
    reg.register(ActionSpec(
        name="open_terminal",
        tool_name="open_app",
        description=(
            "Opens a terminal window. Default: Windows Terminal (wt). "
            "Optional: profile='cmd'|'powershell'|'pwsh'."
        ),
        arg_schema={"profile": "string?"},
        arg_transform=_transform_open_terminal,
        risk_hint="monitor",
    ))

    # ---- Composite (multi-tool sequences) ----------------------------
    reg.register(ActionSpec(
        name="open_new_tab",
        tool_name=None,
        description="Opens a new tab in the active window (sends Ctrl+T).",
        arg_schema={},
        composite=_composite_open_new_tab,
        risk_hint="monitor",
    ))
    reg.register(ActionSpec(
        name="run_terminal_command_through_ui",
        tool_name=None,
        description=(
            "Types a command into the active terminal and presses Enter. "
            "Requires: the terminal must already be open & focused."
        ),
        arg_schema={"command": "string"},
        composite=_composite_run_terminal_command,
        risk_hint="monitor",
    ))

    # ---- Control actions without a tool call (handled by the CU-loop itself) ----
    # 'wait' and 'done' are hard-coded in the loop (loop control). We list them
    # here only for the prompt — the loop remains the owner.
    reg.register(ActionSpec(
        name="wait",
        tool_name="__loop_wait__",
        description="Pauses execution for N seconds (max 60s).",
        arg_schema={"seconds": "number"},
        risk_hint="safe",
    ))
    reg.register(ActionSpec(
        name="done",
        tool_name="__loop_done__",
        description="Marks the plan as done — ends the loop.",
        arg_schema={},
        risk_hint="safe",
    ))

    return reg


# Singleton default registry. Loaded by the CU-loop.
DEFAULT_REGISTRY = build_default_registry()
