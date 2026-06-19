"""Unit-Tests für RiskTierEvaluator."""
from __future__ import annotations

import logging

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


# ----------------------------------------------------------------------
# Per-action risk (forensic 2026-06-19, session dc533e39): a read-only
# ``gmail action=list_messages`` (the morning-routine "check unread mail"
# step) was forced through the ask-tier confirmation and Jarvis spoke
# "Soll ich die E-Mail wirklich senden?" for a calendar question. The
# whole gmail tool declared risk_tier="ask" and ``evaluate`` only ever
# looked at ``tool.risk_tier``, never at the action. A tool may now expose
# an optional ``risk_tier_for_args(args)`` hook so reads stay ``safe`` and
# only ``send_message`` is consequential.
# ----------------------------------------------------------------------


class _ActionTool:
    """Stub tool with an optional per-action risk hook."""

    def __init__(
        self,
        name: str,
        risk_tier: str = "ask",
        tiers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.risk_tier = risk_tier
        self.schema: dict = {}
        self.description = ""
        self._tiers = tiers or {}

    def risk_tier_for_args(self, args):
        return self._tiers.get((args.get("action") or "").strip())

    async def execute(self, args, ctx):  # pragma: no cover - stub
        raise NotImplementedError


def test_per_action_hook_downgrades_read_action():
    ev = RiskTierEvaluator(_make_safety())
    tool = _ActionTool(
        "gmail", risk_tier="ask",
        tiers={"list_messages": "safe", "send_message": "ask"},
    )
    decision = ev.evaluate(tool, {"action": "list_messages"})
    assert decision.tier == "safe"
    assert ev.needs_user_confirmation(decision) is False


def test_per_action_hook_keeps_send_action_ask():
    ev = RiskTierEvaluator(_make_safety())
    tool = _ActionTool(
        "gmail", risk_tier="ask",
        tiers={"list_messages": "safe", "send_message": "ask"},
    )
    decision = ev.evaluate(tool, {"action": "send_message"})
    assert decision.tier == "ask"
    assert ev.needs_user_confirmation(decision) is True


def test_per_action_hook_none_falls_back_to_static_tier():
    # Hook returns None for an unmapped action → static tool tier wins.
    ev = RiskTierEvaluator(_make_safety())
    tool = _ActionTool("gmail", risk_tier="ask", tiers={})
    decision = ev.evaluate(tool, {"action": "whatever"})
    assert decision.tier == "ask"


def test_per_action_hook_blacklist_still_wins():
    # Blacklist is evaluated before the per-action hook can downgrade.
    ev = RiskTierEvaluator(_make_safety(blacklist=["gmail send_message*"]))
    tool = _ActionTool("gmail", risk_tier="ask", tiers={"send_message": "safe"})
    with pytest.raises(ActionBlocked):
        ev.evaluate(tool, {"action": "send_message"})


def test_per_action_hook_exception_is_safe_and_falls_back(caplog):
    class _BrokenTool(_ActionTool):
        def risk_tier_for_args(self, args):
            raise RuntimeError("boom")

    ev = RiskTierEvaluator(_make_safety())
    tool = _BrokenTool("gmail", risk_tier="ask")
    with caplog.at_level(logging.WARNING):
        decision = ev.evaluate(tool, {"action": "list_messages"})
    # A broken hook must never crash the gate — it falls back to the
    # conservative static tier, AND the anomaly is observable (not a silent
    # swallow on the safety path).
    assert decision.tier == "ask"
    assert any("boom" in r.message or "raised" in r.message.lower() for r in caplog.records)


def test_per_action_hook_invalid_tier_is_rejected_and_warns(caplog):
    # A hook returning a value outside the RiskTier vocabulary (typo, wrong
    # casing) must NOT be assigned silently — otherwise it would miss both the
    # always_block and always_confirm membership checks and behave as the most
    # permissive option with no signal. It is rejected → static tier wins.
    ev = RiskTierEvaluator(_make_safety())
    tool = _ActionTool("gmail", risk_tier="ask", tiers={"list_messages": "SAFE"})
    with caplog.at_level(logging.WARNING):
        decision = ev.evaluate(tool, {"action": "list_messages"})
    assert decision.tier == "ask"
    assert ev.needs_user_confirmation(decision) is True
    assert any("SAFE" in r.message or "unknown tier" in r.message.lower()
               for r in caplog.records)


def test_invalid_static_risk_tier_falls_back_to_default():
    # A misconfigured plugin with a truthy-but-invalid static tier must fall
    # back to the config default, never carry the bogus value forward.
    ev = RiskTierEvaluator(_make_safety(default_tier="monitor"))
    tool = _StubTool("weird", risk_tier="bogus")
    decision = ev.evaluate(tool, {})
    assert decision.tier == "monitor"


def test_tool_without_hook_is_unchanged():
    ev = RiskTierEvaluator(_make_safety())
    tool = _StubTool("delete_file", risk_tier="ask")
    decision = ev.evaluate(tool, {"path": "x"})
    assert decision.tier == "ask"
