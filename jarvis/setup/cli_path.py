"""Put the ``jarvis`` commands on the user's PATH — Windows, macOS and Linux.

The one-line installer builds a private virtualenv inside the install folder,
so its console scripts (``jarvis``, ``personal-jarvis``, ...) start out
reachable only by their full path. This module makes them plain terminal
commands, per user and without administrator rights:

* **macOS / Linux** — a symlink per command in ``~/.local/bin`` (the XDG user
  bin directory). When that directory is not on ``PATH`` yet, one guarded
  block is appended to the profile of the user's login shell (zsh, bash, fish,
  or ``~/.profile``), the same way rustup and uv do it.
* **Windows** — a copy of each console-script launcher in ``<install>\\bin``,
  plus that folder on the per-user ``Path`` (``HKCU\\Environment``). A launcher
  embeds the absolute path of the venv interpreter, so the copy works from any
  directory. The folder holds ONLY our launchers: putting ``.venv\\Scripts``
  itself on ``Path`` would shadow the user's own ``python`` and ``pip``.

Everything is idempotent (a repeat install changes nothing) and conservative:
an unrelated command that already owns a name is kept, never overwritten.
:func:`remove_commands` undoes all of it for the uninstaller.

Stdlib only: ``install/installer.py`` imports this before the venv has any
third-party package, and ``jarvis --uninstall`` must not need one either.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

#: Every command the install exposes. ``personal-jarvis`` is an alias of
#: ``jarvis``; ``jarvisctl`` is the control CLI's historic name. There is no
#: ``personaljarvis``: on Windows it would be the same file as the branded
#: launcher ``PersonalJarvis.exe`` (``jarvis/ui/icon_utils.py``).
COMMAND_NAMES: tuple[str, ...] = ("jarvis", "personal-jarvis", "jarvisctl")

#: First line of the block written into a shell profile. Finding it means the
#: block is already there; removal deletes it together with the line after it.
PROFILE_MARKER = "# Personal Jarvis: command-line tools (jarvis, personal-jarvis)"

_POSIX_PROFILE_LINE = (
    'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; '
    '*) export PATH="$HOME/.local/bin:$PATH" ;; esac'
)
_FISH_PROFILE_LINE = (
    "contains -- $HOME/.local/bin $PATH; or set -gx PATH $HOME/.local/bin $PATH"
)

# Suffix for a Windows launcher that was still running when we replaced it.
# A running .exe cannot be overwritten, but it can be renamed; the next run
# deletes the leftover.
_STALE_SUFFIX = ".old"


@dataclass
class CliPathReport:
    """What :func:`install_commands` did, for the installer to print."""

    bin_dir: Path
    linked: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Profile file that received the PATH block, or the Windows registry
    #: value that received the bin folder; ``None`` when PATH already had it.
    path_updated: str | None = None
    #: True when the bin folder is on PATH only for NEW terminals.
    needs_new_terminal: bool = False
    #: Another command of the same name that will win over ours on PATH.
    shadowed_by: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #
def is_windows(platform_name: str | None = None) -> bool:
    return (platform_name or sys.platform) == "win32"


def bin_dir(root: Path, *, platform_name: str | None = None, home: Path | None = None) -> Path:
    """Where the PATH-visible commands live on this OS."""
    if is_windows(platform_name):
        return root / "bin"
    return (home or Path.home()) / ".local" / "bin"


def entry_point(root: Path, command: str, *, platform_name: str | None = None) -> Path:
    """The console script pip generated inside the install's venv."""
    if is_windows(platform_name):
        return root / ".venv" / "Scripts" / f"{command}.exe"
    return root / ".venv" / "bin" / command


def _path_entries(environ: Mapping[str, str], platform_name: str | None) -> list[str]:
    raw = environ.get("PATH") or environ.get("Path") or ""
    sep = ";" if is_windows(platform_name) else os.pathsep
    return [entry for entry in raw.split(sep) if entry]


def _same_dir(a: str, b: str, *, windows: bool) -> bool:
    def norm(value: str) -> str:
        value = os.path.expandvars(value).rstrip("\\/")
        return value.lower() if windows else value

    return norm(a) == norm(b)


def _on_path(directory: Path, environ: Mapping[str, str], platform_name: str | None) -> bool:
    windows = is_windows(platform_name)
    return any(
        _same_dir(entry, str(directory), windows=windows)
        for entry in _path_entries(environ, platform_name)
    )


