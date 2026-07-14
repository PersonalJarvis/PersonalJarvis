"""Stage-1 prerequisite bootstrap state-machine regression tests.

The public installer has to run before Python exists, so the implementation
must remain in PowerShell/Bash. These tests extract the marked function blocks
and drive them with deterministic state fakes: an already-ready machine must
not ask or install, while a missing Python+Git machine must install once,
re-check, and return ready to the same installer process.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
INSTALL_PS1 = REPO / "install" / "install.ps1"
INSTALL_SH = REPO / "install" / "install.sh"
BLOCK_BEGIN = "# --- prerequisite-bootstrap begin"
BLOCK_END = "# --- prerequisite-bootstrap end"


def _block(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    assert BLOCK_BEGIN in source and BLOCK_END in source
    return source[source.index(BLOCK_BEGIN) : source.index(BLOCK_END)]


def _find_bash() -> str | None:
    git = shutil.which("git")
    if git:
        for rel in ("../bin/bash.exe", "../../bin/bash.exe", "../usr/bin/bash.exe"):
            candidate = (Path(git).parent / rel).resolve()
            if candidate.exists():
                return str(candidate)
    bash = shutil.which("bash")
    if bash and not any(part in bash.lower() for part in ("windowsapps", "system32")):
        return bash
    return None


POWERSHELLS = tuple(
    dict.fromkeys(
        executable
        for name in ("pwsh", "powershell")
        if (executable := shutil.which(name)) is not None
    )
)
BASH = _find_bash()


def _run_powershell_flow(
    tmp_path: Path, *, powershell: str, initially_ready: bool
) -> str:
    driver = r"""
$ErrorActionPreference = 'Stop'
$PrerequisiteMode = 'auto'
$InitialPath = $env:Path
$env:JARVIS_PYTHON = $null
function Write-Ok { param([string]$Text) }
function Write-Note { param([string]$Text) }
function Write-Err { param([string]$Text) }

__BLOCK__

$script:InitiallyReady = __INITIAL_READY__
$script:StateCalls = 0
$script:InstallCalls = 0
$script:Installed = ''

function New-FakeState {
    param([bool]$Ready)
    $python = [pscustomobject]@{
        Found = $Ready
        Exe = if ($Ready) { 'python' } else { $null }
        Version = if ($Ready) { '3.12.0' } else { $null }
        Closest = $null
    }
    return [pscustomobject]@{
        Python = $python
        GitFound = $Ready
        GitVersion = if ($Ready) { 'git version 2.50.0' } else { $null }
        Ready = $Ready
    }
}

function Get-PrerequisiteState {
    $script:StateCalls += 1
    if ($script:InitiallyReady -or $script:StateCalls -gt 1) {
        return New-FakeState $true
    }
    return New-FakeState $false
}

function Invoke-MissingPrerequisiteInstall {
    param($State)
    $script:InstallCalls += 1
    $items = @()
    if (-not $State.Python.Found) { $items += 'python' }
    if (-not $State.GitFound) { $items += 'git' }
    $script:Installed = $items -join ','
    return $true
}

function Wait-ForPrerequisites { return Get-PrerequisiteState }
function Refresh-ProcessPath { }

$result = Ensure-Prerequisites
if ($null -eq $result) { throw 'flow did not return a ready state' }
$summary = "READY=$($result.Ready);INSTALLS=$script:InstallCalls"
$summary += ";ITEMS=$script:Installed;STATES=$script:StateCalls"
Write-Output $summary
"""
    driver = driver.replace("__BLOCK__", _block(INSTALL_PS1)).replace(
        "__INITIAL_READY__", "$true" if initially_ready else "$false"
    )
    path = tmp_path / "driver.ps1"
    path.write_text(driver, encoding="utf-8")
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


def _run_bash_flow(tmp_path: Path, *, initially_ready: bool) -> str:
    driver = r"""#!/usr/bin/env bash
set -euo pipefail
PREREQUISITE_MODE=auto
unset JARVIS_PYTHON || true
FOUND_TOO_OLD=''
PYTHON_EXE=''
note() { :; }
ok() { :; }
err() { :; }
find_python() { return 1; }

__BLOCK__

INITIALLY_READY=__INITIAL_READY__
STATE_CALLS=0
INSTALL_CALLS=0
INSTALLED=''

refresh_prerequisite_state() {
    STATE_CALLS=$((STATE_CALLS + 1))
    if [ "$INITIALLY_READY" -eq 1 ] || [ "$STATE_CALLS" -gt 1 ]; then
        PYTHON_READY=1
        GIT_READY=1
        PREREQUISITES_READY=1
        PYTHON_EXE=python
    else
        PYTHON_READY=0
        GIT_READY=0
        PREREQUISITES_READY=0
        PYTHON_EXE=''
    fi
}

write_prerequisite_state() { :; }
detect_prerequisite_manager() {
    PREREQ_MANAGER=fake
    PREREQ_MANAGER_CMD=fake
    return 0
}
install_missing_prerequisites() {
    INSTALL_CALLS=$((INSTALL_CALLS + 1))
    INSTALLED=python,git
    return 0
}
wait_for_prerequisites() {
    refresh_prerequisite_state
    [ "$PREREQUISITES_READY" -eq 1 ]
}

