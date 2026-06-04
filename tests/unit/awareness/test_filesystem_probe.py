"""Phase A5 Slice B — FileSystemProbe Tests.

Mit echtem watchdog gegen tmp_path (Fakes statt Mocks). Real-FS-Events
mit kurzen Sleep-Window fuer Watchdog-Latency.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from jarvis.awareness.probes.filesystem import (
    _MAX_WATCHED_ROOTS,
    FileSystemProbe,
)
from jarvis.core.bus import EventBus
from jarvis.core.events import FileSaved

# --- Helper ---

async def _wait_for_event(
    events: list[FileSaved],
    n: int = 1,
    timeout: float = 2.0,  # noqa: ASYNC109 - intentional Polling-Window pro Test
) -> None:
    """Pollt bis events.length >= n oder timeout.

    ASYNC109: timeout-Parameter ist hier intentional — Test-Helper braucht
    konfigurierbares Polling-Window pro Testfall, nicht asyncio.timeout-Cancel.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(events) >= n:
            return
        await asyncio.sleep(0.05)


# --- Tests ---

async def test_probe_with_none_cwd_returns_none() -> None:
    bus = EventBus()
    p = FileSystemProbe(bus=bus)
    await p.start()
    try:
        result = await p.probe(cwd=None)
        assert result == {"open_file_hint": None}
    finally:
        await p.stop()


async def test_probe_with_unwatched_cwd_returns_none(tmp_path: Path) -> None:
    bus = EventBus()
    p = FileSystemProbe(bus=bus)
    await p.start()
    try:
        result = await p.probe(cwd=str(tmp_path))
        assert result == {"open_file_hint": None}
    finally:
        await p.stop()


async def test_watch_then_save_emits_filesaved_event(tmp_path: Path) -> None:
    bus = EventBus()
    received: list[FileSaved] = []

    async def collect(ev: FileSaved) -> None:
        received.append(ev)
    bus.subscribe(FileSaved, collect)

    p = FileSystemProbe(bus=bus)
    await p.start()
    try:
        ok = p.watch(str(tmp_path))
        assert ok is True
        # File-write triggert event
        await asyncio.sleep(0.1)    # watchdog-Init-Window
        (tmp_path / "test.py").write_text("print('hello')")
        await _wait_for_event(received, n=1, timeout=3.0)
        assert len(received) >= 1
        assert any("test.py" in ev.path for ev in received)
    finally:
        await p.stop()


async def test_debounce_collapses_rapid_saves(tmp_path: Path) -> None:
    bus = EventBus()
    received: list[FileSaved] = []

    async def collect(ev: FileSaved) -> None:
        received.append(ev)
    bus.subscribe(FileSaved, collect)

    p = FileSystemProbe(bus=bus)
    await p.start()
    try:
        p.watch(str(tmp_path))
        await asyncio.sleep(0.1)
        f = tmp_path / "rapid.py"
        # 5 rapid writes innerhalb 100ms — debounce (200ms) sollte alle bis auf 1 droppen
        for _ in range(5):
            f.write_text("x")
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.5)    # warten bis debounce-window abgelaufen
        # Erwartung: max 2 events (1 fuer ersten save + evtl. 1 nach debounce-window)
        rapid_events = [e for e in received if "rapid.py" in e.path]
        assert len(rapid_events) <= 2
    finally:
        await p.stop()


async def test_blacklist_skips_git_directory(tmp_path: Path) -> None:
    bus = EventBus()
    received: list[FileSaved] = []

    async def collect(ev: FileSaved) -> None:
        received.append(ev)
    bus.subscribe(FileSaved, collect)

    p = FileSystemProbe(bus=bus)
    await p.start()
    try:
        p.watch(str(tmp_path))
        await asyncio.sleep(0.1)
        (tmp_path / ".git").mkdir(exist_ok=True)
        (tmp_path / ".git" / "ignored.txt").write_text("x")
        (tmp_path / "real_file.py").write_text("real")
        await _wait_for_event(received, n=1, timeout=2.0)
        # Nur real_file.py, NICHT ignored.txt
        paths = [e.path for e in received]
        assert any("real_file.py" in p for p in paths)
        assert not any(".git" in p and "ignored.txt" in p for p in paths)
    finally:
        await p.stop()


async def test_unwatch_stops_events(tmp_path: Path) -> None:
    bus = EventBus()
    received: list[FileSaved] = []

    async def collect(ev: FileSaved) -> None:
        received.append(ev)
    bus.subscribe(FileSaved, collect)

    p = FileSystemProbe(bus=bus)
    await p.start()
    try:
        p.watch(str(tmp_path))
        await asyncio.sleep(0.1)
        p.unwatch(str(tmp_path))
        await asyncio.sleep(0.1)
        (tmp_path / "should_not_emit.py").write_text("x")
        await asyncio.sleep(0.5)
        # Keine events fuer should_not_emit.py
        paths = [e.path for e in received]
        assert not any("should_not_emit.py" in p for p in paths)
    finally:
        await p.stop()


async def test_max_watched_roots_cap(tmp_path: Path) -> None:
    bus = EventBus()
    p = FileSystemProbe(bus=bus)
    await p.start()
    try:
        # Erstelle MAX+1 directories und versuche alle zu watchen
        for i in range(_MAX_WATCHED_ROOTS):
            d = tmp_path / f"root_{i}"
            d.mkdir()
            assert p.watch(str(d)) is True
        # Cap+1 muss False returnen
        d_extra = tmp_path / "root_extra"
        d_extra.mkdir()
        assert p.watch(str(d_extra)) is False
    finally:
        await p.stop()


async def test_probe_returns_latest_save_in_root(tmp_path: Path) -> None:
    bus = EventBus()
    p = FileSystemProbe(bus=bus)
    await p.start()
    try:
        p.watch(str(tmp_path))
        await asyncio.sleep(0.1)
        (tmp_path / "first.py").write_text("a")
        await asyncio.sleep(0.3)
        (tmp_path / "second.py").write_text("b")
        await asyncio.sleep(0.3)
        result = await p.probe(cwd=str(tmp_path))
        assert result["open_file_hint"] is not None
        assert "second.py" in result["open_file_hint"]
    finally:
        await p.stop()


async def test_stop_idempotent(tmp_path: Path) -> None:
    bus = EventBus()
    p = FileSystemProbe(bus=bus)
    await p.start()
    await p.stop()
    await p.stop()    # second call must not raise
