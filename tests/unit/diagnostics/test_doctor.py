"""Tests for the completeness self-check (``jarvis/diagnostics/doctor.py``).

The doctor is the generalised defence against the phantom-openclaw class of bug
(2026-06-28): a name advertised to the brain that resolves to no registered
backend. These tests pin the two highest-value checks — phantom router tools and
inert harness config — plus the isolation guarantee that one crashing probe never
blinds the rest of the report.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.diagnostics import doctor
from jarvis.diagnostics.doctor import (
    check_brain_provider,
    check_harness_config,
    check_router_tools,
    has_failures,
    run_doctor,
)


def test_router_tools_ok_with_real_registry() -> None:
    """The shipped ROUTER_TOOLS set must have NO phantom — every name resolves.

    This is the regression guard for the original bug: a router tool advertised
    but not registered. After the 2026-06-28 fix this is clean.
    """
    findings = check_router_tools()
    assert len(findings) == 1
    assert findings[0].status == "ok", findings[0].message


def test_router_tools_detects_phantom(monkeypatch) -> None:
    """A router tool with no registered entry-point is flagged as a phantom."""
    # Simulate a registry where jarvis.tool entry-points are empty → every
    # ROUTER_TOOLS name that is not a self-mod tool becomes a phantom.
    monkeypatch.setattr(doctor, "entry_points", lambda *a, **k: [])
    findings = check_router_tools()
    assert len(findings) == 1
    assert findings[0].status == "fail"
    assert "advertised but not registered" in findings[0].message


def test_harness_config_flags_inert_openclaw() -> None:
    """[harness.openclaw].enabled=true without registration → warn (dead config)."""
    config = SimpleNamespace(
        harness=SimpleNamespace(openclaw=SimpleNamespace(enabled=True)),
    )
    findings = check_harness_config(config)
    warns = [f for f in findings if f.status == "warn"]
    assert warns, "inert openclaw config was not flagged"
    assert "inert" in warns[0].message


def test_harness_config_silent_when_disabled() -> None:
    config = SimpleNamespace(
        harness=SimpleNamespace(openclaw=SimpleNamespace(enabled=False)),
    )
    findings = check_harness_config(config)
    assert not any(f.status == "warn" for f in findings)


def test_harness_config_silent_when_no_block() -> None:
    config = SimpleNamespace(harness=SimpleNamespace(openclaw=None))
    findings = check_harness_config(config)
    assert not any(f.status == "warn" for f in findings)


def test_brain_provider_fail_when_empty() -> None:
    config = SimpleNamespace(brain=SimpleNamespace(primary=""))
    findings = check_brain_provider(config)
    assert findings[0].status == "fail"


def test_brain_provider_info_when_set() -> None:
    config = SimpleNamespace(brain=SimpleNamespace(primary="gemini"))
    findings = check_brain_provider(config)
    assert findings[0].status == "info"
    assert "gemini" in findings[0].message


def test_run_doctor_aggregates_all_categories() -> None:
    config = SimpleNamespace(
        harness=SimpleNamespace(openclaw=None),
        brain=SimpleNamespace(primary="gemini"),
    )
    findings = run_doctor(config)
    cats = {f.category for f in findings}
    assert {
        "router-tools",
        "harness-config",
        "subagent-backend",
        "brain-provider",
    } <= cats


def test_run_doctor_isolates_a_crashing_check(monkeypatch) -> None:
    """A crashing probe must not blind the rest of the report."""
    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(doctor, "check_router_tools", boom)
    config = SimpleNamespace(
        harness=SimpleNamespace(openclaw=None),
        brain=SimpleNamespace(primary="gemini"),
    )
    findings = run_doctor(config)
    # The crashing check produced a fail finding for its category...
    assert any(f.category == "router-tools" and f.status == "fail" for f in findings)
    # ...and the other checks still ran.
    assert any(f.category == "brain-provider" for f in findings)
    assert has_failures(findings)
