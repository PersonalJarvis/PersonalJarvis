"""Personal Jarvis Stage-2 installer.

The Stage-1 shell bootstraps a venv with ``rich`` + ``packaging`` and then
``exec``s this script with the venv's Python. From here we own the full
install lifecycle in Python, where it is portable and testable.

The installer is fully NON-INTERACTIVE: it downloads and prepares everything,
explains each step in one line, and launches the app as its LAST action. All
setup questions (language, wake word, API keys, Terms) live in the app's
one-time first-launch onboarding — never in this terminal.

Steps:
    1. Detect platform + Python version (sanity check; Stage 1 already
       gate-keeps this, but we re-assert so manual invocations stay safe).
    2. Install Personal Jarvis editable + runtime deps via pip.
    3. Optionally install the ``[desktop]`` extras (Windows + macOS GUI
       users; skipped on headless Linux servers unless ``--with-desktop``).
    4. Optionally install ``[local-voice]`` extras (Silero VAD, WebRTC VAD,
       Porcupine). Off by default — ~1.5 GB (Silero pulls torch). The always-on
       neural wake word (openWakeWord) is a BASE dependency and does NOT need
       this; only an arbitrary custom wake phrase does (via the separate in-app
       local-Whisper install).
    5. Prefetch every voice model the config needs (``python -m jarvis
       --prefetch``) so the first launch has nothing left to download.
    6. Best-effort: install the Jarvis-Agent worker CLI (npm) and, on Windows,
       the Start-Menu shortcut that names the taskbar button.
    7. Verify the shipped UI build is present + intact.
    8. Print the summary, then launch the Desktop App / headless server as the
       last action unless ``--no-launch``.

Environment variables (any can be set before re-invoking the installer):
    JARVIS_INSTALL_DIR      override install location
    JARVIS_INSTALL_NO_PIP   skip the pip install steps

Exit codes:
    0  success
    1  pre-flight failure (Python version, missing files)
    2  pip install failure
    4  launch failure
"""
from __future__ import annotations

import argparse
import json
import os
import platform
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


def write_managed_marker() -> None:
    """Mark this checkout as an installer-managed copy.

    The in-app "Update Now" button (jarvis/ui/web/update_routes.py) only appears,
    and only ever runs ``git reset --hard``, when this marker is present AND the
    checkout's ``origin`` is the official public repo. A maintainer's dev tree or
    a manual clone never gets this marker, so neither can be self-reset — this is
    the load-bearing safety guard for the whole updater. Best-effort: a marker
    failure must never fail the install (it only disables in-app updates).
    """
    marker = repo_root() / ".jarvis-managed-install"
    payload = {
        "managed": True,
        "install_path": str(repo_root()),
        "created_by": "install/installer.py",
    }
    try:
        marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        ok("registered as a managed install (in-app updates enabled)")
    except OSError as exc:
        note(f"could not write update marker ({exc}); in-app updates stay disabled")


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
        plans.append(("local-voice extras (Silero VAD, WebRTC VAD, Porcupine)",
                      pip + ["install", "-e", ".[local-voice]"]))

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


def is_update_run() -> bool:
    """True when this checkout was already installer-managed (re-run = update)."""
    return (repo_root() / ".jarvis-managed-install").exists()


def step_models(*, dry_run: bool) -> None:
    step("Voice models")
    note("downloading everything the voice pipeline needs, so the first")
    note("launch is ready immediately - nothing is fetched at startup")
    cmd = [str(venv_python()), "-m", "jarvis", "--prefetch"]
    if dry_run:
        console.print(f"[muted]      (dry-run) {' '.join(cmd)}[/]")
        return
    rc = run(cmd, cwd=repo_root(), check=False)
    if rc == 0:
        ok("all voice models are on disk")
    else:
        console.print("[bad]      Some models could not be downloaded - the app "
                      "will fetch them on first launch instead.[/]")


