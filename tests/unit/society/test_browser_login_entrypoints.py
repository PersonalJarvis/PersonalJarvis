"""Public login entrypoints preserve the same manual-browser lifecycle as the viewer."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.society.browser.live import LiveUpdates
from jarvis.society.browser.session import BrowserJobs, BrowserUnavailable
from tests.fakes.fake_browser_profiles import LoginControlSession, LoginLive


def setup_jobs(tmp_path, *, capable=True, already_manual=False):
    jobs = BrowserJobs(tmp_path)
    jobs.live = LoginLive(login_available=capable, login_mode=already_manual)
    return jobs, SimpleNamespace(agent_id="scout")


@pytest.mark.parametrize("already_manual", [False, True])
async def test_login_enters_plain_chrome_and_navigates_using_the_new_surface(
    tmp_path, already_manual
):
    jobs, agent = setup_jobs(tmp_path, already_manual=already_manual)

    result = await jobs.login(agent, start_url="https://example.com/login")

    assert jobs.live.calls[:3] == [
        ("ensure", "scout", {"window_view": True}),
        ("takeover", {"enabled": True, "login": True}),
        ("navigate", {"url": "https://example.com/login", "generation": "login-surface"}),
    ]
    assert result["ok"] and result["manual"]
    assert result["authentication"] == "unknown"
    session = jobs.live.sessions["scout"]
    assert session.state["manual"] and session.state["login_mode"]
    assert session.control_owner is None
    # The fake leaves streamed state stale, so this proves navigation used the
    # worker's transition response rather than the pre-transition generation.
    assert session.state["generation"] == "old-surface"


async def test_explicit_done_returns_plain_login_to_automation(tmp_path):
    jobs, agent = setup_jobs(tmp_path)
    await jobs.login(agent)

    assert await jobs.end_login(agent.agent_id)

    assert jobs.live.calls[-2:] == [
        ("takeover", {"enabled": False, "login": False}),
        ("idle", None),
    ]
    assert not jobs.live.sessions["scout"].state["login_mode"]


async def test_connected_extension_keeps_its_existing_manual_protocol(tmp_path):
    jobs, agent = setup_jobs(tmp_path, capable=False)
    await jobs.login(agent, start_url="https://example.com/login")
    assert await jobs.end_login(agent.agent_id)

    assert jobs.live.calls == [
        ("ensure", "scout", {"window_view": True}),
        ("takeover", {"enabled": True}),
        ("navigate", {"url": "https://example.com/login"}),
        ("takeover", {"enabled": False}),
        ("idle", None),
    ]


async def test_failed_navigation_keeps_manual_login_paused(tmp_path):
    jobs, agent = setup_jobs(tmp_path)
    jobs.live.fail_navigation = True

    with pytest.raises(BrowserUnavailable, match="Navigation unavailable"):
        await jobs.login(agent, start_url="https://example.com/login")

    session = jobs.live.sessions["scout"]
    assert session.state["manual"] and session.state["login_mode"]
    assert session.control_owner is None
    assert not any(op == "takeover" and not args["enabled"] for op, args in jobs.live.calls[1:])


async def test_failed_explicit_return_stays_paused(tmp_path):
    jobs, agent = setup_jobs(tmp_path)
    await jobs.login(agent)
    jobs.live.fail_return = True

    with pytest.raises(RuntimeError, match="still closing"):
        await jobs.end_login(agent.agent_id)

    session = jobs.live.sessions["scout"]
    assert session.state["manual"] and session.state["login_mode"]
    assert not any(op == "idle" for op, *_ in jobs.live.calls)


async def test_done_cannot_release_another_viewers_lease(tmp_path):
    jobs, agent = setup_jobs(tmp_path)
    await jobs.login(agent)
    jobs.live.sessions["scout"].control_owner = "another-viewer"
    before = len(jobs.live.calls)

    assert not await jobs.end_login(agent.agent_id)

    assert len(jobs.live.calls) == before


async def test_prepared_login_can_be_claimed_by_its_actual_viewer(tmp_path):
    jobs, agent = setup_jobs(tmp_path)
    await jobs.login(agent)
    session = jobs.live.sessions["scout"]

    await jobs.live.control(session, "visible-viewer", "takeover", {"enabled": True})

    assert session.control_owner == "visible-viewer"
    assert session.state["manual"] and session.state["login_mode"]
    assert not await jobs.end_login(agent.agent_id)


async def test_done_cannot_resume_a_login_prepared_only_by_the_viewer(tmp_path):
    jobs, agent = setup_jobs(tmp_path, already_manual=True)
    await jobs.live.ensure(agent)
    before = len(jobs.live.calls)

    assert not await jobs.end_login(agent.agent_id)

    assert len(jobs.live.calls) == before


async def test_done_cannot_resume_a_replacement_session(tmp_path):
    jobs, agent = setup_jobs(tmp_path)
    await jobs.login(agent)
    del jobs.live.sessions["scout"]
    replacement = await jobs.live.ensure(agent)
    replacement.state.update(manual=True, login_mode=True)
    before = len(jobs.live.calls)

    assert not await jobs.end_login(agent.agent_id)

    assert len(jobs.live.calls) == before


def lifecycle_jobs(tmp_path, monkeypatch, *, capable):
    jobs = BrowserJobs(tmp_path)
    session = LoginControlSession("scout", capable=capable)
    jobs.live.sessions["scout"] = session
    monkeypatch.setattr(jobs.live, "ensure", session.ensure)
    return jobs, session, SimpleNamespace(agent_id="scout")


@pytest.mark.parametrize("capable", [True, False], ids=["managed", "extension"])
async def test_old_api_done_cannot_resume_a_later_viewer_login(
    tmp_path, monkeypatch, capable
):
    jobs, session, agent = lifecycle_jobs(tmp_path, monkeypatch, capable=capable)
    enter = {"enabled": True, **({"login": True} if capable else {})}
    leave = {"enabled": False, **({"login": False} if capable else {})}
    try:
        await jobs.login(agent)
        api_epoch, api_generation = session.manual_epoch, session.generation
        await jobs.live.control(session, "viewer", "takeover", enter)
        assert session.manual_epoch == api_epoch
        await jobs.live.control(session, "viewer", "takeover", leave)
        assert not session.manual_epoch
        await jobs.live.control(session, "viewer", "takeover", enter)
        assert session.manual_epoch != api_epoch
        if not capable:
            assert session.generation == api_generation
        queue = LiveUpdates()
        session.subscribers.add(queue)
        await jobs.live.unsubscribe(session, queue, "viewer")
        before = len(session.commands)

        assert not await jobs.end_login(agent.agent_id)

        assert len(session.commands) == before
        assert session.state["manual"] and session.control_owner is None
    finally:
        await jobs.live.close()


@pytest.mark.parametrize("capable", [True, False], ids=["managed", "extension"])
async def test_same_cycle_viewer_loss_preserves_explicit_api_completion(
    tmp_path, monkeypatch, capable
):
    jobs, session, agent = lifecycle_jobs(tmp_path, monkeypatch, capable=capable)
    enter = {"enabled": True, **({"login": True} if capable else {})}
    try:
        await jobs.login(agent)
        api_epoch = session.manual_epoch
        await jobs.live.control(session, "viewer", "takeover", enter)
        queue = LiveUpdates()
        session.subscribers.add(queue)
        await jobs.live.unsubscribe(session, queue, "viewer")
        assert session.manual_epoch == api_epoch

        assert await jobs.end_login(agent.agent_id)

        assert not session.state["manual"] and not session.manual_epoch
    finally:
        await jobs.live.close()


async def test_failed_handback_preserves_cycle_for_explicit_retry(tmp_path, monkeypatch):
    jobs, session, agent = lifecycle_jobs(tmp_path, monkeypatch, capable=True)
    try:
        await jobs.login(agent)
        api_epoch = session.manual_epoch
        session.fail_return = True
        with pytest.raises(RuntimeError, match="still closing"):
            await jobs.end_login(agent.agent_id)
        assert session.manual_epoch == api_epoch and session.state["manual"]

        session.fail_return = False
        assert await jobs.end_login(agent.agent_id)
        assert not session.manual_epoch
    finally:
        await jobs.live.close()


async def test_api_completion_rechecks_cycle_inside_the_control_lock(tmp_path, monkeypatch):
    jobs, session, agent = lifecycle_jobs(tmp_path, monkeypatch, capable=False)
    finishing = None
    try:
        await jobs.login(agent)
        async with session.control_lock:
            finishing = asyncio.create_task(jobs.end_login(agent.agent_id))
            await asyncio.sleep(0)
            assert not finishing.done()
            # Model a transition already holding this lock when /done arrived.
            session.manual_epoch = "subsequent-manual-cycle"
        assert not await finishing
        assert session.state["manual"]
        assert session.manual_epoch == "subsequent-manual-cycle"
    finally:
        if finishing is not None and not finishing.done():
            finishing.cancel()
            await asyncio.gather(finishing, return_exceptions=True)
        await jobs.live.close()
