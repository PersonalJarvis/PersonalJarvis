"""Stage-1 Store-Python (MSIX) preference + venv-rebuild regression tests.

BUG-109: the Microsoft Store Python runs with MSIX package identity, so a venv
built from it virtualizes every ``%APPDATA%`` write into the package's hidden
LocalCache — the Start-Menu launcher never reaches the real Start Menu. The
Stage-1 installer therefore (a) prefers any non-Store interpreter and keeps the
Store build only as a last resort, and (b) rebuilds an existing Store-based
venv once a normal interpreter is selected. Both behaviors live in PowerShell
(they must run before Python exists), so these tests extract the marked blocks
and drive them with deterministic fakes — the same pattern as
``test_prerequisite_bootstrap.py``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
INSTALL_PS1 = REPO / "install" / "install.ps1"
PREREQ_BEGIN = "# --- prerequisite-bootstrap begin"
PREREQ_END = "# --- prerequisite-bootstrap end"
REBUILD_BEGIN = "# --- store-venv-rebuild begin"
REBUILD_END = "# --- store-venv-rebuild end"

POWERSHELLS = tuple(
    dict.fromkeys(
        executable
        for name in ("pwsh", "powershell")
        if (executable := shutil.which(name)) is not None
    )
)


def _block(begin: str, end: str) -> str:
    source = INSTALL_PS1.read_text(encoding="utf-8")
    assert begin in source and end in source
    return source[source.index(begin) : source.index(end)]


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run(powershell: str, script: str) -> str:
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    return result.stdout or ""


# The candidate scan needs deterministic fakes: `python` is a compatible Store
# build, `py` is a compatible normal build, everything else does not resolve.
_PREFERENCE_DRIVER = """
$ErrorActionPreference = 'Stop'
$env:JARVIS_PYTHON = $null
$env:LOCALAPPDATA = $null
$env:ProgramFiles = $null
function Write-Ok { param([string]$Text) }
function Write-Note { param([string]$Text) }
function Write-Err { param([string]$Text) }

__BLOCK__

function Test-PythonCandidate {
    param([string]$Exe)
    if ($Exe -eq 'python') {
        return [pscustomobject]@{ Exe = $Exe; Version = '3.13.1'; Compatible = $true }
    }
    if ($Exe -eq 'py' -and $env:FAKE_NON_STORE_PRESENT -eq '1') {
        return [pscustomobject]@{ Exe = $Exe; Version = '3.12.5'; Compatible = $true }
    }
    return $null
}

function Test-StorePythonSource {
    param([string]$Exe)
    return ($Exe -eq 'python')
}

$result = Find-CompatiblePython
Write-Output "FOUND=$($result.Found);EXE=$($result.Exe);STORE=$($result.IsStore)"
"""


@pytest.mark.skipif(not POWERSHELLS, reason="no PowerShell host available")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_find_compatible_python_prefers_non_store(powershell: str) -> None:
    """A Store build scanning FIRST must lose to a later non-Store build."""
    driver = _PREFERENCE_DRIVER.replace("__BLOCK__", _block(PREREQ_BEGIN, PREREQ_END))
    driver = "$env:FAKE_NON_STORE_PRESENT = '1'\n" + driver
    out = _run(powershell, driver)
    assert "FOUND=True;EXE=py;STORE=False" in out


@pytest.mark.skipif(not POWERSHELLS, reason="no PowerShell host available")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_find_compatible_python_uses_store_as_last_resort(powershell: str) -> None:
    """With ONLY the Store build present, it is still used — flagged IsStore."""
    driver = _PREFERENCE_DRIVER.replace("__BLOCK__", _block(PREREQ_BEGIN, PREREQ_END))
    driver = "$env:FAKE_NON_STORE_PRESENT = '0'\n" + driver
    out = _run(powershell, driver)
    assert "FOUND=True;EXE=python;STORE=True" in out


def _rebuild_driver(venv_path: Path, *, is_store_selected: bool) -> str:
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "function Write-Note { param([string]$Text) }\n"
        f"$VenvPath = {_ps_quote(str(venv_path))}\n"
        "$VenvPython = Join-Path $VenvPath 'Scripts/python.exe'\n"
        "$prerequisites = [pscustomobject]@{ Python = [pscustomobject]@{ "
        f"IsStore = ${str(is_store_selected).lower()} }}}}\n"
        + _block(REBUILD_BEGIN, REBUILD_END)
        + "Write-Output \"VENV_EXISTS=$(Test-Path $VenvPath)\"\n"
    )


def _make_fake_venv(tmp_path: Path, *, home: str) -> Path:
    venv = tmp_path / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "Scripts" / "python.exe").write_bytes(b"stub")
    (venv / "pyvenv.cfg").write_text(f"home = {home}\n", encoding="utf-8")
    return venv


@pytest.mark.skipif(not POWERSHELLS, reason="no PowerShell host available")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_store_venv_is_rebuilt_when_normal_python_selected(
    powershell: str, tmp_path: Path
) -> None:
    venv = _make_fake_venv(
        tmp_path,
        home=r"C:\Users\u\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_x",
    )
    out = _run(powershell, _rebuild_driver(venv, is_store_selected=False))
    assert "VENV_EXISTS=False" in out


@pytest.mark.skipif(not POWERSHELLS, reason="no PowerShell host available")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_store_venv_is_kept_when_store_python_still_selected(
    powershell: str, tmp_path: Path
) -> None:
    """No better interpreter available -> never delete the working venv."""
    venv = _make_fake_venv(
        tmp_path,
        home=r"C:\Users\u\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_x",
    )
    out = _run(powershell, _rebuild_driver(venv, is_store_selected=True))
    assert "VENV_EXISTS=True" in out


@pytest.mark.skipif(not POWERSHELLS, reason="no PowerShell host available")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_non_store_venv_is_never_rebuilt(powershell: str, tmp_path: Path) -> None:
    venv = _make_fake_venv(tmp_path, home=r"C:\Python313")
    out = _run(powershell, _rebuild_driver(venv, is_store_selected=False))
    assert "VENV_EXISTS=True" in out
