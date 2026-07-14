"""install.sh Python detection — honest failures + off-PATH interpreters.

Regression guard for the 2026-07-10 Mac field bug: the tester's machine only
exposed the Apple system Python 3.8.2 as `python3`, and the installer said a
bare "Python 3.11+ not found." — factually right, but it never reported what
it DID find, so the failure read as a false negative ("but python3 works!").
Worse, the finder only consulted PATH: on macOS a freshly installed
python.org or Homebrew interpreter routinely lives OFF the PATH of a
`curl | bash` session (and Homebrew's versioned python@3.x kegs are keg-only,
so they never reach PATH at all) — a machine WITH a suitable Python could hit
the same dead end.

Contract under test (the marked block inside install/install.sh):
  1. A too-old interpreter is remembered and reported, not silently skipped.
  2. Well-known off-PATH install prefixes are probed (overridable via
     JARVIS_PYTHON_SEARCH_DIRS for these tests).
  3. JARVIS_PYTHON pins one interpreter authoritatively — used when suitable,
     an honest failure (never a silent substitute) when not.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
INSTALL_SH = REPO / "install" / "install.sh"


def _find_bash() -> str | None:
    """A bash that understands host paths. On Windows that is Git Bash (found
    next to git.exe) — NEVER the WSL bash.exe stubs in WindowsApps/System32,
    which run in a separate filesystem namespace and cannot source our
    tmp-path scripts."""
    git = shutil.which("git")
    if git:
        for rel in ("../bin/bash.exe", "../../bin/bash.exe", "../usr/bin/bash.exe"):
            cand = (Path(git).parent / rel).resolve()
            if cand.exists():
                return str(cand)
    bash = shutil.which("bash")
    if bash and not any(t in bash.lower() for t in ("windowsapps", "system32")):
        return bash
    return None


BASH = _find_bash()

BLOCK_BEGIN = "# --- python-detection begin"
BLOCK_END = "# --- python-detection end"

DRIVER = """#!/usr/bin/env bash
set -u
# Convert host-OS paths (possibly Windows-style) into the shell's own form
# using only builtins, BEFORE we clamp PATH to the stub directory.
if [ -n "${STUB_SEARCH_DIR:-}" ]; then
    JARVIS_PYTHON_SEARCH_DIRS="$(cd "$STUB_SEARCH_DIR" && pwd)"
else
    JARVIS_PYTHON_SEARCH_DIRS='/jarvis-test-nonexistent'
fi
export JARVIS_PYTHON_SEARCH_DIRS
if [ -n "${STUB_PIN_DIR:-}" ]; then
    JARVIS_PYTHON="$(cd "$STUB_PIN_DIR" && pwd)/$STUB_PIN_NAME"
    export JARVIS_PYTHON
else
    unset JARVIS_PYTHON
fi
PATH="$(cd "$STUB_PATH_DIR" && pwd)"
export PATH
hash -r
source "$BLOCK_FILE"
if find_python; then
    printf 'FOUND|%s\\n' "$PYTHON_EXE"
else
    printf 'MISS|%s\\n' "$FOUND_TOO_OLD"
