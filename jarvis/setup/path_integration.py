"""Make the ``jarvis`` terminal command work after install — on every OS.

The website's "Run it" section advertises ``jarvis`` / ``jarvis serve`` as THE
way to start the app, but pip installs the console scripts only into the
install venv (``~/.personal-jarvis/.venv/{Scripts,bin}/``), which is never on
PATH — so a fresh terminal answered "command not found" on every OS (field
report 2026-07-21). This module bridges that gap portably, without ever
putting the whole venv scripts dir on PATH (that would shadow the user's
``python`` / ``pip`` with the venv's copies):

1. **Shim dir** — ``~/.local/bin``, the cross-OS user-bin convention the
   running app already probes (``jarvis/core/path_augment.py``) and that
   other CLI installers (pipx, uv, Claude Code) use on all three OSes.

   - POSIX: each command is a symlink onto the venv console script (whose
     shebang pins the venv interpreter); where symlinks are unavailable a
     tiny ``exec`` wrapper script is written instead.
   - Windows: a tiny ``.cmd`` shim per command calls the venv ``.exe``.

2. **PATH persistence**

   - Windows: append ``%USERPROFILE%\\.local\\bin`` to the per-user registry
     PATH (``HKCU\\Environment``) and broadcast ``WM_SETTINGCHANGE`` so newly
     opened terminals pick it up without a re-login.
   - POSIX: when ``~/.local/bin`` is not on PATH, append ONE marker-guarded
     ``export PATH`` line to the login shell's rc file(s) (idempotent).

Everything here is best-effort and idempotent: any failure degrades to an
honest warning in the installer transcript, never a failed install. The
uninstaller removes only OUR shims (``remove_cli_shims``); the PATH entry and
rc line stay behind on purpose — ``~/.local/bin`` is a generic user dir shared
with other tools, and an entry for it is harmless without our shims.
"""
from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from pathlib import Path

# The user-facing console scripts from ``pyproject.toml [project.scripts]``.
# The internal review-pipeline binaries (jarvis-review-*) stay venv-only.
COMMANDS: tuple[str, ...] = ("jarvis", "jarvisctl", "jctl")

# Ownership marker embedded in every generated shim so re-runs may overwrite
# and the uninstaller knows what is safe to delete.
_MARKER = "managed by the Personal Jarvis installer"
# Marker comment guarding the PATH line appended to shell rc files.
PROFILE_MARKER = "# Added by the Personal Jarvis installer (puts `jarvis` on your PATH)"


@dataclass(frozen=True)
class PathIntegrationReport:
    """What ``ensure_cli_on_path`` actually achieved (never raises instead)."""

    bin_dir: Path
    installed: tuple[str, ...]
    path_updated: bool
    needs_new_terminal: bool
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return "jarvis" in self.installed


def user_bin_dir(home: Path | None = None) -> Path:
    """The cross-OS per-user bin dir the shims land in."""
    return (home or Path.home()) / ".local" / "bin"


def venv_scripts_dir(install_dir: Path, *, platform: str | None = None) -> Path:
    """Where pip put the console scripts inside the install venv."""
    plat = platform or sys.platform
    return install_dir / ".venv" / ("Scripts" if plat == "win32" else "bin")


# ------------------------------------------------------------------- shims
def _windows_shim_text(target_exe: Path) -> str:
    # %* forwards all arguments; the shim's exit code is the exe's exit code
    # because the call is the script's last (only) command.
    return f'@echo off\r\nrem {_MARKER} - do not edit\r\n"{target_exe}" %*\r\n'


def _posix_wrapper_text(target: Path) -> str:
    return f'#!/bin/sh\n# {_MARKER} - do not edit\nexec "{target}" "$@"\n'


def _shim_is_ours(shim: Path, install_dir: Path) -> bool:
    """True when ``shim`` may be overwritten/removed by us.

    Ours: a symlink pointing into ``install_dir``, a BROKEN symlink (a dead
    leftover no matter whose — replacing it can only heal), or a regular file
    carrying our marker AND referencing this install (so a shim belonging to a
    second Jarvis install in another dir is never touched).
    """
    try:
        if shim.is_symlink():
            raw = os.readlink(shim)
            if not shim.exists():  # broken link — safe to heal
                return True
            try:
                return shim.resolve().is_relative_to(install_dir.resolve())
            except OSError:
                return str(install_dir) in raw
        content = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _MARKER in content and str(install_dir) in content


def _install_shim(
    shim: Path, target: Path, install_dir: Path, *, platform: str
) -> str | None:
    """Create/refresh one shim. Returns a warning string on failure, else None."""
    if shim.exists() or shim.is_symlink():
        if not _shim_is_ours(shim, install_dir):
            return (
                f"{shim.name}: a different program already owns {shim} — "
                f"left untouched; run {target} directly instead"
            )
        try:
            shim.unlink()
        except OSError as exc:
            return f"{shim.name}: could not replace the old shim ({exc})"
    try:
        if platform == "win32":
            shim.write_text(_windows_shim_text(target), encoding="utf-8", newline="")
            return None
        try:
            shim.symlink_to(target)
        except OSError:
            # Filesystems / hosts without symlink support: exec wrapper.
            shim.write_text(_posix_wrapper_text(target), encoding="utf-8")
            shim.chmod(0o755)
        return None
    except OSError as exc:
        return f"{shim.name}: could not create the shim ({exc})"


