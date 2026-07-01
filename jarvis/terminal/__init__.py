"""Terminal layer for the Desktop App (Phase 1a extension).

Provides an in-process PTY manager that spawns shells (PowerShell 7,
Windows PowerShell 5.1, CMD, Git Bash) via ConPTY and forwards their
I/O to the web UI over the event bus.

Module structure:
- shells.py     — discovery of the shells available on this system.
- pty_manager.py — async wrapper around pywinpty, manages sessions.
"""
from __future__ import annotations

from .pty_manager import PtyManager, PtySession
from .shells import ShellInfo, discover_shells, get_shell

__all__ = [
    "PtyManager",
    "PtySession",
    "ShellInfo",
    "discover_shells",
    "get_shell",
]
