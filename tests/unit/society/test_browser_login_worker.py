"""Plain login cannot observe pages, inherit a running task, or change profiles."""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fakes.browser_login import (
    AutomationHandle,
    NativeSurface,
    PlainChromeFactory,
    PlaywrightStartup,
)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    previous = sys.stdout
    try:
        from jarvis.society.browser import live_runner
    finally:
        sys.stdout = previous
    events, wire = [], []
    worker = live_runner.Worker()
    factory = PlainChromeFactory(events)
    monkeypatch.setitem(
        sys.modules,
        "manual_chrome",
        SimpleNamespace(
            PlainChrome=factory,
            select_chrome_executable=lambda profile: "stable-chrome.exe",
            pinned_chrome_executable=lambda profile: None,
            find_installed_chrome=lambda: "stable-chrome.exe",
        ),
    )
    monkeypatch.setattr(live_runner, "emit", lambda kind, **payload: wire.append((kind, payload)))
    monkeypatch.setattr(worker, "start_monitors", lambda: events.append(("monitors", "started")))
    worker.start_args = {
        "profile_dir": str(tmp_path / "profile"),
        "executable": "testing-chrome.exe",
        "workspace": str(tmp_path / "workspace"),
        "creationflags": 0x08000000,
    }
    worker.login_available = True
    worker.native = NativeSurface(events, "managed")
    worker.context = AutomationHandle(events, "context")
    worker.browser = AutomationHandle(events, "engine")
    worker.playwright = AutomationHandle(events, "playwright")
    return SimpleNamespace(
        worker=worker, factory=factory, events=events, wire=wire, module=live_runner
    )


async def test_login_cancels_and_awaits_task_before_replacing_browser(runtime):
    worker, events = runtime.worker, runtime.events
    began = asyncio.Event()

    async def job():
        began.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            events.append(("job", "stopped"))

    worker.job = asyncio.create_task(job())
    await began.wait()
    old_generation = worker.generation
    worker.latest, worker.target = {"data": "old-frame"}, "old-tab"
    result = await worker.command("takeover", {"enabled": True, "login": True})
    assert events.index(("job", "stopped")) < events.index(("context", "closed"))
    assert events.index(("context", "closed")) < events.index(("plain", "started"))
    assert events.index(("playwright", "stopped")) < events.index(("plain", "started"))
    assert result["login_mode"] and result["manual"] and result["login_available"]
    assert not worker.agent_gate.is_set()
    assert worker.context is worker.browser is worker.playwright is None
    assert worker.latest is None and not worker.target and not worker.tabs
    assert result["generation"] != old_generation
    plain = runtime.factory.instances[0]
    assert str(plain.profile) == worker.start_args["profile_dir"]
    assert plain.executable == "stable-chrome.exe" and plain.creationflags == 0x08000000


async def test_normal_release_supersedes_pause_before_the_held_agent_step_finishes(runtime):
    worker = runtime.worker
    worker.step_idle.clear()
    held_step = asyncio.Event()
    worker.job = asyncio.create_task(held_step.wait())
    pausing = asyncio.create_task(worker.command("takeover", {"enabled": True}))
    try:
        await asyncio.sleep(0)
        assert not worker.agent_gate.is_set() and not pausing.done()
        released = await asyncio.wait_for(
            worker.command("takeover", {"enabled": False}),
            timeout=1,
        )
        assert released["manual"] is False and worker.agent_gate.is_set()
        assert not worker.step_idle.is_set() and not pausing.done()
        worker.step_idle.set()
        assert (await pausing)["manual"] is False
        assert not worker.manual and not worker.login_mode
    finally:
        worker.job.cancel()
        pausing.cancel()
        await asyncio.gather(worker.job, pausing, return_exceptions=True)


async def test_explicit_handback_reports_mode_if_login_never_reached_the_worker(runtime):
    result = await runtime.worker.command("takeover", {"enabled": False, "login": False})
    assert result["manual"] is False and result["login_mode"] is False
    assert result["generation"] == runtime.worker.generation