ensure_prerequisites
printf 'READY=%s;INSTALLS=%s;ITEMS=%s;STATES=%s\n' \
    "$PREREQUISITES_READY" "$INSTALL_CALLS" "$INSTALLED" "$STATE_CALLS"
"""
    driver = driver.replace("__BLOCK__", _block(INSTALL_SH)).replace(
        "__INITIAL_READY__", "1" if initially_ready else "0"
    )
    path = tmp_path / "driver.sh"
    path.write_text(driver, encoding="utf-8", newline="\n")
    result = subprocess.run(
        [BASH, str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


@pytest.mark.parametrize("powershell", POWERSHELLS or (None,))
@pytest.mark.parametrize("initially_ready", [True, False])
def test_powershell_rechecks_and_continues_in_same_process(
    tmp_path: Path, powershell: str | None, initially_ready: bool
) -> None:
    if powershell is None:
        pytest.skip("PowerShell is not available")
    out = _run_powershell_flow(
        tmp_path, powershell=powershell, initially_ready=initially_ready
    )
    if initially_ready:
        assert out == "READY=True;INSTALLS=0;ITEMS=;STATES=1"
    else:
        assert out == "READY=True;INSTALLS=1;ITEMS=python,git;STATES=2"


@pytest.mark.parametrize("powershell", POWERSHELLS or (None,))
def test_powershell_probes_a_real_python_without_nested_quote_breakage(
    tmp_path: Path, powershell: str | None
) -> None:
    """Windows PowerShell 5 strips some nested native-command quotes."""
    if powershell is None:
        pytest.skip("PowerShell is not available")
    driver = r"""
$ErrorActionPreference = 'Stop'
$PrerequisiteMode = 'never'
$InitialPath = $env:Path
function Write-Ok { param([string]$Text) }
function Write-Note { param([string]$Text) }
function Write-Err { param([string]$Text) }
__BLOCK__
$probe = Test-PythonCandidate $env:JARVIS_TEST_PYTHON
if ($null -eq $probe) { throw 'real Python probe returned null' }
Write-Output "FOUND=$($probe.Compatible);VERSION=$($probe.Version)"
""".replace("__BLOCK__", _block(INSTALL_PS1))
    path = tmp_path / "real-python-probe.ps1"
    path.write_text(driver, encoding="utf-8")
    env = dict(os.environ, JARVIS_TEST_PYTHON=sys.executable)
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip().startswith("FOUND=True;VERSION=3.")


@pytest.mark.parametrize("powershell", POWERSHELLS or (None,))
def test_powershell_missing_prerequisite_never_blocks_redirected_input(
    tmp_path: Path, powershell: str | None
) -> None:
    if powershell is None:
        pytest.skip("PowerShell is not available")
    driver = r"""
$ErrorActionPreference = 'Stop'
$PrerequisiteMode = 'ask'
$InitialPath = $env:Path
function Write-Ok { param([string]$Text) }
function Write-Note { param([string]$Text) }
function Write-Err { param([string]$Text) }
__BLOCK__
$accepted = Request-PrerequisiteConsent @('Git')
Write-Output "ACCEPTED=$accepted"
""".replace("__BLOCK__", _block(INSTALL_PS1))
    path = tmp_path / "redirected-input.ps1"
    path.write_text(driver, encoding="utf-8")
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip() == "ACCEPTED=False"


@pytest.mark.skipif(BASH is None, reason="Bash is not available")
@pytest.mark.parametrize("initially_ready", [True, False])
def test_posix_rechecks_and_continues_in_same_process(
    tmp_path: Path, initially_ready: bool
) -> None:
    out = _run_bash_flow(tmp_path, initially_ready=initially_ready)
    if initially_ready:
        assert out == "READY=1;INSTALLS=0;ITEMS=;STATES=1"
    else:
        assert out == "READY=1;INSTALLS=1;ITEMS=python,git;STATES=2"


def test_windows_uses_exact_trusted_package_ids_and_refreshes_path() -> None:
    source = INSTALL_PS1.read_text(encoding="utf-8")
    assert "Invoke-PrerequisitePackage 'Python.Python.3.12'" in source
    assert "Invoke-PrerequisitePackage 'Git.Git'" in source
    assert "--exact --source winget" in source
    assert "--accept-source-agreements --accept-package-agreements" in source
    assert source.index("Refresh-ProcessPath") < source.index("$state = Get-PrerequisiteState")


def test_posix_covers_established_native_package_managers() -> None:
    source = INSTALL_SH.read_text(encoding="utf-8")
    for manager in ("Homebrew", "apt-get", "dnf", "yum", "zypper", "pacman", "apk"):
        assert manager in source
    # A curl-piped macOS shell may not have Homebrew's prefix on PATH yet.
    assert "/opt/homebrew/bin/git" in source
    assert "/usr/local/bin/git" in source
