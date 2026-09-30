"""Manager wiring for the evidence gate: override + defensive degradation."""
from types import SimpleNamespace

import pytest

from jarvis.brain.manager import BrainManager


def _bare_manager() -> BrainManager:
    m = BrainManager.__new__(BrainManager)
    m._tools = {"screenshot": object(), "cli_gam": object(), "spawn_worker": object()}
    return m


def test_smalltalk_override_keeps_required_evidence_tool():
    # "was steht heute an" classifies as smalltalk; the mandated evidence tool
    # must stay visible or the directive is unfulfillable (AD-CLI8).
    m = _bare_manager()
    m._evidence_required_tool = "cli_gam"
    visible = m._smalltalk_tool_override()
    assert "cli_gam" in visible
    assert "spawn_worker" not in visible


def test_smalltalk_override_keeps_a_cli_tool_without_a_mandate():
    # Since 2026-08-17 the override is a hide-list, not an allowlist: only the
    # spawn vehicles go. A CLI read tool needs no mandate to stay reachable.
    m = _bare_manager()
    m._evidence_required_tool = ""
    visible = m._smalltalk_tool_override()
    assert "cli_gam" in visible
    assert "spawn_worker" not in visible


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


def test_run_evidence_gate_stands_down_on_matched_skill_turn(monkeypatch):
    """AD-S3: a deterministically matched skill IS the capability — the gate
    must never overrule it. Live 2026-07-18 (voice 18:31): the paired-
    capability sync had not reached the serving process' CapabilityRegistry,
    so a CONNECTED Google Calendar plugin turn — freshly matched to
    plugin-google_calendar — was answered with the deterministic
    "no calendar access" refusal plus a CLI-setup detour."""
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
    # Empty capability registry — exactly the live failure state:
    monkeypatch.setattr(cap_mod, "get_registry", lambda: cap_mod.CapabilityRegistry())
    m._skill_turn_match = SimpleNamespace(name="plugin-google_calendar")
    verdict = m._run_evidence_gate("Kannst du mal meinen Kalender vorlesen?")
    assert verdict.kind == "pass"


def test_run_evidence_gate_disabled_passes():
    m = _bare_manager()
    m._config = SimpleNamespace(
        brain=SimpleNamespace(
            evidence_domains=SimpleNamespace(enabled=False, domains={})
        )
    )
    assert m._run_evidence_gate("Was steht heute noch an?").kind == "pass"


def test_run_evidence_gate_activity_refuses_honestly(monkeypatch):
    """Jarvis keeps no activity history, so a window/activity question gets an
    honest refusal — never a confabulated timeline."""
    import jarvis.clis.shared as shared
    import jarvis.core.capabilities as cap_mod
    from jarvis.core.config import EvidenceDomainsConfig

    m = _bare_manager()
    m._tools = {"screenshot": object()}
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


@pytest.mark.parametrize(
    ("pin", "expected_fragment"),
    [
        pytest.param("es", "calendario", id="spanish-pin"),
        pytest.param("de", "Kalenderzugriff", id="german-pin"),  # i18n-allow
        pytest.param("en", "calendar access", id="english-pin"),
    ],
)
def test_run_evidence_gate_refusal_follows_the_reply_language_pin(
    monkeypatch, pin: str, expected_fragment: str,
):
    """The refusal speaks the turn's language, not the utterance's.

    The gate used to sniff the utterance for its own refusal language and knew
    only German and English, so a Spanish user asking in English was told in
    English that there is no calendar access — the pin was ignored and there
    was no Spanish table to reach (OF-15). The utterance below is English on
    purpose: only the pin can produce the Spanish and German answers.
    """
    import jarvis.clis.shared as shared
    import jarvis.core.capabilities as cap_mod

    m = _bare_manager()
    m._reply_language = pin
    m._config = SimpleNamespace(
        brain=SimpleNamespace(
            evidence_domains=SimpleNamespace(
                enabled=True,
                domains={"calendar": ["calendar", "meetings"]},
            )
        )
    )
    monkeypatch.setattr(shared, "get_active_registry", lambda: None)
    monkeypatch.setattr(cap_mod, "get_registry", lambda: cap_mod.CapabilityRegistry())

    verdict = m._run_evidence_gate("What meetings do I have today?")

    assert verdict.kind == "honest_refusal"
    assert expected_fragment in verdict.refusal_text
