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
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

# Binary name + platform shim variants. ``shutil.which`` honors PATHEXT, so the
# bare name usually resolves; the explicit variants are belt-and-suspenders.
_AGY: tuple[str, ...] = ("agy", "agy.exe")
_GEMINI: tuple[str, ...] = ("gemini", "gemini.cmd", "gemini.exe")

# The bundle path inside any npm-global node_modules root.
_BUNDLE_REL = os.path.join("@google", "gemini-cli", "bundle", "gemini.js")


@dataclass(frozen=True)
class GoogleCli:
    """A resolved official Google CLI and the argv prefix used to invoke it."""

    kind: str  # "agy" | "gemini"
    argv_prefix: list[str] = field(default_factory=list)
    version: str | None = None


def _npm_global_roots() -> list[str]:
    """Well-known npm-global ``node_modules`` roots, platform-aware (no subprocess).

    On Windows the global root is ``%APPDATA%\\npm\\node_modules``; on POSIX it is
    one of the standard prefixes. Probing these directly avoids calling ``npm``,
    which on Windows is a ``.cmd`` shim that ``subprocess`` cannot launch without
    a shell (it would raise ``FileNotFoundError`` and we'd find nothing).
    """
    roots: list[str] = []
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(os.path.join(appdata, "npm", "node_modules"))
    else:
        home = os.path.expanduser("~")
        roots += [
            "/usr/local/lib/node_modules",
            "/usr/lib/node_modules",
            os.path.join(home, ".npm-global", "lib", "node_modules"),
        ]
    return roots


def _default_npm_bundle(
    *,
    roots: list[str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    isfile: Callable[[str], bool] = os.path.isfile,
) -> str | None:
    """Path to the npm-global Gemini bundle, or ``None``. Best-effort, never raises.

    Covers this machine's broken-shim case: ``@google/gemini-cli`` is installed
    but its ``gemini``/``gemini.cmd`` PATH shims are stale npm temp files, so
    ``shutil.which`` misses it. We can still drive ``node <bundle>/gemini.js``.

    Probes the well-known roots directly; additionally consults ``npm root -g``
    only where ``npm`` resolves to a *real* executable (POSIX), never a Windows
    ``.cmd``/``.ps1`` shim that ``subprocess`` cannot run without a shell.
    """
    candidates = list(roots) if roots is not None else _npm_global_roots()
    npm = which("npm")
    if npm and not npm.lower().endswith((".cmd", ".ps1", ".bat")):
        try:
            out = subprocess.run(
                [npm, "root", "-g"],
                capture_output=True,
                text=True,
                timeout=5.0,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            ).stdout.strip()
            if out:
                candidates.append(out)
        except (OSError, subprocess.SubprocessError):
            pass
    for root in candidates:
        bundle = os.path.join(root, _BUNDLE_REL)
        if isfile(bundle):
            return bundle
    return None


def _agy_winget_roots() -> list[str]:
    """Well-known locations where the winget ``Google.AntigravityCLI`` package and
    its PATH shim drop ``agy.exe`` (Windows). winget appends ``WinGet\\Links`` to
    the user PATH only after a shell restart, so the running process / a fresh
    subprocess does not see ``agy`` on PATH yet — probe the install dirs directly.
    """
    roots: list[str] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "Microsoft", "WinGet", "Links"))
        pkgs = os.path.join(local, "Microsoft", "WinGet", "Packages")
        with suppress(OSError):
            for entry in os.listdir(pkgs):
                if entry.startswith("Google.AntigravityCLI"):
                    roots.append(os.path.join(pkgs, entry))
    return roots


def _default_agy_path(
    *,
    roots: list[str] | None = None,
    isfile: Callable[[str], bool] = os.path.isfile,
) -> str | None:
    """Path to a winget-installed ``agy.exe``, or ``None``. Best-effort, never raises."""
    for root in roots if roots is not None else _agy_winget_roots():
        cand = os.path.join(root, "agy.exe")
        if isfile(cand):
            return cand
    return None


def resolve_google_cli(
    *,
    which: Callable[[str], str | None] = shutil.which,
    npm_bundle: Callable[[], str | None] = _default_npm_bundle,
    agy_path: Callable[[], str | None] = _default_agy_path,
) -> GoogleCli | None:
    """Resolve the preferred official Google CLI. ``which``/``npm_bundle``/
    ``agy_path`` are injectable seams for tests. Order: Antigravity ``agy`` (on
    PATH, then its winget install dir) > Gemini CLI (on PATH, then npm bundle)."""
    for name in _AGY:
        path = which(name)
        if path:
            return GoogleCli(kind="agy", argv_prefix=[path])
    agy = agy_path()
    if agy:
        return GoogleCli(kind="agy", argv_prefix=[agy])
    for name in _GEMINI:
        path = which(name)
        if path:
            return GoogleCli(kind="gemini", argv_prefix=[path])
    bundle = npm_bundle()
    if bundle:
        node = which("node") or "node"
        return GoogleCli(kind="gemini", argv_prefix=[node, bundle])
    return None
