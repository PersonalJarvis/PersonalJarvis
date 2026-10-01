"""Busy conversation storage must not freeze the desktop's serving loop."""
from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from jarvis.society.conversation import ConversationArchive
from jarvis.society.runtime import SocietyRuntime


def test_deferred_archive_does_not_touch_storage_until_used(tmp_path):
    path = tmp_path / "unused" / "archive.db"
    archive = ConversationArchive(path, defer_open=True)
    assert not path.parent.exists()
    archive.open()
    try:
        archive.ingest(
            "agent-a", [{"seq": 1, "kind": "user_message", "payload": {"text": "hello"}}]
        )
        assert archive.search("agent-a", "hello")[0]["seq"] == 1
        assert archive.search("agent-b", "hello") == []
    finally:
        archive.close()


def test_closed_deferred_archive_cannot_create_storage(tmp_path):
    path = tmp_path / "unused" / "archive.db"
    archive = ConversationArchive(path, defer_open=True)
    archive.close()
    archive.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        archive.open()
    assert not path.parent.exists()


def test_failed_initialization_releases_connection_and_can_retry(tmp_path, monkeypatch):
    archive = ConversationArchive(tmp_path / "archive.db", defer_open=True)
    initialize = archive._initialize_schema
    attempts = 0

    def initialize_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("test storage unavailable")
        initialize()

    monkeypatch.setattr(archive, "_initialize_schema", initialize_once)
    with pytest.raises(sqlite3.OperationalError):
        archive.open()
    assert archive._connection is None
    archive.open()
    try:
        assert archive.read("agent-a") == []
    finally:
        archive.close()


async def test_slow_archive_start_and_cancelled_cleanup_keep_loop_responsive(tmp_path, monkeypatch):
    loop_thread = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    initialize = ConversationArchive._initialize_schema

    def slow_schema(self):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(10), "test must release the database initializer"
        initialize(self)

    monkeypatch.setattr(ConversationArchive, "_initialize_schema", slow_schema)
    runtime = SocietyRuntime(tmp_path)
    assert not (tmp_path / "society-conversations.db").exists()
    startup = asyncio.create_task(runtime.ensure_started())
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        heartbeat = asyncio.Event()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), 1)
        startup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup
        closing = asyncio.create_task(runtime.close())
        heartbeat.clear()
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), 1)
    finally:
        release.set()
        await asyncio.gather(startup, return_exceptions=True)
        if closing is not None:
            await closing
        else:
            await runtime.close()
    assert runtime.conversations._connection is None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        runtime.conversations.open()
