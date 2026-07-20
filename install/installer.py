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
        (Windows/macOS/Linux, or ``--with-desktop``) get the ``[full]`` extras;
        headless keeps the torch-free base floor.
    5/6 Voice models — prefetch everything the config needs (``python -m
        jarvis --prefetch``) + verify what actually landed on disk.
    6/6 Finish & launch — best-effort worker CLI (npm) + native desktop-shell
        registration + UI-bundle integrity check, then the flat summary, then
        launch the Desktop App / headless server as the LAST action unless
        ``--no-launch``.

Environment variables (any can be set before re-invoking the installer):
    JARVIS_INSTALL_DIR      override install location
    JARVIS_INSTALL_NO_PIP   skip the pip install steps

Exit codes:
    0  success
    1  pre-flight failure (Python version, missing files)
    2  pip install failure
    4  desktop registration or launch failure
    5  shipped UI bundle missing or incomplete
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import sysconfig
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parent.parent
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from jarvis.core.branding import (  # noqa: E402 - bootstrap source path above
    MACOS_APP_NAME,
    MANAGED_INSTALL_MARKER,
    PRODUCT_NAME,
)

# CLAUDE.md: new CLI modules must use UTF-8 stdout or stick to ASCII. Without
# this, the Rich panels render fine but inline bullets break on cp1252 cmd.exe.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from rich.console import Console
    from rich.markup import escape as rich_escape
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
# Connected-journey look (maintainer request 2026-07-16, visuals only): every
# line hangs off one continuous dim │ gutter, phases are gold ◆ diamonds, and
# the flow closes with a └ outro — the clack-style wizard grammar, recolored
# to the brand gold. Twins: the phase/ok/note/err helpers in install.sh and
# install.ps1; keep the three surfaces in visual lockstep.
GUTTER = "[muted]│[/]"


def phase(num: str, title: str) -> None:
    """A numbered phase diamond (gold ``◆ N/6``), continuing the Stage-1 journey.

    One six-phase journey spans BOTH installer stages: the Stage-1 shell owns
    phases 1-3 (prerequisites, fetch, venv), this script owns 4-6 — keep the
    numbering in sync with install.sh / install.ps1.
    """
    console.print(GUTTER)
    console.print(f"[brand]◆[/]  [brand]{num}[/]  [brand.bold]{title}[/]")


def ok(text: str) -> None:
    console.print(f"{GUTTER}  [ok]✓[/] [muted]{text}[/]")


def note(text: str) -> None:
    console.print(f"{GUTTER}    [muted]{text}[/]")


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    """Run an interactive/streaming subprocess (wizard, launch)."""
    result = subprocess.run(cmd, cwd=cwd)
    if check and result.returncode != 0:
        console.print(f"[bad]│    command failed with exit code {result.returncode}[/]")
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
            console.print(f"[bad]│  ✗ {label} failed[/]")
            tail = (result.stdout or "") + (result.stderr or "")
            for line in tail.strip().splitlines()[-20:]:
                console.print(f"[muted]│      {line}[/]")
        return result.returncode
    # Non-interactive: stream for the log.
    note(f"{label}…")
    return subprocess.run(cmd, cwd=cwd).returncode


