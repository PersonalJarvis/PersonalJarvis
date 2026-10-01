"""Shared profile ownership and viewer-loss safety without any real browser."""

from types import SimpleNamespace

import pytest

from jarvis.society.browser.live import LiveSessions, LiveUpdates
from tests.fakes.fake_browser_profiles import ProfileSession


def agent(aid):
    return SimpleNamespace(agent_id=aid, browser_mode="own", browser_allowed_domains=[])


def manager(tmp_path, monkeypatch):
    live = LiveSessions(tmp_path)

    async def ensure(value, binding, **kwargs):
        session = ProfileSession(value.agent_id)
        live.sessions[value.agent_id] = session
        return session

    monkeypatch.setattr(live, "_ensure_managed", ensure)
    return live


async def test_shared_profile_has_one_owner_across_agents_and_instances(tmp_path, monkeypatch):
    live = manager(tmp_path, monkeypatch)
    other = manager(tmp_path, monkeypatch)
    pid = live.profiles.create("Shared", "managed", [])["id"]
    live.profiles.share(pid, "all", [], {"lead", "scout"})
    first = await live.ensure(agent("lead"))
    await first.run_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="in use"):
            await live.ensure(agent("scout"))
        with pytest.raises(RuntimeError, match="another Jarvis instance"):
            await other.ensure(agent("scout"))
    finally:
        first.run_lock.release()
    second = await live.ensure(agent("scout"))
    assert first.closed and not second.closed
    assert second.profile_binding.path == first.profile_binding.path
    await live.close()
    await other.close()


async def test_reassignment_stops_old_trace_and_releases_profile(tmp_path, monkeypatch):
    live = manager(tmp_path, monkeypatch)
    session = await live.ensure(agent("lead"))
    session.active_trace = "user-turn"
    pid = live.profiles.create("New", "managed", [])["id"]
    await live.configure_profiles(live.profiles.assign, "lead", "profile", pid, agent_ids={"lead"})
    assert session.closed and session.profile_lease is None
    assert ("lead", "user-turn") in live.stopped_turns
    assert "lead" not in live.sessions
    await live.close()


async def test_profile_edit_leaves_unrelated_and_name_only_sessions_running(tmp_path, monkeypatch):
    live = manager(tmp_path, monkeypatch)
    pid = live.profiles.create("Shared", "managed", [])["id"]
    live.profiles.assign("lead", "profile", pid)
    lead = await live.ensure(agent("lead"))
    scout = await live.ensure(agent("scout"))
    lead.active_trace, scout.active_trace = "lead-turn", "scout-turn"
    await live.configure_profiles(live.profiles.update, pid, name="Renamed", domains=None)
    assert not lead.closed and not scout.closed
    await live.configure_profiles(live.profiles.update, pid, name=None, domains=["example.com"])
    assert lead.closed and not scout.closed
    assert ("lead", "lead-turn") in live.stopped_turns
    assert ("scout", "scout-turn") not in live.stopped_turns
    await live.close()


async def test_another_agent_cannot_reclaim_manual_profile_after_viewer_loss(tmp_path, monkeypatch):
    live = manager(tmp_path, monkeypatch)
    pid = live.profiles.create("Shared", "managed", [])["id"]
    live.profiles.share(pid, "all", [], {"lead", "scout"})
    session = await live.ensure(agent("lead"))
    session.state["manual"] = True
    session.control_owner = None
    with pytest.raises(RuntimeError, match="in use"):
        await live.ensure(agent("scout"))
    assert not session.closed
    await live.close()


async def test_lost_viewer_keeps_chrome_login_paused_until_explicit_reclaim(tmp_path):
    live = LiveSessions(tmp_path)
    session = ProfileSession("lead")
    session.profile_binding = SimpleNamespace(kind="chrome")
    session.state.update(manual=True, preview_paused=True)
    session.control_owner = "old-viewer"
    queue = LiveUpdates()
    session.subscribers.add(queue)
    await live.unsubscribe(session, queue, "old-viewer")
    assert session.state["manual"]
    assert session.control_owner is None
    assert not any(op == "takeover" for op, _ in session.commands)
    await live.control(session, "new-viewer", "takeover", {"enabled": True})
    assert session.control_owner == "new-viewer"
    await live.control(session, "new-viewer", "takeover", {"enabled": False})
    assert session.control_owner is None
    await live.close()
