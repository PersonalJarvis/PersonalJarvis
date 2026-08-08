"""Augment the process PATH with well-known CLI install locations.

GUI-launched processes do not inherit the user's shell PATH: on macOS an app
started from Finder/Dock/launchd gets the minimal ``/usr/bin:/bin:/usr/sbin:
/sbin`` (no Homebrew, no npm-global, no ``~/.local/bin``), and on Windows a
fresh installer can update only the registry PATH or place a binary in an app-
specific user directory. Already-running processes see neither change. Every
``shutil.which``-based CLI probe (claude / codex / agy / gemini / node / npm,
the CLI-tools catalog prober, worker spawns) then reports a perfectly-installed
binary as "not installed" — the 2026-07-18 Mac-test-machine symptom: Claude
Code installed via the shell, the desktop app insisting it is missing.

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
import shutil
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
    node_dirs: set[str] = set()
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        program_files = os.environ.get(var)
        if not program_files:
            continue
        # The official Node.js Windows installer writes this directory to the
        # machine PATH. A running desktop process keeps its old PATH, though,
        # so a freshly installed npm Codex shim is found while its `node.exe`
        # runtime is not. ProgramW6432 keeps the 64-bit install visible even
        # when Jarvis itself runs as a 32-bit process.
        candidate = os.path.join(program_files, "nodejs")
        key = os.path.normcase(os.path.normpath(candidate))
        if key not in node_dirs:
            node_dirs.add(key)
            dirs.append(candidate)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        # User-scoped Node installers commonly use this UAC-free location.
        dirs.append(os.path.join(local, "Programs", "nodejs"))
        # The official Antigravity PowerShell/CMD installer uses this directory.
        dirs.append(os.path.join(local, "agy", "bin"))
        # winget's shim dir lands on the *registry* PATH only — a running
        # process (and every subprocess it spawns) misses fresh installs.
        dirs.append(os.path.join(local, "Microsoft", "WinGet", "Links"))
        # Ollama's per-user installer (OllamaSetup.exe / winget) writes here
        # and appends it to the registry PATH only — a Jarvis that just
        # installed Ollama would otherwise not find the binary it installed.
        dirs.append(os.path.join(local, "Programs", "Ollama"))
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


def _registered_agent_dirs() -> list[str]:
    """Install dirs contributed by registered coding-CLI entries.

    A CLI whose own installer drops the binary somewhere the platform lists are
    not going to guess (``~/.local/bin`` on a GUI-launched macOS process, for
    one) says so on its registry entry rather than earning a line in the
    platform tables here — which would put product knowledge in a module that
    has no business holding any.

    Failures are swallowed: PATH augmentation is best-effort by contract, and a
    registry that cannot be imported must not take the whole lookup with it.
    """
    try:
        from jarvis.workspace import agents as workspace_agents

        return [
            os.path.expanduser(d)
            for agent in workspace_agents.coding_agents()
            for d in agent.extra_path_dirs
        ]
    except Exception:  # noqa: BLE001 - see docstring
        return []


def candidate_dirs() -> list[str]:
    """The platform's well-known CLI install dirs (existing or not)."""
    base = _windows_candidates() if sys.platform == "win32" else _posix_candidates()
    return [*base, *_registered_agent_dirs()]


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


def resolve_node_executable() -> str | None:
    """Return an absolute Node.js executable, including stale-GUI-PATH hosts."""
    ensure_cli_paths()
    found = shutil.which("node") or shutil.which("node.exe")
    if found:
        return found
    if sys.platform == "win32":
        for directory in _windows_candidates():
            candidate = Path(directory) / "node.exe"
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
    return None


__all__ = ["candidate_dirs", "ensure_cli_paths", "resolve_node_executable"]
