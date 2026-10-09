"""The ``computer`` tool: a live session's reasoning model operates the screen itself.

Voice-only, like ``screen_snapshot``: the supervisor gateway adds it to the
live catalog (ADR-0039). The router stays a pure dispatcher (ADR-0011) and the
classic Computer-Use mission keeps serving text chat and scheduled tasks.
"""

from __future__ import annotations

from typing import Any

from jarvis.cu.direct import TOOL_DESCRIPTION, TOOL_NAME, get_direct_computer, tool_schema


class ComputerTool:
    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    # Same tier as screen_snapshot and the input primitives it replaces: the
    # action rules (stop before payments, posts, deletions, credentials) live
    # in the model instructions, Escape stops it at any time.
    risk_tier = "monitor"
    is_action_tool = True
    schema = tool_schema()

    def describe_args(self, args: dict[str, Any]) -> dict[str, str]:
        """The impact contract (``{"level", ...}``) every caller reads as a dict.

        Looking (screenshot, wait) only reads; any input step changes the
        screen. A plain string broke the voice read check on every call, so a
        repeated screenshot could be answered with an earlier, stale one.
        """
        # Read the call as ``direct.parse_steps`` does (one bare step allowed)
        # and fail closed: only a well-formed call of pure looks is a read.
        args = args if isinstance(args, dict) else {}
        raw = args.get("steps")
        if raw is None and "action" in args:
            raw = [args]
        steps = raw if isinstance(raw, list) else []
        actions = [
            str(step.get("action", "")).strip().lower() for step in steps if isinstance(step, dict)
        ]
        looks_only = bool(actions) and len(actions) == len(steps) and all(
            action in {"screenshot", "wait"} for action in actions
        )
        return {
            "level": "read" if looks_only else "modify",
            "commands": "Operate the screen: " + (", ".join(actions) or "screenshot"),
        }

    async def execute(self, args, ctx):
        from jarvis.core.protocols import ToolResult
        from jarvis.plugins.tool.appshot import _app_bus

        config = getattr(ctx, "config", None) or {}
        owner = str(config.get("live_session_id") or config.get("mission_id") or "default")
        result = await get_direct_computer().run(
            owner,
            dict(args or {}),
            revision=config.get("task_revision"),
            bus=_app_bus(),
        )
        success = bool(result.get("success"))
        error = None if success else str(result.get("error") or "Computer control failed.")
        return ToolResult(success, result, error)