fi
"""


def _sh_path(p: Path) -> str:
    """Forward-slash form: Git Bash on Windows digests C:/... reliably,
    while backslashed paths get mangled between env vars and builtins."""
    return str(p).replace("\\", "/")


def _block_text() -> str:
    src = INSTALL_SH.read_text(encoding="utf-8")
    assert BLOCK_BEGIN in src and BLOCK_END in src, (
        "install.sh must keep the marked python-detection block these tests drive"
    )
    return src[src.index(BLOCK_BEGIN) : src.index(BLOCK_END)]


def _make_stub(directory: Path, name: str, version: str) -> None:
    stub = directory / name
    stub.write_text(f'#!/bin/sh\necho "{version}"\n', encoding="utf-8", newline="\n")
    stub.chmod(0o755)


def _run_detection(
    tmp_path: Path,
    *,
    path_stubs: dict[str, str],
    search_stubs: dict[str, str] | None = None,
    pin: tuple[str, str] | None = None,
) -> str:
    path_dir = tmp_path / "on-path"
    path_dir.mkdir(exist_ok=True)
    for name, version in path_stubs.items():
        _make_stub(path_dir, name, version)

    env = os.environ.copy()
    env.pop("JARVIS_PYTHON", None)
    env.pop("JARVIS_PYTHON_SEARCH_DIRS", None)
    env["STUB_PATH_DIR"] = _sh_path(path_dir)

    if search_stubs is not None:
        search_dir = tmp_path / "off-path"
        search_dir.mkdir(exist_ok=True)
        for name, version in search_stubs.items():
            _make_stub(search_dir, name, version)
        env["STUB_SEARCH_DIR"] = _sh_path(search_dir)

    if pin is not None:
        pin_dir = tmp_path / "pinned"
        pin_dir.mkdir(exist_ok=True)
        pin_name, pin_version = pin
        _make_stub(pin_dir, pin_name, pin_version)
        env["STUB_PIN_DIR"] = _sh_path(pin_dir)
        env["STUB_PIN_NAME"] = pin_name

    block_file = tmp_path / "detection-block.sh"
    block_file.write_text(_block_text(), encoding="utf-8", newline="\n")
    env["BLOCK_FILE"] = _sh_path(block_file)

    driver = tmp_path / "driver.sh"
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")

    result = subprocess.run(
        [BASH, str(driver)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"driver crashed: {result.stderr}"
    return result.stdout.strip()


needs_bash = pytest.mark.skipif(BASH is None, reason="bash not available")


@needs_bash
def test_too_old_interpreter_is_reported_not_silently_skipped(tmp_path) -> None:
    """The exact Mac field case: only a 3.8 `python3` exists -> the failure
    must name the version and path it found, so the error is self-explaining."""
    out = _run_detection(tmp_path, path_stubs={"python3": "3.8.2"})
    assert out.startswith("MISS|"), out
    assert "3.8.2" in out
    assert "python3" in out


@needs_bash
def test_off_path_interpreter_is_found(tmp_path) -> None:
    """A suitable interpreter in a probed prefix must be found even when
    nothing on PATH qualifies (python.org / Homebrew keg-only layout)."""
    out = _run_detection(
        tmp_path,
        path_stubs={"python3": "3.8.2"},
        search_stubs={"python3.12": "3.12.4"},
    )
    assert out.startswith("FOUND|"), out
    assert out.endswith("/python3.12")


@needs_bash
def test_jarvis_python_pin_wins(tmp_path) -> None:
    out = _run_detection(
        tmp_path,
        path_stubs={"python3": "3.8.2"},
        pin=("my-python", "3.13.1"),
    )
    assert out.startswith("FOUND|"), out
    assert out.endswith("/my-python")


@needs_bash
def test_too_old_pin_fails_honestly_instead_of_substituting(tmp_path) -> None:
    """An explicit pin is authoritative: when it is too old the install must
    fail and say so - never silently pick a different interpreter."""
    out = _run_detection(
        tmp_path,
        path_stubs={"python3.12": "3.12.4"},
        pin=("my-python", "3.9.7"),
    )
    assert out.startswith("MISS|"), out
    assert "3.9.7" in out


def test_candidate_list_keeps_up_with_python_releases() -> None:
    """python3.14 shipped 2025-10; a finder frozen at 3.13 slowly rots."""
    assert "python3.14" in INSTALL_SH.read_text(encoding="utf-8")


def test_failure_message_advertises_the_pin_escape_hatch() -> None:
    src = INSTALL_SH.read_text(encoding="utf-8")
    assert "JARVIS_PYTHON=" in src


def test_ps1_failure_reports_what_was_found() -> None:
    """Windows parity for the honesty half of the fix: the PowerShell
    bootstrap must also name the too-old interpreter it found."""
    ps1 = (REPO / "install" / "install.ps1").read_text(encoding="utf-8")
    assert "Closest match" in ps1


def test_prefers_python_with_full_native_wheel_support() -> None:
    """BUG-059: the local-voice native stack (av / ctranslate2 / onnxruntime)
    ships no cp314 wheels yet — a 3.14 venv boots but cannot install the
    local speech pack. The finder must prefer 3.13/3.12/3.11 and keep 3.14
    only as a working core fallback."""
    src = INSTALL_SH.read_text(encoding="utf-8")
    line = next(ln for ln in src.splitlines() if "for candidate in" in ln)
    for older in ("python3.13", "python3.12", "python3.11"):
        assert line.index(older) < line.index("python3.14"), older


def test_stale_venv_is_rebuilt_on_interpreter_version_change() -> None:
    """BUG-059 follow-up: an existing install whose venv is pinned to a
    Python without local-voice wheels (e.g. 3.14) must be rebuilt when the
    finder now selects a different major.minor — otherwise the 3.13-first
    preference never reaches existing installs."""
    src = INSTALL_SH.read_text(encoding="utf-8")
    assert "rebuilding the Python environment" in src
    # The comparison must be version-based, not existence-based only.
    assert 'if [ "$_venv_mm" != "$_sel_mm" ]' in src


def test_bootstraps_full_support_python_when_host_only_has_314() -> None:
    """Maintainer mandate 2026-07-14: the one-liner leaves NOTHING to install
    afterwards. A host offering only 3.14+ gets a self-contained CPython 3.13
    fetched via uv; an explicit JARVIS_PYTHON pin is never substituted, and
    JARVIS_NO_PYTHON_BOOTSTRAP=1 opts out."""
    src = INSTALL_SH.read_text(encoding="utf-8")
    assert "bootstrap_full_support_python" in src
    assert "JARVIS_NO_PYTHON_BOOTSTRAP" in src
    assert "3.11|3.12|3.13" in src  # the full-wheel-support set
    # Pin stays authoritative: the bootstrap hook is gated on no JARVIS_PYTHON.
    assert '[ -z "${JARVIS_PYTHON:-}" ]' in src
    # Honest degrade when the bootstrap fails.
    assert "the speech pack needs Python 3.11-3.13" in src


def test_update_run_stops_the_live_app_before_touching_the_venv() -> None:
    """Field report 2026-07-14: during an update, the previously installed
    app (often revived by the login autostart) kept running with a stale,
    half-rewritten venv underneath — 'the app is already open but nothing
    works'. install.sh must stop instances of THIS install before phase 3."""
    src = INSTALL_SH.read_text(encoding="utf-8")
    assert 'pkill -f "$VENV_PATH"' in src
    assert "stopped the running Jarvis app for the update" in src
    # The stop must come BEFORE the venv rebuild block.
    assert src.index("stopped the running Jarvis app") < src.index(
        "rebuilding the Python environment"
    )


def test_welcome_gate_asks_before_touching_anything() -> None:
    """Maintainer request 2026-07-14: the very first thing the one-liner shows
    is a single choose-don't-type question. It must read keys from /dev/tty
    (stdin carries the piped script), skip cleanly without a tty (CI opt-in
    = running the command), honor JARVIS_INSTALL_YES=1, and exit 0 on No
    having touched nothing."""
    src = INSTALL_SH.read_text(encoding="utf-8")
    assert "Would you like to install Personal Jarvis?" in src
    assert "JARVIS_INSTALL_YES" in src
    assert "< /dev/tty" in src
    assert "nothing was installed" in src
    # The gate runs BEFORE phase 1 (prerequisites) — nothing precedes consent.
    assert src.index("ask_welcome") < src.index("phase '1/6'")
