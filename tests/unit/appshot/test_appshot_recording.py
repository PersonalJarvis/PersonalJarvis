"""Recording lifecycle, permissions, safe downloads and playable video output."""

from __future__ import annotations

import asyncio
import json
import tomllib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.appshot import recording
from jarvis.appshot.hotkey import AppshotShortcut, configured_hotkeys
from jarvis.core.config import AppshotConfig
from jarvis.core.config_writer import set_appshot_settings


class FakeInput:
    def __init__(self, process):
        self.process = process
        self.closed = False

    def write(self, data):
        self.process.commands.append(data)
        if data == b"stop\n":
            self.process.finish("saved")
        else:
            assert json.loads(data)["cmd"] == "start"

    async def drain(self):
        pass

    def close(self):
        self.closed = True
        if self.process.returncode is None:
            self.process.finish("cancelled")


class FakeProcess:
    def __init__(self):
        self.stdout = asyncio.StreamReader()
        self.stdin = FakeInput(self)
        self.returncode = None
        self.exited = asyncio.Event()
        self.commands = []
        self.stdout.feed_data(b'{"phase":"ready"}\n')

    def finish(self, phase, message=""):
        self.stdout.feed_data((json.dumps({"phase": phase, "message": message}) + "\n").encode())
        self.stdout.feed_eof()
        self.returncode = 0
        self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode

    def kill(self):
        self.finish("error")
        self.returncode = -9


@pytest.fixture
def setup_service(monkeypatch, tmp_path):
    from jarvis.core import config

    monkeypatch.setattr(recording, "recording_dir", lambda: tmp_path)
    monkeypatch.setattr(recording, "capability", lambda: {"available": True})
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: SimpleNamespace(
            screen_context=SimpleNamespace(enabled=True),
            ui=SimpleNamespace(language="en"),
        ),
    )
    processes = []

    async def spawn(*args, **kwargs):
        assert "jarvis.appshot.recording_worker" in args
        assert "creationflags" in kwargs
        process = FakeProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return recording.RecordingService(), processes


async def test_concurrent_start_owns_one_process_and_stop_finalizes(setup_service):
    service, processes = setup_service
    first, second = await asyncio.gather(service.start(), service.start())
    assert first["id"] == second["id"]
    assert len(processes) == 1
    assert first["phase"] == "selecting"
    final = await service.stop()
    assert final["phase"] == "saved"
    assert processes[0].stdin.closed
    assert service._process is None


async def test_failure_is_reported_without_claiming_recording_started(setup_service):
    service, processes = setup_service
    await service.start()
    processes[0].finish("error", "Permission denied")
    processes[0].returncode = 1
    await service._reader
    assert service.status()["phase"] == "error"
    assert service.status()["message"] == "Permission denied"
    assert service._started is None


async def test_stop_without_recording_is_idempotent(setup_service):
    service, processes = setup_service
    assert (await service.stop())["phase"] == "idle"
    assert not processes


async def test_standby_takes_no_pixels_or_file_and_start_reuses_it(setup_service, tmp_path):
    service, processes = setup_service
    await service.set_warm(True)
    await service._warm_task
    assert service.status()["phase"] == "idle"
    assert len(processes) == 1 and not processes[0].commands
    assert not list(tmp_path.iterdir())
    first = processes[0]
    try:
        started = await service.start()
        assert service._process is first and len(processes) == 1
        command = json.loads(first.commands[0])
        assert command["output"].endswith(started["id"] + ".mp4")
        assert command["language"] == "en"
        await service.stop()
        await service._warm_task
        assert len(processes) == 2 and not processes[1].commands
        assert service.status()["phase"] == "saved"
    finally:
        await service.close()
    assert all(process.stdin.closed and process.returncode is not None for process in processes)


async def test_disabling_warmup_preserves_active_recording_and_prevents_replacement(setup_service):
    service, processes = setup_service
    await service.set_warm(True)
    await service._warm_task
    await service.start()
    await service.set_warm(False)
    assert processes[0].returncode is None
    assert len(processes[0].commands) == 1
    await service.stop()
    assert len(processes) == 1 and service._warm_task is None


async def test_dead_standby_is_replaced_on_next_press(setup_service):
    service, processes = setup_service
    await service.set_warm(True)
    await service._warm_task
    processes[0].finish("error")
    try:
        await service.start()
        assert service._process is processes[1]
    finally:
        await service.close()


