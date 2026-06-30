"""Per-turn relevance gate: a connected plugin/MCP tool reaches the brain only
when the turn signals it (the user named it, or its usage-card keywords match) —
never just because it is connected.

A card-less, un-named plugin/MCP is DROPPED. This is the fix for the live
over-trigger: a plain flight question reflexively fired the card-less
``notebooklm-mcp/chat_configure``, wasting ~35s before timing out. Keyword-only,
no LLM, no IO (AP-9 / AP-11).
"""
from jarvis.marketplace.plugin_relevance import filter_plugin_tools


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def _heavy(plugin_id: str, n: int) -> list[_Tool]:
    return [_Tool(f"{plugin_id}/tool_{i}") for i in range(n)]


# --- The reported bug: a card-less connected MCP server must not ride along. ---


def test_cardless_mcp_dropped_on_unrelated_turn():
    # notebooklm-mcp has no usage card; a flight question signals it in no way,
    # so none of its tools may reach the brain.
    tools = [
        _Tool("notebooklm-mcp/chat_configure"),
        _Tool("notebooklm-mcp/notebook_list"),
        _Tool("search_web"),
    ]
    kept = [
        t.name
        for t in filter_plugin_tools(
            "Was ist der kürzeste Flug von München nach Bora Bora?", tools
        )
    ]
    assert "search_web" in kept  # native tool survives
    assert all(not n.startswith("notebooklm-mcp/") for n in kept)


def test_cardless_mcp_dropped_even_on_tiny_surface():
    # The old small-surface bypass kept a couple of plugin tools ungated — that
    # is exactly how a single connected MCP server leaked onto every turn. A tiny
    # surface no longer bypasses the relevance decision.
    tools = [_Tool("notebooklm-mcp/chat_configure"), _Tool("search_web")]
    kept = [t.name for t in filter_plugin_tools("what's the weather tomorrow", tools)]
    assert kept == ["search_web"]


def test_cardless_mcp_kept_when_user_names_it():
    # Explicit mention is the clear keep case; spacing/casing variants collapse:
    # "NotebookLM", "Notebook LM", "notebook-lm" all match the id "notebooklm-mcp".
    tools = [_Tool("notebooklm-mcp/notebook_query"), _Tool("search_web")]
    for utter in (
        "ask NotebookLM about my sources",
        "frag das Notebook LM nach der Zusammenfassung",
        "use notebook-lm for this",
    ):
        kept = [t.name for t in filter_plugin_tools(utter, tools)]
        assert "notebooklm-mcp/notebook_query" in kept, utter
        assert "search_web" in kept


# --- No regression: carded plugins still gate by their curated keywords. ---


def test_carded_plugin_kept_when_relevant():
    # github-unique wording ("repository" is not a Linear keyword).
    tools = _heavy("github", 37) + _heavy("linear", 35)
    kept = [
        t.name for t in filter_plugin_tools("zeig mir meine github repositories", tools)
    ]
    assert any(n.startswith("github/") for n in kept)  # github card matches
    assert all(not n.startswith("linear/") for n in kept)  # linear dropped


def test_carded_plugins_dropped_on_unrelated_turn():
    tools = _heavy("github", 37) + _heavy("linear", 35) + [_Tool("run-shell")]
    kept = [
        t.name for t in filter_plugin_tools("was habe ich heute für termine", tools)
    ]
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
