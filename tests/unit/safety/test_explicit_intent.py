"""Deletion consent must bind a single operation to its literal target."""

from __future__ import annotations

import sys

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.plugins.tool.run_shell import RunShellTool
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.explicit_intent import command_confirms_destruction
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor


@pytest.mark.parametrize(
    "utterance",
    [
        "List this directory. Do not delete any files.",
        "Do not delete /tmp/keep-me",
        "Bitte lösche /tmp/keep-me nicht",
        "No borres /tmp/keep-me",
        'The instructions say "delete /tmp/keep-me"',
        "Did you delete /tmp/keep-me?",
        "I deleted /tmp/keep-me yesterday",
        "delete /tmp/something-else",
        "delete keep-me",
        "delete /tmp/keep-me and keep /tmp/other",
        "",
    ],
)
def test_mentions_and_unbound_targets_do_not_authorize(utterance):
    assert not command_confirms_destruction("rm /tmp/keep-me", utterance)


@pytest.mark.parametrize(
    "command",
    [
        "rm /tmp/keep-me; rm /tmp/other",
        "rm /tmp/keep-me /tmp/other",
        "rm /tmp/*",
        "rm $(echo /tmp/keep-me)",
        "rm /tmp/keep-me > /tmp/other",
        "sh -c 'rm /tmp/keep-me'",
        "rm -rf /tmp/keep-me",
    ],
)
def test_compound_or_ambiguous_shell_keeps_confirmation(command):
    assert not command_confirms_destruction(command, "delete /tmp/keep-me")


@pytest.mark.parametrize(
    "utterance",
    [
        "delete /tmp/keep-me",
        "please remove the file /tmp/keep-me",
        "Bitte lösche die Datei /tmp/keep-me",
        "borra el archivo /tmp/keep-me",
    ],
)
def test_exact_single_deletion_is_authorized(utterance):
    assert command_confirms_destruction("rm -- /tmp/keep-me", utterance, windows=False)


def test_windows_literal_target_and_mismatch():
    assert command_confirms_destruction(
        "Remove-Item -LiteralPath 'C:/test/old folder' -Recurse",
        "delete the folder 'C:/test/old folder'",
        windows=True,
    )
    assert not command_confirms_destruction(
        "Remove-Item -LiteralPath C:/test/other",
        "delete C:/test/old",
    )
    assert not command_confirms_destruction("rm /", "delete /")
    assert not command_confirms_destruction("rm C:/test/file", "delete C:/test/file", windows=False)
    assert not command_confirms_destruction("rm /test/file", "delete /test/file", windows=True)


def test_shell_parsing_cannot_change_the_authorized_operation_or_target():
    target = r"/tmp/keep\me"  # noqa: S108 -- parser input, never opened
    assert not command_confirms_destruction(f"rm {target}", f"delete '{target}'", windows=False)
    assert command_confirms_destruction(f"rm '{target}'", f"delete '{target}'", windows=False)
    assert not command_confirms_destruction(
        "rm\n/tmp/command", "delete /tmp/command", windows=False
    )


@pytest.mark.parametrize("authorized", [False, True])
async def test_real_executor_leaves_protected_file_and_deletes_authorized_target(
    tmp_path, authorized
):
    target = tmp_path / "keep-me"
    target.write_text("fixture", encoding="utf-8")
    operand = target.as_posix()
    command = (
        f"Remove-Item -LiteralPath '{operand}'" if sys.platform == "win32" else f"rm -- '{operand}'"
    )
    utterance = (
        f"delete '{operand}'" if authorized else "List this directory. Do not delete any files."
    )
    bus = EventBus()
    executor = ToolExecutor(
        bus, RiskTierEvaluator(SafetyConfig(approval_mode="ask")), ApprovalWorkflow(bus)
    )
    result = await executor.execute(
        RunShellTool(),
        {"command": command},
        user_utterance=utterance,
        config_snapshot={"approval_surface": "unattended"},
    )
    assert result.success is authorized
    assert target.exists() is not authorized
    if not authorized:
        assert "approval" in result.error.lower()
