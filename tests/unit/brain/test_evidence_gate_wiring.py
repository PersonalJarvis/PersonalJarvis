"""Manager wiring for the evidence gate: override + defensive degradation."""
from types import SimpleNamespace

import pytest

from jarvis.brain.manager import BrainManager


def _bare_manager() -> BrainManager:
    m = BrainManager.__new__(BrainManager)
    m._tools = {"screenshot": object(), "cli_gam": object(), "spawn-worker": object()}
    return m


def test_smalltalk_override_keeps_required_evidence_tool():
    m = _bare_manager()
    m._evidence_required_tool = "cli_gam"
    visible = m._smalltalk_tool_override()
    assert "cli_gam" in visible
    assert "spawn-worker" not in visible


def test_smalltalk_override_unchanged_without_required_tool():
    m = _bare_manager()
    m._evidence_required_tool = ""
    visible = m._smalltalk_tool_override()
    assert "cli_gam" not in visible


def test_run_evidence_gate_degrades_to_pass_on_missing_config():
    m = _bare_manager()
    m._config = SimpleNamespace(brain=SimpleNamespace())  # no evidence_domains
    verdict = m._run_evidence_gate("Was steht heute noch an?")
    assert verdict.kind == "pass"


def test_run_evidence_gate_refuses_without_any_integration(monkeypatch):
    import jarvis.clis.shared as shared
    import jarvis.core.capabilities as cap_mod

    m = _bare_manager()
    m._config = SimpleNamespace(
        brain=SimpleNamespace(
            evidence_domains=SimpleNamespace(
                enabled=True,
                domains={"calendar": ["kalender", "steht heute"]},
            )
        )
    )
    monkeypatch.setattr(shared, "get_active_registry", lambda: None)
    # Fresh, empty capability registry so no other source covers the domain:
    monkeypatch.setattr(cap_mod, "get_registry", lambda: cap_mod.CapabilityRegistry())
    verdict = m._run_evidence_gate("Was steht heute noch an?")
    assert verdict.kind == "honest_refusal"


def test_run_evidence_gate_disabled_passes():
    m = _bare_manager()
    m._config = SimpleNamespace(
        brain=SimpleNamespace(
            evidence_domains=SimpleNamespace(enabled=False, domains={})
        )
    )
    assert m._run_evidence_gate("Was steht heute noch an?").kind == "pass"


def test_run_evidence_gate_maps_activity_to_awareness_recall(monkeypatch):
    """Window/activity-history questions force the always-on awareness-recall.

    The fast brain otherwise confabulates "der lokale Verlaufsspeicher ist nicht
    verfügbar" without ever calling the tool (live 2026-06-18). awareness-recall
    is an internal router tool, not a connected CLI, so the manager must inject
    the domain→tool mapping itself.
    """
    import jarvis.clis.shared as shared
    import jarvis.core.capabilities as cap_mod
    from jarvis.core.config import EvidenceDomainsConfig

    m = _bare_manager()
    m._tools = {"awareness-recall": object()}
    m._config = SimpleNamespace(
        brain=SimpleNamespace(
            evidence_domains=SimpleNamespace(
                enabled=True, domains=EvidenceDomainsConfig().domains,
            )
        )
    )
    monkeypatch.setattr(shared, "get_active_registry", lambda: None)
    monkeypatch.setattr(cap_mod, "get_registry", lambda: cap_mod.CapabilityRegistry())
    verdict = m._run_evidence_gate("Was hatte ich heute offen?")
    assert verdict.kind == "require_tool"
    assert verdict.tool_name == "awareness-recall"


def test_run_evidence_gate_activity_refuses_when_tool_absent(monkeypatch):
    """If awareness-recall is not registered, the activity domain degrades to an
    honest refusal — never a confabulated outage."""
    import jarvis.clis.shared as shared
    import jarvis.core.capabilities as cap_mod
    from jarvis.core.config import EvidenceDomainsConfig

    m = _bare_manager()
    m._tools = {"screenshot": object()}  # no awareness-recall
    m._config = SimpleNamespace(
        brain=SimpleNamespace(
            evidence_domains=SimpleNamespace(
                enabled=True, domains=EvidenceDomainsConfig().domains,
            )
        )
    )
    monkeypatch.setattr(shared, "get_active_registry", lambda: None)
    monkeypatch.setattr(cap_mod, "get_registry", lambda: cap_mod.CapabilityRegistry())
    verdict = m._run_evidence_gate("Was hatte ich heute offen?")
    assert verdict.kind == "honest_refusal"


class _FakeResult:
    def __init__(self, success, output):
        self.success = success
        self.output = output


class _FakeExecutor:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def execute(self, tool, args, **kw):
        self.calls.append((tool, args, kw))
        return self._result


@pytest.mark.asyncio
async def test_prefetch_activity_block_returns_tool_output():
    """The deterministic pre-fetch returns the awareness-recall output so the
    brain answers from real data instead of confabulating an outage (the fast
    brain never calls the soft-mandated tool itself — live 2026-06-18)."""
    m = _bare_manager()
    tool = object()
    m._tools = {"awareness-recall": tool}
    execr = _FakeExecutor(_FakeResult(True, "Recent activity: - [20:49, Terminal.exe]"))
    m._tool_executor = execr
    block = await m._prefetch_activity_block(
        "awareness-recall", "Was hatte ich heute offen?",
    )
    assert "Recent activity" in block
    # It went through the ToolExecutor (never a direct Tool.execute — AP-3).
    assert execr.calls and execr.calls[0][0] is tool


@pytest.mark.asyncio
async def test_prefetch_activity_block_empty_on_failure_keeps_fallback():
    m = _bare_manager()
    m._tools = {"awareness-recall": object()}
    m._tool_executor = _FakeExecutor(_FakeResult(False, ""))
    assert await m._prefetch_activity_block("awareness-recall", "x") == ""


@pytest.mark.asyncio
async def test_prefetch_activity_block_no_tool_or_executor():
    m = _bare_manager()
    m._tools = {}
    m._tool_executor = _FakeExecutor(_FakeResult(True, "x"))
    assert await m._prefetch_activity_block("awareness-recall", "x") == ""
    m._tools = {"awareness-recall": object()}
    m._tool_executor = None
    assert await m._prefetch_activity_block("awareness-recall", "x") == ""
