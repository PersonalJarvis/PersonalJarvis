"""Future catalog additions require real, current, complete browser evidence."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SPEC = spec_from_file_location(
    "plugin_auth_gate",
    Path(__file__).resolve().parents[3] / "scripts/ci/check_plugin_auth_contract.py",
)
assert _SPEC is not None and _SPEC.loader is not None
gate = module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


@pytest.fixture
def journey():
    plugin = {"id": "github", "auth": {"mode": "oauth_pkce_loopback"}}
    row = {
        "plugin_id": "github",
        "result": "PASS",
        "config_fingerprint": gate.config_fingerprint(plugin),
        "evidence": {
            stage: {
                "status": "PASS",
                "reference": "docs/marketplace/plugin-auth-audit.md",
                "detail": "Observed the actual provider journey in the browser.",
                **({"real_action": True} if stage == "smoke" else {}),
            }
            for stage in gate.E2E_STAGES
        },
    }
    return plugin, {"schema_version": 1, "plugins": [row]}


def test_complete_journey_passes(journey):
    plugin, audit = journey
    assert gate.validate_e2e_audit([plugin], audit, strict=True) == []


@pytest.mark.parametrize("field", gate.FINGERPRINT_FIELDS)
def test_auth_or_execution_change_invalidates_evidence(journey, field):
    plugin, audit = journey
    plugin[field] = {"changed": True}
    assert any("stale" in item for item in gate.validate_e2e_audit([plugin], audit))


def test_cosmetic_change_does_not_invalidate_evidence(journey):
    plugin, audit = journey
    plugin["description"] = "Updated cosmetic description"
    assert gate.validate_e2e_audit([plugin], audit) == []


@pytest.mark.parametrize("stage", gate.E2E_STAGES)
def test_pass_requires_every_stage(journey, stage):
    plugin, audit = journey
    del audit["plugins"][0]["evidence"][stage]
    assert any(f"missing {stage}" in item for item in gate.validate_e2e_audit([plugin], audit))


def test_discovery_is_not_a_real_smoke_action(journey):
    plugin, audit = journey
    del audit["plugins"][0]["evidence"]["smoke"]["real_action"]
    assert any("real_action" in item for item in gate.validate_e2e_audit([plugin], audit))


def blocked(audit):
    row = audit["plugins"][0]
    row["result"] = "BLOCKED"
    row["blocker"] = {
        "reason": "Provider requires a publisher-managed application registration.",
        "owner": "Marketplace publisher operations",
        "next_action": "Register the production application with the provider.",
        "evidence": "Observed the missing publisher client message in the connection dialog.",
    }
    for stage in gate.E2E_STAGES[1:]:
        row["evidence"][stage]["status"] = "BLOCKED"


def test_legacy_blocker_is_honest_but_not_release_qualification(journey):
    plugin, audit = journey
    blocked(audit)
    assert gate.validate_e2e_audit([plugin], audit) == []
    assert any(
        "PASS required" in item for item in gate.validate_e2e_audit([plugin], audit, strict=True)
    )


def test_new_plugin_cannot_bypass_with_blocked_row(journey):
    plugin, audit = journey
    blocked(audit)
    plugin["id"] = audit["plugins"][0]["plugin_id"] = "new_plugin"
    assert any("new built-in" in item for item in gate.validate_e2e_audit([plugin], audit))


@pytest.mark.parametrize("key", ["reason", "owner", "next_action", "evidence"])
def test_vague_blockers_are_rejected(journey, key):
    plugin, audit = journey
    blocked(audit)
    audit["plugins"][0]["blocker"][key] = "TODO"
    assert any("concrete" in item for item in gate.validate_e2e_audit([plugin], audit))


def test_pass_cannot_hide_a_blocked_stage(journey):
    plugin, audit = journey
    audit["plugins"][0]["evidence"]["restart"]["status"] = "BLOCKED"
    assert any("PASS cannot" in item for item in gate.validate_e2e_audit([plugin], audit))


def test_ui_attempt_cannot_be_skipped(journey):
    plugin, audit = journey
    blocked(audit)
    audit["plugins"][0]["evidence"]["ui"]["status"] = "BLOCKED"
    assert any("real UI" in item for item in gate.validate_e2e_audit([plugin], audit))


def test_manual_auth_requires_provider_exception(journey):
    plugin, audit = journey
    plugin["auth"]["mode"] = "pat_paste"
    row = audit["plugins"][0]
    row["config_fingerprint"] = gate.config_fingerprint(plugin)
    assert any("provider_exception" in item for item in gate.validate_e2e_audit([plugin], audit))
    row["provider_exception"] = {
        "reason": "Provider publishes no OAuth application flow for this service.",
        "source": "https://provider.example/documented-auth-limitations",
    }
    row["evidence"]["browser"]["status"] = "NOT_APPLICABLE"
    row["evidence"]["callback"]["status"] = "NOT_APPLICABLE"
    assert gate.validate_e2e_audit([plugin], audit) == []


def test_hardware_exception_cannot_skip_safe_action(journey):
    plugin, audit = journey
    plugin["auth"]["mode"] = "local"
    row = audit["plugins"][0]
    row["config_fingerprint"] = gate.config_fingerprint(plugin)
    row["provider_exception"] = {
        "reason": "Local hardware has no remote identity.",
        "source": "Hardware provider architecture documentation",
    }
    row["evidence"]["smoke"]["status"] = "NOT_APPLICABLE"
    assert any("smoke cannot" in item for item in gate.validate_e2e_audit([plugin], audit))


@pytest.mark.parametrize(
    "reference",
    ["missing", "docs/marketplace/missing-evidence.md", "docs/marketplace/../../../../outside.md"],
)
def test_evidence_requires_existing_repository_artifact(journey, reference):
    plugin, audit = journey
    audit["plugins"][0]["evidence"]["ui"]["reference"] = reference
    assert gate.validate_e2e_audit([plugin], audit)


def test_duplicate_or_orphaned_rows_are_rejected(journey):
    plugin, audit = journey
    audit["plugins"].append(deepcopy(audit["plugins"][0]))
    assert any("duplicate" in item for item in gate.validate_e2e_audit([plugin], audit))
    audit["plugins"][1]["plugin_id"] = "orphan"
    assert any("no catalog" in item for item in gate.validate_e2e_audit([plugin], audit))


@pytest.mark.parametrize("audit", [None, [], {}, {"schema_version": 1, "plugins": None}])
def test_malformed_audit_fails_closed(audit):
    assert gate.validate_e2e_audit([], audit)
