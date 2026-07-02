"""The interactive wizard's finish step requires accepting the Terms of Use.

Guards the installer-path fix: the CLI wizard now records terms acceptance +
onboarding completion so the desktop app does not re-run its own onboarding,
and declining the terms stops setup instead of silently completing it.
"""
from __future__ import annotations

from unittest.mock import patch

from jarvis.setup import wizard


def test_finalize_declined_terms_raises_and_skips_completion() -> None:
    """Declining the terms raises _TermsDeclined and never marks setup complete."""
    with (
        patch("jarvis.setup.wizard._ask_yesno", return_value=False) as ask,
        patch("jarvis.setup.wizard.cfg.mark_setup_complete") as mark_complete,
        patch("jarvis.setup.state.accept_terms") as accept,
        patch("jarvis.setup.state.mark_onboarding_complete") as mark_onb,
    ):
        try:
            wizard.step_finalize()
            raised = False
        except wizard._TermsDeclined:
            raised = True

    assert raised, "declining the terms must raise _TermsDeclined"
    ask.assert_called_once()  # stopped at the terms prompt, before autostart
    accept.assert_not_called()
    mark_onb.assert_not_called()
    mark_complete.assert_not_called()


def test_finalize_accepted_terms_records_and_completes() -> None:
    """Accepting records terms + onboarding completion + the setup marker."""
    with (
        patch("jarvis.setup.wizard._ask_yesno", return_value=True),
        patch("jarvis.setup.wizard._apply_autostart_choice"),
        patch("jarvis.setup.wizard.cfg.mark_setup_complete") as mark_complete,
        patch("jarvis.setup.state.accept_terms") as accept,
        patch("jarvis.setup.state.mark_onboarding_complete") as mark_onb,
    ):
        wizard.step_finalize()

    accept.assert_called_once()
    mark_onb.assert_called_once()
    mark_complete.assert_called_once()
