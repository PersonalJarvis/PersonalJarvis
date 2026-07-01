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
    from rich.theme import Theme
except ImportError:  # pragma: no cover - Stage 1 installs rich; failure here is a bug
    print("ERROR: rich is not installed. The Stage-1 bootstrap should have done this.")
    print("Run 'pip install rich packaging' inside the .venv and re-invoke installer.py.")
    sys.exit(1)


# Brand palette (Charcoal + Gold) — matches the Stage-1 shell banner so the
# two stages read as one continuous install experience.
THEME = Theme(
    {
        "brand": "#e7c46e",
        "brand.bold": "bold #e7c46e",
        "ok": "#7ac88c",
        "muted": "#8c8c8c",
        "bad": "#e07a6e",
    }
)
console = Console(theme=THEME, highlight=False)


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


# ---------------------------------------------------------------- presentation
def step(title: str) -> None:
    """A top-level phase marker (gold ●), matching the Stage-1 shell."""
    console.print()
    console.print(f"[brand]  ●[/] [brand.bold]{title}[/]")


def ok(text: str) -> None:
    console.print(f"[ok]    ✓[/] [muted]{text}[/]")


def note(text: str) -> None:
    console.print(f"[muted]      {text}[/]")


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    """Run an interactive/streaming subprocess (wizard, launch)."""
    result = subprocess.run(cmd, cwd=cwd)
    if check and result.returncode != 0:
        console.print(f"[bad]      command failed with exit code {result.returncode}[/]")
    return result.returncode


def run_quiet(cmd: list[str], *, label: str, cwd: Path | None = None) -> int:
    """Run a noisy, non-interactive command (pip) behind a clean spinner.

    On a real terminal the raw output is hidden behind a gold spinner so the
    transcript stays calm. On a headless/piped run (no TTY — a VPS, CI) we let
    the output stream through so operators keep a readable log. Either way, a
    failure prints a captured tail so the error is never swallowed.
    """
    if console.is_terminal:
        with console.status(f"[brand]{label}…[/]", spinner="dots", spinner_style="brand"):
            result = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        if result.returncode != 0:
            console.print(f"[bad]    ✗ {label} failed[/]")
            tail = (result.stdout or "") + (result.stderr or "")
            for line in tail.strip().splitlines()[-20:]:
                console.print(f"[muted]      {line}[/]")
        return result.returncode
    # Non-interactive: stream for the log.
    note(f"{label}…")
    return subprocess.run(cmd, cwd=cwd).returncode


def is_headless_linux() -> bool:
    """Best-effort: True on a Linux VPS without a display server."""
    return sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


# ---------------------------------------------------------------- steps
def step_preflight() -> None:
    step("Environment")
    table = Table.grid(padding=(0, 2))
    table.add_column(style="muted")
    table.add_column(style="brand")
    table.add_row("      platform", f"{platform.system()} {platform.release()} ({platform.machine()})")
    table.add_row("      python", f"{sys.version.split()[0]}")
    table.add_row("      repo", str(repo_root()))
    table.add_row("      headless", "yes" if is_headless_linux() else "no")
    console.print(table)

    if sys.version_info < (3, 11):
        console.print("[bad]      Python 3.11+ required.[/]")
        sys.exit(1)

    if not (repo_root() / "pyproject.toml").exists():
        console.print("[bad]      pyproject.toml not found — installer.py was invoked outside the repo.[/]")
        sys.exit(1)