async def test_standby_does_not_bypass_permission_checks(setup_service, monkeypatch):
    service, processes = setup_service
    await service.set_warm(True)
    await service._warm_task
    monkeypatch.setattr(
        recording, "capability", lambda: {"available": False, "detail": "Permission revoked"}
    )
    try:
        with pytest.raises(ValueError, match="Permission revoked"):
            await service.start()
        assert not processes[0].commands
    finally:
        await service.close()


async def test_headless_prewarm_does_not_spawn(setup_service, monkeypatch):
    service, processes = setup_service
    monkeypatch.setattr(recording, "capability", lambda: {"available": False})
    await service.set_warm(True)
    await service._warm_task
    await service.close()
    assert not processes


@pytest.mark.parametrize(("enabled", "owns"), [(True, True), (False, True), (True, False)])
async def test_shortcut_prepares_recording_only_for_enabled_primary_instance(
    monkeypatch, enabled, owns
):
    calls = []

    async def warm(value):
        calls.append(value)

    monkeypatch.setattr(recording, "warm_recording_service", warm)
    monkeypatch.setattr(
        "jarvis.core.config.load_config",
        lambda: SimpleNamespace(
            screen_context=SimpleNamespace(enabled=enabled),
            appshot=SimpleNamespace(hotkey="", region_hotkey="", recording_hotkey=""),
        ),
    )
    monkeypatch.setattr(
        "jarvis.core.instance.current_instance", lambda: SimpleNamespace(owns_ambient_duties=owns)
    )
    shortcut = AppshotShortcut(object())
    await shortcut.reload()
    assert calls == [False, enabled and owns]
    await shortcut.stop()
    assert calls[-1] is False