async def test_login_survives_ensure_subscriber_loss_and_denies_agent(runtime):
    worker = runtime.worker
    await worker.command("takeover", {"enabled": True, "login": True})
    await worker.command("subscribe", {"enabled": False})
    status = await worker.command("ensure", {})
    assert status["login_mode"] and status["manual"]
    assert len(runtime.factory.instances) == 1 and worker.context is None
    for operation in ("run",):
        with pytest.raises(RuntimeError, match="Return browser control"):
            await worker.command(operation, {})
    with pytest.raises(RuntimeError, match="Finish signing in"):
        await worker.ensure_browser()
    assert not worker.agent_gate.is_set()


@pytest.mark.parametrize("implicit", [{"enabled": False}, {"enabled": False, "login": None}])
async def test_subscriber_release_cannot_end_login_without_explicit_handback(runtime, implicit):
    worker = runtime.worker
    await worker.command("takeover", {"enabled": True, "login": True})
    plain = worker.plain_chrome
    result = await worker.command("takeover", implicit)
    assert result["manual"] and result["login_mode"]
    assert worker.plain_chrome is plain and ("plain", "closing") not in runtime.events
    assert worker.context is None and not worker.agent_gate.is_set()


async def test_login_input_and_state_never_inspect_automation(runtime, monkeypatch):
    worker = runtime.worker
    await worker.command("takeover", {"enabled": True, "login": True})
    worker.context = AutomationHandle(runtime.events, "forbidden")
    current = {"generation": worker.generation}
    await worker.command("key", {"key": "Control+t", **current})
    await worker.command("navigate", {"url": "https://accounts.example/login", **current})
    await worker.command("click", {"x": 12, "y": 13, **current})
    await worker.command("text", {"text": "fixture-input", **current})
    assert [op for op, args in worker.native.calls] == [
        "key",
        "key",
        "text",
        "key",
        "click",
        "text",
    ]
    worker.viewers = True

    def state(kind, **payload):
        runtime.wire.append((kind, payload))
        worker.closed = True

    monkeypatch.setattr(runtime.module, "emit", state)
    await worker.watch_state()
    kind, payload = runtime.wire[-1]
    assert kind == "state" and payload["login_mode"]
    assert payload["url"] == "" and payload["tabs"] == []
    assert "fixture-input" not in str(runtime.wire)


async def test_return_waits_for_profile_release_and_reopens_same_stable_profile(
    runtime, monkeypatch
):
    worker = runtime.worker
    await worker.command("takeover", {"enabled": True, "login": True})
    plain = worker.plain_chrome
    plain.close_gate = asyncio.Event()
    reopened = []

    async def start(args):
        reopened.append(dict(args))
        runtime.events.append(("automation", "started"))
        return worker.status()

    monkeypatch.setattr(worker, "start", start)
    previous_generation = worker.generation
    returning = asyncio.create_task(worker.command("takeover", {"enabled": False, "login": False}))
    await asyncio.wait_for(plain.close_started.wait(), timeout=1)
    assert not reopened and worker.manual and not worker.agent_gate.is_set()
    plain.close_gate.set()
    result = await returning
    assert reopened[0]["profile_dir"] == str(plain.profile)
    assert reopened[0]["executable"] == plain.executable
    assert runtime.events.index(("plain", "closed")) < runtime.events.index(
        ("automation", "started")
    )
    assert result["generation"] != previous_generation
    assert not result["login_mode"] and not result["manual"] and worker.agent_gate.is_set()


@pytest.mark.parametrize(
    "failure", ["plain-start", "plain-close", "managed-close", "managed-start"]
)
async def test_failed_handoff_remains_paused_without_automation_fallback(
    runtime, monkeypatch, failure
):
    worker = runtime.worker
    if failure == "plain-start":
        runtime.factory.start_error = True
    elif failure == "managed-close":
        worker.context.fail_close = True
    else:
        await worker.command("takeover", {"enabled": True, "login": True})
        if failure == "plain-close":
            worker.plain_chrome.close_error = True
        else:

            async def broken_start(args):
                worker.context = AutomationHandle(runtime.events, "partial-start")
                raise RuntimeError("Could not restore managed Chrome")

            monkeypatch.setattr(worker, "start", broken_start)
    with pytest.raises(RuntimeError):
        await worker.command(
            "takeover",
            {
                "enabled": failure in {"plain-start", "managed-close"},
                "login": failure in {"plain-start", "managed-close"},
            },
        )
    assert worker.login_mode and worker.manual and not worker.agent_gate.is_set()
    assert (await worker.command("ensure", {}))["login_mode"]
    with pytest.raises(RuntimeError, match="Return browser control"):
        await worker.command("run", {})
    if failure == "plain-close":
        assert worker.plain_chrome is runtime.factory.instances[0]
    if failure == "managed-start":
        assert worker.context is None and ("partial-start", "closed") in runtime.events


