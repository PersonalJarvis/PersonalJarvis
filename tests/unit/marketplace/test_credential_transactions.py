"""Synthetic credentials only; storage races must not destroy a live grant."""

import asyncio
import concurrent.futures
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.marketplace.credential_lock import refresh_lock
from jarvis.marketplace.refresh_scheduler import refresh_plugin_token
from jarvis.marketplace.token_store import ChunkedBackend, InMemoryBackend, Tokens, TokenStore


class InterruptedWrite(BaseException):
    """Simulate process death, bypassing ordinary exception rollback."""


class ObservingBackend(InMemoryBackend):
    observer = None

    def set(self, key, value):
        super().set(key, value)
        if self.observer and key.endswith("_0"):
            self.observer()


def test_reader_never_observes_a_mix_of_old_and_new_chunks():
    primitive = ObservingBackend()
    backend = ChunkedBackend(primitive, chunk_size=20)
    old, new = "A" * 55, "B" * 55
    backend.set("synthetic", old)
    observed = []
    primitive.observer = lambda: observed.append(backend.get("synthetic"))
    backend.set("synthetic", new)
    assert observed == [old]
    assert backend.get("synthetic") == new


def test_process_death_mid_write_preserves_old_token_and_removable_fragments():
    primitive = ObservingBackend()
    backend = ChunkedBackend(primitive, chunk_size=20)
    old = "A" * 55
    backend.set("synthetic", old)

    def die():
        raise InterruptedWrite()

    primitive.observer = die
    with pytest.raises(InterruptedWrite):
        backend.set("synthetic", "B" * 55)
    restarted = ChunkedBackend(primitive, chunk_size=20)
    assert restarted.get("synthetic") == old
    primitive.observer = None
    restarted.delete("synthetic")
    assert primitive._store == {}


def test_legacy_chunked_tokens_migrate_without_losing_the_grant():
    primitive = InMemoryBackend()
    primitive.set("synthetic", "\x00JCHUNKS\x002")
    primitive.set("synthetic__0", "A" * 20)
    primitive.set("synthetic__1", "A" * 15)
    backend = ChunkedBackend(primitive, chunk_size=20)
    assert backend.get("synthetic") == "A" * 35
    backend.set("synthetic", "B" * 40)
    assert backend.get("synthetic") == "B" * 40
    assert primitive.get("synthetic__0") is None
    backend.delete("synthetic")
    assert primitive._store == {}


def test_compare_and_save_never_overwrites_a_new_login():
    store = TokenStore(InMemoryBackend())
    old, new = Tokens("old"), Tokens("new")
    store.save("synthetic", new)
    assert not store.compare_and_save("synthetic", old, Tokens("refreshed-old"))
    assert store.load("synthetic") == new


def test_refresh_single_flight_spans_event_loops_and_threads():
    store = TokenStore(InMemoryBackend())
    store.save("synthetic", Tokens("old", "refresh", datetime.now(UTC) - timedelta(seconds=1)))
    old_access = "old"
    calls = []
    barrier = threading.Barrier(2)

    class Handler:
        async def refresh(self, tokens):
            calls.append(1)
            await asyncio.sleep(0.1)
            return Tokens("new", "rotated", datetime.now(UTC) + timedelta(hours=1))

    def run():
        barrier.wait(timeout=3)
        return asyncio.run(
            refresh_plugin_token(
                "synthetic",
                store,
                lambda _: Handler(),
                force=True,
                observed_access_token=old_access,
            )
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: run(), range(2)))
    assert all(outcome.usable for outcome in outcomes)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_refresh_lock_excludes_another_process_and_releases(tmp_path):
    child = """
import asyncio, sys
from pathlib import Path
from jarvis.marketplace.credential_lock import refresh_lock
async def main():
    try:
        lease = refresh_lock(
            'synthetic', shared=True, directory=Path(sys.argv[1]), wait_seconds=0.1,
        )
        async with lease:
            print('ACQUIRED')
    except TimeoutError:
        print('BUSY')
asyncio.run(main())
"""

    def probe():
        return subprocess.run(
            [sys.executable, "-c", child, str(tmp_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=True,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        ).stdout.strip()

    async with refresh_lock("synthetic", shared=True, directory=tmp_path):
        assert await asyncio.to_thread(probe) == "BUSY"
    assert await asyncio.to_thread(probe) == "ACQUIRED"


@pytest.mark.asyncio
async def test_cancelling_a_waiter_does_not_leak_its_lock():
    async with refresh_lock("synthetic-cancel"):

        async def wait():
            async with refresh_lock("synthetic-cancel"):
                pytest.fail("The held lock must exclude the waiter")

        task = asyncio.create_task(wait())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with refresh_lock("synthetic-cancel", wait_seconds=0.1):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_rejects", [False, True])
async def test_reconnect_between_reload_and_commit_remains_usable(provider_rejects):
    new_login = Tokens("user-new-login", "user-new-refresh")

    class ReconnectingStore(TokenStore):
        def compare_and_save(self, plugin_id, expected, updated):
            self.save(plugin_id, new_login)
            return False

    store = ReconnectingStore(InMemoryBackend())
    store.save("synthetic", Tokens("old", "old-refresh"))

    class Handler:
        async def refresh(self, tokens):
            if provider_rejects:
                raise RuntimeError("invalid_grant")
            return Tokens("provider-new", "provider-rotated")

    outcome = await refresh_plugin_token("synthetic", store, lambda _: Handler(), force=True)
    assert outcome.usable
    assert outcome.access_changed
    assert store.load("synthetic") == new_login


@pytest.mark.asyncio
async def test_new_login_during_rotated_save_backoff_is_never_overwritten():
    failed = asyncio.Event()

    class FlakyStore(TokenStore):
        def save(self, plugin_id, tokens):
            if tokens.access == "provider-new":
                failed.set()
                raise RuntimeError("synthetic write failure")
            super().save(plugin_id, tokens)

    store = FlakyStore(InMemoryBackend())
    store.save("synthetic", Tokens("old", "old-refresh"))

    class Handler:
        async def refresh(self, tokens):
            return Tokens("provider-new", "provider-rotated")

    task = asyncio.create_task(
        refresh_plugin_token(
            "synthetic",
            store,
            lambda _: Handler(),
            force=True,
        )
    )
    await asyncio.wait_for(failed.wait(), timeout=2)
    new_login = Tokens("user-new-login", "user-new-refresh")
    store.save("synthetic", new_login)
    outcome = await task
    assert outcome.usable
    assert store.load("synthetic") == new_login



def test_busy_storage_preserves_the_existing_status_error_contract(monkeypatch, tmp_path):
    import filelock

    from jarvis.marketplace.credential_lock import storage_lock

    class Busy:
        def __init__(self, path):
            self.path = path

        def acquire(self, timeout):
            raise filelock.Timeout(str(self.path))

    monkeypatch.setattr(filelock, "FileLock", Busy)
    with pytest.raises(RuntimeError, match="Plugin credential storage is busy"):
        with storage_lock(shared=True, directory=tmp_path):
            pytest.fail("A busy store must not be read")
