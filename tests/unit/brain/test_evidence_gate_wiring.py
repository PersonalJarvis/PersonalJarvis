"""Manager wiring for the evidence gate: override + defensive degradation."""
from types import SimpleNamespace

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
