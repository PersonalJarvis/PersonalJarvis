"""BrainManager._plugin_usage_cards_block builds a prompt segment only for the
plugins active in the turn (their tools present in the dict)."""
from jarvis.brain.manager import BrainManager


class _T:
    def __init__(self, name):
        self.name = name


def test_card_block_includes_only_active_plugins():
    mgr = BrainManager.__new__(BrainManager)
    tools = {
        "run-shell": _T("run-shell"),
        "google-calendar/list_events": _T("google-calendar/list_events"),
    }
    block = mgr._plugin_usage_cards_block(tools)
    assert "Plugin: google-calendar" in block
    assert "list_events" in block          # the card body mentions the tool


def test_card_block_empty_without_plugin_tools():
    mgr = BrainManager.__new__(BrainManager)
    assert mgr._plugin_usage_cards_block({"run-shell": _T("run-shell")}) == ""
