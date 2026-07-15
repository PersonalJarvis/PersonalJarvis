from jarvis.setup import onboarding_meta as m


def test_meta_constants():
    assert m.CURRENT_TERMS_VERSION == "1.0"
    assert m.ONBOARDING_STEPS[0] == "welcome"
    # The standalone Terms & Disclaimer step was removed (2026-07-03); the risk
    # gate + MIT-license disclaimer carry the legal posture now.
    assert "terms" not in m.ONBOARDING_STEPS
    assert m.ONBOARDING_STEPS[1] == "language"
    assert m.ONBOARDING_STEPS[2] == "permissions"
    assert m.ONBOARDING_STEPS.index("permissions") < m.ONBOARDING_STEPS.index("wake-word")
    assert m.ONBOARDING_STEPS[-1] == "finish"
    assert "wake-word" in m.ONBOARDING_STEPS
    assert "persona-theme" not in m.ONBOARDING_STEPS
    # Persona-name, overlay-style and the mic check were moved out of onboarding
    # to keep first-run short; it ends at api-keys → finish (removed 2026-06-20).
    assert "system-style" not in m.ONBOARDING_STEPS
    assert "mic-test" not in m.ONBOARDING_STEPS
    assert m.ONBOARDING_STEPS[-2] == "api-keys"
    assert len(m.ONBOARDING_STEPS) == 6
    assert len(m.WAKE_WORD_LEGAL_REFERENCES) >= 3
    for ref in m.WAKE_WORD_LEGAL_REFERENCES:
        assert ref["label"] and ref["url"].startswith("https://")


def test_read_terms_text_returns_versioned_body():
    text = m.read_terms_text()
    assert "Personal Jarvis" in text
    assert "v1.0" in text
    # The 'no affiliation' clause must be present (legal core).
    assert "affiliat" in text.lower()
