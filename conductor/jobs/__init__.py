"""Job-Handler — drei Implementations, alle via ``JobHandler``-Protocol.

Registry-Pattern: ``HANDLERS[spec.type]`` gibt den passenden Handler
zurueck. Neue Job-Types sind eine 2-Datei-Aenderung (Spec-Model in
``core/schema.py`` + Handler hier).
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
