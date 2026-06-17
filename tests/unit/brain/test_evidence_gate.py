"""Evidence gate verdicts + hard negatives (AD-CLI4..AD-CLI8)."""
from jarvis.brain.evidence_gate import check_evidence_domain
from jarvis.core.capabilities import Capability, CapabilityRegistry

DOMAINS = {
    "calendar": ["kalender", "termin", "termine", "steht heute", "calendar"],
    "email": ["mail", "mails", "inbox", "postfach"],
    "repos": ["pull request", "pr", "prs", "issue", "issues"],
}


def _gate(text, *, registry=None, tool_map=None, hint_fn=None, enabled=True):
    return check_evidence_domain(
        text,
        enabled=enabled,
        domains=DOMAINS,
        capability_registry=registry if registry is not None else CapabilityRegistry(),
        domain_tool_map=tool_map or {},
        refusal_hint_fn=hint_fn,
    )


# --- verdict: require_tool ---------------------------------------------------


def test_calendar_question_with_cli_requires_tool():
    v = _gate("Was steht heute noch an?", tool_map={"calendar": "cli_gam"})
    assert v.kind == "require_tool"
    assert v.tool_name == "cli_gam"
    assert "cli_gam" in v.directive and "NEVER invent" in v.directive


def test_umlaut_form_matches():
    v = _gate("Welche Termine habe ich morgen?", tool_map={"calendar": "cli_gam"})
    assert v.kind == "require_tool"


def test_cloud_billing_question_with_gcloud_requires_tool():
    # Live 2026-06-17: "use the Google Cloud CLI ... my latest billing".
    # The connected gcloud must be FORCED, first try, using the real default
    # config keywords for the "cloud" domain.
    from jarvis.core.config import EvidenceDomainsConfig

    domains = EvidenceDomainsConfig().domains
    for utterance in [
        "Was sind meine aktuellsten Abrechnungen?",
        "Wie viel Guthaben habe ich noch?",
        "Zeig mir meine Google Cloud Kosten.",
    ]:
        v = check_evidence_domain(
            utterance,
            enabled=True,
            domains=domains,
            capability_registry=CapabilityRegistry(),
            domain_tool_map={"cloud": "cli_gcloud"},
            refusal_hint_fn=None,
        )
        assert v.kind == "require_tool", utterance
        assert v.tool_name == "cli_gcloud", utterance
        assert "NEVER invent" in v.directive


def test_general_cost_question_does_not_force_gcloud():
    # A generic price question must NOT hijack the connected gcloud (no bare
    # "kosten"/"cost" keyword), else every "was kostet X" forces a billing call.
    from jarvis.core.config import EvidenceDomainsConfig

    domains = EvidenceDomainsConfig().domains
    v = check_evidence_domain(
        "Was kostet ein Tesla Model 3?",
        enabled=True,
        domains=domains,
        capability_registry=CapabilityRegistry(),
        domain_tool_map={"cloud": "cli_gcloud"},
        refusal_hint_fn=None,
    )
    assert v.kind == "pass"


# --- verdict: honest_refusal -------------------------------------------------


def test_calendar_question_without_anything_refuses_honestly():
    v = _gate("Was steht heute noch an?")
    assert v.kind == "honest_refusal"
    assert "Kalenderzugriff" in v.refusal_text


def test_refusal_appends_hint():
    v = _gate(
        "Was steht heute noch an?",
        hint_fn=lambda domain, lang: " HINT",
    )
    assert v.refusal_text.endswith("HINT")


def test_english_refusal_for_english_text():
    v = _gate("Do I have any appointments on my calendar today?")
    assert v.kind == "honest_refusal"
    assert "calendar access" in v.refusal_text


def test_refusal_survives_broken_hint_fn():
    def _boom(domain, lang):
        raise RuntimeError("hint broke")

    v = _gate("Was steht heute noch an?", hint_fn=_boom)
    assert v.kind == "honest_refusal"


# --- verdict: pass (preference order, AD-CLI6) -------------------------------


def test_non_cli_capability_wins_and_passes():
    reg = CapabilityRegistry()
    reg.register(Capability(
        id="skill.paired.gmail", source="skill",
        verbs=("lies",), objects=("mail", "inbox", "postfach"),
        description="Paired Gmail skill.", risk_tier="ask",
        requires_evidence=True,
    ))
    v = _gate("Hab ich neue Mails?", registry=reg, tool_map={"email": "cli_gam"})
    assert v.kind == "pass"


# --- hard negatives ----------------------------------------------------------


def test_smalltalk_passes():
    assert _gate("Danke dir, das war's").kind == "pass"
    assert _gate("Wie geht es dir heute?").kind == "pass"


def test_domain_word_in_passing_passes():
    # statement, not a lookup — must not trigger
    assert _gate("Ich habe dir das vorhin per Mail geschickt").kind == "pass"


def test_definition_question_passes():
    assert _gate("Was ist ein Pull Request?").kind == "pass"
    assert _gate("What is an issue tracker?").kind == "pass"


def test_definition_with_possessive_is_a_lookup_not_definition():
    # "Was sind MEINE X" is a data lookup, not a definition — a possessive
    # marker must defeat the definitional short-circuit, else lookups phrased
    # as "was sind meine ..." silently pass (live 2026-06-17, billing query).
    v = _gate("Was sind meine offenen Pull Requests?", tool_map={"repos": "cli_gh"})
    assert v.kind == "require_tool"
    # A true definition (no possessive) still passes untouched.
    assert _gate("Was sind Pull Requests?").kind == "pass"


def test_send_action_passes_to_existing_gates():
    # imperative "schick eine Mail" is the unsupported-intent gate's turf
    assert _gate("Schick eine Mail an Christoph").kind == "pass"


def test_disabled_flag_bypasses():
    assert _gate("Was steht heute noch an?", enabled=False).kind == "pass"


def test_empty_and_garbage_pass():
    assert _gate("").kind == "pass"
    assert _gate("   ").kind == "pass"


def test_broken_registry_degrades_to_pass():
    class _Broken:
        def all(self):
            raise RuntimeError("boom")

    v = _gate("Was steht heute noch an?", registry=_Broken())
    assert v.kind == "pass"
