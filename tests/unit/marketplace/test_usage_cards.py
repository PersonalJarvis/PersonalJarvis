from jarvis.marketplace.usage_cards.loader import UsageCard, load_usage_card


def test_load_calendar_card_parses_frontmatter_and_body():
    card = load_usage_card("google-calendar")
    assert card is not None
    assert card.plugin_id == "google-calendar"
    assert "kalender" in card.keywords
    assert "list_events" in card.body


def test_unknown_plugin_returns_none():
    assert load_usage_card("does-not-exist") is None


def test_keyword_match_is_case_insensitive_substring():
    card = UsageCard(plugin_id="x", keywords=["kalender", "termine"], body="...")
    assert card.matches("Was habe ich heute für TERMINE?") is True  # i18n-allow: simulated German user utterance, content under test
    assert card.matches("erzähl mir einen witz") is False  # i18n-allow: simulated German user utterance, content under test
