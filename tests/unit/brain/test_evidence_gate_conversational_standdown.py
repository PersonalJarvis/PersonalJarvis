"""A false READ tool-mandate must never collapse a conversational voice turn.

Live 2026-06-30 (Bora-Bora voice session, ``voice-session-2026-06-30_17-58``):
the user asked a plain travel question — "...bin jetzt bei meinem Budget bei so
25.000 Euro für zwei Personen, passt es?". The word **"budget"** sat in the
Google-Cloud-billing keyword list, so the evidence gate FORCED ``cli_gcloud``;
the model answered the travel question without that (irrelevant) tool, and the
honesty backstop then VOIDED the good answer with a canned failure phrase
(``executed=[]``). Two defenses, tested here:

* A1 — a bare "budget" no longer selects the cloud-billing domain.
* A2 — an opinion/advice/conversational turn stands the READ gate down entirely
  (it never *forces* a tool, so the answer is never voided), while a pure data
  lookup ("Was sind meine Abrechnungen?") stays gated (no confab regression).
* B3 — when a *genuine* read mandate is unmet, the spoken fallback NAMES the
  capability ("…deine Cloud-Abrechnung…") instead of the generic "the tool",
  which also carries the info forward in history for a follow-up "which tool?".
"""
from jarvis.brain.evidence_gate import check_evidence_domain
from jarvis.brain.manager import (
    _conversational_turn_suppresses_read_mandate,
    _evidence_unfulfilled_answer,
)
from jarvis.core.capabilities import CapabilityRegistry
from jarvis.core.config import EvidenceDomainsConfig

_RECOMBINED_BORA = (
    "Was geht ab? Was ist morgen für ein Tag? Guten Tag, ich habe noch eine "
    "Frage an dich und zwar möchte ich nach Bora Bora, was würdest du mir "
    "empfehlen, was ich für den Urlaub brauche? Ich hab jetzt so für zwei "
    "Wochen geplant und bin jetzt bei meinem Budget bei so 25.000 Euro für "
    "zwei Personen, passt es?"
)


def _cloud_gate(text: str):
    return check_evidence_domain(
        text,
        enabled=True,
        domains=EvidenceDomainsConfig().domains,
        capability_registry=CapabilityRegistry(),
        domain_tool_map={"cloud": "cli_gcloud"},
        refusal_hint_fn=None,
    )


# --- A1: bare "budget" must not hijack the cloud-billing domain --------------


def test_bare_travel_budget_does_not_force_gcloud():
    # A travel / household / project budget is not a cloud bill (mirror of the
    # existing "was kostet X" hard negative).
    v = _cloud_gate("Was ist mit meinem Budget für den Urlaub?")
    assert v.kind == "pass"


def test_real_cloud_billing_question_still_forces_gcloud():
    # Capability preserved: a genuine cloud-billing lookup still mandates the CLI.
    for utterance in [
        "Zeig mir meine cloud billing",
        "Zeig mir mein cloud budget",
        "Was sind meine aktuellsten Abrechnungen?",
    ]:
        v = _cloud_gate(utterance)
        assert v.kind == "require_tool", utterance
        assert v.tool_name == "cli_gcloud", utterance


# --- A2: conversational turns stand the READ gate down ----------------------


def test_conversational_turn_suppresses_read_mandate():
    # The exact Bora-Bora turn (continuation-recombined) is opinion/advice.
    assert _conversational_turn_suppresses_read_mandate(_RECOMBINED_BORA) is True
    assert _conversational_turn_suppresses_read_mandate(
        "Was würdest du mir empfehlen für meinen Urlaub?"
    ) is True


def test_pure_data_lookup_is_not_suppressed():
    # The 2026-06-17 confabulation guard must NOT be re-opened: a bare data
    # lookup carries no opinion/advice opener, so it stays gated.
    assert _conversational_turn_suppresses_read_mandate(
        "Was sind meine Abrechnungen?"
    ) is False
    assert _conversational_turn_suppresses_read_mandate(
        "Wie hoch ist meine Cloud-Abrechnung?"
    ) is False


# --- B3: the unmet-mandate fallback names the capability --------------------


def test_unfulfilled_answer_names_the_domain():
    de = _evidence_unfulfilled_answer(lang="de", domain="cloud")
    assert "Cloud-Abrechnung" in de
    assert "durchgelaufen" in de.lower() or "abrufen" in de.lower()
    # Honesty preserved — never claims the tool "blocked" / invents a reason.
    assert "blockiert" not in de.lower() and "erkl" not in de.lower()
    en = _evidence_unfulfilled_answer(lang="en", domain="calendar")
    assert "calendar" in en.lower()
    es = _evidence_unfulfilled_answer(lang="es", domain="email")
    assert "bandeja" in es.lower() or "entrada" in es.lower()


def test_unfulfilled_answer_unknown_domain_uses_generic():
    # An unrecognised domain degrades to the existing generic phrase.
    assert _evidence_unfulfilled_answer(
        lang="de", domain="totally-unknown"
    ) == _evidence_unfulfilled_answer(lang="de")


def test_unfulfilled_answer_no_domain_is_unchanged_generic():
    # Back-compat: the no-domain call keeps the original generic wording.
    de = _evidence_unfulfilled_answer(lang="de")
    assert "abrufen" in de.lower() or "durchgelaufen" in de.lower()