async def test_commands_queued_before_replacement_cannot_hit_new_window(runtime):
    worker = runtime.worker
    worker.manual = True
    await worker.transition_lock.acquire()
    clicking = asyncio.create_task(
        worker.command(
            "click",
            {
                "x": 10,
                "y": 20,
                "generation": worker.generation,
            },
        )
    )
    await asyncio.sleep(0)
    worker.reset_surface()
    worker.transition_lock.release()
    with pytest.raises(RuntimeError, match="window changed"):
        await clicking
    assert worker.native.calls == []


@pytest.mark.parametrize("frame", [{}, {"generation": "previous-window"}])
async def test_late_input_requires_a_decoded_frame_from_the_current_window(runtime, frame):
    worker = runtime.worker
    await worker.command("takeover", {"enabled": True, "login": True})
    with pytest.raises(RuntimeError, match="window changed"):
        await worker.command("text", {"text": "fixture-input", **frame})
    assert not worker.native.calls


async def test_stopping_monitors_awaits_inflight_capture_and_cursor_work(runtime):
    worker, events = runtime.worker, runtime.events
    ready = asyncio.Event()

    async def monitor(name):
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append((name, "cancelled"))

    worker.stream = asyncio.create_task(monitor("stream"))
    worker.state_task = asyncio.create_task(monitor("state"))
    worker.cursor_jobs.add(asyncio.create_task(monitor("cursor")))
    await ready.wait()
    await worker.command("takeover", {"enabled": True, "login": True})
    for name in ("stream", "state", "cursor"):
        assert events.index((name, "cancelled")) < events.index(("plain", "started"))
    assert worker.stream is worker.state_task is None and not worker.cursor_jobs


@pytest.mark.parametrize("pinned", [None, "stable-chrome.exe", "invalid"])
async def test_startup_reuses_durable_chrome_pin_and_never_falls_back_if_invalid(
    runtime, monkeypatch, pinned
):
    worker = runtime.worker
    pw = PlaywrightStartup()
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        SimpleNamespace(
            async_playwright=lambda: pw,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "native_window",
        SimpleNamespace(
            NativeWindow=NativeSurface,
            available=lambda: False,
        ),
    )
    monkeypatch.setitem(
        sys.modules, "page_cursor", SimpleNamespace(install_cursor=pw.install_cursor)
    )

    def read_pin(profile):
        if pinned == "invalid":
            raise RuntimeError("The Chrome used by this profile is unavailable")
        return pinned

    monkeypatch.setattr(sys.modules["manual_chrome"], "pinned_chrome_executable", read_pin)
    profile = Path(worker.start_args["profile_dir"])
    await asyncio.to_thread(profile.mkdir)
    (profile / "DevToolsActivePort").write_text("43210\n", encoding="utf-8")
    if pinned == "invalid":
        with pytest.raises(RuntimeError, match="profile is unavailable"):
            await worker.start(worker.start_args)
        assert not pw.started and not pw.launches
    else:
        result = await worker.start(worker.start_args)
        assert pw.launches[0][0] == str(profile)
        assert pw.launches[0][1]["executable_path"] == (pinned or "testing-chrome.exe")
        assert not result["login_mode"] and not result["login_available"]


async def test_worker_shutdown_closes_its_plain_chrome(runtime, monkeypatch):
    worker = runtime.worker
    await worker.command("takeover", {"enabled": True, "login": True})
    monkeypatch.setitem(sys.modules, "native_window", SimpleNamespace(available=lambda: False))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    await worker.main()
    assert ("plain", "closed") in runtime.events
    assert worker.plain_chrome is None and worker.native is None and worker.closed


async def test_closed_login_window_leaves_agent_paused_and_worker_alive(runtime):
    worker = runtime.worker
    await worker.command("takeover", {"enabled": True, "login": True})
    worker.native.failed = True
    await worker.watch()
    assert not worker.closed and worker.login_mode and not worker.agent_gate.is_set()
    assert runtime.wire[-1][0] == "warning"
    assert not any(kind == "fatal" for kind, _ in runtime.wire)
