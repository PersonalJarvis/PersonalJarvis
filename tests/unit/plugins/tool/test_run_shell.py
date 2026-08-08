"""run_shell must EXECUTE commands in the shell the model expects.

Forensic 2026-07-13 18:15: on Windows, ``shlex.split(posix=False)`` kept the
surrounding quotes inside tokens, so ``powershell -Command "X"`` received a
string literal and echoed it with exit 0 — a false success that sent the
delegated brain into a retry loop until its iteration budget was exhausted.

Redesign 2026-08-08: Windows now runs Windows PowerShell (via
``-EncodedCommand``), POSIX runs ``/bin/sh -c``. The tests below pin the
contract that matters to the brain: commands execute (not echo), native exit
codes propagate (no false success), and pipes work on both platforms.
"""

from __future__ import annotations

import sys

import pytest

from jarvis.plugins.tool.run_shell import RunShellTool, _windows_subprocess_env

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows PowerShell semantics"
)
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX sh semantics"
)


@pytest.mark.asyncio
async def test_empty_command_is_rejected() -> None:
    result = await RunShellTool().execute({"command": "  "}, None)
    assert result.success is False


def test_description_names_the_shell() -> None:
    """The brain writes commands for the shell the description declares."""
    description = RunShellTool().description
    if sys.platform == "win32":
        assert "PowerShell" in description
    else:
        assert "/bin/sh" in description


@windows_only
@pytest.mark.asyncio
async def test_quoted_powershell_command_is_executed_not_echoed() -> None:
    result = await RunShellTool().execute(
        {"command": 'powershell -Command "Write-Output hello-from-ps"'},
        None,
    )
    assert result.success is True
    stdout = result.output["stdout"]
    assert "hello-from-ps" in stdout
    assert "Write-Output" not in stdout


@windows_only
@pytest.mark.asyncio
async def test_powershell_cmdlet_works(tmp_path) -> None:
    (tmp_path / "probe-file.md").write_text("x", encoding="utf-8")
    result = await RunShellTool().execute(
        {"command": "Get-ChildItem -Name", "cwd": str(tmp_path)},
        None,
    )
    assert result.success is True
    assert "probe-file.md" in result.output["stdout"]


@windows_only
@pytest.mark.asyncio
async def test_pipes_work_on_windows() -> None:
    result = await RunShellTool().execute(
        {"command": 'Write-Output alpha beta | Select-Object -First 1'},
        None,
    )
    assert result.success is True
    stdout = result.output["stdout"]
    assert "alpha" in stdout
    assert "beta" not in stdout


@windows_only
@pytest.mark.asyncio
async def test_native_exit_code_propagates() -> None:
    """PowerShell itself exits 0 after a failed native command — the wrapper
    must surface the REAL exit code, or the brain sees a false success."""
    result = await RunShellTool().execute({"command": "cmd.exe /c exit 7"}, None)
    assert result.success is False
    assert result.output["exit_code"] == 7


@windows_only
@pytest.mark.asyncio
async def test_failing_cmdlet_is_not_a_false_success() -> None:
    result = await RunShellTool().execute(
        {"command": "Get-Item C:\\definitely-missing-probe-xyz"},
        None,
    )
    assert result.success is False
    stderr = result.output["stderr"]
    assert "definitely-missing-probe-xyz" in stderr
    assert "CLIXML" not in stderr  # error must be plain text the brain can read


@windows_only
def test_windows_subprocess_env_compacts_semantic_path_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv(
        "PATH",
        (
            r'"C:\Tools";c:\tools\;%SystemRoot%\System32;'
            r"C:\Windows\System32;C:\Other"
        ),
    )
    monkeypatch.setenv("JARVIS_TEST_SENTINEL", "preserved")

    env = _windows_subprocess_env()

    assert env["PATH"].split(";") == [
        r'"C:\Tools"',
        r"%SystemRoot%\System32",
        r"C:\Other",
    ]
    assert env["JARVIS_TEST_SENTINEL"] == "preserved"


@posix_only
@pytest.mark.asyncio
async def test_posix_simple_command_works() -> None:
    result = await RunShellTool().execute({"command": "echo hello"}, None)
    assert result.success is True
    assert "hello" in result.output["stdout"]


@posix_only
@pytest.mark.asyncio
async def test_pipes_work_on_posix() -> None:
    result = await RunShellTool().execute(
        {"command": "echo hello | tr a-z A-Z"}, None
    )
    assert result.success is True
    assert "HELLO" in result.output["stdout"]


@posix_only
@pytest.mark.asyncio
async def test_posix_exit_code_propagates() -> None:
    result = await RunShellTool().execute({"command": "exit 7"}, None)
    assert result.success is False
    assert result.output["exit_code"] == 7


@posix_only
@pytest.mark.asyncio
async def test_posix_missing_command_is_readable_failure() -> None:
    """sh reports 127 with the command name on stderr — the brain needs that
    text to reformulate instead of retrying blindly."""
    result = await RunShellTool().execute(
        {"command": "definitely-missing-cmd-xyz"}, None
    )
    assert result.success is False
    assert result.output["exit_code"] == 127
    assert "definitely-missing-cmd-xyz" in result.output["stderr"]
