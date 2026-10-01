"""Background setup owns its subprocess until cancellation has actually drained."""

import asyncio
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from jarvis.society.browser import install
from tests.fakes.fake_society_shutdown import society_shutdown_server


@pytest.fixture
def running_install(monkeypatch, tmp_path):
    command = (
        "import os, pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    reached = []

    def ensure(data_dir, **_kwargs):
        install._run(
            [sys.executable, "-c", command, str(Path(data_dir) / "child.pid")],
            env=install.worker_env(data_dir),
            timeout=60,
        )
        reached.append("must not start another command")

    monkeypatch.setattr(install, "ensure_installed", ensure)
    assert install.start_install(tmp_path)[0]
    marker = tmp_path / "child.pid"
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists(), "the real installer command did not start"
    process = psutil.Process(int(marker.read_text()))
    try:
        yield tmp_path, process, reached
    finally:
        install.request_stop(tmp_path)
        install.wait_stopped(tmp_path)
        install._reset_for_tests()


def assert_exited(process):
    deadline = time.monotonic() + 1
    while process.is_running():
        try:
            if process.status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        assert time.monotonic() < deadline, "an owned installer process survived shutdown"
        time.sleep(0.01)


def test_cancellation_reaps_command_and_fences_late_requests(running_install):
    data_dir, process, reached = running_install
    install.request_stop(data_dir)
    assert not install.start_install(data_dir, repair=True)[0]
    install.wait_stopped(data_dir)
    assert not install.snapshot(data_dir)["running"]
    assert not reached
    assert_exited(process)
    install.resume_installation(data_dir)


async def test_server_shutdown_joins_its_background_installer(running_install):
    data_dir, process, reached = running_install
    server, cleaned = society_shutdown_server(None)
    server._browser_install_data_dir = data_dir
    await asyncio.wait_for(server.stop(), 5)
    assert server._shutdown_complete
    assert not install.snapshot(data_dir)["running"]
    assert not reached and "chat" in cleaned
    assert_exited(process)


def test_unfinished_installer_cannot_be_reopened(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()

    def ensure(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(install, "ensure_installed", ensure)
    assert install.start_install(tmp_path)[0]
    assert entered.wait(2)
    try:
        install.request_stop(tmp_path)
        with pytest.raises(TimeoutError, match="shutdown is incomplete"):
            install.wait_stopped(tmp_path, timeout=0.01)
        with pytest.raises(RuntimeError, match="shutdown is incomplete"):
            install.resume_installation(tmp_path)
        assert not install.start_install(tmp_path, repair=True)[0]
    finally:
        release.set()
        install.wait_stopped(tmp_path)
        install._reset_for_tests()