def step_worker_cli(*, dry_run: bool) -> None:
    step("Jarvis-Agent worker CLI")
    note("the coding-agent worker Jarvis delegates missions to (needs Node.js)")
    if dry_run:
        console.print("[muted]      (dry-run) npm i -g @anthropic-ai/claude-code[/]")
        return
    probe = (
        "from jarvis.setup.dependencies import check_claude_cli, check_npm, install_claude_cli\n"
        "import sys\n"
        "if check_claude_cli().present:\n"
        "    print('present'); sys.exit(0)\n"
        "if not check_npm().present:\n"
        "    print('no-npm'); sys.exit(0)\n"
        "installed, _status = install_claude_cli()\n"
        "print('installed' if installed else 'failed')\n"
    )
    try:
        result = subprocess.run(
            [str(venv_python()), "-c", probe], cwd=repo_root(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600,
        )
        stdout = (result.stdout or "").strip()
        verdict = stdout.splitlines()[-1] if stdout else "failed"
    except (OSError, subprocess.TimeoutExpired):
        verdict = "failed"
    if verdict == "present":
        ok("worker CLI already installed")
    elif verdict == "installed":
        ok("worker CLI installed (npm)")
    elif verdict == "no-npm":
        note("Node.js/npm not found - the Jarvis-Agent worker can be added later in-app")
    else:
        note("worker CLI install failed - it can be added later in-app")


def step_shortcut(*, dry_run: bool) -> None:
    if sys.platform != "win32":
        return
    step("Start Menu & taskbar identity")
    note("so the very first launch shows the Jarvis name + icon, not a generic Python entry")
    if dry_run:
        console.print("[muted]      (dry-run) ensure_start_menu_shortcut()[/]")
        return
    probe = (
        "from jarvis.ui.icon_utils import ensure_start_menu_shortcut\n"
        "print('ok' if ensure_start_menu_shortcut() else 'skipped')\n"
    )
    try:
        result = subprocess.run(
            [str(venv_python()), "-c", probe], cwd=repo_root(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        outcome = (result.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        outcome = ""
    if outcome.endswith("ok"):
        ok("shortcut in place")
    else:
        note("could not create the shortcut - the app will retry on first launch")


def step_ui_bundle_check() -> None:
    """Honest packaging check: the shipped UI build must be present + intact.

    The public snapshot ships a prebuilt ``jarvis/ui/web/dist``; a dev clone
    may not have one. Missing or torn builds are the 'old/broken app' symptom,
    so say it out loud instead of letting the first launch look broken.
    """
    step("UI bundle")
    dist = repo_root() / "jarvis" / "ui" / "web" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        note("no prebuilt UI found (dev clone?) - the app will serve a minimal page")
        note("public installs always ship the UI; if you used the one-liner, please report this")
        return
    import re

    html = index.read_text(encoding="utf-8", errors="replace")
    missing = [
        ref for ref in re.findall(r'(?:src|href)="/?(assets/[^"]+)"', html)
        if not (dist / ref.replace("/", os.sep)).is_file()
    ]
    if missing:
        console.print(f"[bad]      UI build is incomplete ({missing[0]} missing) - "
                      "please report this; the app may look broken.[/]")
    else:
        ok("UI build present and intact")


def _resolved_admin_port() -> int:
    """The port the headless launcher will ACTUALLY bind — a jarvis.toml
    ``[ui].admin_api_port`` override if present, else the packaged default.

    Reads it from the launcher itself so the "your server is here" hint can never
    drift from the port the process opens. A hard-coded hint (the old
    ``localhost:8765``) sent every headless/VPS downloader to a dead port while
    the server was serving elsewhere. Falls back to the packaged default if the
    import is somehow unavailable, so this never breaks the launch step."""
    try:
        from jarvis.ui.web.launcher import _DEFAULT_ADMIN_PORT, _fast_admin_port

        return _fast_admin_port()
    except Exception:  # noqa: BLE001 — a hint must never block the launch
        try:
            from jarvis.ui.web.launcher import _DEFAULT_ADMIN_PORT

            return _DEFAULT_ADMIN_PORT
        except Exception:  # noqa: BLE001
            return 47821


def step_launch(*, headless: bool, dry_run: bool) -> None:
    step("Launch")
    if headless or is_headless_linux():
        cmd = [str(venv_python()), "-m", "jarvis.ui.web.launcher", "--headless"]
        msg = f"headless server on http://localhost:{_resolved_admin_port()}"
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


def step_summary(*, no_launch: bool, update: bool, headless: bool) -> None:
    console.print()
    if update:
        next_line = "Your setup and settings are kept - no re-onboarding."
    elif headless or is_headless_linux():
        next_line = (
            "Open the printed server address in your browser - a one-time\n"
            "  setup guide (language, wake word, API keys) runs there.\n"
            "  It never shows again after that."
        )
    else:
        next_line = (
            "The app opens with a one-time setup guide (language, wake word,\n"
            "  API keys). It never shows again after that."
        )
    console.print(Panel.fit(
        "[ok]Personal Jarvis is " + ("updated" if update else "installed") + ".[/]\n\n"
        f"[muted]Repo[/]   {repo_root()}\n"
        f"[muted]Venv[/]   {venv_python().parent.parent}\n\n"
        f"[brand.bold]{'Update' if update else 'What happens next'}[/]\n"
        f"  {next_line}\n\n"
        "[brand.bold]Re-run anytime[/]\n"
        "  • Windows:  [brand]run.bat[/]\n"
        "  • macOS/Linux:  [brand]python -m jarvis.ui.web.launcher[/]\n\n"
        "[brand.bold]Update later[/]\n"
        "  Re-run the same install one-liner - it updates in place and keeps\n"
        "  your setup.",
        border_style="brand",
        title="[brand.bold]✓ Done[/]",
        title_align="left",
    ))
    if not no_launch:
        console.print("\n[muted]Launching now…[/]")


# ---------------------------------------------------------------- entry
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="installer.py",
        description="Personal Jarvis Stage-2 installer",
    )
    parser.add_argument("--no-wizard", action="store_true",
                        help="deprecated no-op: the installer never runs the terminal "
                             "wizard anymore (setup happens in the app)")
    parser.add_argument("--no-launch", action="store_true",
                        help="don't launch the Desktop App at the end")
    parser.add_argument("--headless", action="store_true",
                        help="install headless (no GUI extras, no App launch)")
    parser.add_argument("--with-desktop", action="store_true",
                        help="install [desktop] extras (default: auto-detect by platform)")
    parser.add_argument("--with-voice-local", action="store_true",
                        help="install the heavier local voice extras (Silero/WebRTC VAD, "
                             "Porcupine, ~1.5 GB). NOT needed for the always-on neural "
                             "wake word — that ships in the base install.")
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

    # Detect BEFORE write_managed_marker stamps the tree: a pre-existing
    # marker means this run is an update of a managed install.
    update = is_update_run()

    step_preflight()
    if not args.dry_run:
        write_managed_marker()
    if not os.environ.get("JARVIS_INSTALL_NO_PIP"):
        step_pip_install(
            with_desktop=with_desktop,
            with_voice_local=args.with_voice_local,
            dry_run=args.dry_run,
        )

    step_models(dry_run=args.dry_run)
    step_worker_cli(dry_run=args.dry_run)
    step_shortcut(dry_run=args.dry_run)
    step_ui_bundle_check()

    # Summary FIRST, launch LAST: when the app window appears, the terminal
    # story is already told — and everything the first launch needs is on disk.
    step_summary(no_launch=args.no_launch, update=update, headless=args.headless)
    if not args.no_launch:
        step_launch(headless=args.headless, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