def _shim_path(bin_dir: Path, command: str, *, platform: str) -> Path:
    return bin_dir / (f"{command}.cmd" if platform == "win32" else command)


def _script_path(scripts: Path, command: str, *, platform: str) -> Path:
    return scripts / (f"{command}.exe" if platform == "win32" else command)


# ------------------------------------------------------- Windows PATH (HKCU)
def _norm_windows_path(entry: str, expand: Callable[[str], str]) -> str:
    """Normalize one PATH component for comparison (windows semantics,
    deterministic on every host so the logic stays testable off-Windows)."""
    return expand(entry).strip().replace("/", "\\").rstrip("\\").lower()


def merge_windows_path_entry(
    current: str | None,
    entry: str,
    *,
    expand: Callable[[str], str] = os.path.expandvars,
) -> str | None:
    """The new user-PATH value with ``entry`` appended, or None if present.

    Comparison expands ``%VAR%`` references (the stored value is often
    ``%USERPROFILE%\\...``) and is case/trailing-slash insensitive.
    """
    parts = [p for p in (current or "").split(";") if p.strip()]
    wanted = _norm_windows_path(entry, expand)
    if any(_norm_windows_path(p, expand) == wanted for p in parts):
        return None
    return ";".join([*parts, entry])


def _read_windows_user_path() -> tuple[str | None, int]:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return None, winreg.REG_EXPAND_SZ
    return value, kind


def _write_windows_user_path(value: str, kind: int) -> None:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, "Path", 0, kind, value)


def _broadcast_environment_change() -> None:
    """Tell running shells' parents (Explorer, Terminal) the PATH changed, so
    terminals opened from NOW on inherit it without a re-login."""
    import ctypes

    hwnd_broadcast, wm_settingchange, smto_abortifhung = 0xFFFF, 0x001A, 0x0002
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
        hwnd_broadcast, wm_settingchange, 0, "Environment",
        smto_abortifhung, 5000, ctypes.byref(result),
    )


def _ensure_windows_path(bin_dir: Path, warnings: list[str]) -> tuple[bool, bool]:
    """Persist ``bin_dir`` on the per-user PATH. Returns (updated, new_terminal)."""
    entry = str(bin_dir)
    try:
        current, kind = _read_windows_user_path()
    except OSError as exc:
        warnings.append(
            f"could not read the user PATH ({exc}) — add {entry} to PATH manually"
        )
        return False, True
    merged = merge_windows_path_entry(current, entry)
    if merged is None:
        return False, False  # already persisted (update run) — nothing to do
    try:
        _write_windows_user_path(merged, kind)
    except OSError as exc:
        warnings.append(
            f"could not update the user PATH ({exc}) — add {entry} to PATH manually"
        )
        return False, True
    with contextlib.suppress(Exception):
        # Cosmetic only: without the broadcast, a re-login applies the PATH.
        _broadcast_environment_change()
    return True, True


# ------------------------------------------------------- POSIX PATH (rc files)
def _posix_profile_files(home: Path, shell: str) -> list[Path]:
    """The rc files that receive the PATH line.

    Existing files are always edited; the LOGIN shell's own rc is created when
    missing so the line is guaranteed to land somewhere (a fresh macOS user
    has no ``~/.zshrc`` at all). ``~/.profile`` is the sh/dash catch-all.
    """
    files: list[Path] = []
    zshrc, bashrc = home / ".zshrc", home / ".bashrc"
    if shell == "zsh" or zshrc.exists():
        files.append(zshrc)
    if shell == "bash" or bashrc.exists():
        files.append(bashrc)
    bash_profile = home / ".bash_profile"  # macOS bash login shells skip ~/.profile
    if bash_profile.exists():
        files.append(bash_profile)
    profile = home / ".profile"
    if profile.exists() or not files:
        files.append(profile)
    fish = home / ".config" / "fish" / "config.fish"
    if fish.exists():
        files.append(fish)
    return files


def _profile_block(rc_file: Path) -> str:
    if rc_file.name == "config.fish":
        line = 'fish_add_path -g "$HOME/.local/bin"'
    else:
        line = 'export PATH="$HOME/.local/bin:$PATH"'
    return f"\n{PROFILE_MARKER}\n{line}\n"


def _path_contains_dir(path_value: str, bin_dir: Path) -> bool:
    wanted = os.path.normcase(os.path.normpath(str(bin_dir)))
    for part in path_value.split(os.pathsep):
        if not part.strip():
            continue
        expanded = os.path.expanduser(os.path.expandvars(part.strip()))
        if os.path.normcase(os.path.normpath(expanded)) == wanted:
            return True
    return False


