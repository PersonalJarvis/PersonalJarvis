"""command_impact must never under-escalate: reads may be misread as modify,
but a destructive command must never classify as read."""

from __future__ import annotations

import pytest

from jarvis.plugins.tool.run_shell import RunShellTool
from jarvis.safety.command_impact import (
    DESTRUCTIVE,
    MODIFY,
    READ,
    classify_command,
)


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat notes.txt",
        "Get-ChildItem -Name",
        "gci",
        "dir",
        "git status",
        "git log --oneline",
        "echo hello | tr a-z A-Z",
        "ps aux | grep python | wc -l",
        "Get-Process | Select-Object -First 3",
    ],
)
def test_read_commands(command: str) -> None:
    assert classify_command(command).level == READ


@pytest.mark.parametrize(
    "command",
    [
        "mv a.txt b.txt",
        "mkdir new-folder",
        "New-Item -ItemType File probe.md",
        "npm install",
        "git push origin main",
        "sed -i s/a/b/ file.txt",
        "echo data > out.txt",          # redirect escalates a read
        "cat a.txt >> b.txt",
        "some-unknown-binary --flag",   # unknown must never default to read
        "Stop-Process -Name notepad",
    ],
)
def test_modify_commands(command: str) -> None:
    assert classify_command(command).level == MODIFY


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf ~/old-project",
        "sudo rm -rf /var/log/foo",
        "del C:\\temp\\x.txt",
        "Remove-Item -Recurse -Force C:\\temp",
        "Clear-Content log.txt",
        "format d:",
        "dd if=/dev/zero of=/dev/sda",
        "shutdown -h now",
        "Stop-Computer",
        "git reset --hard HEAD~1",
        "git clean -fd",
        "reg delete HKCU\\Software\\Foo /f",
        "find . -name '*.tmp' -delete",
        "echo ok && rm -rf build",      # worst segment wins across chains
        "ls | xargs rm",                # destructive after a pipe...
    ],
)
def test_destructive_commands(command: str) -> None:
    assert classify_command(command).level == DESTRUCTIVE


def test_powershell_verb_fallback() -> None:
    assert classify_command("Uninstall-Module Foo").level == DESTRUCTIVE
    assert classify_command("Get-Whatever").level == READ
    assert classify_command("Set-Whatever x").level == MODIFY  # unknown verb


def test_empty_command_reads() -> None:
    assert classify_command("").level == READ


def test_commands_are_collected_for_previews() -> None:
    impact = classify_command("ls | grep foo && rm -rf build")
    assert "ls" in impact.commands
    assert "rm" in impact.commands


# ── risk_tier_for_args hook on RunShellTool ──────────────────────────────


def test_run_shell_escalates_destructive_to_ask() -> None:
    tier = RunShellTool().risk_tier_for_args({"command": "rm -rf ~/x"})
    assert tier == "ask"


def test_run_shell_keeps_static_tier_for_reads_and_modifies() -> None:
    tool = RunShellTool()
    assert tool.risk_tier_for_args({"command": "ls -la"}) is None
    assert tool.risk_tier_for_args({"command": "mkdir x"}) is None
    assert tool.risk_tier_for_args({"command": ""}) is None


# ── describe_args hook (explain layer) ───────────────────────────────────


def test_run_shell_describe_args_summarizes_the_impact() -> None:
    summary = RunShellTool().describe_args({"command": "rm -rf build"})
    assert summary == {"level": DESTRUCTIVE, "commands": "rm"}


def test_run_shell_describe_args_dedupes_command_words() -> None:
    summary = RunShellTool().describe_args(
        {"command": "ls a | grep x && ls b"}
    )
    assert summary is not None
    assert summary["commands"] == "ls, grep"


def test_run_shell_describe_args_empty_command_is_none() -> None:
    assert RunShellTool().describe_args({"command": "  "}) is None
