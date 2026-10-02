"""Background agent service: hand-off on quit, hand-back on launch.

Routines and chat channels must outlive the desktop window. These tests pin
the decisions (when a service starts, who owns the lock, when it stops) with
fakes; the process-level round trip is covered by the live smoke described in
docs/background-service.md.
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

from jarvis.core import background_service as bg


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_INSTANCE", raising=False)


class _Scheduler:
    def __init__(self, armed: int = 0, running: int = 0) -> None:
        self._value = (armed, running)

    def pending_work(self) -> tuple[int, int]:
        return self._value


class _Channels:
    def __init__(self, *names: str) -> None:
        self._names = list(names)

    def started(self) -> list[str]:
        return self._names


def _state(*, armed: int = 0, running: int = 0, channels: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        task_scheduler=_Scheduler(armed, running), channel_manager=_Channels(*channels)
    )


def _cfg(*, keep: bool = True) -> SimpleNamespace:
    return SimpleNamespace(background=SimpleNamespace(keep_agents_running=keep))


# ---------------------------------------------------------------------------
# What is worth keeping
# ---------------------------------------------------------------------------


def test_work_counts_routines_runs_and_channels() -> None:
    work = bg.work_from_state(_state(armed=2, running=1, channels=("telegram",)))
    assert work == bg.BackgroundWork(routines=2, running=1, channels=("telegram",))
    assert work
    assert "2 armed routine(s)" in work.describe()
    assert "telegram" in work.describe()


def test_no_scheduler_and_no_channels_is_no_work() -> None:
    assert not bg.work_from_state(SimpleNamespace())
    assert not bg.work_from_state(_state())


def test_broken_scheduler_reads_as_no_routines() -> None:
    class Broken:
        def pending_work(self):
            raise RuntimeError("store closed")

    state = SimpleNamespace(task_scheduler=Broken(), channel_manager=_Channels("discord"))
    assert bg.work_from_state(state) == bg.BackgroundWork(channels=("discord",))


def test_keep_running_defaults_on() -> None:
    assert bg.keep_running_enabled(SimpleNamespace()) is True
    assert bg.keep_running_enabled(_cfg(keep=False)) is False


def test_config_defaults() -> None:
    from jarvis.core.config import JarvisConfig

    cfg = JarvisConfig()
    assert cfg.background.keep_agents_running is True
    assert cfg.autostart.background_only is False


# ---------------------------------------------------------------------------
# Marker + handover request
# ---------------------------------------------------------------------------


def test_marker_round_trip_and_only_own_marker_is_cleared() -> None:
    bg.write_marker(47821)
    data = bg.read_marker()
    assert data is not None and data["pid"] == os.getpid() and data["port"] == 47821
    bg.clear_marker()
    assert bg.read_marker() is None

    bg._write_json(bg.marker_path(), {"pid": os.getpid() + 1, "port": 1})
    bg.clear_marker()
    assert bg.read_marker() is not None


def test_service_pid_requires_a_live_service_process() -> None:
    assert bg.service_pid(is_service=lambda _pid: True) is None
    bg._write_json(bg.marker_path(), {"pid": 4242, "port": 1})
    assert bg.service_pid(is_service=lambda pid: pid == 4242) == 4242
    assert bg.service_pid(is_service=lambda _pid: False) is None


def test_marker_paths_are_per_instance(monkeypatch) -> None:
    default = bg.marker_path()
    monkeypatch.setenv("JARVIS_INSTANCE", "dev")
    assert bg.marker_path() != default
    assert bg.handover_path().name.startswith("handover")


def test_handover_request_expires() -> None:
    assert not bg.handover_requested()
    bg.request_handover()
    assert bg.handover_requested()
    mtime = bg.handover_path().stat().st_mtime
    assert not bg.handover_requested(now=lambda: mtime + bg.HANDOVER_MAX_AGE_S + 1)
    bg.clear_handover_request()
    assert not bg.handover_requested()


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def test_source_command_runs_the_headless_launcher_as_service() -> None:
    argv = bg.service_command(after_pid=42, executable="/usr/bin/python3", frozen=False)
    assert argv[1:] == [
        "-m",
        "jarvis.ui.web.launcher",
        "--headless",
        bg.SERVICE_FLAG,
        bg.AFTER_PID_FLAG,
        "42",
    ]


def test_command_carries_the_instance(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_INSTANCE", "dev")
    argv = bg.service_command(after_pid=None, executable="/usr/bin/python3", frozen=False)
    assert argv[-2:] == ["--instance", "dev"]


def test_frozen_command_reenters_the_executable() -> None:
    argv = bg.service_command(after_pid=7, executable="/opt/jarvis/Jarvis", frozen=True)
    assert argv == ["/opt/jarvis/Jarvis", bg.SERVICE_FLAG, bg.AFTER_PID_FLAG, "7"]


def test_after_pid_is_parsed() -> None:
    from jarvis.ui.web import launcher

    assert launcher._parse_args([bg.SERVICE_FLAG, bg.AFTER_PID_FLAG, "9"]).after_pid == 9


# ---------------------------------------------------------------------------
# Hand-off on quit
# ---------------------------------------------------------------------------


@pytest.fixture
def spawned(monkeypatch):
    calls: list[int | None] = []

    def _spawn(*, after_pid):
        calls.append(after_pid)
        return True

    monkeypatch.setattr(bg, "spawn_service", _spawn)
    return calls


def test_quit_with_work_starts_the_service_and_drops_a_stale_request(spawned) -> None:
    bg.request_handover()
    assert bg.hand_off_on_quit(_state(armed=1), _cfg(), pid=1234) is True
    assert spawned == [1234]
    assert not bg.handover_path().exists()


def test_quit_without_work_starts_nothing(spawned) -> None:
    assert bg.hand_off_on_quit(_state(), _cfg()) is False
    assert spawned == []


def test_quit_with_setting_off_starts_nothing(spawned) -> None:
    assert bg.hand_off_on_quit(_state(channels=("telegram",)), _cfg(keep=False)) is False
    assert spawned == []


def test_dev_instance_never_hands_off(monkeypatch, spawned) -> None:
    monkeypatch.setenv("JARVIS_INSTANCE", "dev")
    assert bg.hand_off_on_quit(_state(armed=3), _cfg()) is False
    assert spawned == []


def test_desktop_skips_the_hand_off_on_restart(monkeypatch) -> None:
    from jarvis.ui.desktop_app import DesktopApp

    seen: list[object] = []
    monkeypatch.setattr(bg, "hand_off_on_quit", lambda state, cfg: seen.append(state))
    app = DesktopApp.__new__(DesktopApp)
    state = _state(armed=1)
    app._server = SimpleNamespace(app=SimpleNamespace(state=state), cfg=_cfg())
    app.cfg = _cfg()

    app._skip_background_handoff = True
    app._hand_off_to_background_service()
    assert seen == []

    app._skip_background_handoff = False
    app._hand_off_to_background_service()
    assert seen == [state]


# ---------------------------------------------------------------------------
# Hand-back on launch
# ---------------------------------------------------------------------------


class _Busy(Exception):
    pass


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_no_service_means_no_takeover() -> None:
    def acquire():
        raise AssertionError("must not touch the lock")

    assert bg.take_over_from_service(acquire, busy_error=_Busy, find_pid=lambda: None) is None


def test_service_hands_back_and_the_request_is_cleared() -> None:
    clock = _Clock()
    attempts: list[bool] = []

    def acquire():
        attempts.append(bg.handover_path().exists())
        if len(attempts) < 3:
            raise _Busy
        return "lock"

    lock = bg.take_over_from_service(
        acquire, busy_error=_Busy, find_pid=lambda: 99, sleep=clock.sleep, now=clock.now
    )
    assert lock == "lock"
    assert attempts == [True, True, True]
    assert not bg.handover_path().exists()


def test_service_that_never_lets_go_is_stopped() -> None:
    clock = _Clock()
    killed: list[int] = []

    def acquire():
        if not killed:
            raise _Busy
        return "lock"

    lock = bg.take_over_from_service(
        acquire,
        busy_error=_Busy,
        wait_s=2.0,
        find_pid=lambda: 77,
        alive=lambda _pid: True,
        terminate=lambda pid: killed.append(pid) or True,
        sleep=clock.sleep,
        now=clock.now,
    )
    assert lock == "lock"
    assert killed == [77]


# ---------------------------------------------------------------------------
# The service side (launcher)
# ---------------------------------------------------------------------------


def test_service_steps_aside_for_an_opening_desktop(monkeypatch) -> None:
    from jarvis.ui import desktop_log
    from jarvis.ui.web import launcher

    monkeypatch.setattr(desktop_log, "_install_desktop_log_sink", lambda _path: None)
    monkeypatch.setenv("JARVIS_VOICE", "1")
    bg.request_handover()
    args = launcher._parse_args([bg.SERVICE_FLAG])
    assert launcher._prepare_background_service(args) is None
    assert os.environ["JARVIS_VOICE"] == "0"


def test_service_never_evicts_a_running_app(monkeypatch) -> None:
    from jarvis.ui import desktop_app, desktop_log
    from jarvis.ui.web import launcher

    monkeypatch.setattr(desktop_log, "_install_desktop_log_sink", lambda _path: None)
    monkeypatch.setenv("JARVIS_VOICE", "1")
    calls: list[object] = []

    def busy(**kwargs):
        calls.append(kwargs["terminate"](1))
        raise desktop_app.SingleInstanceError("Jarvis is already running (pid=1).")

    monkeypatch.setattr(desktop_app, "acquire_single_instance_lock", busy)
    assert launcher._prepare_background_service(launcher._parse_args([bg.SERVICE_FLAG])) is None
    assert calls == [False]


def test_background_service_flag_implies_headless() -> None:
    from jarvis.ui.web import launcher

    args = launcher._parse_args([bg.SERVICE_FLAG])
    assert args.background_service is True


def _run_watch(state, **kwargs) -> bool:
    from jarvis.ui.web import launcher

    async def scenario() -> bool:
        stop = asyncio.Event()
        try:
            await asyncio.wait_for(
                launcher._watch_background_service(state, stop, poll_s=0.01, **kwargs), 1.0
            )
        except TimeoutError:
            return stop.is_set()
        return stop.is_set()

    return asyncio.run(scenario())


def test_watch_stops_on_a_handover_request() -> None:
    bg.request_handover()
    assert _run_watch(_state(armed=1), idle_check_s=60.0) is True


def test_watch_stops_when_the_work_is_gone() -> None:
    assert _run_watch(_state(), idle_check_s=0.01, idle_strikes=2) is True


def test_watch_keeps_running_while_there_is_work() -> None:
    assert _run_watch(_state(channels=("telegram",)), idle_check_s=0.01, idle_strikes=1) is False


def test_scheduler_reports_pending_work() -> None:
    from jarvis.tasks.scheduler import TaskScheduler

    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler._known = {"a", "b"}

    class _Done:
        def done(self) -> bool:
            return True

    class _Live:
        def done(self) -> bool:
            return False

    scheduler._runner_tasks = {_Done(), _Live()}
    assert scheduler.pending_work() == (2, 1)


# ---------------------------------------------------------------------------
# Login entry
# ---------------------------------------------------------------------------


def _autostart_cfg(*, background_only: bool) -> SimpleNamespace:
    return SimpleNamespace(
        autostart=SimpleNamespace(
            enabled=True, start_minimized=False, background_only=background_only
        )
    )


def test_login_entry_starts_only_the_service_when_asked(monkeypatch) -> None:
    from jarvis.autostart.command import LAUNCHER_MODULE, resolve_launch_spec

    monkeypatch.setattr(sys, "platform", "linux")
    assert resolve_launch_spec(_autostart_cfg(background_only=False)).args == (
        "-m",
        LAUNCHER_MODULE,
    )
    assert resolve_launch_spec(_autostart_cfg(background_only=True)).args == (
        "-m",
        LAUNCHER_MODULE,
        bg.SERVICE_FLAG,
    )


def test_macos_login_entry_passes_the_flag_through_launchservices(monkeypatch, tmp_path) -> None:
    import jarvis.setup.macos_app_bundle as bundle_module
    from jarvis.autostart.command import resolve_launch_spec

    bundle = tmp_path / "Personal Jarvis.app"
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(bundle_module, "macos_app_bundle_path", lambda: bundle)
    monkeypatch.setattr(bundle_module, "macos_app_bundle_is_launchable", lambda _b: True)
    spec = resolve_launch_spec(_autostart_cfg(background_only=True))
    assert spec.args == ("-g", "-W", "-a", str(bundle), "--args", bg.SERVICE_FLAG)


# ---------------------------------------------------------------------------
# Settings route
# ---------------------------------------------------------------------------


def test_settings_route_reports_and_persists(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from jarvis.core import config_writer
    from jarvis.ui.web import settings_routes

    writes: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        config_writer, "set_background_keep_running", lambda v: writes.append(("keep", v))
    )
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.state.config = SimpleNamespace(
        background=SimpleNamespace(keep_agents_running=True),
        autostart=SimpleNamespace(enabled=False, background_only=False),
    )
    app.state.task_scheduler = _Scheduler(armed=2)
    app.state.channel_manager = _Channels("telegram")
    client = TestClient(app)

    body = client.get("/api/settings/background").json()
    assert body["keep_agents_running"] is True
    assert body["work"] == {"routines": 2, "running": 0, "channels": ["telegram"]}
    assert body["supported"] is True

    body = client.put("/api/settings/background", json={"keep_agents_running": False}).json()
    assert writes == [("keep", False)]
    assert body["keep_agents_running"] is False


def test_service_age_constants_are_sane() -> None:
    assert bg.HANDOVER_POLL_S < 1.0
    assert bg.TAKEOVER_WAIT_S < bg.PARENT_EXIT_WAIT_S
    assert bg.HANDOVER_MAX_AGE_S > bg.TAKEOVER_WAIT_S


def test_service_is_findable_the_moment_it_holds_the_lock(monkeypatch) -> None:
    from jarvis.ui import desktop_app, desktop_log
    from jarvis.ui.web import launcher

    monkeypatch.setattr(desktop_log, "_install_desktop_log_sink", lambda _path: None)
    monkeypatch.setenv("JARVIS_VOICE", "1")
    monkeypatch.setenv("JARVIS_PRIMARY_INSTANCE", "0")
    monkeypatch.setattr(desktop_app, "acquire_single_instance_lock", lambda **_kw: "lock")
    assert launcher._prepare_background_service(launcher._parse_args([bg.SERVICE_FLAG])) == "lock"
    marker = bg.read_marker()
    assert marker is not None and marker["pid"] == os.getpid()


def test_hand_back_lets_running_routines_finish_first() -> None:
    from jarvis.ui.web import launcher

    class _Draining:
        def __init__(self) -> None:
            self.calls = 0

        def pending_work(self) -> tuple[int, int]:
            self.calls += 1
            return (1, 1 if self.calls < 4 else 0)

    scheduler = _Draining()
    state = SimpleNamespace(task_scheduler=scheduler, channel_manager=_Channels())
    asyncio.run(launcher._drain_running_routines(state, drain_s=5.0, poll_s=0.01))
    assert scheduler.calls == 4


def test_drain_is_bounded() -> None:
    from jarvis.ui.web import launcher

    state = _state(running=1)

    async def scenario() -> None:
        await asyncio.wait_for(
            launcher._drain_running_routines(state, drain_s=0.05, poll_s=0.01), 1.0
        )

    asyncio.run(scenario())


class _FakeMenuItem:
    def __init__(self, text, action, **_kwargs) -> None:
        self.text = text
        self._action = action


class _FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items) -> None:
        self.items = [i for i in items if isinstance(i, _FakeMenuItem)]


def test_tray_quit_all_skips_the_hand_off(monkeypatch) -> None:
    # A fake pystray: the real one opens an X display on import, which a
    # headless Linux runner does not have.
    monkeypatch.setitem(
        sys.modules, "pystray", SimpleNamespace(Menu=_FakeMenu, MenuItem=_FakeMenuItem)
    )
    from jarvis.ui.tray import JarvisTray, TrayCommand

    seen: list[str] = []
    tray = JarvisTray(on_command=lambda cmd: seen.append(cmd.action))
    monkeypatch.setattr(tray, "stop", lambda: seen.append("stopped"))
    menu = tray._build_menu()
    item = next(i for i in menu.items if str(i.text) == "Quit and stop background agents")
    item._action(None, item)
    assert seen == ["quit_all", "stopped"]
    assert isinstance(tray._command_queue.get_nowait(), TrayCommand)
