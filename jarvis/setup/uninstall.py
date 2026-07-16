"""Personal Jarvis uninstaller — remove a local install cleanly for a re-test.

A download leaves FOUR things on a machine, and a plain folder-delete only
gets the first:

1. **The install folder** (``~/.personal-jarvis``) — code, the Python venv,
   ``jarvis.toml``, ``data/`` and the ``.setup-complete`` marker.
2. **Desktop-shell registration** (Windows Start menu + Installed Apps, a
   macOS ``.app`` bundle, or a Linux application-menu entry).
3. **A login-autostart entry** next to it (a Windows logon task / a macOS
   ``LaunchAgent`` / a Linux XDG ``.desktop``) — survives a folder delete and
   then points at nothing.
4. **API keys in the OS keyring** (service ``personal-jarvis`` — Windows
   Credential Manager / macOS Keychain / Linux Secret Service) — survive a
   folder delete, so a fresh install would show them as "already set".

This module removes all four (with an explicit confirmation, a ``--dry-run``
preview and per-item ``--keep-*`` opt-outs) so "download → test → wipe → re-test"
is one command. It is intentionally cross-platform and has **no** heavy imports,
so it runs on a headless VPS as happily as on a laptop.

Self-delete note (Windows): when invoked as ``python -m jarvis --uninstall`` the
running interpreter lives INSIDE the folder we must delete, and Windows locks a
running ``.exe``/loaded ``.dll``. We can't ``rmtree`` our own venv there, so on
Windows we hand the final folder removal to a tiny detached batch that waits for
this process to exit and then deletes the tree. On POSIX the unlink succeeds
while we run (open handles keep working from memory), so we delete directly. The
``install/uninstall.ps1`` / ``install/uninstall.sh`` bootstraps sidestep the lock
entirely: they pass ``--keep-folder`` and remove the tree themselves from OUTSIDE
the venv.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.theme import Theme

from jarvis.core import config as cfg
from jarvis.setup.wizard import SECRETS

# Same Charcoal + Gold palette as install/installer.py and the first-run wizard,
# so uninstalling reads as part of the same on-brand experience. Rich strips
# color on a non-TTY (headless/CI/piped), so these calls degrade to plain text.
_THEME = Theme(
    {
        "brand": "#e7c46e",
        "brand.bold": "bold #e7c46e",
        "ok": "#7ac88c",
        "muted": "#8c8c8c",
        "bad": "#e07a6e",
    }
)
_console = Console(theme=_THEME, highlight=False)


@dataclass(slots=True, frozen=True)
class UninstallPlan:
    """What an uninstall WOULD remove — computed without any side effect."""

    install_dir: Path
    is_jarvis_install: bool
    autostart_supported: bool
    autostart_entry: str | None
    keyring_keys: list[str] = field(default_factory=list)

    @property
    def config_file(self) -> Path:
        return self.install_dir / "jarvis.toml"

    @property
    def data_dir(self) -> Path:
        return self.install_dir / "data"


# ---------------------------------------------------------------- discovery
def _looks_like_jarvis_install(path: Path) -> bool:
    """Safety guard: only ever touch a directory that really is a Jarvis tree.

    ``PROJECT_ROOT`` is derived from this package's own ``__file__``, so under
    normal use it is always the real install root. This guard is the
    belt-and-suspenders that stops a mis-set path (or a future caller) from
    handing ``rmtree`` an unrelated directory."""
    try:
        return (path / "jarvis").is_dir() and (path / "pyproject.toml").is_file()
    except OSError:
        return False


def _keyring_keys_present() -> list[str]:
    """Which of Jarvis's known secret slots are actually stored in the keyring.

    Prefers a direct keyring probe (so an ENV-provided key is NOT mistaken for a
    stored one — we never delete ENV vars, they aren't ours). Falls back to the
    general getter only if the keyring module is unavailable."""
    cfg._ensure_keyring_backend()
    probe = None
    try:
        import keyring

        def probe(key: str) -> str | None:
            return keyring.get_password(cfg.KEYRING_SERVICE, key)
    except Exception:  # noqa: BLE001 — no keyring module → fall back to the getter
        def probe(key: str) -> str | None:
            return cfg.get_secret(key)

    present: list[str] = []
    for spec in SECRETS:
        try:
            if probe(spec.key):
                present.append(spec.key)
        except Exception:  # noqa: BLE001, S112 — a single unreadable slot must not abort
            continue
    return present


def _autostart_state() -> tuple[bool, str | None]:
    """(supported, entry_path) for the current install's login-autostart entry.

    Best-effort and never raises — an autostart probe failure must not block the
    uninstall (the folder + keys are the important removals)."""
    try:
        from jarvis.autostart import make_autostart_manager, resolve_launch_spec
        from jarvis.platform.capabilities import detect_capabilities

        manager = make_autostart_manager(detect_capabilities())
        status = manager.status(resolve_launch_spec(None))
        return status.supported, (status.entry_path if status.installed else None)
    except Exception:  # noqa: BLE001
        return False, None


def build_plan() -> UninstallPlan:
    """Inspect the machine and report what an uninstall would remove. Pure."""
    install_dir = Path(cfg.PROJECT_ROOT).resolve()
    supported, entry = _autostart_state()
    return UninstallPlan(
        install_dir=install_dir,
        is_jarvis_install=_looks_like_jarvis_install(install_dir),
        autostart_supported=supported,
        autostart_entry=entry,
        keyring_keys=_keyring_keys_present(),
    )


# ---------------------------------------------------------------- presentation
def _print_plan(plan: UninstallPlan, *, keep_keys: bool, keep_folder: bool) -> None:
    _console.print()
    _console.print(" [brand.bold]Uninstall Personal Jarvis[/]")
    lines = [
        "[bad]This removes Jarvis from THIS machine.[/] "
        "It does not touch your accounts or anything you created elsewhere.\n",
    ]
    if not keep_folder:
        lines.append(
            f"[brand]•[/] Delete the install folder:\n"
            f"    [muted]{escape(str(plan.install_dir))}[/]"
        )
        lines.append("    [muted](code, the Python environment, jarvis.toml, and data folder)[/]")
    if plan.autostart_entry:
        lines.append(
            f"[brand]•[/] Remove the login-autostart entry:\n"
            f"    [muted]{escape(plan.autostart_entry)}[/]"
        )
    else:
        lines.append("[brand]•[/] Login autostart: [muted]nothing to remove[/]")
    lines.append(
        "[brand]•[/] Remove the operating-system app launcher and registration"
    )
    if not keep_keys:
        if plan.keyring_keys:
            lines.append(
                f"[brand]•[/] Delete [brand.bold]{len(plan.keyring_keys)}[/] saved API key(s) "
                "from your system keychain"
            )
        else:
            lines.append("[brand]•[/] Saved API keys: [muted]none found in the keychain[/]")
    _console.print(Panel("\n".join(lines), border_style="brand", padding=(1, 2)))


def _confirm() -> bool:
    """Fail-closed confirmation — the user must type 'yes' to proceed."""
    try:
        answer = input("  Type 'yes' to remove Jarvis (anything else cancels): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("yes", "y")


# ---------------------------------------------------------------- removal steps
def _remove_desktop_registration() -> None:
    try:
        from jarvis.setup.desktop_integration import remove_desktop_integration

        report = remove_desktop_integration()
        if report.ok:
            _console.print("    [ok]→ desktop app registration removed.[/]")
        else:
            detail = "; ".join(report.warnings)
            _console.print(
                f"    [bad]⚠ desktop app registration cleanup was incomplete: "
                f"{escape(detail)}[/]"
            )
    except Exception as exc:  # noqa: BLE001 - never abort uninstall on shell cleanup
        _console.print(
            f"    [bad]⚠ could not remove desktop app registration: "
            f"{escape(str(exc))}[/]"
        )


def _remove_autostart() -> None:
    try:
        from jarvis.autostart import make_autostart_manager
        from jarvis.platform.capabilities import detect_capabilities

        manager = make_autostart_manager(detect_capabilities())
        # interactive=True: on Windows removing the logon task may show one UAC
        # prompt; macOS/Linux ignore the flag (per-user entry, no elevation).
        status = manager.uninstall(interactive=True)
        if status.supported:
            _console.print("    [ok]→ login-autostart entry removed.[/]")
        else:
            _console.print("    [muted]→ login autostart not supported here — skipped.[/]")
    except Exception as exc:  # noqa: BLE001 — never abort the uninstall on this
        _console.print(f"    [bad]⚠ could not remove the autostart entry: {escape(str(exc))}[/]")


def _remove_keys(keys: list[str]) -> int:
    deleted = 0
    for key in keys:
        if cfg.delete_secret(key):
            deleted += 1
    if deleted:
        _console.print(f"    [ok]→ removed {deleted} saved key(s) from the keychain.[/]")
    elif keys:
        _console.print("    [muted]→ no keychain keys were removed.[/]")
    return deleted


def _running_inside(path: Path) -> bool:
    """True when the current interpreter lives inside ``path`` (self-hosted).

    On Windows this means the venv ``.exe``/DLLs are locked and we cannot delete
    the tree from within our own process."""
    try:
        exe = Path(sys.executable).resolve()
        path = path.resolve()
        return path == exe or path in exe.parents
    except OSError:
        return False


def _spawn_windows_self_deleter(target: Path) -> None:
    """Hand the folder removal to a detached batch that waits for us to exit.

    Uses ``ping`` (not ``timeout``) for its delays because ``timeout`` needs a
    console this detached process does not have. The batch retries until the
    tree is gone, then deletes itself. Spawned with CREATE_NO_WINDOW so no
    console flashes (AP-1) and DETACHED so it outlives this process."""
    pid = os.getpid()
    bat = Path(tempfile.gettempdir()) / f"jarvis_uninstall_{pid}.bat"
    script = textwrap.dedent(
        f"""\
        @echo off
        :waitloop
        tasklist /FI "PID eq {pid}" | find "{pid}" >nul
        if not errorlevel 1 (
            ping -n 2 127.0.0.1 >nul
            goto waitloop
        )
        :delloop
        rmdir /s /q "{target}" 2>nul
        if exist "{target}" (
            ping -n 2 127.0.0.1 >nul
            goto delloop
        )
        del "%~f0" >nul 2>&1
        """
    )
    bat.write_text(script, encoding="utf-8")
    creationflags = 0
    # DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP — the deleter
    # must survive our exit and never pop a window.
    for name in ("DETACHED_PROCESS", "CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= getattr(subprocess, name, 0)
    subprocess.Popen(  # noqa: S603 — fixed cmd, no shell, self-authored batch
        ["cmd", "/c", str(bat)],
        creationflags=creationflags,
        close_fds=True,
    )


def _remove_folder(install_dir: Path) -> bool:
    """Remove the install tree. Returns True when it is gone (or scheduled to be).

    POSIX (and Windows when NOT self-hosted): direct ``rmtree``. Windows +
    self-hosted: schedule a detached deleter and report that it will finish a
    moment after we exit."""
    if sys.platform == "win32" and _running_inside(install_dir):
        try:
            _spawn_windows_self_deleter(install_dir)
            _console.print(
                "    [ok]→ the install folder will be removed a moment after this "
                "window closes.[/]"
            )
            return True
        except Exception as exc:  # noqa: BLE001
            _console.print(
                f"    [bad]⚠ could not schedule folder removal: {escape(str(exc))}[/]\n"
                f"    [muted]Delete it yourself: {escape(str(install_dir))}[/]"
            )
            return False
    try:
        shutil.rmtree(install_dir)
        _console.print("    [ok]→ install folder deleted.[/]")
        return True
    except Exception as exc:  # noqa: BLE001
        _console.print(
            f"    [bad]⚠ could not delete the folder: {escape(str(exc))}[/]\n"
            f"    [muted]Delete it yourself: {escape(str(install_dir))}[/]"
        )
        return False


# ---------------------------------------------------------------- orchestrator
def run_uninstall(
    *,
    assume_yes: bool = False,
    keep_keys: bool = False,
    keep_folder: bool = False,
    dry_run: bool = False,
) -> int:
    """Uninstall Personal Jarvis from this machine.

    Returns: 0 success, 1 aborted by the user, 2 not a Jarvis install.
    """
    plan = build_plan()

    if not plan.is_jarvis_install:
        _console.print(
            f"[bad]This does not look like a Personal Jarvis install:[/] "
            f"{escape(str(plan.install_dir))}\n[muted]Refusing to delete anything.[/]"
        )
        return 2

    _print_plan(plan, keep_keys=keep_keys, keep_folder=keep_folder)

    if dry_run:
        _console.print("  [muted](dry run — nothing was changed.)[/]\n")
        return 0

    if not assume_yes and not _confirm():
        _console.print("  [muted]Cancelled — nothing was changed.[/]\n")
        return 1

    _console.print()
    _console.print(" [brand.bold]Removing…[/]")

    # Order matters: remove every external registration and the keys FIRST, then
    # remove the folder last — on Windows the folder step may
    # end this process's ability to do further work if it self-deletes.
    _remove_desktop_registration()
    _remove_autostart()
    if not keep_keys:
        _remove_keys(plan.keyring_keys)
    if not keep_folder:
        _remove_folder(plan.install_dir)

    _console.print()
    _console.print("  [ok]Done.[/] [muted]Personal Jarvis has been removed. "
                   "Re-run the installer any time to start fresh.[/]\n")
    return 0
