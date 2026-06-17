"""Capability provider: CliSpec.capabilities -> CapabilityRegistry (AD-CLI1..3)."""
from dataclasses import replace

from jarvis.clis.capability_provider import (
    DOMAIN_VOCAB,
    PLUGIN_CLI_OVERLAP,
    capability_for_spec,
    connected_domain_keyword_map,
    connected_domain_tool_map,
    merged_evidence_domains,
    refusal_hint,
    suppress_plugin_tools_covered_by_cli,
    sync_registry,
)
from jarvis.clis.spec import CliCapabilityDecl, CliStatus
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


# --- Component 1: derive trigger keywords from connected CLI objects ----------


def test_keyword_map_unions_objects_per_domain():
    fake = FakeCliRegistry(
        {"gh": make_spec("gh", domains=("repos",))},  # objects ("pull request","issue")
        active=[FakeTool("cli_gh")],
    )
    out = connected_domain_keyword_map(fake)
    assert set(out["repos"]) == {"pull request", "issue"}


def test_keyword_map_empty_when_no_active_cli():
    fake = FakeCliRegistry({"gh": make_spec("gh")}, active=[])
    assert connected_domain_keyword_map(fake) == {}


def test_keyword_map_drops_ambiguous_cost_nouns():
    spec = replace(
        make_spec("gcloud", domains=("cloud",)),
        capabilities=(
            CliCapabilityDecl(
                domains=("cloud",),
                verbs=("zeig",),
                objects=("kosten", "cost", "abrechnung", "guthaben"),
                description="cloud billing",
            ),
        ),
    )
    fake = FakeCliRegistry({"gcloud": spec}, active=[FakeTool("cli_gcloud")])
    out = connected_domain_keyword_map(fake)
    assert "abrechnung" in out["cloud"] and "guthaben" in out["cloud"]
    assert "kosten" not in out["cloud"] and "cost" not in out["cloud"]


def test_keyword_map_defensive_against_broken_registry():
    class _Broken:
        def active_tools(self):
            raise RuntimeError("boom")

    assert connected_domain_keyword_map(_Broken()) == {}


# --- Component 1 (cont.): merge derived keywords with config -----------------


def test_merge_adds_cli_domain_absent_from_config():
    # stripe declares payments with billing objects; config has no payments.
    spec = replace(
        make_spec("stripe", domains=("payments",)),
        capabilities=(
            CliCapabilityDecl(
                domains=("payments",), verbs=("zeig",),
                objects=("stripe", "umsatz", "invoice"), description="payments",
            ),
        ),
    )
    fake = FakeCliRegistry({"stripe": spec}, active=[FakeTool("cli_stripe")])
    out = merged_evidence_domains(fake, {"calendar": ["kalender"]})
    assert "umsatz" in out["payments"] and "stripe" in out["payments"]
    # config-only domain preserved
    assert out["calendar"] == ["kalender"]


def test_merge_config_keywords_always_win():
    # gcloud objects include "kosten" (denylisted in derivation); a curated
    # config keyword for the same domain still survives.
    spec = replace(
        make_spec("gcloud", domains=("cloud",)),
        capabilities=(
            CliCapabilityDecl(
                domains=("cloud",), verbs=("zeig",),
                objects=("kosten", "gcp"), description="cloud",
            ),
        ),
    )
    fake = FakeCliRegistry({"gcloud": spec}, active=[FakeTool("cli_gcloud")])
    out = merged_evidence_domains(fake, {"cloud": ["abrechnung"]})
    assert "abrechnung" in out["cloud"]  # config curated
    assert "gcp" in out["cloud"]         # derived
    assert "kosten" not in out["cloud"]  # denylisted in derivation


def test_merge_defensive_returns_config_on_fault():
    class _Broken:
        def active_tools(self):
            raise RuntimeError("boom")

    assert merged_evidence_domains(_Broken(), {"cloud": ["abrechnung"]}) == {
        "cloud": ["abrechnung"]
    }


# --- Component 3: plugin-as-fallback dedup ----------------------------------


def test_suppress_drops_namespaced_plugin_when_cli_present():
    tools = {
        "cli_gh": FakeTool("cli_gh"),
        "github/list_prs": FakeTool("github/list_prs"),
        "github/create_issue": FakeTool("github/create_issue"),
        "search_web": FakeTool("search_web"),
    }
    out = suppress_plugin_tools_covered_by_cli(tools)
    assert "cli_gh" in out and "search_web" in out
    assert "github/list_prs" not in out and "github/create_issue" not in out


def test_suppress_drops_native_tool_when_cli_present():
    tools = {"cli_vercel": FakeTool("cli_vercel"), "vercel": FakeTool("vercel")}
    out = suppress_plugin_tools_covered_by_cli(tools)
    assert "cli_vercel" in out and "vercel" not in out


def test_suppress_keeps_plugin_when_cli_absent():
    tools = {"github/list_prs": FakeTool("github/list_prs")}
    out = suppress_plugin_tools_covered_by_cli(tools)
    assert "github/list_prs" in out  # no cli_gh -> plugin stays as fallback


def test_suppress_defensive_on_bad_input():
    assert suppress_plugin_tools_covered_by_cli(None) is None  # type: ignore[arg-type]


def test_overlap_map_is_nonempty_dict():
    assert isinstance(PLUGIN_CLI_OVERLAP, dict) and PLUGIN_CLI_OVERLAP
