"""Where the turn that calls an app route came from — spoken, written, its trace.

The app tools (``app-command``, ``run-app-action``) run a REST route
in-process on behalf of a brain turn. These two headers tell the route about
that turn, so one endpoint can answer a voice question, a typed chat or a
Telegram message the right way:

- ``X-Jarvis-Turn-Delivery``: ``written`` for typed turns (the turn's tool
  context says ``delivery = written``), else ``spoken``;
- ``X-Jarvis-Turn-Trace``: the turn's trace id — a channel that routes its
  replies by trace id (Telegram) can recognise its own messages.

A direct UI or CLI call carries neither header.
"""

from __future__ import annotations

from typing import Any, Final

HEADER_DELIVERY: Final = "X-Jarvis-Turn-Delivery"
HEADER_TRACE: Final = "X-Jarvis-Turn-Trace"


def turn_origin_headers(ctx: Any) -> dict[str, str]:
    """Headers describing the calling turn; empty without a turn context."""
    if ctx is None:
        return {}
    config = getattr(ctx, "config", None)
    delivery = config.get("delivery") if isinstance(config, dict) else None
    headers = {HEADER_DELIVERY: "written" if delivery == "written" else "spoken"}
    trace = getattr(ctx, "trace_id", None)
    if trace is not None:
        headers[HEADER_TRACE] = str(trace)
    return headers


__all__ = ["HEADER_DELIVERY", "HEADER_TRACE", "turn_origin_headers"]
