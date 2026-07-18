"""Augment the process PATH with well-known CLI install locations.

GUI-launched processes do not inherit the user's shell PATH: on macOS an app
started from Finder/Dock/launchd gets the minimal ``/usr/bin:/bin:/usr/sbin:
/sbin`` (no Homebrew, no npm-global, no ``~/.local/bin``), and on Windows a
winget install appends its ``WinGet\\Links`` shim dir to the *registry* PATH,
which already-running processes never see. Every ``shutil.which``-based CLI
probe (claude / codex / gemini / node / npm, the CLI-tools catalog prober,
worker spawns) then reports a perfectly-installed binary as "not installed" —
the 2026-07-18 Mac-test-machine symptom: Claude Code installed via the shell,
the desktop app insisting it is missing.

``ensure_cli_paths()`` appends the well-known, actually-existing install dirs
to ``os.environ["PATH"]``. Existing entries keep priority — only *missing*
dirs are appended, so a user-managed PATH always wins and the call is
idempotent. Pure ``stat()`` probes, no subprocess: boot-budget-safe (AP-26).

Cross-platform per CLOUD.md Rule #1: the candidate list covers macOS
(Intel + Apple Silicon Homebrew), Linux (incl. headless), and Windows; a dir
that does not exist on this host is simply skipped.
"""
from __future__ import annotations

import glob
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _posix_candidates() -> list[str]:
    home = Path.home()
    dirs: list[str] = [
        # Homebrew (Intel Macs + Linuxbrew default) and the npm default prefix.
        "/usr/local/bin",
        # Homebrew on Apple Silicon.
        "/opt/homebrew/bin",
        # MacPorts.
        "/opt/local/bin",
        # Native installers (Claude Code, pipx, uv) and the XDG user bin dir.
        str(home / ".local" / "bin"),
        # Claude Code's npm-local migration target (`claude install`).
        str(home / ".claude" / "local"),
        # A user-configured npm prefix (`npm config set prefix ~/.npm-global`).
        str(home / ".npm-global" / "bin"),
        # Volta pins a single stable shim dir.
        str(home / ".volta" / "bin"),
    ]
    if sys.platform.startswith("linux"):
        dirs.append("/snap/bin")
    # nvm/fnm keep per-node-version bin dirs; a GUI process sees none of them
    # because the shims live in shell init files. Newest-first is best-effort
    # (lexicographic, same trade-off as jarvis.google_cli.resolver) — for a
    # global CLI install ANY hit beats "not installed".
    for pattern in (
        str(home / ".nvm" / "versions" / "node" / "*" / "bin"),
        str(
            home / ".local" / "share" / "fnm" / "node-versions" / "*"
            / "installation" / "bin"
        ),
    ):
        dirs.extend(sorted(glob.glob(pattern), reverse=True))
    return dirs


def _windows_candidates() -> list[str]:
    home = Path.home()
    dirs: list[str] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        # winget's shim dir lands on the *registry* PATH only — a running
        # process (and every subprocess it spawns) misses fresh installs.
        dirs.append(os.path.join(local, "Microsoft", "WinGet", "Links"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(os.path.join(appdata, "npm"))
    # Claude Code's NATIVE installer (install.ps1) drops the binary into
    # %USERPROFILE%\.local\bin, and `claude install` migrates npm setups to
    # %USERPROFILE%\.claude\local — neither is on a default Windows PATH, so
    # the desktop app reported a working terminal `claude` as "not installed"
    # (Windows test-machine report 2026-07-18; same class as the Mac case in
    # the module docstring).
    dirs.append(str(home / ".local" / "bin"))
    dirs.append(str(home / ".claude" / "local"))
    return dirs


def candidate_dirs() -> list[str]:
    """The platform's well-known CLI install dirs (existing or not)."""
    return _windows_candidates() if sys.platform == "win32" else _posix_candidates()


def ensure_cli_paths() -> list[str]:
    """Append missing well-known CLI dirs to ``PATH``; return what was added.

    Idempotent: dirs already on PATH (case-normalized) are never re-added, so
    repeated calls — and a PATH the user already curated — are both safe.
    """
    current = os.environ.get("PATH", "")
    seen = {
        os.path.normcase(os.path.normpath(p))
        for p in current.split(os.pathsep)
        if p
    }
    added: list[str] = []
    for cand in candidate_dirs():
        try:
            if not os.path.isdir(cand):
                continue
        except OSError:
            continue
        key = os.path.normcase(os.path.normpath(cand))
        if key in seen:
            continue
        seen.add(key)
        added.append(cand)
    if added:
        joined = os.pathsep.join(added)
        os.environ["PATH"] = f"{current}{os.pathsep}{joined}" if current else joined
        log.info("PATH augmented with %d CLI install dir(s): %s", len(added), added)
    return added


__all__ = ["candidate_dirs", "ensure_cli_paths"]