def run_captured(
    cmd: list[str], *, label: str, cwd: Path | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a captured command behind the standard gold spinner.

    Like ``run_quiet``, but for callers that PARSE the captured stdout (probe
    verdicts, JSON reports): output stays captured on every host and the full
    ``CompletedProcess`` is returned. The spinner renders only on a real
    terminal so a long step never looks hung; a non-tty run still prints one
    honest "label…" note so logs show what the wait was. Raises ``OSError`` /
    ``TimeoutExpired`` exactly like ``subprocess.run`` — callers keep their
    own handling.
    """
    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )

    if console.is_terminal:
        with console.status(f"[brand]{label}…[/]", spinner="dots", spinner_style="brand"):
            return _run()
    note(f"{label}…")
    return _run()


def run_noted(cmd: list[str], *, label: str, cwd: Path | None = None) -> int:
    """Stream a milestone-printing command as live gutter notes under a spinner.

    For subprocesses whose output is a short series of human-readable
    milestone lines (the voice-model prefetch): each line renders as a dim
    │-gutter note the moment it appears, so a multi-minute download shows
    real, honest progress instead of a frozen label or raw column-0 spam
    (Intel-Mac field report 2026-07-16). stderr joins stdout so stray
    library noise can never break the layout. Non-tty hosts get the same
    lines without the spinner.
    """
    proc = subprocess.Popen(  # noqa: S603 — fixed argv built by the caller, no shell
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    def _pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                note(rich_escape(line))

    if console.is_terminal:
        with console.status(f"[brand]{label}…[/]", spinner="dots", spinner_style="brand"):
            _pump()
    else:
        note(f"{label}…")
        _pump()
    return proc.wait()


def is_headless_linux() -> bool:
    """Best-effort: True on a Linux VPS without a display server."""
    return sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


# ---------------------------------------------------------------- steps
def step_preflight() -> None:
    """Sanity re-assert (Stage 1 already gate-keeps) + a quiet environment line."""
    # A gutter line, not a blank one: Stage 1 ends mid-journey and this stage
    # continues the same connected └/│ rail without a visual break.
    console.print(GUTTER)
    note(
        f"{platform.system()} {platform.release()} ({platform.machine()})"
        f" · Python {sys.version.split()[0]}"
        f" · {repo_root()}"
    )
    if is_headless_linux():
        note("headless Linux detected — installing the server profile")

    if sys.version_info < (3, 11):  # noqa: UP036 - Stage 2 rechecks Stage 1
        console.print("[bad]│    Python 3.11+ required.[/]")
        sys.exit(1)

    if not (repo_root() / "pyproject.toml").exists():
        console.print(
            "[bad]│    pyproject.toml not found — installer.py was invoked "
            "outside the repo.[/]"
        )
        sys.exit(1)


def write_managed_marker(*, with_desktop: bool) -> None:
    """Mark this checkout as an installer-managed copy.

    The in-app "Update Now" button (jarvis/ui/web/update_routes.py) only appears,
    and only ever runs ``git reset --hard``, when this marker is present AND the
    checkout's ``origin`` is the official public repo. A maintainer's dev tree or
    a manual clone never gets this marker, so neither can be self-reset — this is
    the load-bearing safety guard for the whole updater. Best-effort: a marker
    failure must never fail the install (it only disables in-app updates).
    """
    marker = repo_root() / MANAGED_INSTALL_MARKER
    payload = {
        "managed": True,
        "install_path": str(repo_root()),
        "created_by": "install/installer.py",
        "profile": "full" if with_desktop else "headless",
        "desktop": with_desktop,
    }
    try:
        marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        ok("registered as a managed install (in-app updates enabled)")
    except OSError as exc:
        note(f"could not write update marker ({exc}); in-app updates stay disabled")


def _make_tree_owner_writable(root: Path) -> None:
    """Make a metadata tree removable without following directory symlinks."""

    def _make_writable(path: Path, *, directory: bool) -> None:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            return
        required = stat.S_IRUSR | stat.S_IWUSR
        if directory:
            required |= stat.S_IXUSR
        path.chmod(stat.S_IMODE(mode) | required)

    # A read-only root cannot be traversed reliably on POSIX. In top-down
    # order, each child directory is then made searchable before os.walk
    # descends into it. Adding S_IWUSR also clears the Windows read-only flag.
    _make_writable(root, directory=True)
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        _make_writable(current_path, directory=True)
        for name in directories:
            _make_writable(current_path / name, directory=True)
        for name in files:
            _make_writable(current_path / name, directory=False)


def repair_distribution_metadata(
    *, site_packages: Path | None = None, dry_run: bool = False
) -> bool:
    """Remove torn ``dist-info`` records left by an interrupted old update.

    The legacy updater could run while Python still had the environment open.
    On Windows that sometimes left empty or RECORD-only metadata directories;
    pip then treated the environment as corrupt and could not prove that the
    next update was complete. Removing only invalid metadata is safe: the
    normal install plan immediately restores every distribution still required
    by the selected profile, while obsolete packages remain unregistered.
    """
    if dry_run:
        note("(dry-run) repair interrupted package metadata")
        return True

    root = site_packages or Path(sysconfig.get_path("purelib"))
    try:
        candidates = sorted(root.glob("*.dist-info"))
    except OSError as exc:
        note(f"could not inspect package metadata ({exc})")
        return False

    broken: list[Path] = []
    for candidate in candidates:
        metadata = candidate / "METADATA"
        try:
            content = metadata.read_text(encoding="utf-8", errors="replace")
        except OSError:
            broken.append(candidate)
            continue
        if "Metadata-Version:" not in content or "\nName:" not in f"\n{content}":
            broken.append(candidate)

    for candidate in broken:
        try:
            if candidate.is_symlink():
                candidate.unlink()
            else:
                _make_tree_owner_writable(candidate)
                shutil.rmtree(candidate)
        except OSError as exc:
            note(f"could not repair package metadata at {candidate.name} ({exc})")
            return False

    if broken:
        ok(f"repaired {len(broken)} interrupted package metadata record(s)")
    return True


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
    plans.append(("dependency consistency check", pip + ["check"]))

    note("this can take a minute — grabbing dependencies")

    if not repair_distribution_metadata(dry_run=dry_run):
        console.print("[bad]│  ✗ package metadata repair failed — installation stopped.[/]")
        sys.exit(2)

    if dry_run:
        for label, cmd in plans:
            # rich would swallow literal command text like ``.[full]`` as
            # markup — escape so the dry-run shows the REAL command.
            console.print(f"[muted]│    (dry-run) {label}: {rich_escape(' '.join(cmd))}[/]")
        return

    for label, cmd in plans:
        rc = run_quiet(cmd, label=label, cwd=repo_root())
        if rc != 0:
            # The advertised desktop install is the [full] profile. Continuing
            # after that profile fails would advertise a ready app without the
            # microphone, macOS bridge, or local-voice dependencies it needs.
            console.print(f"[bad]│  ✗ {label} failed — installation stopped.[/]")
            sys.exit(2)
        ok(label)


def is_update_run() -> bool:
    """True when this checkout was already installer-managed (re-run = update)."""
    return (repo_root() / MANAGED_INSTALL_MARKER).exists()


def step_models(*, dry_run: bool) -> None:
    phase("5/6", "Voice models")
    note("downloading everything the voice pipeline needs, so the first")
    note("launch is ready immediately - nothing is fetched at startup")
    cmd = [str(venv_python()), "-m", "jarvis", "--prefetch"]
    if dry_run:
        console.print(f"[muted]│    (dry-run) {' '.join(cmd)}[/]")
        return
    # The download step's exit code alone is not proof: a skipped or cache-served
    # model can still leave "done" looking complete. So don't stop at rc — VERIFY
    # what actually landed on disk and print a per-model truth. Read-only +
    # best-effort: this never bricks the install (CLAUDE.md section 3).
    # run_noted, not run_quiet: with the HF progress bars silenced inside the
    # prefetch itself, its output is a handful of milestone lines ("downloading
    # wake model … ~40 MB", "speech model 'base': ready") — streaming them as
    # gutter notes under the spinner is the honest progress indicator a
    # multi-minute download needs; a static label just reads as a hang.
    run_noted(
        cmd,
        label="downloading voice models (the long step - a few hundred MB)",
        cwd=repo_root(),
    )
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
        result = run_captured(
            [str(venv_python()), "-c", probe], cwd=repo_root(),
            label="checking the voice models", timeout=180,
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
        # The whole line (gutter included) takes the state color — clack
        # colors its side bar by state the same way.
        console.print(f"│     {line}", style=style, markup=False)
    if not produced:
        note("could not verify the voice models - they will be checked on first launch")
        for tail in (result.stderr or "").strip().splitlines()[-5:]:
            console.print(f"[muted]│      {tail}[/]")
        return
    if result.returncode == 0:
        ok("everything the default voice path needs is present")
    else:
        console.print("[bad]│    Some required voice models are missing - re-run "
                      "the installer or check your connection.[/]")


def step_worker_cli(*, dry_run: bool) -> None:
    """Finish & launch sub-step: the coding-agent worker CLI (needs Node.js)."""
    if dry_run:
        console.print("[muted]│    (dry-run) npm i -g @anthropic-ai/claude-code[/]")
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
        result = run_captured(
            [str(venv_python()), "-c", probe], cwd=repo_root(),
            label="setting up the Jarvis-Agent worker CLI (npm download)", timeout=600,
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


def _write_desktop_integration_log(result: object) -> Path | None:
    """Persist the registration subprocess output; return the path or None."""
    log_path = repo_root() / "data" / "logs" / "install-desktop-integration.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "--- stdout ---\n"
            + (getattr(result, "stdout", "") or "")
            + "\n--- stderr ---\n"
            + (getattr(result, "stderr", "") or "")
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        return None
    return log_path


def step_desktop_integration(*, enabled: bool, dry_run: bool) -> bool:
    """Register the managed install with the current desktop shell."""
    if not enabled:
        return True
    if dry_run:
        console.print("[muted]│    (dry-run) repair desktop-shell registration[/]")
        return True
    result = None
    report = {}
    try:
        result = run_captured(
            [
                str(venv_python()),
                "-m",
                "jarvis.setup.desktop_integration",
                "--install-dir",
                str(repo_root()),
                "--json",
            ],
            cwd=repo_root(),
            label="registering the desktop app (a macOS first run can take a few minutes)",
            # py2app build + icon conversion + signing + LaunchServices import
            # probe can legitimately exceed two minutes on Intel macOS.
            timeout=600,
        )
        output = (result.stdout or "").strip().splitlines()
        report = json.loads(output[-1]) if output else {}
    except (OSError, subprocess.TimeoutExpired):
        report = {}
    except (json.JSONDecodeError, TypeError, ValueError):
        report = {}
    log_path = _write_desktop_integration_log(result)
    if (
        result is not None
        and result.returncode == 0
        and report.get("ok")
        and report.get("attempted")
    ):
        ok("desktop app registered with the operating system")
        return True
    console.print(
        "[bad]│  ✗ desktop app registration failed — installation stopped. "
        "The app must have a stable launcher identity.[/]"
    )
    warnings = report.get("warnings") if isinstance(report, dict) else None
    for warning in warnings or []:
        note(f"warning: {rich_escape(str(warning))}")
    stderr_tail = (getattr(result, "stderr", "") or "").strip().splitlines()[-15:]
    if stderr_tail:
        note("stderr tail:")
        for line in stderr_tail:
            note(rich_escape(line))
    if log_path is not None:
        note(f"full log: {log_path}")
    note("tip: re-running the installer with '--headless' installs without a desktop app")
    return False


def step_ui_bundle_check() -> bool:
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
        return False
    import re

    try:
        html = index.read_text(encoding="utf-8", errors="replace")
    except OSError:
        console.print("[bad]│    UI build index is unreadable.[/]")
        return False
    refs = [
        ref.split("?", 1)[0]
        for ref in re.findall(r'(?:src|href)="/?(assets/[^"]+)"', html)
    ]
    if not any(ref.endswith(".js") for ref in refs):
        console.print("[bad]│    UI build has no JavaScript entry bundle.[/]")
        return False
    missing = [
        ref for ref in refs
        if not (dist / ref.replace("/", os.sep)).is_file()
    ]
    if missing:
        console.print(f"[bad]│    UI build is incomplete ({missing[0]} missing) - "
                      "please report this; the app may look broken.[/]")
        return False
    else:
        ok("UI build present and intact")
        return True


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


def _rescan_venv_site_packages() -> None:
    """Make packages installed by THIS run importable in THIS process.

    The installer process starts before phase 4 runs ``pip install -e .``, and
    editable installs work through a ``.pth`` finder hook that the interpreter
    only processes at startup — so a fresh install could never ``import
    jarvis`` in-process (update runs could, the hook already existed at
    startup, which is why only fresh installs crashed at launch).
    ``site.addsitedir`` re-processes the venv's ``.pth`` files now.
    """
    import importlib
    import site
    import sysconfig

    site.addsitedir(sysconfig.get_paths()["purelib"])
    importlib.invalidate_caches()


def step_launch(*, headless: bool, dry_run: bool) -> None:
    if headless or is_headless_linux():
        cmd = [str(venv_python()), "-m", "jarvis.ui.web.launcher", "--headless"]
        msg = f"the headless server on http://localhost:{_resolved_admin_port()}"
    elif sys.platform == "win32":
        cmd = [str(repo_root() / "run.bat")]
        msg = "the Desktop App"
    elif sys.platform == "darwin":
        try:
            _rescan_venv_site_packages()
            from jarvis.setup.macos_app_bundle import (
                macos_app_bundle_is_launchable,
                macos_app_bundle_path,
                macos_launch_services_command,
            )
        except Exception:  # noqa: BLE001 — a hint-import must never fail the install
            # The install itself is complete; launch through LaunchServices
            # by app name instead of failing the whole run. Ported from the Mac
            # line (BUG-066 there; the local register merged it as BUG-078).
            cmd = ["/usr/bin/open", "-a", MACOS_APP_NAME]
            msg = "the Desktop App"
        else:
            bundle = macos_app_bundle_path()
            if not macos_app_bundle_is_launchable(bundle):
                console.print(
                    "[bad]│    Could not launch: the installed macOS app bundle is missing "
                    "or invalid.[/]"
                )
                sys.exit(4)
            cmd = macos_launch_services_command(bundle)
            msg = "the Desktop App"
    else:
        cmd = [str(venv_python()), "-m", "jarvis.ui.web.launcher"]
        msg = "the Desktop App"

    # └ closes the connected journey the Stage-1 shell opened with ┌.
    console.print(f"[muted]└[/]  [brand]Launching {msg}[/] [muted]— the app takes over from here…[/]")
    if dry_run:
        console.print(f"[muted]│    (dry-run) {' '.join(cmd)}[/]")
        return

    # We deliberately do not wait for the App — the installer returns control
    # to the user's shell as soon as the App is spawned.
    try:
        subprocess.Popen(cmd, cwd=repo_root(), close_fds=True)
    except OSError as exc:
        console.print(f"[bad]│    Could not launch: {exc}[/]")
        sys.exit(4)


def step_summary(*, no_launch: bool, update: bool, headless: bool) -> None:
    """The finale: a clack-style note box HANGING off the journey gutter.

    ``◇  Title ──╮`` header, deep-gold borders, ``├──╯`` foot — the left rail
    runs THROUGH the box and on to step_launch's ``└`` outro, so the journey
    never visually breaks (maintainer request 2026-07-16; supersedes both the
    flat two-rule finale of 2026-07-09 and the free-standing Panel draft).
    """
    rows: list[tuple[str, str, str]] = [("Installed to", str(repo_root()), "muted")]
    if sys.platform == "win32":
        rows.append(("Start again", f'Windows search -> "{PRODUCT_NAME}"', "brand"))
    elif sys.platform == "darwin":
        rows.append(
            ("Start again", f'Spotlight → "{PRODUCT_NAME}" (app in ~/Applications)', "brand")
        )
        rows.append(("Permissions", "macOS asks on first launch - approve each prompt", "muted"))
    elif sys.platform.startswith("linux") and not (headless or is_headless_linux()):
        rows.append(("Start again", f'app menu -> "{PRODUCT_NAME}"', "brand"))
    else:
        rows.append(("Start again", ".venv/bin/python -m jarvis.ui.web.launcher", "brand"))
        rows.append(("", "(in the install folder)", "muted"))
    rows.append(("Update", "re-run the same install one-liner - it updates in place", "muted"))
    if update:
        rows.append(("Next", "your setup and settings are kept - no re-onboarding", "muted"))
    elif headless or is_headless_linux():
        rows.append(("Next", f"open http://localhost:{_resolved_admin_port()} in your browser -", "muted"))
        rows.append(("", "the one-time setup guide (language, wake word,", "muted"))
        rows.append(("", "API keys) runs there, once", "muted"))
    else:
        rows.append(("Next", "the app opens with a one-time setup guide", "muted"))
        rows.append(("", "(language, wake word, API keys) - it never shows again", "muted"))

    title = f"{PRODUCT_NAME} is {'updated' if update else 'ready'}"
    key_w = 13
    # Widths are computed on the PLAIN text (markup added only when printing),
    # so the right border always lines up.
    plain = [f"{key:<{key_w}} {value}" for key, value, _ in rows]
    inner_w = max(max(len(p) for p in plain), len(title) + 3)
    # Header total width must equal the body's: 6 fixed header chars
    # (diamond, gaps, corner) vs inner_w + 7 body columns.
    top_dashes = "─" * max(inner_w + 1 - len(title), 2)
    console.print(GUTTER)
    console.print(f"[ok]◇[/]  [ok.bold]{title}[/]  [brand.deep]{top_dashes}╮[/]")
    console.print(f"[brand.deep]│[/]{' ' * (inner_w + 5)}[brand.deep]│[/]")
    for (key, value, vstyle), p in zip(rows, plain):
        pad = " " * (inner_w - len(p))
        console.print(
            f"[brand.deep]│[/]  [muted]{key:<{key_w}}[/] "
            f"[{vstyle}]{rich_escape(value)}[/]{pad}   [brand.deep]│[/]"
        )
    console.print(f"[brand.deep]│[/]{' ' * (inner_w + 5)}[brand.deep]│[/]")
    console.print(f"[brand.deep]├{'─' * (inner_w + 5)}╯[/]")


# ---------------------------------------------------------------- entry
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="installer.py",
        description=f"{PRODUCT_NAME} Stage-2 installer",
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
            with_desktop = sys.platform in {"win32", "darwin"} or sys.platform.startswith(
                "linux"
            )
    else:
        with_desktop = True

    # Detect BEFORE write_managed_marker stamps the tree: a pre-existing
    # marker means this run is an update of a managed install.
    update = is_update_run()

    step_preflight()
    if not args.dry_run:
        write_managed_marker(with_desktop=with_desktop)
    if not os.environ.get("JARVIS_INSTALL_NO_PIP"):
        step_pip_install(
            with_desktop=with_desktop,
            with_voice_local=args.with_voice_local,
            dry_run=args.dry_run,
        )

    step_models(dry_run=args.dry_run)

    phase("6/6", "Finish & launch")
    step_worker_cli(dry_run=args.dry_run)
    if not step_desktop_integration(enabled=with_desktop, dry_run=args.dry_run):
        sys.exit(4)
    if not step_ui_bundle_check() and not args.dry_run:
        sys.exit(5)

    # Summary FIRST, launch LAST: when the app window appears, the terminal
    # story is already told — and everything the first launch needs is on disk.
    step_summary(no_launch=args.no_launch, update=update, headless=args.headless)
    if not args.no_launch:
        step_launch(headless=args.headless, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
