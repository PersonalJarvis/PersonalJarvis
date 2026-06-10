"""[brain.evidence_domains] config model defaults + override (AD-CLI5)."""
from jarvis.core.config import BrainConfig, EvidenceDomainsConfig


def test_defaults_ship_five_domains_enabled():
    cfg = EvidenceDomainsConfig()
    assert cfg.enabled is True
    assert set(cfg.domains) == {"calendar", "email", "tasks", "repos", "deployments"}
    assert "kalender" in cfg.domains["calendar"]
    assert "inbox" in cfg.domains["email"]


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