def _ensure_posix_path(
    bin_dir: Path,
    home: Path,
    environ: MutableMapping[str, str],
    warnings: list[str],
) -> tuple[bool, bool]:
    """Persist ``bin_dir`` on PATH via rc files. Returns (updated, new_terminal)."""
    if _path_contains_dir(environ.get("PATH", ""), bin_dir):
        return False, False  # the shell already provides it (most distros do)
    shell = os.path.basename(environ.get("SHELL", "") or "")
    wrote_any = False
    for rc_file in _posix_profile_files(home, shell):
        try:
            existing = rc_file.read_text(encoding="utf-8") if rc_file.exists() else ""
            if PROFILE_MARKER in existing:
                continue
            rc_file.parent.mkdir(parents=True, exist_ok=True)
            with rc_file.open("a", encoding="utf-8") as handle:
                handle.write(_profile_block(rc_file))
            wrote_any = True
        except OSError as exc:
            warnings.append(f"could not update {rc_file.name} ({exc})")
    if not wrote_any and not any(w.startswith("could not update") for w in warnings):
        # Marker already present everywhere: persisted by an earlier run, the
        # current shell just has not sourced it yet.
        return False, True
    if not wrote_any:
        warnings.append(
            f'add ~/.local/bin to PATH yourself: export PATH="{bin_dir}:$PATH"'
        )
        return False, True
    return True, True


# ---------------------------------------------------------------- entrypoints
def ensure_cli_on_path(
    install_dir: Path,
    *,
    platform: str | None = None,
    home: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> PathIntegrationReport:
    """Link the CLI commands into ``~/.local/bin`` and persist it on PATH.

    Best-effort on every axis; the report says honestly what happened. The
    keyword overrides exist for tests — production callers pass only
    ``install_dir``.
    """
    plat = platform or sys.platform
    home_dir = home or Path.home()
    env = os.environ if environ is None else environ
    warnings: list[str] = []
    bin_dir = user_bin_dir(home_dir)

    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return PathIntegrationReport(
            bin_dir=bin_dir, installed=(), path_updated=False,
            needs_new_terminal=False,
            warnings=(f"could not create {bin_dir} ({exc})",),
        )

    scripts = venv_scripts_dir(install_dir, platform=plat)
    installed: list[str] = []
    for command in COMMANDS:
        target = _script_path(scripts, command, platform=plat)
        if not target.exists():
            warnings.append(f"{command}: not found in the install venv ({target})")
            continue
        warning = _install_shim(
            _shim_path(bin_dir, command, platform=plat), target, install_dir,
            platform=plat,
        )
        if warning is None:
            installed.append(command)
        else:
            warnings.append(warning)

    path_updated = needs_new_terminal = False
    if installed:
        if plat == "win32":
            path_updated, needs_new_terminal = _ensure_windows_path(bin_dir, warnings)
        else:
            path_updated, needs_new_terminal = _ensure_posix_path(
                bin_dir, home_dir, env, warnings
            )
        # The current process (and everything it spawns, e.g. the launched
        # app) should resolve the commands immediately as well.
        if not _path_contains_dir(env.get("PATH", ""), bin_dir):
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    return PathIntegrationReport(
        bin_dir=bin_dir,
        installed=tuple(installed),
        path_updated=path_updated,
        needs_new_terminal=needs_new_terminal,
        warnings=tuple(warnings),
    )


def list_cli_shims(
    install_dir: Path, *, platform: str | None = None, home: Path | None = None
) -> list[Path]:
    """The shims in ``~/.local/bin`` owned by THIS install (for plans/uninstall)."""
    plat = platform or sys.platform
    bin_dir = user_bin_dir(home)
    found: list[Path] = []
    for command in COMMANDS:
        shim = _shim_path(bin_dir, command, platform=plat)
        try:
            if (shim.exists() or shim.is_symlink()) and _shim_is_ours(shim, install_dir):
                found.append(shim)
        except OSError:  # noqa: PERF203 — a single unreadable shim must not abort
            continue
    return found


def remove_cli_shims(
    install_dir: Path, *, platform: str | None = None, home: Path | None = None
) -> list[Path]:
    """Delete this install's shims from ``~/.local/bin``; return what was removed.

    Deliberately leaves the PATH entry / rc line behind: ``~/.local/bin`` is a
    generic user dir shared with other tools (pipx, uv, Claude Code), and an
    entry for it without our shims is harmless.
    """
    removed: list[Path] = []
    for shim in list_cli_shims(install_dir, platform=platform, home=home):
        try:
            shim.unlink()
            removed.append(shim)
        except OSError:  # noqa: PERF203 — best-effort; report only what IS gone
            continue
    return removed


__all__ = [
    "COMMANDS",
    "PROFILE_MARKER",
    "PathIntegrationReport",
    "ensure_cli_on_path",
    "list_cli_shims",
    "merge_windows_path_entry",
    "remove_cli_shims",
    "user_bin_dir",
    "venv_scripts_dir",
]
