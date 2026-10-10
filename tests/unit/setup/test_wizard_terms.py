"""The interactive wizard's finish step requires accepting the Terms of Use.

Guards the installer-path fix: the CLI wizard now records terms acceptance +
onboarding completion so the desktop app does not re-run its own onboarding,
and declining the terms stops setup instead of silently completing it.
"""

from __future__ import annotations

import pytest

from jarvis.setup import state, wizard
from jarvis.setup.onboarding_meta import CURRENT_TERMS_VERSION


def test_finalize_declined_terms_raises_and_skips_completion(monkeypatch) -> None:
    """Declining the terms raises _TermsDeclined and never marks setup complete."""
    calls: list[str] = []

    def decline(*args, **kwargs):
        calls.append("ask")
        return False

    monkeypatch.setattr(wizard, "_ask_yesno", decline)
    monkeypatch.setattr(wizard.cfg, "mark_setup_complete", lambda: calls.append("setup"))
    monkeypatch.setattr(state, "accept_terms", lambda version: calls.append("accept"))
    monkeypatch.setattr(state, "mark_onboarding_complete", lambda: calls.append("complete"))

    with pytest.raises(wizard._TermsDeclined):
        wizard.step_finalize()

    assert calls == ["ask"]  # stopped at the terms prompt, before autostart


def test_finalize_accepted_terms_records_and_completes(monkeypatch, capsys) -> None:
    """Accepting records terms + onboarding completion + the setup marker."""
    calls: list[tuple[str, str | bool | None]] = []
    monkeypatch.setattr(wizard, "_ask_yesno", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        wizard,
        "_apply_autostart_choice",
        lambda enabled: calls.append(("autostart", enabled)),
    )
    monkeypatch.setattr(wizard.cfg, "mark_setup_complete", lambda: calls.append(("setup", None)))
    monkeypatch.setattr(state, "accept_terms", lambda version: calls.append(("accept", version)))
    monkeypatch.setattr(
        state,
        "mark_onboarding_complete",
        lambda: calls.append(("complete", None)),
    )

    wizard.step_finalize()

    assert calls == [
        ("accept", CURRENT_TERMS_VERSION),
        ("autostart", True),
        ("complete", None),
        ("setup", None),
    ]
    output = capsys.readouterr().out
    assert "token.personaljarvis.ai" in output
    assert "Cloudflare" in output
    assert "authorization codes, PKCE verifiers" in output
    assert "refresh tokens" in output
    assert "authors run no server" not in output
    assert "receive none of your data" not in output
