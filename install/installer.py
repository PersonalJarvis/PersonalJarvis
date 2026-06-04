"""Personal Jarvis Stage-2 installer.

The Stage-1 shell bootstraps a venv with ``rich`` + ``packaging`` and then
``exec``s this script with the venv's Python. From here we own the full
install lifecycle in Python, where it is portable and testable.

Steps:
    1. Detect platform + Python version (sanity check; Stage 1 already
       gate-keeps this, but we re-assert so manual invocations stay safe).
    2. Install the full Personal Jarvis desktop app via pip. The DEFAULT is
       the complete cross-platform product (``pip install -e .[full]`` —
       desktop GUI + local voice models + telephony + chat channels). Use
       ``--no-voice-local`` for a slimmer cloud-voice desktop install, or
       ``--headless`` for the base server (no GUI/local-voice extras).
    3. Install the three companion packages the main ``jarvis`` package
       imports at boot (``board-backend``, ``OS-Level`` → ``overlay``,
       ``skillbook``). These live in their own sub-directories with their own
       pyproject.toml and are NOT pulled in by ``pip install -e .``; skipping
       them crashes the app at startup with ModuleNotFoundError.
    4. Build the React desktop UI (``npm ci && npm run build``) unless
       ``--headless``. The compiled bundle (``jarvis/ui/web/dist``) is a
       git-ignored build artifact, so a fresh clone has none — without this
       step the window only ever shows the "Frontend wird gerade gebaut"
       placeholder. Degrades to an actionable message when Node/npm is absent.
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
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table
except ImportError:  # pragma: no cover - Stage 1 installs rich; failure here is a bug
    print("ERROR: rich is not installed. The Stage-1 bootstrap should have done this.")
    print("Run 'pip install rich packaging' inside the .venv and re-invoke installer.py.")
    sys.exit(1)


console = Console()


# The main ``jarvis`` package imports three companion packages that live in
# their own sub-directories, each with its own pyproject.toml. A plain
# ``pip install -e .`` only installs the ``jarvis`` package, so a fresh install
# that omits these crashes at boot with ModuleNotFoundError (board_backend /
# overlay / skillbook). They are installed editable, always, on every profile.
COMPANION_PACKAGES: tuple[str, ...] = ("board-backend", "OS-Level", "skillbook")


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
    # escape() so extras like ``.[full]`` aren't swallowed as Rich markup tags.
    console.print(f"  [dim]$ {escape(' '.join(cmd))}[/dim]")
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


def _runtime_step(pip: list[str], *, profile: str, locked: bool) -> tuple[str, list[str]]:
    """Return the (label, cmd) tuple for the main runtime-dependency install.

    ``profile`` is one of ``"full"`` / ``"desktop"`` / ``"headless"``.

    The default resolves dependencies from ``pyproject.toml`` so pip picks the
    correct per-platform wheels (e.g. the Windows torch wheel that bundles its
    own CUDA, never the Linux-only ``nvidia-*`` packages). The platform markers
    inside the ``[desktop]`` / ``[desktop-macos]`` extras keep each OS to its
    own native packages, so the same command works on Linux, macOS and Windows.
    """
    if locked:
        # Advanced, Linux-only path. ``requirements.txt`` is the Wave-6
        # hash-pinned lockfile (``pip-compile --generate-hashes``) used for the
        # supply-chain-hardened install. It was generated on Linux and pins
        # unmarked ``nvidia-*`` packages, so ``--require-hashes`` FAILS on
        # Windows/macOS. Only opt into this on Linux. Per-platform locked files
        # are future work; see docs/supply-chain/threat-model.md §11.
        return ("runtime deps (hash-pinned lockfile, --require-hashes)",
                pip + ["install", "--require-hashes", "-r", "requirements.txt"])
    if profile == "full":
        return ("full app (desktop GUI + local voice + telephony + channels)",
                pip + ["install", "-e", ".[full]"])
    if profile == "desktop":
        return ("desktop app (GUI, cloud voice)",
                pip + ["install", "-e", ".[desktop,telephony,channels]"])
    return ("base (headless / cloud server)", pip + ["install", "-e", "."])


def step_pip_install(*, profile: str, locked: bool, dry_run: bool) -> None:
    header("Installing Personal Jarvis")
    pip = [str(venv_python()), "-m", "pip"]

    plans: list[tuple[str, list[str]]] = [
        # Editable install of the main package activates the entry-points
        # (plugins). --no-deps first so a later resolver pass owns the deps.
        ("editable install (entry-points)", pip + ["install", "-e", ".", "--no-deps"]),
        _runtime_step(pip, profile=profile, locked=locked),
    ]
    # Companion packages are MANDATORY on every profile — the main package
    # imports them at boot. Failing one is fatal (unlike optional extras).
    for sub in COMPANION_PACKAGES:
        plans.append((f"companion package · {sub}",
                      pip + ["install", "-e", str(repo_root() / sub)]))

    if dry_run:
        for label, cmd in plans:
            console.print(f"  [yellow](dry-run)[/yellow] {label}: {escape(' '.join(cmd))}")
        return

    for label, cmd in plans:
        console.print(f"[bold]· {label}[/bold]")
        rc = run(cmd, cwd=repo_root())
        if rc != 0:
            sys.exit(2)


def step_build_frontend(*, headless: bool, dry_run: bool) -> None:
    """Build the React SPA the desktop app serves from ``jarvis/ui/web/dist``.

    The compiled bundle is a git-ignored build artifact, so a fresh clone has
    no ``dist/``. Without this step the FastAPI server only ever returns the
    "Frontend wird gerade gebaut" placeholder and the real UI never appears.
    Headless installs skip it (no GUI). When Node/npm is missing we emit an
    actionable English message and continue — the backend still works; only
    the desktop window needs the build.
    """
    if headless or is_headless_linux():
        return
    header("Building desktop UI")
    frontend = repo_root() / "jarvis" / "ui" / "web" / "frontend"
    if not frontend.exists():
        console.print("[yellow]Frontend sources not found — skipping UI build.[/yellow]")
        return

    npm = shutil.which("npm")
    if not npm:
        console.print(
            "[yellow]Node.js / npm not found — cannot build the desktop UI.[/yellow]\n"
            "  The backend will run, but the window shows a loading placeholder\n"
            "  until the UI is built. Install Node.js LTS from https://nodejs.org/\n"
            "  then run:\n"
            f"    cd {frontend}\n"
            "    npm ci && npm run build"
        )
        return

    # ``npm ci`` needs a committed lockfile; fall back to ``npm install``.
    install_cmd = [npm, "ci"] if (frontend / "package-lock.json").exists() else [npm, "install"]
    if dry_run:
        console.print(f"  [yellow](dry-run)[/yellow] {escape(' '.join(install_cmd))} && {npm} run build "
                      f"(cwd={frontend})")
        return

    rc = run(install_cmd, cwd=frontend, check=False)
    if rc != 0:
        console.print("[yellow]npm install failed — UI not built. See output above; "
                      "you can retry with 'npm ci && npm run build' in the frontend dir.[/yellow]")
        return
    rc = run([npm, "run", "build"], cwd=frontend, check=False)
    if rc != 0:
        console.print("[yellow]npm run build failed — UI not built. See output above.[/yellow]")
        return
    console.print("[green]Desktop UI built → jarvis/ui/web/dist[/green]")


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
        msg = "headless server on http://localhost:47821"
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
                        help="install the base server only (no GUI/local-voice "
                             "extras) and launch headless — for servers/containers")
    parser.add_argument("--no-voice-local", action="store_true",
                        help="desktop app without the local faster-whisper STT "
                             "extra (uses cloud STT instead; smaller install)")
    parser.add_argument("--locked", action="store_true",
                        help="LINUX ONLY: install runtime deps from the "
                             "hash-pinned requirements.txt (--require-hashes)")
    # Back-compat: the full desktop app (incl. desktop + local voice) is now the
    # default, so these legacy flags are accepted but no longer change anything.
    parser.add_argument("--with-desktop", action="store_true",
                        help="(deprecated; the desktop app is the default)")
    parser.add_argument("--with-voice-local", action="store_true",
                        help="(deprecated; local voice ships in the default install)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be done; don't run pip/wizard/launch")
    args = parser.parse_args(argv)

    # Profile selection. The full cross-platform desktop app is the default
    # product; --headless drops to the base server, --no-voice-local keeps the
    # GUI but skips the heavy local-STT extra.
    if args.headless or is_headless_linux():
        profile = "headless"
    elif args.no_voice_local:
        profile = "desktop"
    else:
        profile = "full"

    step_preflight()
    if not os.environ.get("JARVIS_INSTALL_NO_PIP"):
        step_pip_install(
            profile=profile,
            locked=args.locked,
            dry_run=args.dry_run,
        )
        # Build the desktop SPA so a fresh install actually shows the UI, not
        # the loading placeholder. Self-skips on headless / when npm is absent.
        step_build_frontend(headless=args.headless, dry_run=args.dry_run)

    if not args.no_wizard:
        step_wizard(dry_run=args.dry_run)

    if not args.no_launch:
        step_launch(headless=args.headless, dry_run=args.dry_run)

    step_summary(no_launch=args.no_launch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
