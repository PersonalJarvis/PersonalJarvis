"""Unit-Tests für RiskTierEvaluator."""
from __future__ import annotations

import pytest

from jarvis.core.config import (
    SafetyBlacklistConfig,
    SafetyConfig,
    SafetyWhitelistConfig,
)
from jarvis.safety.risk_tier import ActionBlocked, RiskTierEvaluator


class _StubTool:
    def __init__(self, name: str, risk_tier: str = "ask"):
        self.name = name
        self.risk_tier = risk_tier
        self.schema: dict = {}
        self.description = ""

    async def execute(self, args, ctx):  # pragma: no cover - stub
        raise NotImplementedError


def _make_safety(
    whitelist: list[str] | None = None,
    blacklist: list[str] | None = None,
    default_tier: str = "safe",
) -> SafetyConfig:
    return SafetyConfig(
        default_tier=default_tier,
        whitelist=SafetyWhitelistConfig(commands=whitelist or []),
        blacklist=SafetyBlacklistConfig(commands=blacklist or []),
    )


def test_default_tier_from_tool():
    ev = RiskTierEvaluator(_make_safety())
    tool = _StubTool("run_shell", risk_tier="monitor")
    decision = ev.evaluate(tool, {"command": "echo hi"})
    assert decision.tier == "monitor"
    assert decision.approved_by is None


def test_whitelist_downgrades_to_safe():
    ev = RiskTierEvaluator(_make_safety(whitelist=["run_shell git status*"]))
    tool = _StubTool("run_shell", risk_tier="ask")
    decision = ev.evaluate(tool, {"command": "git status"})
    assert decision.tier == "safe"
    assert decision.approved_by == "whitelist"


def test_blacklist_blocks_hard():
    ev = RiskTierEvaluator(_make_safety(blacklist=["run_shell format*"]))
    tool = _StubTool("run_shell", risk_tier="safe")
    with pytest.raises(ActionBlocked):
        ev.evaluate(tool, {"command": "format c:"})


def test_blacklist_beats_whitelist():
    ev = RiskTierEvaluator(_make_safety(
        whitelist=["run_shell *"],
        blacklist=["run_shell format *"],
    ))
    tool = _StubTool("run_shell")
    with pytest.raises(ActionBlocked):
        ev.evaluate(tool, {"command": "format c:"})


def test_case_insensitive_match():
    ev = RiskTierEvaluator(_make_safety(blacklist=["run_shell FORMAT*"]))
    tool = _StubTool("run_shell")
    with pytest.raises(ActionBlocked):
        ev.evaluate(tool, {"command": "format C:"})


def test_needs_confirmation_ask_tier():
    ev = RiskTierEvaluator(_make_safety())
    tool = _StubTool("delete_file", risk_tier="ask")
    decision = ev.evaluate(tool, {"path": "/tmp/x"})
    assert ev.needs_user_confirmation(decision) is True


def test_needs_confirmation_whitelist_skips():
    ev = RiskTierEvaluator(_make_safety(whitelist=["delete_file *"]))
    tool = _StubTool("delete_file", risk_tier="ask")
    decision = ev.evaluate(tool, {"path": "/tmp/x"})
    assert ev.needs_user_confirmation(decision) is False
