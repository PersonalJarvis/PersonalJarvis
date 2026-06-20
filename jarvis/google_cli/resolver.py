"""Resolve which official Google agent CLI to drive, and how to invoke it.

Order: Antigravity ``agy`` (the official successor, 2026-06) > the Gemini CLI on
PATH > the npm-global Gemini bundle via ``node`` (covers a broken PATH shim).
Returns ``None`` when no official binary is present — the caller then reports the
provider as not installed.

Cross-platform (CLOUD.md Rule #1): pure stdlib, capability-probed via
``shutil.which``, never raises on a probe. Only the official binary is ever
invoked — the stored OAuth token is never read to make our own HTTP request
(Google ToS).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

# Binary name + platform shim variants. ``shutil.which`` honors PATHEXT, so the
# bare name usually resolves; the explicit variants are belt-and-suspenders.
_AGY: tuple[str, ...] = ("agy", "agy.exe")
_GEMINI: tuple[str, ...] = ("gemini", "gemini.cmd", "gemini.exe")


@dataclass(frozen=True)
class GoogleCli:
    """A resolved official Google CLI and the argv prefix used to invoke it."""

    kind: str  # "agy" | "gemini"
    argv_prefix: list[str] = field(default_factory=list)
    version: str | None = None


def _default_npm_bundle() -> str | None:
    """Path to the npm-global Gemini bundle, or ``None``. Best-effort, never raises.

    Covers this machine's broken-shim case: ``@google/gemini-cli`` is installed
    but its ``gemini``/``gemini.cmd`` PATH shims are stale npm temp files, so
    ``shutil.which`` misses it. We can still drive ``node <bundle>/gemini.js``.
    """
    try:
        root = subprocess.run(
            ["npm", "root", "-g"],
            capture_output=True,
            text=True,
            timeout=5.0,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not root:
        return None
    bundle = os.path.join(root, "@google", "gemini-cli", "bundle", "gemini.js")
    return bundle if os.path.isfile(bundle) else None


def resolve_google_cli(
    *,
    which: Callable[[str], str | None] = shutil.which,
    npm_bundle: Callable[[], str | None] = _default_npm_bundle,
) -> GoogleCli | None:
    """Resolve the preferred official Google CLI. ``which``/``npm_bundle`` are
    injectable seams for tests."""
    for name in _AGY:
        path = which(name)
        if path:
            return GoogleCli(kind="agy", argv_prefix=[path])
    for name in _GEMINI:
        path = which(name)
        if path:
            return GoogleCli(kind="gemini", argv_prefix=[path])
    bundle = npm_bundle()
    if bundle:
        node = which("node") or "node"
        return GoogleCli(kind="gemini", argv_prefix=[node, bundle])
    return None
