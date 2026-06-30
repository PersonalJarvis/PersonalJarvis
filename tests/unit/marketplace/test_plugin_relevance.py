"""Per-turn relevance gate: a connected plugin/MCP tool reaches the brain only
when the turn signals it — the user NAMED it, a usage card matches, OR a
distinctive noun auto-derived from the plugin's OWN tools matches. A card-less,
un-named, off-topic plugin/MCP is DROPPED, so a connected server never rides
along on an unrelated turn (the live ~35s NotebookLM stall on a flight
question). Keyword-only, no LLM, no IO (AP-9 / AP-11).
"""
from jarvis.marketplace.plugin_relevance import (
    derive_plugin_keywords,
    filter_plugin_tools,
    plugin_is_relevant,
)


class _Tool:
    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description


def _heavy(plugin_id: str, n: int) -> list[_Tool]:
    return [_Tool(f"{plugin_id}/tool_{i}") for i in range(n)]


def _notebooklm_tools() -> list[_Tool]:
    """The real NotebookLM MCP toolset: distinctive nouns flashcards / audio /
    overview / mind / slide / deck / quiz / research / infographic / video /
    notebook (+ "podcast" from the audio tool's description) — none of which a
    flight question utters."""
    return [
        _Tool("notebooklm-mcp/chat_configure"),
        _Tool("notebooklm-mcp/flashcards_create"),
        _Tool(
            "notebooklm-mcp/audio_overview_create",
            "Create an audio overview (a podcast) of the notebook sources",
        ),
        _Tool("notebooklm-mcp/mind_map_create"),
        _Tool("notebooklm-mcp/slide_deck_create"),
        _Tool("notebooklm-mcp/quiz_create"),
        _Tool("notebooklm-mcp/research_start"),
        _Tool("notebooklm-mcp/infographic_create"),
        _Tool("notebooklm-mcp/video_overview_create"),
        _Tool("notebooklm-mcp/notebook_list"),
    ]


def _weather_tools() -> list[_Tool]:
    return [
        _Tool("weather-mcp/get_weather", "Get the current weather for a city"),
        _Tool("weather-mcp/get_forecast", "Return a multi-day forecast"),
    ]


# --- The reported bug: a card-less server must EARN the turn, not ride along. ---


def test_cardless_mcp_dropped_on_unrelated_flight_turn():
    # The over-trigger that started this: a flight question signals NotebookLM in
    # NO way (not named, no card, no topical noun of its own), so none of its
    # tools may reach the brain even though it is connected.
    tools = _notebooklm_tools() + [_Tool("search_web")]
    kept = [
        t.name
        for t in filter_plugin_tools(
            "Was ist der kürzeste Flug von München nach Bora Bora?", tools
        )
    ]
    assert "search_web" in kept  # native tool survives
    assert all(not n.startswith("notebooklm-mcp/") for n in kept)


def test_cardless_mcp_fires_on_its_own_topical_noun():
    # The SMART half: "flashcards" / "mind map" / "podcast" are nouns the
    # NotebookLM tools carry (flashcards_create, mind_map_create, and
    # audio_overview_create whose description says "podcast"), so a deliberately
    # added NotebookLM MCP now fires on a topical word without a usage card.
    tools = _notebooklm_tools() + [_Tool("search_web")]
    for utter in (
        "make some flashcards about the French Revolution",
        "build a mind map of my notes",
        "turn my sources into a podcast",
    ):
        kept = [t.name for t in filter_plugin_tools(utter, tools)]
        assert any(n.startswith("notebooklm-mcp/") for n in kept), utter
        assert "search_web" in kept


def test_weather_mcp_fires_on_weather_question():
    # RECONCILED: a deliberately added weather MCP whose tools carry the nouns
    # "weather"/"forecast" now FIRES on a weather question. (The old doctrine
    # dropped EVERY card-less MCP; the smart doctrine fires it on its own topical
    # word — exactly the case the over-correction had broken.)
    tools = _weather_tools() + [_Tool("search_web")]
    kept = [t.name for t in filter_plugin_tools("what's the weather tomorrow", tools)]
    assert any(n.startswith("weather-mcp/") for n in kept)
    assert "search_web" in kept


def test_weather_mcp_still_dropped_on_unrelated_turn():
    # ...but the very same weather MCP stays hidden on a turn topical to
    # something else: a flight question carries no weather/forecast noun.
    tools = _weather_tools() + [_Tool("search_web")]
    kept = [
        t.name
        for t in filter_plugin_tools("shortest flight from Munich to Bora Bora", tools)
    ]
    assert all(not n.startswith("weather-mcp/") for n in kept)
    assert kept == ["search_web"]


