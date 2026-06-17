"""Unit tests for ``CliTool`` — binary guard, output truncation, usage logging.

The tool runs a real subprocess. To stay portable (no dependency on an installed
CLI), the test spec uses the Python interpreter itself as the "binary" and runs
``python -c "..."`` snippets. This exercises the exact subprocess + truncation +
usage-logging path the real ``cli_<name>`` tools use.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.clis.auth import CliAuthManager
from jarvis.clis.spec import AuthConfig, CliSpec, InstallMethods, RiskConfig
from jarvis.clis.tool import (
    MAX_STDERR_CHARS,
    MAX_STDOUT_CHARS,
    CliTool,
    _noninteractive_env_for,
)
from jarvis.clis.usage_log import UsageLog
from jarvis.core.protocols import ExecutionContext


def _python_spec() -> CliSpec:
    binary = Path(sys.executable).name  # "python.exe" / "python"
    return CliSpec(
        name="pytool",
        display_name="Python Tool",
        description="test tool",
        homepage="",
        binary_name=binary,
        check_command=(binary, "--version"),
        version_parse_regex=r"(\d+\.\d+\.\d+)",
        install=InstallMethods(manual_url="https://python.org"),
        auth=AuthConfig(type="none"),
        risk=RiskConfig(default_tier="safe"),
    )


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="",
        config={},
        memory_read=None,
    )


def _make_tool(tmp_path: Path) -> tuple[CliTool, UsageLog]:
    spec = _python_spec()
    usage = UsageLog(db_path=tmp_path / "usage.db")
    auth = CliAuthManager()
    return CliTool(spec, auth=auth, usage_log=usage), usage


def _script_command(tmp_path: Path, body: str, *, name: str = "s.py") -> str:
    """Write a Python script and return a ``python <path>`` command.

    Avoids shell-quoting pitfalls: ``CliTool`` runs ``shlex.split(command,
    posix=False)`` on Windows, which keeps inner double-quotes literal. Running
    a script file sidesteps inline ``-c "..."`` quoting entirely so the test is
    portable across platforms.
    """
    binary = Path(sys.executable).name
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return f"{binary} {script}"


def test_tool_name_is_prefixed() -> None:
    tool, _ = _make_tool(Path("."))
    assert tool.name == "cli_pytool"


def test_tool_risk_tier_from_spec() -> None:
    tool, _ = _make_tool(Path("."))
    assert tool.risk_tier == "safe"


@pytest.mark.asyncio
async def test_empty_command_rejected(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path)
    result = await tool.execute({"command": "   "}, _ctx())
    assert result.success is False
    assert "command" in (result.error or "")


@pytest.mark.asyncio
async def test_binary_guard_rejects_other_binary(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path)
    result = await tool.execute({"command": "rm -rf /"}, _ctx())
    assert result.success is False
    assert "beginnen" in (result.error or "")


@pytest.mark.asyncio
async def test_successful_command_returns_exit_zero(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path)
    cmd = _script_command(tmp_path, "print('hello')")
    result = await tool.execute({"command": cmd}, _ctx())
    assert result.success is True
    assert result.output["exit_code"] == 0
    assert "hello" in result.output["stdout"]
    assert result.output["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_nonzero_exit_is_failure(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path)
    cmd = _script_command(tmp_path, "import sys; sys.exit(3)")
    result = await tool.execute({"command": cmd}, _ctx())
    assert result.success is False
    assert result.output["exit_code"] == 3
    assert "exit 3" in (result.error or "")


@pytest.mark.asyncio
async def test_stdout_is_truncated(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path)
    cmd = _script_command(tmp_path, "print('x' * 20000)")
    result = await tool.execute({"command": cmd}, _ctx())
    assert result.success is True
    assert len(result.output["stdout"]) <= MAX_STDOUT_CHARS


@pytest.mark.asyncio
async def test_stderr_is_truncated(tmp_path: Path) -> None:
    tool, _ = _make_tool(tmp_path)
    cmd = _script_command(tmp_path, "import sys; sys.stderr.write('e' * 20000)")
    result = await tool.execute({"command": cmd}, _ctx())
    assert len(result.output["stderr"]) <= MAX_STDERR_CHARS


@pytest.mark.asyncio
async def test_usage_logging_records_invocation(tmp_path: Path) -> None:
    tool, usage = _make_tool(tmp_path)
    cmd = _script_command(tmp_path, "print(1)")
    await tool.execute({"command": cmd}, _ctx())
    rows = usage.list_for("pytool", limit=10)
    assert len(rows) == 1
    assert rows[0].exit_code == 0
    assert rows[0].caller == "brain"
    # privacy: stdout is not persisted, only its length
    assert rows[0].stdout_len >= 1


@pytest.mark.asyncio
async def test_timeout_kills_and_logs_failure(tmp_path: Path) -> None:
    tool, usage = _make_tool(tmp_path)
    cmd = _script_command(tmp_path, "import time; time.sleep(10)")
    result = await tool.execute({"command": cmd, "timeout_s": 0.5}, _ctx())
    assert result.success is False
    assert "Timeout" in (result.error or "")
    rows = usage.list_for("pytool", limit=10)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Non-interactive execution (live repro 2026-06-17).
#
# `gcloud billing budgets list` emitted an interactive
# "Would you like to enable and retry (y/N)?" prompt. Without prompt
# suppression + a closed stdin a prompting CLI can hang on stdin until the 60s
# timeout under pythonw.exe (no console). Two defenses: a per-CLI
# non-interactive env (gcloud -> CLOUDSDK_CORE_DISABLE_PROMPTS=1) and
# stdin=DEVNULL for every CLI.
# ---------------------------------------------------------------------------


def test_noninteractive_env_for_gcloud_disables_prompts() -> None:
    env = _noninteractive_env_for("gcloud")
    assert env.get("CLOUDSDK_CORE_DISABLE_PROMPTS") == "1"


def test_noninteractive_env_for_unknown_binary_is_empty() -> None:
    assert _noninteractive_env_for("python") == {}


@pytest.mark.asyncio
async def test_execute_injects_noninteractive_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Configure the policy table so the test binary gets a probe var, then
    # prove execute() actually merges _noninteractive_env_for() into the child.
    import jarvis.clis.tool as tool_mod

    binary = Path(sys.executable).name
    monkeypatch.setitem(
        tool_mod._NONINTERACTIVE_ENV, binary, {"JARVIS_NONINTERACTIVE_PROBE": "1"}
    )
    tool, _ = _make_tool(tmp_path)
    cmd = _script_command(
        tmp_path,
        "import os; print(os.environ.get('JARVIS_NONINTERACTIVE_PROBE', 'UNSET'))",
    )
    result = await tool.execute({"command": cmd}, _ctx())
    assert result.success is True
    assert "1" in result.output["stdout"]


@pytest.mark.asyncio
async def test_stdin_is_devnull_so_reading_stdin_gets_eof(tmp_path: Path) -> None:
    # A command that reads stdin must get EOF immediately (DEVNULL), never block
    # waiting for input that will never arrive.
    tool, _ = _make_tool(tmp_path)
    cmd = _script_command(
        tmp_path,
        "import sys; d = sys.stdin.read(); "
        "print('STDIN_EOF' if d == '' else 'STDIN_DATA')",
    )
    result = await tool.execute({"command": cmd, "timeout_s": 5}, _ctx())
    assert result.success is True
    assert "STDIN_EOF" in result.output["stdout"]
