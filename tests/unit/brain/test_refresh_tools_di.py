"""``BrainManager.refresh_tools`` must preserve the boot-time DI.

Regression for the live 2026-06-18 voice bug: every CLI/MCP connect at boot
triggers a tool refresh. The refresh rebuilt the tool set via
``_load_tools_for_tier`` but dropped the shared DI references the boot path
passes, so the rebuilt tools lost their managers after the first CLI
connected. The fix mirrors the boot DI on refresh.
"""
from types import SimpleNamespace

from jarvis.brain.manager import BrainManager


def _bare_for_refresh(**di):
    m = BrainManager.__new__(BrainManager)
    m._tier = "router"
    m._tools = {}
    m._local_action_tools = {}
    m._bus = SimpleNamespace()
    m._tool_executor = object()
    m._config = SimpleNamespace(safety=SimpleNamespace())
    m._user_profile = None
    m._people = None
    m._contacts = di.get("contacts")
    return m


def test_refresh_tools_preserves_managers(monkeypatch):
    import jarvis.brain.factory as factory
    import jarvis.harness.manager as harness_mod

    captured: dict = {}

    def _fake_load_tools_for_tier(tier, **kwargs):
        captured.update(kwargs)
        captured["tier"] = tier
        return {"contact-lookup": object()}

    monkeypatch.setattr(factory, "_load_tools_for_tier", _fake_load_tools_for_tier)
    monkeypatch.setattr(factory, "_load_local_action_tools", lambda **kw: {})
    monkeypatch.setattr(factory, "_resolve_mission_manager", lambda: "MM")
    monkeypatch.setattr(harness_mod, "HarnessManager", lambda **kw: object())

    contacts = object()
    m = _bare_for_refresh(contacts=contacts)

    m.refresh_tools()

    # The boot-time DI must survive the refresh — this is the whole bug.
    assert captured.get("contacts") is contacts
    assert captured.get("mission_manager") == "MM"
    assert "contact-lookup" in m._tools
