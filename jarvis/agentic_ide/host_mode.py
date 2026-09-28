"""Whether this process keeps its coding agents in the PTY host.

A switch, deliberately in a module that imports nothing: the desktop shell
turns it on BEFORE its API answers the UI, and importing the registry itself
there would put ~0.4 s of imports in front of the app becoming usable (AP-26).
:class:`jarvis.agentic_ide.session.Registry` reads it when it is created.

Why it has to be on that early: the grid restores and attaches panes the
moment the API answers. A registry that met its first pane with the host still
off started that pane's agent in-process — while the agent the PTY host had
kept running since the app closed went on, unseen (RUB-102, 2026-09-28).
"""

from __future__ import annotations

_enabled = False


def enable() -> None:
    """Keep this process's coding agents in the PTY host from now on."""
    global _enabled
    _enabled = True


def enabled() -> bool:
    return _enabled


def reset() -> None:
    """Tests only."""
    global _enabled
    _enabled = False


__all__ = ["enable", "enabled", "reset"]
