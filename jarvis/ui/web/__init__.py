"""Web backend for the desktop UI (Phase 1a).

Exposes FastAPI + WebSocket on 127.0.0.1 for the pywebview shell, and
optionally for mobile companion clients (Phase 8+). Token auth locks the
endpoint down against nosy neighbors on localhost (browser extensions,
other dev servers) — the bind address alone is not enough.

Lazy package surface (PEP 562). Importing ``jarvis.ui.web`` (which
``python -m jarvis.ui.web.launcher`` forces) must NOT eagerly import
``.server`` — that pulls FastAPI + every route schema and cost ~500 ms on the
boot critical path, blocking the fast-boot bootstrap server from binding its
port. ``WebServer`` / the WS schema symbols still resolve on first access.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # static analysers + IDEs still see the real symbols
    from .schema import (
        WSCommand,
        WSEventEnvelope,
        WSMessageIn,
        WSWelcome,
        event_to_ws_envelope,
    )
    from .server import WebServer

_LAZY: dict[str, str] = {
    "WebServer": ".server",
    "WSCommand": ".schema",
    "WSEventEnvelope": ".schema",
    "WSMessageIn": ".schema",
    "WSWelcome": ".schema",
    "event_to_ws_envelope": ".schema",
}


def __getattr__(name: str):  # noqa: ANN202 (PEP 562 module hook)
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "WebServer",
    "WSEventEnvelope",
    "WSMessageIn",
    "WSCommand",
    "WSWelcome",
    "event_to_ws_envelope",
]
