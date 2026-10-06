"""Shared profile ownership and viewer-loss safety without any real browser."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from jarvis.society.browser.live import LiveSession, LiveSessions, LiveUpdates
from tests.fakes.fake_browser_profiles import ProfileSession


def agent(aid):
    return SimpleNamespace(agent_id=aid, browser_mode="own", browser_allowed_domains=[])


async def test_login_transition_drops_a_cached_frame_from_the_previous_browser():
    updates = LiveUpdates()
    updates.put_nowait({"kind": "frame", "generation": "old", "data": "old pixels"})
    updates.put_nowait({"kind": "state", "generation": "login", "login_mode": True})
    assert (await updates.get())["generation"] == "login"
    assert updates.frame is None


async def test_old_worker_heartbeat_cannot_remove_the_parent_login_guard():
    reader = asyncio.StreamReader()
    reader.feed_data(
        json.dumps({"kind": "state", "manual": False, "login_mode": False}).encode() + b"\n"
    )
    reader.feed_eof()
    session = LiveSession(
        "lead", SimpleNamespace(stdout=reader), SimpleNamespace(close=lambda: None)
    )
    session.login_guard = True
    await session.read()
    assert session.state["manual"] and session.state["login_mode"]


async def test_paused_login_without_a_window_is_not_restarted_on_viewer_reconnect(tmp_path):
    live = LiveSessions(tmp_path)
    session = ProfileSession("lead")
    session.state.update(full_window=False, manual=True, login_mode=True)
    session.login_guard = True
    live.sessions["lead"] = session
    assert await live._ensure_managed(agent("lead"), None, window_view=True) is session
    assert not session.closed
    await live.close()


async def test_input_must_reference_the_current_browser_generation(tmp_path):
    live = LiveSessions(tmp_path)
    session = ProfileSession("lead")
    session.control_owner = "viewer"
    session.state.update(login_available=True, generation="login")
    for generation in (None, "old"):
        with pytest.raises(ValueError, match="browser changed"):
            await live.control(
                session, "viewer", "text", {"text": "fixture", "generation": generation}
            )
    assert not session.commands


async def test_viewing_an_existing_running_browser_never_switches_it_to_login(tmp_path):
    live = LiveSessions(tmp_path)
    session = ProfileSession("lead")
    session.state.update(full_window=True, manual=False, login_mode=False)
    live.sessions["lead"] = session
    await session.run_lock.acquire()
    try:
        assert await live._ensure_managed(agent("lead"), None, window_view=True) is session
        assert not session.closed and not session.state["login_mode"]
        assert not session.commands
    finally:
        session.run_lock.release()
        await live.close()


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


async def test_opening_another_agent_moves_a_left_open_sign_in_there(tmp_path, monkeypatch):
    live = manager(tmp_path, monkeypatch)
    pid = live.profiles.create("Shared", "managed", [])["id"]
    live.profiles.share(pid, "all", [], {"lead", "scout"})
    lead = await live.ensure(SimpleNamespace(**vars(agent("lead")), name="Juno"))
    lead.state.update(manual=True, login_mode=True)
    lead.control_owner = "person"
    lead.subscribers.add(LiveUpdates())
    wren = SimpleNamespace(**vars(agent("scout")), name="Wren")
    scout = await live.ensure(wren, window_view=True)
    assert lead.closed and not scout.closed and "lead" not in live.sessions
    assert ("event", {"kind": "disconnected", "reason": "moved", "agent_id": "scout",
                      "agent_name": "Wren"}) in lead.commands
    await live.close()


async def test_watching_one_agent_never_blocks_another_agents_task(tmp_path, monkeypatch):
    live = manager(tmp_path, monkeypatch)
    pid = live.profiles.create("Shared", "managed", [])["id"]
    live.profiles.share(pid, "all", [], {"lead", "scout"})
    lead = await live.ensure(agent("lead"))
    lead.subscribers.add(LiveUpdates())
    scout = await live.ensure(agent("scout"))
    assert lead.closed and not scout.closed
    await live.close()


async def test_a_running_task_keeps_the_profile_and_names_its_agent(tmp_path, monkeypatch):
    from jarvis.society.browser.live import BrowserProfileBusy

    live = manager(tmp_path, monkeypatch)
    pid = live.profiles.create("Shared", "managed", [])["id"]
    live.profiles.share(pid, "all", [], {"lead", "scout"})
    lead = await live.ensure(SimpleNamespace(**vars(agent("lead")), name="Juno"))
    await lead.run_lock.acquire()
    try:
        with pytest.raises(BrowserProfileBusy, match="Juno is running a task") as busy:
            await live.ensure(agent("scout"), window_view=True)
        assert (busy.value.holder_id, busy.value.running) == ("lead", True)
        assert not lead.closed
    finally:
        lead.run_lock.release()
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


async def test_lost_viewer_never_restarts_automation_during_inline_login(tmp_path):
    live = LiveSessions(tmp_path)
    session = ProfileSession("lead")
    session.profile_binding = SimpleNamespace(kind="managed")
    session.state.update(manual=True, login_mode=True)
    session.control_owner = "old-viewer"
    queue = LiveUpdates()
    session.subscribers.add(queue)
    await live.unsubscribe(session, queue, "old-viewer")
    assert session.state["manual"] and session.state["login_mode"]
    assert session.control_owner is None
    assert not any(op == "takeover" for op, _ in session.commands)
    await live.close()


async def test_inline_login_cancels_the_old_turn_before_changing_browser(tmp_path):
    live = LiveSessions(tmp_path)
    session = ProfileSession("lead")
    session.state["login_available"] = True
    session.active_trace = "old-turn"
    await live.control(session, "viewer", "takeover", {"enabled": True, "login": True})
    commands = [(op, args) for op, args in session.commands if op != "event"]
    assert commands == [("cancel", None), ("takeover", {"enabled": True, "login": True})]
    assert ("lead", "old-turn") in live.stopped_turns
    assert session.control_owner == "viewer"
    assert session.state["manual"] and session.state["login_mode"]


async def test_inline_login_requires_the_worker_capability(tmp_path):
    live = LiveSessions(tmp_path)
    session = ProfileSession("lead")
    with pytest.raises(ValueError, match="unavailable"):
        await live.control(session, "viewer", "takeover", {"enabled": True, "login": True})
    assert not session.commands


@pytest.mark.parametrize("entering", [True, False])
async def test_inline_login_failure_never_implicitly_returns_control(tmp_path, entering):
    class FailedLogin(ProfileSession):
        async def command(self, op, args=None, **kwargs):
            self.commands.append((op, args))
            if op == "takeover":
                raise RuntimeError("Owned Chrome is still closing")
            return {}

    live = LiveSessions(tmp_path)
    session = FailedLogin("lead")
    session.control_owner = "viewer"
    session.state.update(login_available=True, manual=not entering, login_mode=not entering)
    args = {"enabled": True, "login": True} if entering else {"enabled": False, "login": False}
    with pytest.raises(RuntimeError, match="still closing"):
        await live.control(session, "viewer", "takeover", args)
    assert session.state["manual"] and session.state["login_mode"]
    assert len([op for op, _ in session.commands if op == "takeover"]) == 1


async def test_inline_login_explicit_return_records_worker_state(tmp_path):
    class ReturnedLogin(ProfileSession):
        async def command(self, op, args=None, **kwargs):
            return {"manual": False, "login_mode": False}

    live = LiveSessions(tmp_path)
    session = ReturnedLogin("lead")
    session.state.update(manual=True, login_mode=True)
    await live.control(session, "viewer", "takeover", {"enabled": False, "login": False})
    assert not session.state["manual"] and not session.state["login_mode"]
    assert session.control_owner is None
