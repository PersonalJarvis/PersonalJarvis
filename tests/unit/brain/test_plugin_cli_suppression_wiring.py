"""BrainManager hides plugin tools whose CLI is connected (req 4 fallback)."""
from types import SimpleNamespace

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


def _mgr() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    return BrainManager(config=cfg, bus=EventBus(), tools={}, tool_executor=None)


def test_method_drops_plugin_when_cli_present():
    mgr = _mgr()
    tools = {
        "cli_gh": SimpleNamespace(name="cli_gh"),
        "github/list_prs": SimpleNamespace(name="github/list_prs"),
        "search_web": SimpleNamespace(name="search_web"),
    }
    out = mgr._suppress_plugins_covered_by_cli(tools)
    assert set(out) == {"cli_gh", "search_web"}


def test_method_keeps_plugin_when_cli_absent():
    mgr = _mgr()
    tools = {"github/list_prs": SimpleNamespace(name="github/list_prs")}
    assert mgr._suppress_plugins_covered_by_cli(tools) == tools
