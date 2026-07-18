"""Tests for the completeness self-check (``jarvis/diagnostics/doctor.py``).

The doctor is the generalised defence against the phantom-jarvis-agent class of
bug (2026-06-28): a name advertised to the brain that resolves to no registered
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


def test_harness_config_flags_inert_legacy_openclaw_alias() -> None:
    """[harness.openclaw].enabled=true without registration → warn (dead config)."""
    config = SimpleNamespace(
        harness=SimpleNamespace(openclaw=SimpleNamespace(enabled=True)),
    )
    findings = check_harness_config(config)
    warns = [f for f in findings if f.status == "warn"]
    assert warns, "inert legacy openclaw alias config was not flagged"
    assert "inert" in warns[0].message


def test_harness_config_flags_unregistered_enabled_adapter() -> None:
    config = SimpleNamespace(
        harness=SimpleNamespace(
            enabled=["python-script", "mcp-remote"],
            jarvis_agent=None,
        ),
    )

    findings = check_harness_config(config)

    assert any(
        finding.status == "warn" and "mcp-remote" in finding.message
        for finding in findings
    )


def test_harness_config_flags_real_aliased_config() -> None:
    """The real Pydantic alias must not make inert config invisible."""
    from jarvis.core.config import HarnessConfig

    config = SimpleNamespace(
        harness=HarnessConfig.model_validate({
            "openclaw": {"enabled": True, "version": "test-only"},
        }),
    )

    findings = check_harness_config(config)

    assert any(f.status == "warn" and "inert" in f.message for f in findings)


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
        harness=SimpleNamespace(jarvis_agent=None),
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
        harness=SimpleNamespace(jarvis_agent=None),
        brain=SimpleNamespace(primary="gemini"),
    )
    findings = run_doctor(config)
    # The crashing check produced a fail finding for its category...
    assert any(f.category == "router-tools" and f.status == "fail" for f in findings)
    # ...and the other checks still ran.
    assert any(f.category == "brain-provider" for f in findings)
    assert has_failures(findings)


# ---------------------------------------------------------------------------
# Computer-Use prerequisites (deep-dive 2026-07-15, H-01)
# ---------------------------------------------------------------------------


def _cu_config(enabled: bool = True):
    return SimpleNamespace(computer_use=SimpleNamespace(enabled=enabled))


def test_cu_prereqs_silent_off_linux(monkeypatch) -> None:
    """Windows/macOS use native APIs — no Linux tool findings there."""
    monkeypatch.setattr("sys.platform", "win32")
    assert doctor.check_computer_use_prereqs(_cu_config()) == []


def test_cu_prereqs_flags_missing_xdotool_wmctrl_on_x11(monkeypatch) -> None:
    """A clean X11 box without xdotool/wmctrl gets a fail + install hint."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        "jarvis.platform.probes.display_present", lambda: True
    )
    monkeypatch.setattr("jarvis.platform.probes.is_wayland", lambda: False)
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    findings = doctor.check_computer_use_prereqs(_cu_config(enabled=True))
    tool_findings = [f for f in findings if "xdotool" in f.message]
    assert tool_findings and tool_findings[0].status == "fail"
    assert "apt install" in (tool_findings[0].hint or "")


def test_cu_prereqs_missing_tools_only_warn_when_cu_disabled(monkeypatch) -> None:
    """CU off: the gap is a warn (heads-up), never a hard doctor failure."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        "jarvis.platform.probes.display_present", lambda: True
    )
    monkeypatch.setattr("jarvis.platform.probes.is_wayland", lambda: False)
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    findings = doctor.check_computer_use_prereqs(_cu_config(enabled=False))
    tool_findings = [f for f in findings if "xdotool" in f.message]
    assert tool_findings and tool_findings[0].status == "warn"


def test_cu_prereqs_wayland_and_headless_are_info_not_failure(monkeypatch) -> None:
    """Wayland/headless are outside the support envelope BY DESIGN."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        "jarvis.platform.probes.display_present", lambda: False
    )
    findings = doctor.check_computer_use_prereqs(_cu_config())
    assert len(findings) == 1
    assert findings[0].status == "info"
    assert "headless" in findings[0].message

    monkeypatch.setattr(
        "jarvis.platform.probes.display_present", lambda: True
    )
    monkeypatch.setattr("jarvis.platform.probes.is_wayland", lambda: True)
    findings = doctor.check_computer_use_prereqs(_cu_config())
    assert len(findings) == 1
    assert findings[0].status == "info"
    assert "Wayland" in findings[0].message
