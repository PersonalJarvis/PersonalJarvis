from jarvis.setup import onboarding_meta as m


def test_meta_constants():
    assert m.CURRENT_TERMS_VERSION == "1.0"
    # Setup runs inside the real app (2026-09-30): consent, one key on the API
    # Keys page, a subscription for the agents on its Agents tab (2026-10-01),
    # macOS permissions, the wake word in Settings, then the start. The "how"
    # step (2026-10-01) explains the product before anything is set up.
    assert m.ONBOARDING_STEPS == [
        "welcome", "how", "keys", "subscriptions", "permissions", "voice", "ready",
    ]
    assert m.ONBOARDING_STEPS.index("how") < m.ONBOARDING_STEPS.index("keys")
    # Restart batching (2026-07-18): permissions + voice sit LAST before the
    # final step so the single unconditional completion restart covers both.
    assert m.ONBOARDING_STEPS.index("permissions") < m.ONBOARDING_STEPS.index("voice")
    assert m.ONBOARDING_STEPS[-2] == "voice"
    # Retired step ids must not come back through a partial revert.
    for retired in ("terms", "language", "api-keys", "wake-word", "finish", "brain", "agents"):
        assert retired not in m.ONBOARDING_STEPS
    assert len(m.WAKE_WORD_LEGAL_REFERENCES) >= 3
    for ref in m.WAKE_WORD_LEGAL_REFERENCES:
        assert ref["label"] and ref["url"].startswith("https://")


def test_read_terms_text_returns_versioned_body():
    text = m.read_terms_text()
    assert "Personal Jarvis" in text
    assert "v1.0" in text
    # The 'no affiliation' clause must be present (legal core).
    assert "affiliat" in text.lower()
