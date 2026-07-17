"""Windows-only dev scripts must refuse to run on other platforms.

Both scripts drive raw Win32 surfaces (win32gui window enumeration,
ctypes.windll layered-window capture) that do not exist off Windows.
The guard has to sit ABOVE the heavy/Windows-only imports so that on
macOS/Linux the script exits with a one-line message instead of an
ImportError traceback (and without importing PIL / pywin32 at all).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# (script path, first Windows-only token the guard must precede)
SCRIPTS = [
    (REPO_ROOT / "scripts" / "smoke_poav_5terminals.py", "import win32"),
    (REPO_ROOT / "scripts" / "jarvisbar_live_probe.py", "ctypes.windll"),
]

GUARD_LINE = 'if sys.platform != "win32":'


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the guard only fires off Windows",
)
@pytest.mark.parametrize(
    "script", [s for s, _ in SCRIPTS], ids=lambda p: p.name
)
def test_script_exits_with_guard_message_off_windows(script: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "run it on Windows" in result.stderr


@pytest.mark.parametrize(
    ("script", "windows_token"), SCRIPTS, ids=lambda v: getattr(v, "name", v)
)
def test_guard_sits_above_the_first_windows_only_import(
    script: Path, windows_token: str
) -> None:
    """Textual contract: the platform guard must never drift below the
    Windows-only imports/calls, or off-Windows runs crash with an
    ImportError/AttributeError instead of the honest exit message."""
    source = script.read_text(encoding="utf-8")
    assert GUARD_LINE in source, f"{script.name} lost its platform guard"
    assert windows_token in source, (
        f"{script.name} no longer contains {windows_token!r}; "
        "update this contract test alongside the script"
    )
    assert source.index(GUARD_LINE) < source.index(windows_token), (
        f"{script.name}: the platform guard must come before "
        f"{windows_token!r}"
    )
