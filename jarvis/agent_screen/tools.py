"""Action tools bound to one agent screen.

These carry the SAME tool names as the physical-desktop tools (``click``,
``type_text``, ``hotkey``, ``scroll``, ``drag``, ``click_element``,
``open_app``, ``switch_window``). That is the whole trick: the Computer-Use
engine dispatches by name through the ToolExecutor, so swapping the tool map
redirects every action into the isolated session without touching a line of
the engine's action code — and without leaving the ToolExecutor choke point
that gates and records every action (AP-3).

Each tool re-checks the captured foreground identity immediately before it
acts, exactly like its physical counterpart. On a screen nobody is watching
that check matters MORE, not less: there is no user to notice that a dialog
stole focus between the screenshot and the click.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

#: Every action name an agent screen answers. Kept in one place so the broker
#: allowlist, the tool map and the tests cannot drift apart.
SCREEN_TOOL_NAMES: tuple[str, ...] = (
    "click",
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "click_element",
    "open_app",
    "switch_window",
)


class _ScreenBoundTool:
    """Shared body: validate, re-check the foreground, dispatch, report."""

    name: str = ""
    risk_tier: str = "monitor"
    description: str = ""
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    #: Actions that move the pointer or the keyboard focus and therefore must
    #: honour the captured-window guard. ``open_app`` is excluded because it
    #: is expected to CHANGE the foreground.
    guarded: bool = True

    def __init__(self, session: Any) -> None:
        self._session = session

    @property
    def screen_id(self) -> str:
        return str(getattr(self._session, "screen_id", ""))

    def _action_params(self, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _guard_ok(self, expected: tuple[Any, ...] | None) -> bool:
        if not self.guarded or expected is None:
            return True
        if not expected or expected[0] == "none":
            return False
        return self._session.foreground().signature() == tuple(expected)

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        try:
            params = self._action_params(args)
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(success=False, output=None, error=str(exc))

        expected_raw = args.get("_expected_window_signature")
        if expected_raw is not None and not isinstance(expected_raw, (list, tuple)):
            return ToolResult(
                success=False,
                output=None,
                error=f"Refusing {self.name}: invalid captured-window identity.",
            )
        expected = tuple(expected_raw) if expected_raw is not None else None
        if not await asyncio.to_thread(self._guard_ok, expected):
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"Refusing {self.name} on screen {self.screen_id}: the "
                    "foreground window identity is unavailable or changed "
                    "after the screenshot."
                ),
            )
        outcome = await asyncio.to_thread(
            self._session.act,
            self._screen_action,
            params,
        )
        if not outcome.ok:
            return ToolResult(success=False, output=None, error=outcome.detail)
        return ToolResult(
            success=True,
            output=f"{outcome.detail} [screen {self.screen_id}]",
        )

    #: Wire verb this tool maps to; equals ``name`` unless the screen has a
    #: coarser primitive (``click_element`` resolves to a plain click).
    _screen_action: str = ""


class ScreenClickTool(_ScreenBoundTool):
    name = "click"
    _screen_action = "click"
    description = "Click at a coordinate on the agent's own screen."
    schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "double": {"type": "boolean"},
        },
        "required": ["x", "y"],
    }

    def _action_params(self, args: dict[str, Any]) -> dict[str, Any]:
        button = str(args.get("button", "left")).lower()
        if button not in ("left", "right", "middle"):
            raise ValueError(f"Unknown button={button!r}. Allowed: left/right/middle")
        return {
            "x": int(args["x"]),
            "y": int(args["y"]),
            "button": button,
            "double": bool(args.get("double", False)),
        }


class ScreenTypeTextTool(_ScreenBoundTool):
    name = "type_text"
    _screen_action = "type_text"
    description = "Type text into the focused control on the agent's own screen."
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def _action_params(self, args: dict[str, Any]) -> dict[str, Any]:
        text = args.get("text")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        return {"text": text}


class ScreenHotkeyTool(_ScreenBoundTool):
    name = "hotkey"
    _screen_action = "hotkey"
    description = "Press a key combination on the agent's own screen."
    schema = {
        "type": "object",
        "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
        "required": ["keys"],
    }

    def _action_params(self, args: dict[str, Any]) -> dict[str, Any]:
        from jarvis.cu.actuate.base import is_known_key_name  # noqa: PLC0415

        keys = [str(k) for k in (args.get("keys") or [])]
        if not keys:
            raise ValueError("keys must contain at least one key name")
        unknown = [k for k in keys if not is_known_key_name(k)]
        if unknown:
            raise ValueError(f"unknown key name(s): {', '.join(unknown)}")
        return {"keys": keys}


class ScreenScrollTool(_ScreenBoundTool):
    name = "scroll"
    _screen_action = "scroll"
    description = "Scroll on the agent's own screen."
    schema = {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer"},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["direction"],
    }

    def _action_params(self, args: dict[str, Any]) -> dict[str, Any]:
        direction = str(args.get("direction", "down")).lower()
        if direction not in ("up", "down", "left", "right"):
            raise ValueError(f"unknown scroll direction {direction!r}")
        params: dict[str, Any] = {
            "direction": direction,
            "amount": max(1, int(args.get("amount", 3))),
        }
        if args.get("x") is not None and args.get("y") is not None:
            params["x"] = int(args["x"])
            params["y"] = int(args["y"])
        return params


class ScreenDragTool(_ScreenBoundTool):
    name = "drag"
    _screen_action = "drag"
    description = "Drag from one point to another on the agent's own screen."
    schema = {
        "type": "object",
        "properties": {
            "x1": {"type": "integer"},
            "y1": {"type": "integer"},
            "x2": {"type": "integer"},
            "y2": {"type": "integer"},
            "duration_ms": {"type": "integer"},
        },
        "required": ["x1", "y1", "x2", "y2"],
    }

    def _action_params(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "x1": int(args["x1"]),
            "y1": int(args["y1"]),
            "x2": int(args["x2"]),
            "y2": int(args["y2"]),
            "duration_ms": max(0, int(args.get("duration_ms", 400))),
        }


class ScreenClickElementTool(_ScreenBoundTool):
    """Click a named control — honestly refused where no tree exists.

    The physical-screen tool resolves a label through the accessibility tree
    and clicks its centre. A screen whose runner reports no tree cannot do
    that, and guessing a coordinate from a label would be exactly the
    hallucinated click this engine's guards were built to stop. Refusing with
    a reason sends the model back to pixel coordinates, which DO work.
    """

    name = "click_element"
    _screen_action = "click"
    description = "Click a named control on the agent's own screen."
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    def _action_params(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"name": str(args.get("name", ""))}

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        label = str(args.get("name", "")).strip()
        if not label:
            return ToolResult(success=False, output=None, error="name is required")
        labels, _hint, _handoff, rects = await asyncio.to_thread(
            self._session.ui_snapshot,
        )
        match = next(
            (
                bounds
                for name, _role, bounds in rects
                if name.strip().casefold() == label.casefold()
            ),
            None,
        )
        if match is None:
            available = ", ".join(labels[:12]) if labels else "none reported"
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"screen {self.screen_id} exposes no control named "
                    f"{label!r} (available: {available}). Use click with pixel "
                    "coordinates from the screenshot instead."
                ),
            )
        left, top, width, height = match
        outcome = await asyncio.to_thread(
            self._session.act,
            "click",
            {
                "x": int(left + width // 2),
                "y": int(top + height // 2),
                "button": "left",
                "double": False,
            },
        )
        if not outcome.ok:
            return ToolResult(success=False, output=None, error=outcome.detail)
        return ToolResult(
            success=True,
            output=f"clicked {label!r} [screen {self.screen_id}]",
        )


class ScreenOpenAppTool(_ScreenBoundTool):
    name = "open_app"
    _screen_action = "open_app"
    guarded = False  # launching an app is meant to change the foreground
    description = "Launch or focus an application on the agent's own screen."
    schema = {
        "type": "object",
        "properties": {"app_name": {"type": "string"}},
        "required": ["app_name"],
    }

    def _action_params(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("app_name", "")).strip()
        if not name:
            raise ValueError("app_name is required")
        return {"app_name": name}


class ScreenSwitchWindowTool(_ScreenBoundTool):
    name = "switch_window"
    _screen_action = "switch_window"
    guarded = False  # switching windows is meant to change the foreground
    description = "Focus an open window on the agent's own screen."
    schema = {
        "type": "object",
        "properties": {"title_contains": {"type": "string"}},
        "required": ["title_contains"],
    }

    def _action_params(self, args: dict[str, Any]) -> dict[str, Any]:
        needle = str(args.get("title_contains", "")).strip()
        if not needle:
            raise ValueError("title_contains is required")
        return {"title_contains": needle}


def screen_bound_tools(session: Any) -> dict[str, Any]:
    """The action tool map for one screen, keyed exactly as the engine asks."""
    tools = (
        ScreenClickTool(session),
        ScreenTypeTextTool(session),
        ScreenHotkeyTool(session),
        ScreenScrollTool(session),
        ScreenDragTool(session),
        ScreenClickElementTool(session),
        ScreenOpenAppTool(session),
        ScreenSwitchWindowTool(session),
    )
    return {tool.name: tool for tool in tools}


__all__ = [
    "SCREEN_TOOL_NAMES",
    "ScreenClickElementTool",
    "ScreenClickTool",
    "ScreenDragTool",
    "ScreenHotkeyTool",
    "ScreenOpenAppTool",
    "ScreenScrollTool",
    "ScreenSwitchWindowTool",
    "ScreenTypeTextTool",
    "screen_bound_tools",
]
