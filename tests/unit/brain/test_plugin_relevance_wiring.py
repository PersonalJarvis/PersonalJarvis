"""BrainManager._apply_plugin_relevance drops plugin tools irrelevant to the
turn while leaving native tools (and card-less plugins) untouched."""
from jarvis.brain.manager import BrainManager


class _T:
    def __init__(self, name):
        self.name = name


def test_apply_plugin_relevance_drops_irrelevant():
    # A heavy plugin (github exposes ~37 tools live) crosses the tool-count gate.
    # github's card keywords do not match "termine" -> all its tools dropped;
    # google-calendar's card matches -> kept; run-shell is native -> always kept.
    mgr = BrainManager.__new__(BrainManager)
    tools = {
        "run-shell": _T("run-shell"),
        "google-calendar/list_events": _T("google-calendar/list_events"),
    }
    for i in range(20):
        tools[f"github/tool_{i}"] = _T(f"github/tool_{i}")
    out = mgr._apply_plugin_relevance("was habe ich heute für termine", tools)
    assert "run-shell" in out
    assert "google-calendar/list_events" in out
    assert not any(n.startswith("github/") for n in out)


def test_apply_plugin_relevance_keeps_all_when_no_plugins():
    mgr = BrainManager.__new__(BrainManager)
    tools = {"run-shell": _T("run-shell"), "screen-snapshot": _T("screen-snapshot")}
    out = mgr._apply_plugin_relevance("anything", tools)
    assert out == tools
