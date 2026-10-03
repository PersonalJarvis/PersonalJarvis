"""Plain login has no debugging transport and retains ownership until exit."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from jarvis.society.browser import manual_chrome as chrome


@pytest.fixture
def installed(monkeypatch, tmp_path):
    executable = tmp_path / "Application" / "chrome.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")
    profile = tmp_path / "agent-profile"
    profile.mkdir()
    monkeypatch.setattr(chrome, "os", SimpleNamespace(
        name="nt", environ={}, fsync=os.fsync, replace=os.replace,
    ))
    monkeypatch.setattr(chrome, "_candidates", lambda: [executable])
    monkeypatch.setattr(chrome, "_file_version", lambda path: (150, 1, 2, 3))
    return profile, executable


def test_installed_selection_pins_binary_for_next_automation_start(installed):
    profile, executable = installed
    assert chrome.pinned_chrome_executable(profile) is None
    assert chrome.select_chrome_executable(profile) == str(executable.resolve())
    assert chrome.pinned_chrome_executable(profile) == str(executable.resolve())
    assert json.loads((profile / chrome._PIN).read_text())["version"] == 1


def test_discovery_never_mutates_profile(installed):
    profile, executable = installed
    assert chrome.find_installed_chrome() == str(executable.resolve())
    assert list(profile.iterdir()) == []


def test_newer_profile_rejects_downgrade_without_pin_or_cookie_changes(installed):
    profile, _ = installed
    (profile / "Last Version").write_text("151.0.0.0")
    (profile / "Cookies").write_bytes(b"untouched")
    with pytest.raises(RuntimeError, match="Update Google Chrome"):
        chrome.select_chrome_executable(profile)
    assert not (profile / chrome._PIN).exists()
    assert (profile / "Cookies").read_bytes() == b"untouched"


@pytest.mark.parametrize("version", ["partial", "150.1.bad.3", "150.1.2"])
def test_unknown_profile_version_fails_closed(installed, version):
    profile, _ = installed
    (profile / "Last Version").write_text(version)
    with pytest.raises(RuntimeError, match="could not be verified"):
        chrome.select_chrome_executable(profile)


@pytest.mark.parametrize("pin", ["{}", "null", "{broken", '{"version":1,"executable":42}'])
def test_invalid_pin_never_falls_back(installed, pin):
    profile, _ = installed
    (profile / chrome._PIN).write_text(pin)
    with pytest.raises(RuntimeError, match="identity could not be verified"):
        chrome.pinned_chrome_executable(profile)


def test_pinned_missing_binary_never_switches_to_another_install(installed, monkeypatch):
    profile, executable = installed
    chrome.select_chrome_executable(profile)
    executable.unlink()
    replacement = executable.with_name("other.exe")
    replacement.write_bytes(b"replacement")
    monkeypatch.setattr(chrome, "_candidates", lambda: [executable, replacement])
    with pytest.raises(RuntimeError, match="unavailable"):
        chrome.select_chrome_executable(profile)


def test_ordinary_chrome_profile_is_never_opened(installed):
    profile, _ = installed
    chrome.os.environ["LOCALAPPDATA"] = str(profile)
    ordinary = profile / "Google" / "Chrome" / "User Data"
    with pytest.raises(RuntimeError, match="Jarvis-owned"):
        chrome.select_chrome_executable(ordinary)


def test_other_platforms_are_noops(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome, "os", SimpleNamespace(name="posix"))
    assert chrome.find_installed_chrome() is None
    assert chrome.select_chrome_executable(tmp_path) is None
    assert chrome.pinned_chrome_executable(tmp_path) is None
    assert chrome._owned_windows(1) == []


class LoginProcess:
    pid = 123

    def __init__(self):
        self.returncode = None
        self.blocked = False
        self.waited = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waited += 1
        if self.blocked:
            raise subprocess.TimeoutExpired("fixture", timeout)
        self.returncode = 0
        return 0


class LoginWindow:
    def __init__(self):
        self.ready = threading.Event()
        self.ready.set()
        self.parked = self.closed = 0

    def frame(self):
        return {"bytes": b"fixture"}

    def park(self):
        self.parked += 1

    def close(self):
        self.closed += 1


@pytest.fixture
def login(installed, monkeypatch):
    profile, executable = installed
    process, window = LoginProcess(), LoginWindow()
    launches, closures, waits = [], [], []

    def spawn(args, **kwargs):
        launches.append((args, kwargs))
        return process

    async def ready(pid):
        waits.append(pid)

    monkeypatch.setattr(chrome.subprocess, "Popen", spawn)
    monkeypatch.setattr(chrome, "_wait_for_window", ready)
    monkeypatch.setattr(chrome, "_make_native", lambda pid: window)
    monkeypatch.setattr(chrome, "_request_close", closures.append)
    instance = chrome.PlainChrome(profile, str(executable), creationflags=0x08000000)
    return instance, process, window, launches, closures, waits


@pytest.mark.asyncio
async def test_plain_login_uses_owned_profile_and_no_automation_transport(login):
    instance, process, window, launches, _, waits = login
    assert await instance.start() is window
    args, options = launches[0]
    assert args == [
        instance.executable, f"--user-data-dir={instance.profile.resolve()}", "--new-window",
        "--no-first-run", "--disable-background-mode", "chrome://newtab/",
    ]
    assert options["creationflags"] == 0x08000000
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert waits == [process.pid]
    assert window.parked == 1
    await instance.close()


@pytest.mark.asyncio
async def test_graceful_close_waits_then_releases_capture_idempotently(login):
    instance, process, window, _, closures, _ = login
    await instance.start()
    await instance.close()
    await instance.close()
    assert closures == [process.pid]
    assert process.waited == window.closed == 1
    assert instance.process is instance.native is None


@pytest.mark.asyncio
async def test_shutdown_timeout_keeps_ownership_and_forbids_second_launch(login):
    instance, process, window, launches, _, _ = login
    await instance.start()
    process.blocked = True
    with pytest.raises(RuntimeError, match="still closing"):
        await instance.close(timeout=0.01)
    assert instance.process is process and instance.native is window
    assert window.closed == 0
    with pytest.raises(RuntimeError, match="already open"):
        await instance.start()
    assert len(launches) == 1
    process.blocked = False
    await instance.close()
    assert window.closed == 1


@pytest.mark.asyncio
async def test_failed_window_start_retains_process_for_parent_cleanup(login, monkeypatch):
    instance, process, _, _, closures, _ = login

    async def fail(pid):
        raise TimeoutError("fixture")

    monkeypatch.setattr(chrome, "_wait_for_window", fail)
    with pytest.raises(TimeoutError):
        await instance.start()
    assert instance.process is process
    await instance.close()
    assert closures == [process.pid]


@pytest.mark.asyncio
async def test_window_discovery_always_stops_its_listener(monkeypatch):
    lifecycle = []

    class Arrival:
        def __init__(self, pid):
            self.ready = threading.Event()
            self.ready.set()
            self.error = None
            self.thread = SimpleNamespace(start=lambda: lifecycle.append("start"))
            self.events = asyncio.Queue()

        def stop(self):
            lifecycle.append("stop")

    monkeypatch.setattr(chrome, "_WindowArrival", Arrival)
    monkeypatch.setattr(chrome, "_owned_windows", lambda pid: [])
    with pytest.raises(TimeoutError):
        await chrome._wait_for_window(123, timeout=0.01)
    assert lifecycle == ["start", "stop"]


@pytest.mark.asyncio
async def test_startup_hook_is_unregistered_and_thread_joined(monkeypatch):
    from tests.fakes.fake_native_browser import NativeCall, NativeDesktop

    desktop = NativeDesktop()
    desktop.user32.PeekMessageW = NativeCall(lambda *_: 0)
    native = desktop.instance()
    fake_ctypes = native.ctypes
    fake_ctypes.wintypes = native.wintypes
    fake_ctypes.WinDLL = lambda name, **kwargs: (
        desktop.user32 if name == "user32" else SimpleNamespace(
            GetCurrentThreadId=NativeCall(lambda: 789)
        )
    )
    monkeypatch.setattr(chrome, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", native.wintypes)
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(
        CoInitialize=desktop.com_initialize,
        CoUninitialize=desktop.com_uninitialize,
        PumpMessages=lambda: desktop.pump_stop.wait(2),
    ))
    watcher = chrome._WindowArrival(42)
    watcher.thread.start()
    assert await asyncio.to_thread(watcher.ready.wait, 1)
    await asyncio.to_thread(watcher.stop)
    await asyncio.to_thread(watcher.stop)
    assert not watcher.thread.is_alive()
    assert [hook[0] for hook in desktop.hooks] == desktop.unhooks
    assert desktop.co_initialized == desktop.co_uninitialized == 1
    native.close()
