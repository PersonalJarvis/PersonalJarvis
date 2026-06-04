from jarvis.marketplace import plugin_shared
from jarvis.marketplace.plugin_loader import PluginToolLoader


class _FakeRegistry:
    def __init__(self, tools): self._tools = tools
    def active_tools(self): return list(self._tools)


def teardown_function():
    plugin_shared.set_active_plugin_registry(None)


def test_loader_is_virtual():
    loader = PluginToolLoader()
    assert loader.is_virtual_loader is True


def test_expand_returns_shared_registry_tools():
    plugin_shared.set_active_plugin_registry(_FakeRegistry(["t1", "t2"]))
    assert PluginToolLoader().expand() == ["t1", "t2"]


def test_expand_empty_when_no_shared_registry():
    plugin_shared.set_active_plugin_registry(None)
    assert PluginToolLoader().expand() == []
