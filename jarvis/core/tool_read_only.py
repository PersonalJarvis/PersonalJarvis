"""A side-effect gate shared by plan-mode discovery and execution."""

from __future__ import annotations

from typing import Any

# Trusted session policy supplied by the chat service, never by tool arguments.
_sessions: dict[str, bool] = {}


def set_chat_read_only(session_id: str, enabled: bool) -> None:
    if enabled:
        _sessions[session_id] = True
    else:
        _sessions.pop(session_id, None)


def chat_is_read_only(session_id: str) -> bool:
    return _sessions.get(session_id, False)


def allows_read(tool: Any, args: dict[str, Any] | None = None) -> bool:
    # A safe-tier message sender is still an action. Tier alone is not a read proof.
    describe = getattr(tool, "describe_args", None)
    if args is not None and callable(describe):
        try:
            description = describe(args)
        except Exception:
            # Unclassifiable calls are refused in this restrictive mode.
            return False
        return isinstance(description, dict) and description.get("level") == "read"
    return not getattr(tool, "is_action_tool", False) and getattr(tool, "risk_tier", None) == "safe"
