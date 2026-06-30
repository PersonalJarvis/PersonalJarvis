"""Say-do honesty backstop for WRITE mandates (contacts).

Sibling of ``test_evidence_enforcement.py`` (which covers READ mandates). When a
contact-write turn mandated ``contact-upsert`` but the tool never ran, a flat
confirmation ("Okay, sehr gut") is a dishonest say-do gap and must be replaced
with an honest line. A clarifying QUESTION ("Wie lautet die volle E-Mail?") is
the DESIRED behavior for a missing/broken field and must be left intact.
"""

from jarvis.brain.manager import (
    _action_unfulfilled_answer,
    _unfulfilled_replacement,
)

# --- honest write fallback wording -------------------------------------------


def test_action_unfulfilled_answer_is_honest_and_localized():
    de = _action_unfulfilled_answer("contact-upsert", lang="de")
    en = _action_unfulfilled_answer("contact-upsert", lang="en")
    es = _action_unfulfilled_answer("contact-upsert", lang="es")
    # Never claims the save happened.
    for txt in (de, en, es):
        assert txt.strip()
        assert "gespeichert habe ich" not in txt.lower()
    assert "noch nicht" in de.lower()  # "not yet saved"
    assert "haven't" in en.lower() or "not" in en.lower()
    assert "todav" in es.lower() or "aún" in es.lower() or "no he" in es.lower()


def test_action_unfulfilled_answer_unknown_language_is_safe():
    assert isinstance(_action_unfulfilled_answer("contact-upsert", lang="fr"), str)
    assert _action_unfulfilled_answer("contact-upsert", lang="fr").strip()


# --- the replacement decision (pure) -----------------------------------------


def test_write_mandate_flat_confirmation_is_replaced():
    # Mandated contact-upsert, tool NOT run, no question → dishonest → replace.
    out = _unfulfilled_replacement(
        required_tool="contact-upsert",
        executed=set(),
        response_text="Okay, sehr gut.",
        suppressed=False,
        is_write=True,
        lang="de",
    )
    assert out is not None
    assert "okay" not in out.lower()  # the fake confirmation is gone


def test_write_mandate_clarifying_question_is_kept():
    # The brain honestly asked for the broken email → leave it alone.
    out = _unfulfilled_replacement(
        required_tool="contact-upsert",
        executed=set(),
        response_text="Wie lautet Haralds vollständige E-Mail-Adresse?",
        suppressed=False,
        is_write=True,
        lang="de",
    )
    assert out is None


def test_write_mandate_with_executed_tool_stands():
    out = _unfulfilled_replacement(
        required_tool="contact-upsert",
        executed={"contact-upsert"},
        response_text="Hab ich gespeichert.",
        suppressed=False,
        is_write=True,
        lang="de",
    )
    assert out is None


def test_read_mandate_still_uses_read_wording():
    # is_write=False must behave exactly like the legacy evidence enforcement.
    out = _unfulfilled_replacement(
        required_tool="cli_gcloud",
        executed=set(),
        response_text="the gcloud tool blocked execution",
        suppressed=False,
        is_write=False,
        lang="de",
    )
    assert out is not None
    assert "kontakt" not in out.lower()  # read fallback, not the contact line


def test_no_mandate_never_replaces():
    out = _unfulfilled_replacement(
        required_tool="",
        executed=set(),
        response_text="any free answer",
        suppressed=False,
        is_write=False,
        lang="de",
    )
    assert out is None


def test_suppressed_turn_never_replaces():
    out = _unfulfilled_replacement(
        required_tool="contact-upsert",
        executed=set(),
        response_text="",
        suppressed=True,
        is_write=True,
        lang="de",
    )
    assert out is None
