"""Unit tests for :class:`jarvis.memory.wiki.watcher.WikiWatcher`.

These tests drive a real :class:`watchdog.observers.Observer` against a
temporary on-disk vault. The tests are mildly timing-sensitive: each
file event has to make it through the OS-native filesystem watcher and
the per-path debounce window. We compensate by waiting up to a few
seconds for the expected events to arrive on the bus, rather than
relying on tight ``await asyncio.sleep`` calls.

Anti-patterns avoided:
- AP-5: no SQLite or filesystem mocking. Tests use real ``tmp_path``.
- AP-6: tests share a single :class:`EventBus` instance per case.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
import pytest_asyncio

from jarvis.core.bus import EventBus
from jarvis.core.events import WikiPageChanged
from jarvis.memory.wiki.watcher import WikiWatcher

# Plenty of slack — Windows ReadDirectoryChangesW is usually <50 ms but
# we have seen the test runner pause much longer under load.
EVENT_WAIT_S = 5.0


def _make_vault(root: Path) -> Path:
    """Bootstrap an empty vault with the four watched sub-folders."""
    for sub in ("entities", "concepts", "projects", "sessions"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


async def _wait_for_index(
    db_path: Path,
    predicate: Callable[[sqlite3.Connection], bool],
    *,
    wait_s: float = EVENT_WAIT_S,
) -> None:
    """Poll a real FTS database until ``predicate`` observes the change."""
    deadline = asyncio.get_running_loop().time() + wait_s
    while asyncio.get_running_loop().time() < deadline:
        if await asyncio.to_thread(db_path.is_file):
            conn = sqlite3.connect(str(db_path))
            try:
                try:
                    if predicate(conn):
                        return
                except sqlite3.OperationalError:
                    pass
            finally:
                conn.close()
        await asyncio.sleep(0.05)
    pytest.fail("timed out waiting for the wiki FTS index")


async def _collect_events(
    bus: EventBus,
    *,
    count: int,
    wait_s: float = EVENT_WAIT_S,
) -> list[WikiPageChanged]:
    """Wait for ``count`` :class:`WikiPageChanged` events on the bus.

    Returns the received events. Times out into a shorter list if fewer
    than ``count`` arrive — the assertions in the test cases inspect the
    list length explicitly.
    """
    received: list[WikiPageChanged] = []
    fut: asyncio.Future[None] = asyncio.get_event_loop().create_future()

    async def _on_event(ev: WikiPageChanged) -> None:
        received.append(ev)
        if len(received) >= count and not fut.done():
            fut.set_result(None)

    bus.subscribe(WikiPageChanged, _on_event)
    try:
        try:
            await asyncio.wait_for(fut, timeout=wait_s)
        except TimeoutError:
            pass
    finally:
        bus.unsubscribe(WikiPageChanged, _on_event)
    return received


async def _wait_quiescent(
    bus: EventBus,
    *,
    extra_window_s: float = 0.6,
) -> list[WikiPageChanged]:
    """Wait until no further events have been seen for ``extra_window_s``.

    Used to verify that debounce produced *exactly one* event for a
    burst — we collect everything that arrives and assert on the length.
    """
    received: list[WikiPageChanged] = []

    async def _on_event(ev: WikiPageChanged) -> None:
        received.append(ev)

    bus.subscribe(WikiPageChanged, _on_event)
    try:
        deadline = asyncio.get_event_loop().time() + EVENT_WAIT_S
        last_count = -1
        stable_until = 0.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)
            now = asyncio.get_event_loop().time()
            if len(received) != last_count:
                last_count = len(received)
                stable_until = now + extra_window_s
            elif last_count > 0 and now >= stable_until:
                break
        # If nothing arrived at all, still wait the extra window so a
        # late event surfaces.
        if not received:
            await asyncio.sleep(extra_window_s)
    finally:
        bus.unsubscribe(WikiPageChanged, _on_event)
    return received


@pytest_asyncio.fixture
async def watcher_stack(tmp_path: Path):
    """Real vault on tmp_path + real EventBus + started WikiWatcher.

    Uses a short 100 ms debounce so the tests stay fast — the production
    default is 500 ms.
    """
    vault = _make_vault(tmp_path / "vault")
    bus = EventBus()
    watcher = WikiWatcher(vault_root=vault, bus=bus, debounce_ms=100)
    started = watcher.start()
    assert started, "watcher.start() should succeed on tmp_path vault"
    # Give the observer thread a moment to settle before the first
    # filesystem event so we don't race the very first poll cycle.
    await asyncio.sleep(0.1)
    try:
        yield watcher, bus, vault
    finally:
        await watcher.shutdown()


@pytest.mark.asyncio
async def test_create_md_file_emits_created_event(watcher_stack):
    """Writing a fresh .md file produces exactly one created event."""
    _, bus, vault = watcher_stack

    target = vault / "entities" / "ruben.md"
    target.write_text("# Ruben\n\nFresh page.\n", encoding="utf-8")

    events = await _wait_quiescent(bus)
    assert len(events) == 1, f"expected one event, got {events!r}"
    event = events[0]
    assert event.slug == "ruben"
    assert event.path == "entities/ruben.md"
    assert event.kind in ("created", "modified")
    # Path is vault-relative POSIX — no backslashes leak through on
    # Windows.
    assert "\\" not in event.path


@pytest.mark.asyncio
async def test_modify_md_file_emits_modified_event(watcher_stack):
    """Re-writing an existing page produces one modified event."""
    _, bus, vault = watcher_stack
    target = vault / "entities" / "harald.md"
    target.write_text("# Harald\n", encoding="utf-8")
    # Drain the creation event.
    await _wait_quiescent(bus)

    target.write_text("# Harald\n\nUpdated.\n", encoding="utf-8")
    events = await _wait_quiescent(bus)
    assert len(events) == 1, f"expected one event, got {events!r}"
    assert events[0].slug == "harald"
    assert events[0].kind in ("modified", "created")


@pytest.mark.asyncio
async def test_delete_md_file_emits_deleted_event(watcher_stack):
    """Deleting an existing page produces one deleted event."""
    _, bus, vault = watcher_stack
    target = vault / "projects" / "pixel-art.md"
    target.write_text("# Pixel art editor\n", encoding="utf-8")
    await _wait_quiescent(bus)

    target.unlink()
    events = await _wait_quiescent(bus)
    assert len(events) == 1, f"expected one event, got {events!r}"
    assert events[0].slug == "pixel-art"
    assert events[0].path == "projects/pixel-art.md"
    assert events[0].kind == "deleted"


@pytest.mark.asyncio
async def test_burst_modifications_are_debounced(watcher_stack):
    """Five quick modifications collapse to one event."""
    _, bus, vault = watcher_stack
    target = vault / "concepts" / "karpathy.md"
    target.write_text("# Karpathy pattern\n", encoding="utf-8")
    await _wait_quiescent(bus)

    # Hammer the file: 5 modifications inside the 100 ms debounce
    # window. Watchdog emits one or more events per write; debounce
    # must collapse them down to a single bus event.
    for i in range(5):
        target.write_text(f"# Karpathy pattern v{i}\n", encoding="utf-8")
        await asyncio.sleep(0.01)
    events = await _wait_quiescent(bus)
    assert len(events) == 1, f"expected one debounced event, got {events!r}"
    assert events[0].slug == "karpathy"


@pytest.mark.asyncio
async def test_manual_create_modify_delete_maintains_fts(tmp_path: Path):
    """Manual vault edits update and remove their FTS row without restart."""
    vault = _make_vault(tmp_path / "vault")
    db_path = tmp_path / "data" / "jarvis.db"
    bus = EventBus()
    watcher = WikiWatcher(
        vault_root=vault,
        bus=bus,
        debounce_ms=50,
        db_path=db_path,
    )
    assert watcher.start()
    await asyncio.sleep(0.1)
    target = vault / "entities" / "manual.md"
    try:
        target.write_text("# Manual\n\nFirst version.\n", encoding="utf-8")
        await _wait_for_index(
            db_path,
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM wiki_fts WHERE path = ? AND body LIKE ?",
                ("entities/manual.md", "%First version%"),
            ).fetchone()[0]
            == 1,
        )

        target.write_text("# Manual\n\nSecond version.\n", encoding="utf-8")
        await _wait_for_index(
            db_path,
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM wiki_fts WHERE path = ? AND body LIKE ?",
                ("entities/manual.md", "%Second version%"),
            ).fetchone()[0]
            == 1,
        )

        # Atomic-save editors may report an intermediate deletion even though
        # the replacement file exists by the end of the debounce window.
        target.write_text("# Manual\n\nAtomic replacement.\n", encoding="utf-8")
        watcher._handle_event(str(target), "deleted")  # noqa: SLF001
        await _wait_for_index(
            db_path,
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM wiki_fts WHERE path = ? AND body LIKE ?",
                ("entities/manual.md", "%Atomic replacement%"),
            ).fetchone()[0]
            == 1,
        )

        target.unlink()
        await _wait_for_index(
            db_path,
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM wiki_fts WHERE path = ?",
                ("entities/manual.md",),
            ).fetchone()[0]
            == 0,
        )
    finally:
        await watcher.shutdown()


@pytest.mark.asyncio
async def test_visible_root_page_is_indexed_without_ui_event(tmp_path: Path):
    """Full-index pages outside content folders stay searchable but stay quiet."""
    vault = _make_vault(tmp_path / "vault")
    db_path = tmp_path / "data" / "jarvis.db"
    bus = EventBus()
    watcher = WikiWatcher(
        vault_root=vault,
        bus=bus,
        debounce_ms=50,
        db_path=db_path,
    )
    received: list[WikiPageChanged] = []

    async def _on_event(event: WikiPageChanged) -> None:
        received.append(event)

    bus.subscribe(WikiPageChanged, _on_event)
    assert watcher.start()
    await asyncio.sleep(0.1)
    try:
        (vault / "schema.md").write_text(
            "# Schema\n\nSearchable root page.\n",
            encoding="utf-8",
        )
        await _wait_for_index(
            db_path,
            lambda conn: conn.execute(
                "SELECT COUNT(*) FROM wiki_fts WHERE path = ?",
                ("schema.md",),
            ).fetchone()[0]
            == 1,
        )
        await asyncio.sleep(0.2)
        assert received == []
    finally:
        bus.unsubscribe(WikiPageChanged, _on_event)
        await watcher.shutdown()


@pytest.mark.asyncio
async def test_non_markdown_files_are_ignored(watcher_stack):
    """Writing a .txt file produces no event."""
    _, bus, vault = watcher_stack
    target = vault / "entities" / "scratch.txt"
    target.write_text("ignore me", encoding="utf-8")
    events = await _wait_quiescent(bus)
    assert events == []


@pytest.mark.asyncio
async def test_files_outside_watched_subdirs_are_ignored(tmp_path: Path):
    """Files in unwatched folders (e.g. ``_archive``) do not emit events."""
    vault = _make_vault(tmp_path / "vault")
    archive = vault / "_archive"
    archive.mkdir(parents=True)
    bus = EventBus()
    watcher = WikiWatcher(vault_root=vault, bus=bus, debounce_ms=100)
    assert watcher.start()
    try:
        await asyncio.sleep(0.1)
        # File inside an unwatched folder.
        (archive / "old.md").write_text("# old\n", encoding="utf-8")
        events = await _wait_quiescent(bus)
        assert events == [], (
            f"events outside watched subdirs must be ignored, got {events!r}"
        )
    finally:
        await watcher.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_timers(tmp_path: Path):
    """Shutdown stops any debounce timers that have not fired yet."""
    vault = _make_vault(tmp_path / "vault")
    bus = EventBus()
    # Long debounce — guaranteed to still have a timer pending when we
    # call shutdown.
    watcher = WikiWatcher(vault_root=vault, bus=bus, debounce_ms=2000)
    assert watcher.start()
    await asyncio.sleep(0.05)

    (vault / "sessions" / "today.md").write_text("# today\n", encoding="utf-8")
    # Don't wait — just shut down. The timer should be cancelled.
    await watcher.shutdown()

    # No events should fire after shutdown returns. We can verify by
    # collecting for a short window: zero events expected.
    received: list[WikiPageChanged] = []

    async def _on_event(ev: WikiPageChanged) -> None:
        received.append(ev)

    bus.subscribe(WikiPageChanged, _on_event)
    await asyncio.sleep(2.5)
    bus.unsubscribe(WikiPageChanged, _on_event)
    assert received == [], (
        f"no events expected after shutdown, got {received!r}"
    )


def test_start_returns_false_when_vault_missing(tmp_path: Path):
    """A non-existent vault root makes start() bail out cleanly."""
    missing = tmp_path / "does-not-exist"
    bus = EventBus()
    watcher = WikiWatcher(vault_root=missing, bus=bus)
    assert watcher.start() is False
