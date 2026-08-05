"""run_shell must EXECUTE commands, not echo them back as text.

Forensic 2026-07-13 18:15: on Windows, ``shlex.split(posix=False)`` kept the
surrounding quotes inside tokens, so ``powershell -Command "X"`` received a
string literal and echoed it with exit 0 — a false success that sent the
delegated brain into a retry loop until its iteration budget was exhausted.
"""

from __future__ import annotations

import sys

import pytest

from jarvis.plugins.tool.run_shell import RunShellTool, _windows_subprocess_env

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows shell semantics"
)
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX exec semantics"
)


@pytest.mark.asyncio
async def test_empty_command_is_rejected() -> None:
    result = await RunShellTool().execute({"command": "  "}, None)
    assert result.success is False


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
async def test_cmd_builtin_dir_works(tmp_path) -> None:
    (tmp_path / "probe-file.md").write_text("x", encoding="utf-8")
    result = await RunShellTool().execute(
        {"command": "dir /b", "cwd": str(tmp_path)},
        None,
    )
    assert result.success is True
    assert "probe-file.md" in result.output["stdout"]


@windows_only
@pytest.mark.asyncio
async def test_quoted_cmd_payload_is_executed() -> None:
    result = await RunShellTool().execute(
        {"command": 'cmd.exe /c "echo quoted-ok"'},
        None,
    )
    assert result.success is True
    assert "quoted-ok" in result.output["stdout"]


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


@windows_only
def test_windows_subprocess_env_rejects_unique_oversized_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [rf"C:\unique\{index:04d}\{'x' * 40}" for index in range(180)]
    monkeypatch.setenv("PATH", ";".join(entries))

    with pytest.raises(ValueError, match="remains too long for cmd.exe"):
        _windows_subprocess_env()


@posix_only
@pytest.mark.asyncio
async def test_posix_exec_path_unchanged() -> None:
    result = await RunShellTool().execute({"command": "echo hello"}, None)
    assert result.success is True
    assert "hello" in result.output["stdout"]
