"""The honest refusal is spoken in the turn's resolved language, in all locales.

The gate used to sniff the utterance with a private de/en-only heuristic to
choose the refusal language. Two consequences:

* the user's explicit ``brain.reply_language`` pin was ignored — a
  Spanish-pinned or English-pinned user heard whatever language the utterance
  happened to look like;
* there was no Spanish table at all, so a Spanish turn could only ever fall
  back to English.

The refusal is user-facing speech, so it must follow the ONE resolver like
every other layer (CLAUDE.md §1). ``check_evidence_domain`` now takes the
already-resolved ``language``; the utterance is only consulted when a caller
omits it.
"""
from jarvis.brain.evidence_gate import (
    _REFUSAL_DE,
    _REFUSAL_EN,
    _REFUSAL_ES,
    check_evidence_domain,
)
from jarvis.core.capabilities import CapabilityRegistry

DOMAINS = {
    "calendar": ["kalender", "termin", "termine", "calendar", "meeting", "meetings"],
    "email": ["mail", "mails", "inbox", "postfach"],
}

# Same meaning in each language, so only the resolved language can move the
# refusal — the utterance's own language is deliberately varied against it.
DE_UTTERANCE = "Welche Termine habe ich heute?"  # i18n-allow: German utterance under test
EN_UTTERANCE = "What meetings do I have today?"


def _refuse(text, *, language=""):
    return check_evidence_domain(
        text,
        enabled=True,
        domains=DOMAINS,
        capability_registry=CapabilityRegistry(),
        domain_tool_map={},
        language=language,
    )


def test_every_locale_has_a_complete_refusal_table():
    """All locales are equal: no table may lag another (CLAUDE.md §1)."""
    assert set(_REFUSAL_ES) == set(_REFUSAL_DE) == set(_REFUSAL_EN)
    assert all(text.strip() for text in _REFUSAL_ES.values())


def test_resolved_language_wins_over_the_utterance_language():
    for utterance in (DE_UTTERANCE, EN_UTTERANCE):
        for language in ("de", "en", "es"):
            v = _refuse(utterance, language=language)
            assert v.kind == "honest_refusal"
            assert v.refusal_text == {
                "de": _REFUSAL_DE, "en": _REFUSAL_EN, "es": _REFUSAL_ES,
            }[language]["calendar"], (
                f"utterance={utterance!r} language={language!r} "
                f"got {v.refusal_text!r}"
            )


def test_spanish_turn_is_refused_in_spanish():
    """The live defect: a Spanish-pinned user used to hear English."""
    v = _refuse(EN_UTTERANCE, language="es")

    assert v.refusal_text == _REFUSAL_ES["calendar"]
    assert v.refusal_text != _REFUSAL_EN["calendar"]


def test_bcp47_and_whisper_style_tags_are_accepted():
    """The resolver's own tag shapes (``es-ES``, ``spanish``) must not miss."""
    for tag in ("es", "es-ES", "spanish", "Español"):
        assert _refuse(EN_UTTERANCE, language=tag).refusal_text == (
            _REFUSAL_ES["calendar"]
        )


def test_unknown_or_missing_language_falls_back_to_the_utterance():
    """Callers not yet passing the language keep working (no silent break)."""
    assert _refuse(DE_UTTERANCE).refusal_text == _REFUSAL_DE["calendar"]
    assert _refuse(EN_UTTERANCE).refusal_text == _REFUSAL_EN["calendar"]
    # A tag outside de/en/es is not a pin — the utterance decides.
    assert _refuse(DE_UTTERANCE, language="fr").refusal_text == (
        _REFUSAL_DE["calendar"]
    )


def test_domain_fallback_phrase_also_follows_the_resolved_language():
    """An unmapped domain uses that language's fallback, never English's."""
    v = check_evidence_domain(
        "What is in my inbox?",
        enabled=True,
        domains={"email": ["inbox"]},
        capability_registry=CapabilityRegistry(),
        domain_tool_map={},
        language="es",
    )
    assert v.kind == "honest_refusal"
    assert v.refusal_text == _REFUSAL_ES["email"]