# --------------------------------------------------------------------------- #
# Shell profiles (macOS / Linux)
# --------------------------------------------------------------------------- #
def profile_file(
    home: Path, *, login_shell: str | None, platform_name: str | None = None
) -> Path:
    """The profile the user's login shell reads for a new terminal.

    zsh (the macOS default) reads ``~/.zshrc``; bash reads ``~/.bash_profile``
    in a macOS Terminal (every window is a login shell) but ``~/.bashrc`` in a
    Linux terminal; fish gets its own ``conf.d`` snippet. Anything else falls
    back to ``~/.profile``, which every POSIX login shell reads.
    """
    name = Path(login_shell or "").name
    platform_name = platform_name or sys.platform
    if name == "zsh":
        return home / ".zshrc"
    if name == "bash":
        return home / (".bash_profile" if platform_name == "darwin" else ".bashrc")
    if name == "fish":
        return home / ".config" / "fish" / "conf.d" / "personal-jarvis.fish"
    return home / ".profile"


def _profile_block(path: Path) -> str:
    line = _FISH_PROFILE_LINE if path.suffix == ".fish" else _POSIX_PROFILE_LINE
    return f"{PROFILE_MARKER}\n{line}\n"


def add_profile_block(path: Path) -> bool:
    """Append the PATH block to ``path``. Returns False when already present."""
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    if PROFILE_MARKER in existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{prefix}\n{_profile_block(path)}")
    return True


def remove_profile_block(path: Path) -> bool:
    """Delete the block :func:`add_profile_block` wrote. False when absent."""
    try:
        # newline="" keeps CRLF profiles CRLF: only our two lines change.
        with path.open(encoding="utf-8", newline="") as handle:
            lines = handle.read().splitlines(keepends=True)
    except (FileNotFoundError, UnicodeDecodeError):
        return False
    kept: list[str] = []
    skip_next = False
    removed = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if line.rstrip("\r\n") == PROFILE_MARKER:
            removed = True
            skip_next = True
            # Drop the blank separator line add_profile_block put in front.
            if kept and not kept[-1].strip():
                kept.pop()
            continue
        kept.append(line)
    if removed:
        # Write beside, then swap: a crash mid-write must never leave the
        # user's shell profile truncated.
        temp = path.with_name(path.name + ".personal-jarvis.tmp")
        with temp.open("w", encoding="utf-8", newline="") as handle:
            handle.write("".join(kept))
        try:
            shutil.copymode(path, temp)
        except OSError:  # noqa: S110 - mode copy is cosmetic; content is what matters
            pass
        os.replace(temp, path)
    return removed


# --------------------------------------------------------------------------- #
# Windows per-user Path
# --------------------------------------------------------------------------- #
class UserPathStore:
    """Read and write the per-user ``Path`` (``HKCU\\Environment``).

    A small seam so tests drive the PATH logic with a fake on any OS. The real
    store exists only on Windows and is never instantiated elsewhere.
    """

    def read(self) -> tuple[str, int | None]:  # pragma: no cover - Windows only
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                value, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return "", None
        return str(value), int(kind)

    def write(self, value: str, kind: int | None) -> None:  # pragma: no cover - Windows only
        import winreg

        reg_kind = winreg.REG_EXPAND_SZ if kind is None else kind
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "Path", 0, reg_kind, value)
        _broadcast_environment_change()


def _broadcast_environment_change() -> None:  # pragma: no cover - Windows only
    """Tell Explorer the environment changed, so NEW terminals see the Path."""
    try:
        import ctypes
        from ctypes import wintypes

        result = wintypes.DWORD()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,  # HWND_BROADCAST
            0x001A,  # WM_SETTINGCHANGE
            0,
            "Environment",
            0x0002,  # SMTO_ABORTIFHUNG
            5000,
            ctypes.byref(result),
        )
    except Exception:  # noqa: BLE001, S110 - cosmetic: a sign-out also refreshes it
        pass


def add_to_user_path(directory: Path, store: UserPathStore) -> bool:
    """Append ``directory`` to the per-user Path. False when already there."""
    value, kind = store.read()
    entries = [entry for entry in value.split(";") if entry]
    if any(_same_dir(entry, str(directory), windows=True) for entry in entries):
        return False
    store.write(";".join([*entries, str(directory)]), kind)
    return True


def remove_from_user_path(directory: Path, store: UserPathStore) -> bool:
    """Drop ``directory`` from the per-user Path. False when it was absent."""
    value, kind = store.read()
    entries = [entry for entry in value.split(";") if entry]
    kept = [e for e in entries if not _same_dir(e, str(directory), windows=True)]
    if len(kept) == len(entries):
        return False
    store.write(";".join(kept), kind)
    return True


