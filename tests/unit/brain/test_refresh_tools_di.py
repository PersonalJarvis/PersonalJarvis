"""``BrainManager.refresh_tools`` must preserve the boot-time DI.

Regression for the live 2026-06-18 voice bug ("der lokale Verlaufsspeicher ist
nicht verfügbar"): every CLI/MCP connect at boot triggers a tool refresh. The
refresh rebuilt the tool set via ``_load_tools_for_tier`` but dropped the shared
DI references the boot path passes, so the rebuilt ``awareness-recall`` got
``recall_store=None`` and returned "awareness recall store unavailable" forever
after the first CLI connected — a genuine outage the brain faithfully relayed,
never a confabulation. The fix mirrors the boot DI on refresh.
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
    m._recall = di.get("recall")
    m._awareness_manager = di.get("awareness")
    m._contacts = di.get("contacts")
    return m


def test_refresh_tools_preserves_recall_store_and_managers(monkeypatch):
    import jarvis.brain.factory as factory
    import jarvis.harness.manager as harness_mod

    captured: dict = {}

    def _fake_load_tools_for_tier(tier, **kwargs):
        captured.update(kwargs)
        captured["tier"] = tier
        return {"awareness-recall": object()}

    monkeypatch.setattr(factory, "_load_tools_for_tier", _fake_load_tools_for_tier)
    monkeypatch.setattr(factory, "_load_local_action_tools", lambda **kw: {})
    monkeypatch.setattr(factory, "_resolve_mission_manager", lambda: "MM")
    monkeypatch.setattr(harness_mod, "HarnessManager", lambda **kw: object())

    recall, awareness, contacts = object(), object(), object()
    m = _bare_for_refresh(recall=recall, awareness=awareness, contacts=contacts)

    m.refresh_tools()

    # The boot-time DI must survive the refresh — this is the whole bug.
    assert captured.get("recall_store") is recall
    assert captured.get("awareness_manager") is awareness
    assert captured.get("contacts") is contacts
    assert captured.get("mission_manager") == "MM"
    assert "awareness-recall" in m._tools


def test_refresh_tools_recall_store_never_silently_none(monkeypatch):
    """Even after a refresh, awareness-recall must NOT be handed a None store
    when the manager holds a real one (the exact silent-None regression)."""
    import jarvis.brain.factory as factory
    import jarvis.harness.manager as harness_mod

    captured: dict = {}
    monkeypatch.setattr(
        factory, "_load_tools_for_tier",
        lambda tier, **kw: (captured.update(kw), {"x": object()})[1],
    )
    monkeypatch.setattr(factory, "_load_local_action_tools", lambda **kw: {})
    monkeypatch.setattr(factory, "_resolve_mission_manager", lambda: None)
    monkeypatch.setattr(harness_mod, "HarnessManager", lambda **kw: object())

    recall = object()
    m = _bare_for_refresh(recall=recall)
    m.refresh_tools()
    assert captured["recall_store"] is recall
    assert captured["recall_store"] is not None
