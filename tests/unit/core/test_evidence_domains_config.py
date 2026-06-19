"""[brain.evidence_domains] config model defaults + override (AD-CLI5)."""
from jarvis.core.config import BrainConfig, EvidenceDomainsConfig


def test_defaults_ship_seven_domains_enabled():
    cfg = EvidenceDomainsConfig()
    assert cfg.enabled is True
    assert set(cfg.domains) == {
        "calendar", "email", "tasks", "repos", "deployments", "cloud",
        "activity",
    }
    assert "kalender" in cfg.domains["calendar"]
    assert "inbox" in cfg.domains["email"]


def test_defaults_include_activity_window_history_domain():
    # "Was hatte ich heute offen?" must force awareness-recall instead of a
    # confabulated "lokaler Verlaufsspeicher nicht verfügbar" (live 2026-06-18).
    # Keywords are phrase-specific to opened windows / on-device activity.
    cfg = EvidenceDomainsConfig()
    kws = cfg.domains["activity"]
    assert "offen hatte" in kws and "heute offen" in kws
    assert "what did i do today" in kws
    # Must NOT carry a bare "offen"/"open" token that hijacks unrelated turns.
    assert "offen" not in kws
    assert "open" not in kws


def test_defaults_include_cloud_billing_domain():
    # The user wants a billing/cost question to deterministically drive the
    # connected gcloud CLI (live 2026-06-17). The gate maps the "cloud" domain
    # to cli_gcloud; the config must carry the billing keywords that match.
    cfg = EvidenceDomainsConfig()
    kws = cfg.domains["cloud"]
    assert "abrechnung" in kws and "abrechnungen" in kws
    assert "billing" in kws
    assert "guthaben" in kws
    assert "google cloud" in kws
    # Must NOT include a bare generic cost word that would hijack "was kostet X".
    assert "kosten" not in kws
    assert "cost" not in kws


def test_brain_config_carries_evidence_domains():
    cfg = BrainConfig()
    assert cfg.evidence_domains.enabled is True


def test_toml_override_shape():
    cfg = BrainConfig.model_validate({
        "evidence_domains": {
            "enabled": False,
            "domains": {"calendar": ["kalender"]},
        }
    })
    assert cfg.evidence_domains.enabled is False
    assert cfg.evidence_domains.domains == {"calendar": ["kalender"]}
