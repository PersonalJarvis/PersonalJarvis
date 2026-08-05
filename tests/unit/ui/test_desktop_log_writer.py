"""Guards for the desktop log writer.

Incident this pins (2026-08-03): the loguru file sink wrote inline on the
caller's thread. Under machine load one write blocked the backend asyncio
loop for 24.4 seconds, which killed the listening socket and left the window
with no backend. The writer must therefore never block whoever logs, and must
stay honest about records it could not persist.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from jarvis.ui.desktop_app import _AsyncLogWriter


class _BlockingHandle:
    """A file handle whose write() parks until the test releases it."""

    def __init__(self, release: threading.Event, entered: threading.Event) -> None:
        self._release = release
        self._entered = entered
        self.chunks: list[str] = []

    def write(self, payload: str) -> int:
        self._entered.set()
        self._release.wait(timeout=10.0)
        self.chunks.append(payload)
        return len(payload)

    def flush(self) -> None:
        return None

    def tell(self) -> int:
        return sum(len(chunk) for chunk in self.chunks)

    def close(self) -> None:
        return None


def test_logging_never_blocks_the_caller(tmp_path: Path) -> None:
    """A wedged disk must not stall the thread that emits the record."""
    release = threading.Event()
    entered = threading.Event()
    handle = _BlockingHandle(release, entered)

    writer = _AsyncLogWriter(tmp_path / "jarvis_desktop.log", max_queue=5000)
    writer._ensure_handle = lambda: handle  # type: ignore[method-assign]
    try:
        writer("first record\n")
        assert entered.wait(timeout=5.0), "writer thread never reached write()"

        # The writer thread is now parked inside write(). Emitting while the
        # disk is wedged is exactly the situation that stalled the event loop:
        # every one of these calls must return without waiting. If the sink
        # were synchronous this loop would hang until the 10 s handle timeout.
        for index in range(500):
            writer(f"record {index}\n")
    finally:
        release.set()
        writer.stop()


def test_records_reach_the_file(tmp_path: Path) -> None:
    log_path = tmp_path / "jarvis_desktop.log"
    writer = _AsyncLogWriter(log_path)
    writer("hello\n")
    writer("world\n")
    writer.stop()

    content = log_path.read_text(encoding="utf-8")
    assert "hello" in content
    assert "world" in content


def test_rotation_and_retention(tmp_path: Path) -> None:
    """Oversized logs rotate, and only `retention` old files survive."""
    log_path = tmp_path / "jarvis_desktop.log"
    writer = _AsyncLogWriter(log_path, rotation_bytes=200, retention=2)
    try:
        for index in range(60):
            writer(f"{'x' * 80} line {index}\n")
    finally:
        writer.stop()

    rotated = sorted(tmp_path.glob("jarvis_desktop.*.log"))
    assert rotated, "nothing rotated despite exceeding the size limit"
    assert len(rotated) <= 2, f"retention ignored: {[p.name for p in rotated]}"


def test_overflow_is_reported_not_silent(tmp_path: Path) -> None:
    """Dropped records must be counted and surfaced once the disk recovers."""
    release = threading.Event()
    entered = threading.Event()
    handle = _BlockingHandle(release, entered)

    log_path = tmp_path / "jarvis_desktop.log"
    writer = _AsyncLogWriter(log_path, max_queue=2)
    writer._ensure_handle = lambda: handle  # type: ignore[method-assign]
    try:
        writer("first\n")
        assert entered.wait(timeout=5.0)
        for index in range(50):
            writer(f"overflow {index}\n")
        assert writer._dropped > 0, "queue never overflowed; test is not exercising it"
    finally:
        release.set()
        writer.stop()

    # The drop notice is emitted on the next successful batch, so it lands in
    # the fake handle rather than on disk.
    assert any("dropped" in chunk for chunk in handle.chunks), (
        "records were dropped without any trace in the log"
    )


@pytest.mark.parametrize("payload", ["ascii\n", "café — naïve ✓ 日本語\n"])
def test_unicode_survives(tmp_path: Path, payload: str) -> None:
    log_path = tmp_path / "jarvis_desktop.log"
    writer = _AsyncLogWriter(log_path)
    writer(payload)
    writer.stop()
    assert payload.strip() in log_path.read_text(encoding="utf-8")
