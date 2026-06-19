from jarvis.marketplace.plugin_relevance import filter_plugin_tools


class _Tool:
    def __init__(self, name):
        self.name = name


def _heavy(plugin_id: str, n: int) -> list[_Tool]:
    return [_Tool(f"{plugin_id}/tool_{i}") for i in range(n)]


def test_keeps_only_relevant_plugin_namespace():
    # max_unfiltered_tools=1 forces gating with 2 plugin tools (default keeps a
    # handful ungated — gating triggers on the TOTAL plugin-tool count).
    tools = [_Tool("google-calendar/list_events"), _Tool("github/create_issue")]
    kept = filter_plugin_tools("was habe ich heute für termine", tools, max_unfiltered_tools=1)
    assert [t.name for t in kept] == ["google-calendar/list_events"]


def test_non_plugin_tools_always_kept():
    tools = [_Tool("run-shell"), _Tool("github/create_issue")]
    kept = filter_plugin_tools("erzähl einen witz", tools, max_unfiltered_tools=0)
    assert any(t.name == "run-shell" for t in kept)        # native tool survives
    assert all("github/" not in t.name for t in kept)      # irrelevant plugin dropped


def test_small_surface_kept_whole():
    # A handful of plugin tools stays fully visible (no bloat, no wrong-drop).
    tools = [_Tool("google-calendar/list_events")]
    kept = filter_plugin_tools("mach mal", tools, max_unfiltered_tools=12)
    assert [t.name for t in kept] == ["google-calendar/list_events"]


def test_two_heavy_plugins_gate_by_tool_count():
    # The live bug: github(37)+linear(35)=72 tools from only 2 plugins slipped
    # through the old plugin-count gate. With tool-count gating an unrelated
    # utterance drops BOTH heavy plugins.
    tools = _heavy("github", 37) + _heavy("linear", 35) + [_Tool("run-shell")]
    kept = filter_plugin_tools("was habe ich heute für termine", tools)
    names = [t.name for t in kept]
    assert "run-shell" in names
    assert all(not n.startswith("github/") and not n.startswith("linear/") for n in names)


def test_heavy_plugin_kept_when_relevant():
    # github-unique wording ("repository" is not a Linear keyword; "issue" would
    # match both cards, so avoid it here).
    tools = _heavy("github", 37) + _heavy("linear", 35)
    kept = filter_plugin_tools("zeig mir meine github repositories", tools)
    names = [t.name for t in kept]
    assert any(n.startswith("github/") for n in names)     # github card matches
    assert all(not n.startswith("linear/") for n in names)  # linear dropped
