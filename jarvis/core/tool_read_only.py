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
    """Use trusted tool metadata, never a presentation/command heuristic.

    Mixed tools declare a separate argument-aware capability. Neither risk
    tiers nor remote presentation metadata establish read authority.
    """
    capability = getattr(tool, "read_only_for_args", None)
    if callable(capability):
        if args is None:
            return True
        try:
            return capability(args) is True
        except Exception:
            # A broken capability cannot grant permissions in a restrictive mode.
            return False
    return getattr(tool, "read_only", False) is True
