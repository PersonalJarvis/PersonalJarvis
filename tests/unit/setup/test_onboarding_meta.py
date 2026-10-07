from jarvis.setup import onboarding_meta as m


def test_meta_constants():
    assert m.CURRENT_TERMS_VERSION == "1.0"
    # Setup is one window with three steps (2026-10-05) — the name (the wake
    # word), connecting an AI, how the user talks to it — then the walk
    # through the app, which ends in the single completion restart.
    assert m.ONBOARDING_STEPS == ["name", "connect", "voice", "tour"]
    assert m.ONBOARDING_STEPS[-1] == "tour"
    # Retired step ids must not come back through a partial revert.
    for retired in (
        "terms", "language", "api-keys", "wake-word", "finish", "brain", "agents",
        "welcome", "how", "keys", "subscriptions", "permissions", "ready",
    ):
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