# --------------------------------------------------------------------------- #
# Install / remove
# --------------------------------------------------------------------------- #
def _copy_launcher(source: Path, target: Path) -> bool:
    """Copy a Windows launcher; True when the target changed.

    Identical bytes are left alone, so a repeat install never touches a
    launcher that is running right now (``jarvis update`` runs from one). A
    changed launcher that is running is renamed aside first, which Windows
    allows for a running ``.exe``.
    """
    data = source.read_bytes()
    try:
        if target.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    try:
        target.write_bytes(data)
    except PermissionError:
        stale = target.with_name(target.name + _STALE_SUFFIX)
        stale.unlink(missing_ok=True)
        target.rename(stale)
        try:
            target.write_bytes(data)
        except OSError:
            # Put the working launcher back rather than leave no command.
            target.unlink(missing_ok=True)
            stale.rename(target)
            raise
    return True


def _sweep_stale_launchers(directory: Path) -> None:
    for stale in directory.glob(f"*{_STALE_SUFFIX}"):
        try:
            stale.unlink()
        except OSError:  # noqa: S110 - still running; the next install removes it
            pass


def _link_points_into(link: Path, root: Path) -> bool:
    try:
        return link.is_symlink() and root.resolve() in link.resolve().parents
    except OSError:
        return False


def install_commands(
    root: Path,
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    login_shell: str | None = None,
    store: UserPathStore | None = None,
    which: Callable[..., str | None] = shutil.which,
    dry_run: bool = False,
) -> CliPathReport:
    """Make every :data:`COMMAND_NAMES` entry a terminal command."""
    platform_name = platform_name or sys.platform
    home = home or Path.home()
    environ = os.environ if environ is None else environ
    windows = is_windows(platform_name)
    target_dir = bin_dir(root, platform_name=platform_name, home=home)
    report = CliPathReport(bin_dir=target_dir)

    if not dry_run:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            report.errors.append(f"could not create {target_dir}: {exc}")
            return report
        if windows:
            _sweep_stale_launchers(target_dir)

    for command in COMMAND_NAMES:
        source = entry_point(root, command, platform_name=platform_name)
        if not source.is_file():
            report.missing.append(command)
            continue
        if dry_run:
            report.linked.append(command)
            continue
        try:
            if windows:
                _copy_launcher(source, target_dir / f"{command}.exe")
                report.linked.append(command)
                continue
            link = target_dir / command
            if link.is_symlink() and link.resolve() == source.resolve():
                report.linked.append(command)
                continue
            if link.exists() or link.is_symlink():
                report.kept.append(command)
                continue
            link.symlink_to(source)
            report.linked.append(command)
        except OSError as exc:
            report.errors.append(f"{command}: {exc}")

    if dry_run:
        return report

    try:
        if windows:
            if add_to_user_path(target_dir, store or UserPathStore()):
                report.path_updated = "HKCU\\Environment\\Path"
            report.needs_new_terminal = not _on_path(target_dir, environ, platform_name)
        elif not _on_path(target_dir, environ, platform_name):
            profile = profile_file(
                home,
                login_shell=login_shell if login_shell is not None else environ.get("SHELL"),
                platform_name=platform_name,
            )
            if add_profile_block(profile):
                report.path_updated = str(profile)
            report.needs_new_terminal = True
    except OSError as exc:
        report.errors.append(f"could not add {target_dir} to PATH: {exc}")

    # A command of the same name earlier on PATH wins over ours. Say so rather
    # than let `jarvis` silently start something else.
    if not report.needs_new_terminal:
        for command in report.linked:
            found = which(command, path=environ.get("PATH") or environ.get("Path"))
            if found and Path(found).parent.resolve() != target_dir.resolve():
                report.shadowed_by[command] = found
    return report


def remove_commands(
    root: Path,
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    store: UserPathStore | None = None,
) -> list[str]:
    """Undo :func:`install_commands`. Returns one line per removed item."""
    platform_name = platform_name or sys.platform
    home = home or Path.home()
    target_dir = bin_dir(root, platform_name=platform_name, home=home)
    removed: list[str] = []
    if is_windows(platform_name):
        if remove_from_user_path(target_dir, store or UserPathStore()):
            removed.append(f"{target_dir} removed from your Path")
        # The folder itself lives inside the install folder and goes with it.
        return removed
    for command in COMMAND_NAMES:
        link = target_dir / command
        if _link_points_into(link, root):
            link.unlink()
            removed.append(str(link))
    # Every profile add_profile_block can have chosen, whichever shell the
    # user runs today.
    profiles = {
        profile_file(home, login_shell=shell, platform_name=name)
        for shell in ("zsh", "bash", "fish", "sh")
        for name in ("darwin", "linux")
    }
    for path in sorted(profiles):
        if remove_profile_block(path):
            removed.append(f"PATH line in {path}")
    return removed


__all__ = [
    "COMMAND_NAMES",
    "PROFILE_MARKER",
    "CliPathReport",
    "UserPathStore",
    "add_profile_block",
    "add_to_user_path",
    "bin_dir",
    "entry_point",
    "install_commands",
    "profile_file",
    "remove_commands",
    "remove_from_user_path",
    "remove_profile_block",
]