async def test_slow_warmup_and_start_share_one_child(setup_service, monkeypatch):
    service, processes = setup_service
    original = asyncio.create_subprocess_exec
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def spawn(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await service.set_warm(True)
    await entered.wait()
    start = asyncio.create_task(service.start())
    proceed.set()
    try:
        await start
        await service._warm_task
        assert len(processes) == 1
    finally:
        await service.close()


async def test_cancelled_warmup_reaps_a_late_spawn(setup_service, monkeypatch):
    service, processes = setup_service
    original = asyncio.create_subprocess_exec
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def spawn(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await service.set_warm(True)
    await entered.wait()
    close = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    proceed.set()
    await close
    assert len(processes) == 1 and processes[0].stdin.closed
    assert processes[0].returncode is not None


async def test_standby_timeout_reaps_worker_and_allows_retry(setup_service, monkeypatch):
    from jarvis.appshot import recording_runtime

    service, processes = setup_service
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        if len(processes) == 1:
            await process.stdout.readline()  # Simulate a worker that never announces readiness.
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(recording_runtime, "_READY_TIMEOUT_S", 0.02)
    await service.set_warm(True)
    await service._warm_task
    assert processes[0].stdin.closed
    try:
        await service.start()
        assert service._process is processes[1]
    finally:
        await service.close()


async def test_disabled_master_never_spawns(setup_service, monkeypatch):
    from jarvis.core import config

    service, processes = setup_service
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: SimpleNamespace(
            screen_context=SimpleNamespace(enabled=False),
        ),
    )
    with pytest.raises(ValueError, match="Enable AppShots"):
        await service.start()
    assert not processes


async def test_missing_permission_never_spawns(setup_service, monkeypatch):
    service, processes = setup_service
    monkeypatch.setattr(
        recording,
        "capability",
        lambda: {
            "available": False,
            "detail": "Permission denied",
        },
    )
    with pytest.raises(ValueError, match="Permission denied"):
        await service.start()
    assert not processes


async def test_recording_shortcut_toggles_recording_without_screenshot(monkeypatch):
    calls = []

    async def toggle():
        calls.append("toggle")

    monkeypatch.setattr(recording, "get_recording_service", lambda: SimpleNamespace(toggle=toggle))
    await AppshotShortcut(object())._take("recording")
    assert calls == ["toggle"]


def test_single_recording_shortcut_persists_through_config_writer(tmp_path):
    config = AppshotConfig()
    assert configured_hotkeys(config)["recording"] == "ctrl+shift+9"
    path = tmp_path / "jarvis.toml"
    path.write_text("[appshot]\nhotkey = 'alt+alt'\n", encoding="utf-8")
    set_appshot_settings({"recording_hotkey": "ctrl+shift+8"}, path=path)
    block = tomllib.loads(path.read_text(encoding="utf-8"))["appshot"]
    assert AppshotConfig(**block).recording_hotkey == "ctrl+shift+8"
    assert block["hotkey"] == "alt+alt"


def test_headless_and_wayland_missing_multimedia_are_honest(monkeypatch):
    from jarvis.platform import probes

    monkeypatch.setattr(probes, "display_present", lambda: False)
    assert not recording.capability()["available"]
    monkeypatch.setattr(probes, "display_present", lambda: True)
    monkeypatch.setattr(probes, "is_wayland", lambda: True)
    monkeypatch.setattr(
        recording.importlib.util,
        "find_spec",
        lambda name: None if name == "PySide6.QtMultimedia" else object(),
    )
    result = recording.capability()
    assert not result["available"]
    assert "Wayland" in result["detail"]


def test_macos_screen_permission_is_checked_without_requesting(monkeypatch):
    from jarvis.platform import probes, screen_access
    from jarvis.platform.permissions import PermissionState

    monkeypatch.setattr(probes, "display_present", lambda: True)
    monkeypatch.setattr(probes, "is_wayland", lambda: False)
    monkeypatch.setattr(recording.sys, "platform", "darwin")
    monkeypatch.setattr(recording.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(screen_access, "screen_recording_state", lambda: PermissionState.DENIED)
    result = recording.capability()
    assert result["permission_required"]
    assert not result["available"]


def test_download_rejects_paths_partial_files_and_unknown_ids(monkeypatch, tmp_path):
    from jarvis.ui.web.appshot_routes import router

    monkeypatch.setattr(recording, "recording_dir", lambda: tmp_path)
    recording_id = "a" * 32
    (tmp_path / f"{recording_id}.partial").write_bytes(b"unfinished")
    assert recording.recording_file("../secret") is None
    assert recording.recording_file(recording_id) is None
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get(f"/api/appshot/recording/{recording_id}/video").status_code == 404
        (tmp_path / f"{recording_id}.mp4").write_bytes(b"finished")
        response = client.get(f"/api/appshot/recording/{recording_id}/video")
        assert response.content == b"finished"
        assert response.headers["cache-control"] == "no-store"
    assert recording.recent_recordings()[0]["id"] == recording_id


def test_encoder_writes_playable_even_sized_video_and_preserves_duration(tmp_path):
    av = pytest.importorskip("av")
    qt = pytest.importorskip("PySide6.QtGui")
    from jarvis.appshot.video_encoder import VideoEncoder

    path = tmp_path / "video.mp4"
    encoder = VideoEncoder(path)
    encoder.start()
    image = qt.QImage(65, 49, qt.QImage.Format.Format_RGBA8888)
    image.fill(qt.QColor("red"))
    encoder.submit(image, 0)
    assert encoder.ready.wait(10), encoder.error
    assert not path.exists(), "A video is not downloadable before finalization"
    encoder.stop(1.2)
    assert encoder.done.wait(10), encoder.error
    assert not encoder.error
    assert not encoder.partial.exists()
    with av.open(str(path)) as container:
        frames = list(container.decode(video=0))
        assert (frames[0].width, frames[0].height) == (66, 50)
        assert float(frames[-1].pts * frames[-1].time_base) == pytest.approx(1.2, abs=0.04)
        pixel = frames[0].to_ndarray(format="rgb24")[10, 10]
        assert pixel[0] > 200 and pixel[1] < 35


def test_encoder_failure_leaves_no_downloadable_file(tmp_path):
    qt = pytest.importorskip("PySide6.QtGui")
    pytest.importorskip("av")
    from jarvis.appshot.video_encoder import VideoEncoder

    encoder = VideoEncoder(tmp_path / "missing" / "video.mp4")
    encoder.start()
    encoder.submit(qt.QImage(16, 16, qt.QImage.Format.Format_RGBA8888), 0)
    assert encoder.done.wait(10)
    assert encoder.error
    assert not encoder.path.exists()
