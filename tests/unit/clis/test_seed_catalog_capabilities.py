"""Parity guard: curated capabilities blocks stay valid (anti-drift, AD-CLI9)."""
from jarvis.clis.capability_provider import DOMAIN_VOCAB
from jarvis.clis.catalog import CliCatalog

CURATED = {
    "gam", "gh", "glab", "gcloud", "az", "aws", "wrangler", "vercel",
    "netlify", "heroku", "railway", "flyctl", "render", "supabase",
    "firebase", "pscale", "neonctl", "stripe", "twilio", "docker", "kubectl",
}


def _seed_specs():
    return CliCatalog().all()


def test_curated_entries_declare_capabilities():
    specs = _seed_specs()
    for name in CURATED:
        assert name in specs, f"{name} missing from seed catalog"
        spec = specs[name]
        assert spec.capabilities, f"{name} must declare a capabilities block"
        for decl in spec.capabilities:
            assert decl.domains, f"{name}: empty domains"
            unknown = set(decl.domains) - DOMAIN_VOCAB
            assert not unknown, f"{name}: unknown domains {unknown}"
            assert decl.verbs, f"{name}: empty verbs"
            assert decl.objects, f"{name}: empty objects"
            assert decl.description, f"{name}: empty description"


def test_curated_entries_have_read_only_whitelist():
    specs = _seed_specs()
    for name in CURATED:
        assert specs[name].risk.whitelist_patterns, (
            f"{name} needs read-only whitelist patterns (safe-tier inline calls)"
        )


def test_gcloud_vocabulary_resolves_cost_and_overview_phrasings():
    """Live transcript 2026-06-10 19:05: 'guck mit der Google CLI ... Kosten'
    force-spawned a sub-agent mission because the gcloud capability vocabulary
    missed 'gucken', 'Kosten', and 'Google CLI'. These phrasings must resolve
    inline now."""
    from jarvis.clis.capability_provider import capability_for_spec
    from jarvis.core.capabilities import CapabilityRegistry

    cap = capability_for_spec(_seed_specs()["gcloud"])
    assert cap is not None
    reg = CapabilityRegistry()
    reg.register(cap)
    for utterance in [
        "Guck mit der Google CLI, was meine Kosten machen",
        "Schau mal in die Google Cloud, was gerade los ist",
        "Zeig mir meine Google-Kosten",
        "Check mal mein Billing",
        "Zeig mir meine Google-Projekte",
    ]:
        got = reg.resolve_intent(utterance)
        assert got is not None and got.id == "cli.gcloud", utterance
    # Generic lookup verb WITHOUT a gcloud domain noun must NOT resolve —
    # the object-required rule (AD-CLI2) is the anti-hijack guarantee.
    assert reg.resolve_intent("Guck mal kurz her") is None
    assert reg.resolve_intent("Schau dir das Dokument an") is None


def test_evidence_domains_have_at_least_one_cli():
    specs = _seed_specs()
    covered = {
        d for s in specs.values() for decl in s.capabilities for d in decl.domains
    }
    assert {"calendar", "email", "repos", "deployments"} <= covered
