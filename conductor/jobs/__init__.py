"""Job handlers — three implementations, all via the ``JobHandler`` protocol.

Registry pattern: ``HANDLERS[spec.type]`` returns the matching handler.
A new job type is a 2-file change (spec model in ``core/schema.py`` +
handler here).
"""
from __future__ import annotations

from .agent import AgentHandler
from .base import HandlerResult, JobHandler
from .http import HttpHandler
from .shell import ShellHandler

HANDLERS: dict[str, JobHandler] = {
    "shell": ShellHandler(),
    "http": HttpHandler(),
    "agent": AgentHandler(),
}

__all__ = ["AgentHandler", "HANDLERS", "HandlerResult", "HttpHandler",
           "JobHandler", "ShellHandler"]
