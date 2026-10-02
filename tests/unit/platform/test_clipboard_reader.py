"""A stalled clipboard owner must not hold up dictation or leak its text."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import clipboard
from jarvis.platform import clipboard_reader as reader


@pytest.mark.parametrize("value", ["", "line one\nUnicode: café 🦊", None])
def test_reads_preserve_empty_unicode_and_unavailable(monkeypatch, value):
    def run(command, **kwargs):
        assert kwargs["timeout"] == reader.READ_TIMEOUT_S
        assert kwargs["creationflags"] == NO_WINDOW_CREATIONFLAGS
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["stderr"] == subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(value))

    monkeypatch.setattr(reader.subprocess, "run", run)
    assert clipboard._read_windows() == value


def test_timeout_releases_reader_before_the_next_request(monkeypatch, caplog):
    calls = 0

    def run(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(command, 1, output="private clipboard text")
        return subprocess.CompletedProcess(command, 0, stdout='"next clipboard"')

    monkeypatch.setattr(reader.subprocess, "run", run)
    assert reader.read_with_deadline() is None
    assert reader.read_with_deadline() == "next clipboard"
    assert "private clipboard text" not in caplog.text


def test_concurrent_read_is_not_queued(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("a second clipboard reader was started")

    monkeypatch.setattr(reader.subprocess, "run", unexpected)
    with reader._READ_LOCK:
        assert reader.read_with_deadline() is None


def test_invalid_response_does_not_log_contents(monkeypatch, caplog):
    monkeypatch.setattr(reader.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, stdout="private clipboard text"))
    assert reader.read_with_deadline() is None
    assert "private clipboard text" not in caplog.text


def test_frozen_reader_reenters_private_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert reader._reader_command() == [sys.executable, "--clipboard-read"]


def test_stalled_reader_process_is_reaped(monkeypatch, tmp_path):
    """Exercise the real kill/wait path without accessing the user's clipboard."""
    pid_file = tmp_path / "reader.pid"
    script = (
        "import os,sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    )
    monkeypatch.setattr(reader, "_reader_command", lambda:
                        [sys.executable, "-c", script, str(pid_file)])
    started = time.monotonic()
    assert reader.read_with_deadline() is None
    assert time.monotonic() - started < 5
    assert pid_file.exists(), "the helper must have started for this test to prove cleanup"
    assert not psutil.pid_exists(int(pid_file.read_text()))


@pytest.mark.skipif(sys.platform != "win32", reason="windowed Windows pipe contract")
def test_windowed_helper_writes_to_inherited_pipe():
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pytest.skip("windowed interpreter unavailable")
    script = (
        "from jarvis.platform.clipboard_reader import _write_response; "
        "_write_response('sample\\nUnicode: café 🦊')"
    )
    result = subprocess.run(
        [str(pythonw), "-c", script],
        stdin=subprocess.DEVNULL, capture_output=True,
        encoding="utf-8", timeout=5, creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == "sample\nUnicode: café 🦊"


def test_private_entrypoint_does_not_boot_desktop():
    script = (
        "import sys,runpy; import jarvis.platform.clipboard as c; "
        "c._read_windows_native=lambda: 'sample'; "
        "sys.argv=['jarvis','--clipboard-read']; "
        "runpy.run_module('jarvis',run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        stdin=subprocess.DEVNULL, capture_output=True,
        encoding="utf-8", timeout=5, creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == "sample"
