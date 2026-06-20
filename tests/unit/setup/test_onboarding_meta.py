from jarvis.setup import onboarding_meta as m


def test_meta_constants():
    assert m.CURRENT_TERMS_VERSION == "1.0"
    assert m.ONBOARDING_STEPS[0] == "welcome"
    assert m.ONBOARDING_STEPS[1] == "terms"
    assert m.ONBOARDING_STEPS[-1] == "finish"
    assert "wake-word" in m.ONBOARDING_STEPS
    # The "system-style" overlay-surface chooser sits right after persona-theme
    # and right before the finish step.
    assert m.ONBOARDING_STEPS.index("system-style") == m.ONBOARDING_STEPS.index("persona-theme") + 1
    assert m.ONBOARDING_STEPS[-2] == "system-style"
    assert len(m.ONBOARDING_STEPS) == 9
    assert len(m.WAKE_WORD_LEGAL_REFERENCES) >= 3
    for ref in m.WAKE_WORD_LEGAL_REFERENCES:
        assert ref["label"] and ref["url"].startswith("https://")


def test_read_terms_text_returns_versioned_body():
    text = m.read_terms_text()
    assert "Personal Jarvis" in text
    assert "v1.0" in text
    # The 'no affiliation' clause must be present (legal core).
    assert "affiliat" in text.lower()