def step_pip_install(*, with_desktop: bool, with_voice_local: bool, dry_run: bool) -> None:
    step("Installing Personal Jarvis")
    pip = [str(venv_python()), "-m", "pip"]

    # ``requirements.txt`` is the Wave 6 hash-pinned, PLATFORM-UNIVERSAL lockfile
    # generated from ``requirements.in`` (top-level deps mirrored from
    # ``pyproject.toml [project].dependencies``) by ``uv pip compile --universal
    # --generate-hashes``. It carries per-OS environment markers so ONE lockfile
    # installs on Windows/macOS/Linux (each OS pulls only its wheels). Every
    # package-pinning line carries ``--hash=sha256:...`` so an attacker who compromises a PyPI
    # mirror cannot swap out a transitive dependency without invalidating
    # the lockfile signature published in the GitHub Release. The desktop
    # branch installs with ``--require-hashes`` so unhashed/mismatched
    # entries fail-closed. The headless branch keeps ``pip install -e .`` —
    # the cloud-first VPS path resolves from ``pyproject.toml`` directly
    # because the lockfile predates Wave 6 hash pinning on transient bumps
    # and ``pip install --require-hashes`` is the new contract going
    # forward. See ``docs/supply-chain/threat-model.md`` §11.
    if with_desktop:
        runtime_step = ("runtime dependencies (hash-pinned lockfile)",
                        pip + ["install", "--require-hashes", "-r", "requirements.txt"])
    else:
        runtime_step = ("runtime dependencies (cloud-first base)",
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

    note("this can take a minute — grabbing dependencies")

    if dry_run:
        for label, cmd in plans:
            console.print(f"[muted]      (dry-run) {label}: {' '.join(cmd)}[/]")
        return

    for label, cmd in plans:
        rc = run_quiet(cmd, label=label, cwd=repo_root())
        if rc != 0:
            # Desktop / voice-local extras are best-effort — never fatal.
            if "extras" in label:
                console.print(f"[bad]    ✗ {label} failed — continuing without it.[/]")
                continue
            sys.exit(2)
        ok(label)


def step_wizard(*, dry_run: bool) -> None:
    step("First-run wizard")
    note("API keys, microphone, hotkey, mascot")
    cmd = [str(venv_python()), "-m", "jarvis", "--wizard"]
    if dry_run:
        console.print(f"[muted]      (dry-run) {' '.join(cmd)}[/]")
        return
    console.print()
    rc = run(cmd, cwd=repo_root(), check=False)
    if rc != 0:
        console.print("[bad]      Wizard exited non-zero. You can re-run it later with:[/]")
        console.print(f"[muted]        {shutil.which('jarvis') or 'jarvis'} --wizard[/]")
        sys.exit(3)


def step_launch(*, headless: bool, dry_run: bool) -> None:
    step("Launch")
    if headless or is_headless_linux():
        cmd = [str(venv_python()), "-m", "jarvis.ui.web.launcher", "--headless"]
        msg = "headless server on http://localhost:8765"
    elif sys.platform == "win32":
        cmd = [str(repo_root() / "run.bat")]
        msg = "Desktop App via run.bat"
    else:
        cmd = [str(venv_python()), "-m", "jarvis.ui.web.launcher"]
        msg = "Desktop App"

    note(f"starting {msg}")
    if dry_run:
        console.print(f"[muted]      (dry-run) {' '.join(cmd)}[/]")
        return

    # We deliberately do not wait for the App — the installer returns control
    # to the user's shell as soon as the App is spawned.
    try:
        subprocess.Popen(cmd, cwd=repo_root(), close_fds=True)
    except OSError as exc:
        console.print(f"[bad]      Could not launch: {exc}[/]")
        sys.exit(4)


def step_summary(*, no_launch: bool) -> None:
    console.print()
    console.print(Panel.fit(
        "[ok]Personal Jarvis is installed.[/]\n\n"
        f"[muted]Repo[/]   {repo_root()}\n"
        f"[muted]Venv[/]   {venv_python().parent.parent}\n\n"
        "[brand.bold]Re-run anytime[/]\n"
        "  • Windows:  [brand]run.bat[/]\n"
        "  • macOS/Linux:  [brand]python -m jarvis.ui.web.launcher[/]\n"
        "  • Re-run wizard:  [brand]python -m jarvis --wizard[/]\n\n"
        "[brand.bold]Update[/]\n"
        "  Re-invoke the same one-liner — it detects the existing checkout\n"
        "  and pulls latest main.",
        border_style="brand",
        title="[brand.bold]✓ Done[/]",
        title_align="left",
    ))
    if not no_launch:
        console.print("\n[muted]The Desktop App is launching in a separate window.[/]")


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
