"""Personal Jarvis Stage-2 installer.

The Stage-1 shell bootstraps a venv with ``rich`` + ``packaging`` and then
``exec``s this script with the venv's Python. From here we own the full
install lifecycle in Python, where it is portable and testable.

The installer is fully NON-INTERACTIVE: it downloads and prepares everything,
explains each step in one line, and launches the app as its LAST action. All
setup questions (language, wake word, API keys, Terms) live in the app's
one-time first-launch onboarding — never in this terminal.

The user-visible journey is ONE six-phase sequence spanning both stages: the
Stage-1 shell prints phases 1-3 (prerequisites, fetch, venv), this script
continues with 4-6. Keep the numbering in sync with install.sh / install.ps1.

Phases owned here:
    4/6 Dependencies — editable install + runtime deps via pip. Desktop hosts
        (Windows/macOS, or ``--with-desktop``) also get the ``[full]`` extras;
        headless keeps the torch-free base floor.
    5/6 Voice models — prefetch everything the config needs (``python -m
        jarvis --prefetch``) + verify what actually landed on disk.
    6/6 Finish & launch — best-effort worker CLI (npm) + Windows Start-Menu
        shortcut + UI-bundle integrity check, then the flat summary, then
        launch the Desktop App / headless server as the LAST action unless
        ``--no-launch``.

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
    from rich.markup import escape as rich_escape
    from rich.rule import Rule
    from rich.theme import Theme
except ImportError:  # pragma: no cover - Stage 1 installs rich; failure here is a bug
    print("ERROR: rich is not installed. The Stage-1 bootstrap should have done this.")
    print("Run 'pip install rich packaging' inside the .venv and re-invoke installer.py.")
    sys.exit(1)


# Brand palette (docs/BRAND.md): Signal Yellow on matte black, deep gold for
# the finale rules — matches the Stage-1 shell banner gradient so the two
# stages read as one continuous install experience.
THEME = Theme(
    {
        "brand": "#FFD60A",
        "brand.bold": "bold #FFD60A",
        "brand.deep": "#B8960A",
        "ok": "#7ac88c",
        "ok.bold": "bold #7ac88c",
        "muted": "#8F8F8F",
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
def phase(num: str, title: str) -> None:
    """A numbered phase marker (gold ``N/6``), continuing the Stage-1 journey.

    One six-phase journey spans BOTH installer stages: the Stage-1 shell owns
    phases 1-3 (prerequisites, fetch, venv), this script owns 4-6 — keep the
    numbering in sync with install.sh / install.ps1.
    """
    console.print()
    console.print(f"[brand]  {num}[/] [brand.bold]{title}[/]")


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
    """Sanity re-assert (Stage 1 already gate-keeps) + a quiet environment line."""
    console.print()
    note(
        f"{platform.system()} {platform.release()} ({platform.machine()})"
        f" · Python {sys.version.split()[0]}"
        f" · {repo_root()}"
    )
    if is_headless_linux():
        note("headless Linux detected — installing the server profile")

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
    phase("4/6", "Dependencies")
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
        # One official install profile (design 2026-07-07): desktop + telephony
        # + channels + local voice in one shot. Platform markers skip whatever
        # this OS cannot use. --headless keeps the torch-free base floor.
        # ``with_voice_local`` is a deprecated no-op: [full] already carries it.
        plans.append(("full profile extras (desktop, telephony, channels, local voice)",
                      pip + ["install", "-e", ".[full]"]))

    note("this can take a minute — grabbing dependencies")

    if dry_run:
        for label, cmd in plans:
            # rich would swallow literal command text like ``.[full]`` as
            # markup — escape so the dry-run shows the REAL command.
            console.print(f"[muted]      (dry-run) {label}: {rich_escape(' '.join(cmd))}[/]")
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
    phase("5/6", "Voice models")
    note("downloading everything the voice pipeline needs, so the first")
    note("launch is ready immediately - nothing is fetched at startup")
    cmd = [str(venv_python()), "-m", "jarvis", "--prefetch"]
    if dry_run:
        console.print(f"[muted]      (dry-run) {' '.join(cmd)}[/]")
        return
    # The download step's exit code alone is not proof: a skipped or cache-served
    # model can still leave "done" looking complete. So don't stop at rc — VERIFY
    # what actually landed on disk and print a per-model truth. Read-only +
    # best-effort: this never bricks the install (CLAUDE.md section 3).
    run(cmd, cwd=repo_root(), check=False)
    verify_models()


def verify_models() -> None:
    """Print a per-model truth: what is on disk, what is pending, what is missing.

    Runs the read-only report inside the venv's Python — the interpreter that
    will actually run Jarvis — so it sees the real installed packages and model
    caches. Exit 0 = every REQUIRED model present; non-zero = a required model is
    missing. A probe failure degrades to an honest note, never a failed install.
    """
    probe = (
        "from jarvis.setup.model_report import "
        "voice_model_report, report_complete, format_report\n"
        "items = voice_model_report()\n"
        "print('\\n'.join(format_report(items)))\n"
        "import sys; sys.exit(0 if report_complete(items) else 3)\n"
    )
    try:
        result = subprocess.run(
            [str(venv_python()), "-c", probe], cwd=repo_root(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        note("could not verify the voice models - they will be checked on first launch")
        return
    produced = False
    for raw in (result.stdout or "").splitlines():
        line = raw.rstrip()
        if not line:
            continue
        produced = True
        # markup=False: the report text contains literal '[full]' / quotes that
        # must NOT be parsed as rich markup.
        style = "ok" if line.startswith("✓") else ("bad" if line.startswith("✗") else "muted")
        console.print(f"      {line}", style=style, markup=False)
    if not produced:
        note("could not verify the voice models - they will be checked on first launch")
        for tail in (result.stderr or "").strip().splitlines()[-5:]:
            console.print(f"[muted]      {tail}[/]")
        return
    if result.returncode == 0:
        ok("everything the default voice path needs is present")
    else:
        console.print("[bad]      Some required voice models are missing - re-run "
                      "the installer or check your connection.[/]")


def step_worker_cli(*, dry_run: bool) -> None:
    """Finish & launch sub-step: the coding-agent worker CLI (needs Node.js)."""
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
        ok("Jarvis-Agent worker CLI already installed")
    elif verdict == "installed":
        ok("Jarvis-Agent worker CLI installed (npm)")
    elif verdict == "no-npm":
        note("Node.js/npm not found - the Jarvis-Agent worker CLI can be added later in-app")
    else:
        note("Jarvis-Agent worker CLI install failed - it can be added later in-app")


def step_shortcut(*, dry_run: bool) -> None:
    """Finish & launch sub-step (Windows): Start-Menu shortcut = taskbar identity."""
    if sys.platform != "win32":
        return
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
        ok("Start-Menu shortcut in place (taskbar shows the Jarvis name + icon)")
    else:
        note("could not create the Start-Menu shortcut - the app will retry on first launch")


def step_macos_app(*, dry_run: bool) -> None:
    """Finish & launch sub-step (macOS): a real .app so Spotlight/Launchpad
    find Jarvis — without it, closing the app leaves no way back but the
    terminal (BUG-060)."""
    if sys.platform != "darwin":
        return
    if dry_run:
        console.print("[muted]      (dry-run) ensure_macos_app_bundle()[/]")
        return
    probe = (
        "from jarvis.setup.macos_app_bundle import ensure_macos_app_bundle\n"
        "print('ok' if ensure_macos_app_bundle() else 'skipped')\n"
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
        ok('app installed to ~/Applications - find it via Spotlight ("Personal Jarvis")')
    else:
        note("could not create the ~/Applications app - start via the install folder")


def step_ui_bundle_check() -> None:
    """Honest packaging check: the shipped UI build must be present + intact.

    The public snapshot ships a prebuilt ``jarvis/ui/web/dist``; a dev clone
    may not have one. Missing or torn builds are the 'old/broken app' symptom,
    so say it out loud instead of letting the first launch look broken.
    """
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
    if headless or is_headless_linux():
        cmd = [str(venv_python()), "-m", "jarvis.ui.web.launcher", "--headless"]
        msg = f"the headless server on http://localhost:{_resolved_admin_port()}"
    elif sys.platform == "win32":
        cmd = [str(repo_root() / "run.bat")]
        msg = "the Desktop App"
    else:
        cmd = [str(venv_python()), "-m", "jarvis.ui.web.launcher"]
        msg = "the Desktop App"

    console.print(f"  [muted]Launching {msg} — the app takes over from here…[/]")
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
    """The flat finale: two deep-gold rules around a short, calm summary.

    Deliberately NOT a rich ``Panel`` — the boxed panel is the signature look
    of generated projects (design 2026-07-09); modern installers end flat.
    """
    console.print()
    console.print(Rule(style="brand.deep"))
    console.print(f"  [ok.bold]✓ Personal Jarvis is {'updated' if update else 'ready'}.[/]")
    console.print(f"    [muted]Installed to[/]  {repo_root()}")
    if sys.platform == "win32":
        console.print("    [muted]Start again[/]   [brand]run.bat[/] [muted](in the install folder)[/]")
    elif sys.platform == "darwin":
        console.print(
            "    [muted]Start again[/]   [brand]Spotlight → \"Personal Jarvis\"[/] "
            "[muted](app in ~/Applications)[/]"
        )
    else:
        console.print(
            "    [muted]Start again[/]   [brand].venv/bin/python -m jarvis.ui.web.launcher[/] "
            "[muted](in the install folder)[/]"
        )
    console.print(
        "    [muted]Update[/]        [muted]re-run the same install one-liner - "
        "it updates in place[/]"
    )
    if update:
        console.print(
            "    [muted]Next[/]          [muted]your setup and settings are kept - "
            "no re-onboarding[/]"
        )
    elif headless or is_headless_linux():
        console.print(
            f"    [muted]Next[/]          [muted]open http://localhost:{_resolved_admin_port()} "
            "in your browser - a one-time[/]"
        )
        console.print(
            "    [muted]              setup guide (language, wake word, API keys) runs "
            "there, once[/]"
        )
    else:
        console.print(
            "    [muted]Next[/]          [muted]the app opens with a one-time setup guide "
            "(language,[/]"
        )
        console.print(
            "    [muted]              wake word, API keys) - it never shows again[/]"
        )
    console.print(Rule(style="brand.deep"))


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
                        help="deprecated no-op: the full profile already includes "
                             "the local voice extras")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be done; don't run pip/wizard/launch")
    args = parser.parse_args(argv)

    if args.with_voice_local:
        note("--with-voice-local is deprecated: the full profile already "
             "includes local voice.")

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

    phase("6/6", "Finish & launch")
    step_worker_cli(dry_run=args.dry_run)
    step_shortcut(dry_run=args.dry_run)
    step_macos_app(dry_run=args.dry_run)
    step_ui_bundle_check()

    # Summary FIRST, launch LAST: when the app window appears, the terminal
    # story is already told — and everything the first launch needs is on disk.
    step_summary(no_launch=args.no_launch, update=update, headless=args.headless)
    if not args.no_launch:
        step_launch(headless=args.headless, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
