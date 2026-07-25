from jarvis.marketplace.usage_cards.loader import UsageCard, load_usage_card


def test_load_calendar_card_parses_frontmatter_and_body():
    card = load_usage_card("google-calendar")
    assert card is not None
    assert card.plugin_id == "google-calendar"
    assert "kalender" in card.keywords
    assert "list_events" in card.body


def test_unknown_plugin_returns_none():
    assert load_usage_card("does-not-exist") is None


def test_trailing_comment_on_the_keyword_line_is_not_a_keyword():
    """The keyword list is the one place German belongs — it is the vocabulary
    a German utterance has to hit — so it carries an inline `i18n-allow` marker
    for the language gate. That marker must not leak in as a keyword, or the
    plugin would 'match' any turn containing it."""
    card = load_usage_card("todoist")
    assert card is not None
    assert "todoist" in card.keywords
    assert not any("i18n-allow" in kw for kw in card.keywords)
    assert not any(kw.startswith("#") for kw in card.keywords)


def test_keyword_match_is_case_insensitive_substring():
    card = UsageCard(plugin_id="x", keywords=["kalender", "termine"], body="...")
    assert card.matches("Was habe ich heute für TERMINE?") is True  # i18n-allow: simulated German user utterance, content under test
    assert card.matches("erzähl mir einen witz") is False  # i18n-allow: simulated German user utterance, content under test
