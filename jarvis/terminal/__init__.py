"""Terminal-Layer fuer die Desktop-App (Phase 1a-Erweiterung).

Stellt einen in-process PTY-Manager bereit, der Shells (PowerShell 7,
Windows PowerShell 5.1, CMD, Git-Bash) via ConPTY spawnt und deren
I/O ueber den Event-Bus zur Web-UI forwarded.

Modul-Struktur:
- shells.py     — Discovery der verfuegbaren Shells auf diesem System.
- pty_manager.py — Async-Wrapper um pywinpty, verwaltet Sessions.
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
