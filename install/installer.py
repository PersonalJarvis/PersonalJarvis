"""Personal Jarvis Stage-2 installer.

The Stage-1 shell bootstraps a venv with ``rich`` + ``packaging`` and then
``exec``s this script with the venv's Python. From here we own the full
install lifecycle in Python, where it is portable and testable.

Steps:
    1. Detect platform + Python version (sanity check; Stage 1 already
       gate-keeps this, but we re-assert so manual invocations stay safe).
    2. Install Personal Jarvis editable + runtime deps via pip.
    3. Optionally install the ``[desktop]`` extras (Windows + macOS GUI
       users; skipped on headless Linux servers unless ``--with-desktop``).
    4. Optionally install ``[voice-local]`` extras (faster-whisper, Silero,
       openWakeWord). Off by default — 1.5 GB model download.
    5. Run the existing first-run wizard (``python -m jarvis --wizard``)
       unless ``--no-wizard``.
    6. Launch the Desktop App / headless server unless ``--no-launch``.

Environment variables (any can be set before re-invoking the installer):
    JARVIS_INSTALL_DIR      override install location
    JARVIS_INSTALL_NO_PIP   skip the pip install steps (re-run wizard only)

Exit codes:
    0  success
    1  pre-flight failure (Python version, missing files)
    2  pip install failure
    3  wizard failure
    4  launch failure
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# CLAUDE.md: new CLI modules must use UTF-8 stdout or stick to ASCII. Without
# this, the Rich panels render fine but inline bullets break on cp1252 cmd.exe.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:  # pragma: no cover - Stage 1 installs rich; failure here is a bug
    print("ERROR: rich is not installed. The Stage-1 bootstrap should have done this.")
    print("Run 'pip install rich packaging' inside the .venv and re-invoke installer.py.")
    sys.exit(1)


console = Console()


# ---------------------------------------------------------------- discovery
def installer_dir() -> Path:
    return Path(__file__).resolve().parent


def repo_root() -> Path:
    return installer_dir().parent


def venv_python() -> Path:
    """Return the Python executable inside the venv."""
    if sys.platform == "win32":
        return repo_root() / ".venv" / "Scripts" / "python.exe"
    return repo_root() / ".venv" / "bin" / "python"


# ---------------------------------------------------------------- helpers
def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    """Run a subprocess and stream stdout/stderr live."""
    console.print(f"  [dim]$ {' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd, cwd=cwd)
    if check and result.returncode != 0:
        console.print(f"  [red]command failed with exit code {result.returncode}[/red]")
    return result.returncode


def header(title: str) -> None:
    console.print()
    console.print(Panel.fit(title, style="cyan"))


def is_headless_linux() -> bool:
    """Best-effort: True on a Linux VPS without a display server."""
    return sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


# ---------------------------------------------------------------- steps
def step_preflight() -> None:
    header("Pre-flight")
    table = Table.grid(padding=(0, 2))
    table.add_row("Platform", f"{platform.system()} {platform.release()} ({platform.machine()})")
    table.add_row("Python", f"{sys.version.split()[0]} ({sys.executable})")
    table.add_row("Repo", str(repo_root()))
    table.add_row("Headless", "yes" if is_headless_linux() else "no")
    console.print(table)

    if sys.version_info < (3, 11):
        console.print("[red]Python 3.11+ required.[/red]")
        sys.exit(1)

    if not (repo_root() / "pyproject.toml").exists():
        console.print("[red]pyproject.toml not found — installer.py was invoked outside the repo.[/red]")
        sys.exit(1)


def step_pip_install(*, with_desktop: bool, with_voice_local: bool, dry_run: bool) -> None:
    header("Installing Personal Jarvis")
    pip = [str(venv_python()), "-m", "pip"]

    # ``requirements.txt`` is the Wave 6 hash-pinned lockfile generated from
    # ``requirements.in`` (top-level deps mirrored from
    # ``pyproject.toml [project].dependencies``) by ``pip-compile
    # --generate-hashes --resolver=backtracking``. Every package-pinning line
    # carries ``--hash=sha256:...`` so an attacker who compromises a PyPI
    # mirror cannot swap out a transitive dependency without invalidating
    # the lockfile signature published in the GitHub Release. The desktop
    # branch installs with ``--require-hashes`` so unhashed/mismatched
    # entries fail-closed. The headless branch keeps ``pip install -e .`` —
    # the cloud-first VPS path resolves from ``pyproject.toml`` directly
    # because the lockfile predates Wave 6 hash pinning on transient bumps
    # and ``pip install --require-hashes`` is the new contract going
    # forward. See ``docs/supply-chain/threat-model.md`` §11.
    if with_desktop:
        runtime_step = ("runtime dependencies (Wave 6 hash-pinned lockfile, --require-hashes)",
                        pip + ["install", "--require-hashes", "-r", "requirements.txt"])
    else:
        runtime_step = ("runtime dependencies (cloud-first base from pyproject.toml)",
                        pip + ["install", "-e", "."])

    plans: list[tuple[str, list[str]]] = [
        ("editable install (entry-points)", pip + ["install", "-e", ".", "--no-deps"]),
        runtime_step,
    ]
    if with_desktop:
        plans.append(("desktop extras", pip + ["install", "-e", ".[desktop]"]))
    if with_voice_local:
        plans.append(("voice-local extras (faster-whisper, Silero, openWakeWord)",
                      pip + ["install", "-e", ".[voice-local]"]))

    if dry_run:
        for label, cmd in plans:
            console.print(f"  [yellow](dry-run)[/yellow] {label}: {' '.join(cmd)}")
        return

    for label, cmd in plans:
        console.print(f"[bold]· {label}[/bold]")
        rc = run(cmd, cwd=repo_root())
        if rc != 0:
            # Desktop / voice-local extras are best-effort — never fatal.
            if "extras" in label:
                console.print(f"  [yellow]{label} failed — continuing without it.[/yellow]")
                continue
            sys.exit(2)


def step_wizard(*, dry_run: bool) -> None:
    header("First-run wizard")
    console.print("Setting up API keys, microphone, hotkey, mascot...")
    cmd = [str(venv_python()), "-m", "jarvis", "--wizard"]
    if dry_run:
        console.print(f"  [yellow](dry-run)[/yellow] {' '.join(cmd)}")
        return
    rc = run(cmd, cwd=repo_root(), check=False)
    if rc != 0:
        console.print("[yellow]Wizard exited non-zero. You can re-run it later with:[/yellow]")
        console.print(f"  [dim]{shutil.which('jarvis') or 'jarvis'} --wizard[/dim]")
        sys.exit(3)


def step_launch(*, headless: bool, dry_run: bool) -> None:
    header("Launch")
    if headless or is_headless_linux():
        cmd = [str(venv_python()), "-m", "jarvis.ui.web.launcher", "--headless"]
        msg = "headless server on http://localhost:8765"
    elif sys.platform == "win32":
        cmd = [str(repo_root() / "run.bat")]
        msg = "Desktop App via run.bat"
    else:
        cmd = [str(venv_python()), "-m", "jarvis.ui.web.launcher"]
        msg = "Desktop App"

    console.print(f"Starting {msg}...")
    if dry_run:
        console.print(f"  [yellow](dry-run)[/yellow] {' '.join(cmd)}")
        return

    # We deliberately do not wait for the App — the installer returns control
    # to the user's shell as soon as the App is spawned.
    try:
        subprocess.Popen(cmd, cwd=repo_root(), close_fds=True)
    except OSError as exc:
        console.print(f"[red]Could not launch: {exc}[/red]")
        sys.exit(4)


def step_summary(*, no_launch: bool) -> None:
    console.print()
    console.print(Panel.fit(
        "[bold green]Personal Jarvis is installed.[/bold green]\n\n"
        f"Repo:     {repo_root()}\n"
        f"Venv:     {venv_python().parent.parent}\n\n"
        "[bold]Re-run anytime[/bold]\n"
        "  • Windows:  [cyan]run.bat[/cyan]\n"
        "  • macOS/Linux:  [cyan]python -m jarvis.ui.web.launcher[/cyan]\n"
        "  • Re-run wizard:  [cyan]python -m jarvis --wizard[/cyan]\n\n"
        "[bold]Update[/bold]\n"
        "  Re-invoke the same one-liner — it detects the existing checkout\n"
        "  and pulls latest main.",
        style="green",
    ))
    if not no_launch:
        console.print("\n[dim]The Desktop App is launching in a separate window.[/dim]")


# ---------------------------------------------------------------- entry
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="installer.py",
        description="Personal Jarvis Stage-2 installer",
    )
    parser.add_argument("--no-wizard", action="store_true",
                        help="skip the interactive first-run wizard")
    parser.add_argument("--no-launch", action="store_true",
                        help="don't launch the Desktop App at the end")
    parser.add_argument("--headless", action="store_true",
                        help="install headless (no GUI extras, no App launch)")
    parser.add_argument("--with-desktop", action="store_true",
                        help="install [desktop] extras (default: auto-detect by platform)")
    parser.add_argument("--with-voice-local", action="store_true",
                        help="install local STT/wake/VAD models (~1.5 GB download)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be done; don't run pip/wizard/launch")
    args = parser.parse_args(argv)

    # Auto-pick desktop extras unless the user said --headless or we're on a
    # headless Linux VPS. (--with-desktop forces it on either way.)
    if not args.with_desktop:
        if args.headless or is_headless_linux():
            with_desktop = False
        else:
            with_desktop = sys.platform in {"win32", "darwin"}
    else:
        with_desktop = True

    step_preflight()
    if not os.environ.get("JARVIS_INSTALL_NO_PIP"):
        step_pip_install(
            with_desktop=with_desktop,
            with_voice_local=args.with_voice_local,
            dry_run=args.dry_run,
        )

    if not args.no_wizard:
        step_wizard(dry_run=args.dry_run)

    if not args.no_launch:
        step_launch(headless=args.headless, dry_run=args.dry_run)

    step_summary(no_launch=args.no_launch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
