"""Integration test: run_shell("format C:") gets blocked."""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import (
    SafetyBlacklistConfig,
    SafetyConfig,
    SafetyWhitelistConfig,
)
from jarvis.core.events import ActionDenied
from jarvis.plugins.tool.run_shell import RunShellTool
from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor


@pytest.mark.asyncio
async def test_format_is_blacklisted():
    bus = EventBus()
    safety = SafetyConfig(
        default_tier="safe",
        blacklist=SafetyBlacklistConfig(commands=[
            "run_shell format*",
            "run_shell *rm -rf /*",
        ]),
        whitelist=SafetyWhitelistConfig(commands=[]),
    )
    evaluator = RiskTierEvaluator(safety)
    approval = ApprovalWorkflow(bus)
    executor = ToolExecutor(bus, evaluator, approval)

    denied_events: list = []

    async def on_denied(e: ActionDenied):
        denied_events.append(e)

    bus.subscribe(ActionDenied, on_denied)

    tool = RunShellTool()
    result = await executor.execute(tool, {"command": "format c:"})
    assert result.success is False
    assert "blacklist" in (result.error or "").lower() or "blockiert" in (result.error or "").lower()

    # ActionDenied event was published
    assert len(denied_events) == 1
    assert "blacklist" in denied_events[0].reason.lower()


@pytest.mark.asyncio
async def test_git_status_goes_through_whitelist():
    bus = EventBus()
    safety = SafetyConfig(
        default_tier="safe",
        whitelist=SafetyWhitelistConfig(commands=["run_shell git status*"]),
        blacklist=SafetyBlacklistConfig(commands=[]),
    )
    evaluator = RiskTierEvaluator(safety)
    decision = evaluator.evaluate(RunShellTool(), {"command": "git status"})
    assert decision.tier == "safe"
    assert decision.approved_by == "whitelist"
