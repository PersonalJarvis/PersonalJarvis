"""Capability provider: CliSpec.capabilities -> CapabilityRegistry (AD-CLI1..3)."""
from dataclasses import replace

from jarvis.clis.capability_provider import (
    DOMAIN_VOCAB,
    capability_for_spec,
    connected_domain_tool_map,
    refusal_hint,
    sync_registry,
)
from jarvis.clis.spec import CliStatus
from jarvis.core.capabilities import CapabilityRegistry
from tests.unit.clis._fakes import FakeCliRegistry, FakeTool, make_spec


def test_capability_for_spec_maps_fields():
    cap = capability_for_spec(make_spec("gh"))
    assert cap is not None
    assert cap.id == "cli.gh"
    assert cap.source == "cli"
    assert cap.verbs == ("zeig", "list", "show")
    assert cap.objects == ("pull request", "issue")
    assert cap.requires_evidence is True
    assert cap.risk_tier == "monitor"


def test_capability_for_spec_none_without_block():
    assert capability_for_spec(replace(make_spec("gh"), capabilities=())) is None


def test_sync_registers_usable_and_deregisters_unusable():
    cap_reg = CapabilityRegistry()
    fake = FakeCliRegistry({"gh": make_spec("gh")}, active=[FakeTool("cli_gh")])
    sync_registry(fake, cap_reg)
    assert any(c.id == "cli.gh" for c in cap_reg.all())

    fake.active = []  # disconnected
    sync_registry(fake, cap_reg)
    assert not any(c.id == "cli.gh" for c in cap_reg.all())


def test_sync_is_defensive_against_broken_registry():
    class _Broken:
        def active_tools(self):
            raise RuntimeError("boom")

    sync_registry(_Broken(), CapabilityRegistry())  # must not raise


def test_connected_domain_tool_map():
    fake = FakeCliRegistry(
        {"gh": make_spec("gh", domains=("repos",))},
        active=[FakeTool("cli_gh")],
    )
    assert connected_domain_tool_map(fake) == {"repos": "cli_gh"}
    fake.active = []
    assert connected_domain_tool_map(fake) == {}


def test_refusal_hint_installed_not_connected():
    fake = FakeCliRegistry(
        {"gam": make_spec("gam", domains=("calendar",))},
        active=[],
        status={"gam": CliStatus(installed=True, auth_status="not_connected")},
    )
    hint_de = refusal_hint("calendar", fake, "de")
    assert "GAM" in hint_de and "installiert" in hint_de
    hint_en = refusal_hint("calendar", fake, "en")
    assert "GAM" in hint_en and "installed" in hint_en


def test_refusal_hint_known_but_not_installed():
    fake = FakeCliRegistry(
        {"gam": make_spec("gam", domains=("calendar",))},
        active=[],
        status={"gam": CliStatus(installed=False)},
    )
    assert "GAM" in refusal_hint("calendar", fake, "en")


def test_refusal_hint_empty_for_unknown_domain():
    fake = FakeCliRegistry({}, active=[])
    assert refusal_hint("calendar", fake, "de") == ""


def test_domain_vocab_contains_evidence_domains():
    assert {"calendar", "email", "tasks", "repos", "deployments"} <= DOMAIN_VOCAB
