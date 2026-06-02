from importlib.metadata import entry_points


def test_plugin_tools_entry_point_registered():
    eps = {ep.name: ep for ep in entry_points(group="jarvis.tool")}
    assert "plugin-tools" in eps
    cls = eps["plugin-tools"].load()
    assert getattr(cls(), "is_virtual_loader", False) is True