def test_generic_verbs_do_not_fire_a_cardless_mcp():
    # The over-trigger guard: generic verbs every MCP shares (create / list /
    # configure / chat) carry no topical signal, so an utterance built only from
    # them must NOT wake a card-less server.
    tools = _notebooklm_tools() + [_Tool("search_web")]
    for utter in (
        "create a new list please",
        "configure and run that",
        "just chat with me",
    ):
        kept = [t.name for t in filter_plugin_tools(utter, tools)]
        assert all(not n.startswith("notebooklm-mcp/") for n in kept), utter


def test_cardless_mcp_kept_when_user_names_it():
    # Explicit mention is the always-available escape hatch; spacing/casing
    # variants collapse: "NotebookLM", "Notebook LM", "notebook-lm" all match the
    # id "notebooklm-mcp".
    tools = [_Tool("notebooklm-mcp/notebook_query"), _Tool("search_web")]
    for utter in (
        "ask NotebookLM about my sources",
        "frag das Notebook LM nach der Zusammenfassung",
        "use notebook-lm for this",
    ):
        kept = [t.name for t in filter_plugin_tools(utter, tools)]
        assert "notebooklm-mcp/notebook_query" in kept, utter
        assert "search_web" in kept


# --- No regression: carded plugins still gate by their curated card. ---


def test_carded_plugin_kept_when_relevant():
    # github-unique wording ("repository" is not a Linear keyword).
    tools = _heavy("github", 37) + _heavy("linear", 35)
    kept = [
        t.name for t in filter_plugin_tools("zeig mir meine github repositories", tools)
    ]
    assert any(n.startswith("github/") for n in kept)  # github card matches
    assert all(not n.startswith("linear/") for n in kept)  # linear dropped


def test_carded_plugin_dropped_on_unrelated_turn():
    tools = _heavy("github", 37) + _heavy("linear", 35) + [_Tool("run-shell")]
    kept = [t.name for t in filter_plugin_tools("erzähl mir einen witz", tools)]
    assert "run-shell" in kept
    assert all(
        not n.startswith("github/") and not n.startswith("linear/") for n in kept
    )


def test_native_tools_never_touched():
    tools = [_Tool("run-shell"), _Tool("screen-snapshot"), _Tool("github/create_issue")]
    kept = [t.name for t in filter_plugin_tools("erzähl einen witz", tools)]
    assert "run-shell" in kept and "screen-snapshot" in kept  # native always kept
    assert all("github/" not in n for n in kept)  # irrelevant plugin dropped


def test_no_plugin_tools_returns_all_unchanged():
    tools = [_Tool("run-shell"), _Tool("search_web")]
    kept = filter_plugin_tools("anything at all", tools)
    assert [t.name for t in kept] == ["run-shell", "search_web"]


# --- Public API the worker-export sibling depends on (verbatim signatures). ---


def test_derive_plugin_keywords_includes_name_card_and_tool_nouns():
    # A carded plugin's keyword set is the UNION of: id name token, card
    # keywords, and topical tool nouns; generic verbs are excluded.
    kws = derive_plugin_keywords(
        "google_calendar",
        [_Tool("google_calendar/list_events", "List calendar events")],
    )
    assert "googlecalendar" in kws  # normalized id name token
    assert "kalender" in kws  # usage-card keyword (lowercased)
    assert "events" in kws  # auto-derived tool noun
    assert "calendar" in kws  # auto-derived from the description
    assert "list" not in kws  # generic verb stoplisted


def test_derive_plugin_keywords_drops_generic_verbs_and_structural_nouns():
    kws = derive_plugin_keywords(
        "weather-mcp",
        [_Tool("weather-mcp/weather_report", "Create and update the weather report")],
    )
    assert "weather" in kws
    for generic in ("create", "update", "report", "configure", "chat", "tool", "mcp"):
        assert generic not in kws


def test_derive_plugin_keywords_is_namespace_scoped():
    # Only the named plugin's own tools contribute — a co-present other plugin's
    # tool nouns never leak into this plugin's keyword set.
    tools = _weather_tools() + [_Tool("github/create_issue", "Create an issue")]
    kws = derive_plugin_keywords("weather-mcp", tools)
    assert "weather" in kws and "forecast" in kws
    assert "issue" not in kws


def test_plugin_is_relevant_matches_naming_card_and_noun():
    wtools = _weather_tools()
    assert plugin_is_relevant("what's the weather", "weather-mcp", wtools) is True
    assert plugin_is_relevant("weather-mcp please", "weather-mcp", wtools) is True
    assert (
        plugin_is_relevant("shortest flight to Bora Bora", "weather-mcp", wtools)
        is False
    )
