"""A failure cause must reach the user in the TURN's language, composer or not.

Live 2026-08-20: the realtime path finally forwarded the cause instead of the
stock line — and a German turn then said "Das hat nicht geklappt: Gmail is not  # i18n-allow: quotes the mixed-language line this module repairs
connected — connect it in the Plugins view." Half the sentence in the wrong
language, because the contextual composer that was supposed to rephrase the
English tool string was dead twice over: its pinned model (gemini-3.1-flash)
is absent from the AI Studio catalog (404) and the key's credits were gone
(429). The composer is an enhancement; it must never be the only thing between
the user and a comprehensible sentence.

``localize_failure_reason`` is that floor: a deterministic regex table over OUR
OWN tools' error wording (AP-11 — no LLM), passing anything foreign through
unchanged, because a cause in the wrong language still beats no cause.
"""

from __future__ import annotations

import pytest

from jarvis.voice.action_phrases import action_phrase, localize_failure_reason

GMAIL = "Gmail is not connected — connect it in the Plugins view."
CALENDAR_EXPIRED = (
    "Google Calendar authorization expired and could not be renewed — "
    "please reconnect Google Calendar in the Plugins view."
)


# ---------------------------------------------------------------------------
# The families our own tools emit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "must_contain"),
    [
        ("de", "Gmail ist nicht verbunden"),  # i18n-allow: asserts the German product surface
        ("en", "Gmail is not connected"),
        ("es", "Gmail no está conectado"),  # i18n-allow: asserts the Spanish product surface
    ],
)
def test_not_connected_speaks_the_turn_language(language, must_contain) -> None:
    spoken = localize_failure_reason(GMAIL, language)
    assert must_contain in spoken


def test_not_connected_keeps_the_service_name() -> None:
    for service in ("Gmail", "Google Drive", "Google Calendar", "Home Assistant"):
        spoken = localize_failure_reason(
            f"{service} is not connected — connect it in the Plugins view.", "de"
        )
        assert service in spoken


def test_expired_authorization_is_a_distinct_sentence() -> None:
    expired = localize_failure_reason(CALENDAR_EXPIRED, "de")
    never = localize_failure_reason(
        "Google Calendar is not connected — connect it in the Plugins view.", "de"
    )
    assert "Google Calendar" in expired
    assert expired != never, "'never connected' and 'token died' are different facts"


def test_unknown_skill_drops_the_installed_list() -> None:
    spoken = localize_failure_reason(
        "Unknown skill: morgenroutine. Installed skills: a, b, c, d, e", "de"
    )
    assert "morgenroutine" in spoken
    assert "Installed skills" not in spoken, "the whole catalog is not a spoken sentence"


def test_draft_and_disabled_skills_are_told_apart() -> None:
    draft = localize_failure_reason(
        "Skill 'morning-routine' is in DRAFT state and not invocable. Promote it first.",
        "de",
    )
    disabled = localize_failure_reason(
        "Skill 'morning-routine' is DISABLED and not invocable. Re-enable it first.",
        "de",
    )
    assert "morning-routine" in draft
    assert "morning-routine" in disabled
    assert draft != disabled


# ---------------------------------------------------------------------------
# Everything else must survive untouched
# ---------------------------------------------------------------------------


def test_an_unknown_reason_passes_through_verbatim() -> None:
    foreign = "The printer tray is empty and the job was dropped."
    assert localize_failure_reason(foreign, "de") == foreign


def test_whitespace_is_collapsed_but_content_is_kept() -> None:
    assert localize_failure_reason("  a   broken\n  thing ", "de") == "a broken thing"


def test_empty_input_stays_empty() -> None:
    assert localize_failure_reason(None, "de") == ""
    assert localize_failure_reason("", "de") == ""
    assert localize_failure_reason("   ", "de") == ""


def test_an_unknown_language_falls_back_to_the_module_default() -> None:
    assert localize_failure_reason(GMAIL, "fr") == localize_failure_reason(GMAIL, "de")


def test_the_localized_cause_fits_the_failure_phrase() -> None:
    """The end product: one sentence, one language."""
    spoken = action_phrase(
        "action_failed_reason", "de", reason=localize_failure_reason(GMAIL, "de")
    )
    assert "is not connected" not in spoken, "no English clause left in a German line"
    assert "Plugins" in spoken, "the actionable part survives"
