"""End-to-end integration test for the B3 wiki live-reload pipeline.

Wires a real :class:`WikiWatcher` onto a real :class:`EventBus`, hangs
the WS endpoint off the same bus via a FastAPI app, then writes a
markdown file to the tmp vault and asserts the WS client receives the
expected ``page_changed`` frame.

This test is the closest we can get to the real production path without
booting the full Jarvis desktop app — every other moving part is real.
"""
from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.memory.wiki.watcher import WikiWatcher
from jarvis.ui.web.wiki_ws import router as wiki_ws_router


def _make_vault(root: Path) -> Path:
    for sub in ("entities", "concepts", "projects", "sessions"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def live_stack(tmp_path: Path) -> Iterator[tuple[TestClient, Path, EventBus]]:
    """Build the watcher + bus + WS app on a single shared loop.

    The TestClient runs FastAPI on its own background loop. The watcher
    needs to publish onto that same loop's bus subscribers, so we
    construct the bus inside the fixture and pass it to the watcher,
    then start the watcher only after the TestClient has opened a
    session (so the loop is alive).
    """
    vault = _make_vault(tmp_path / "vault")
    bus = EventBus()
    watcher_holder: dict[str, WikiWatcher] = {}
    started_event = threading.Event()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        watcher = WikiWatcher(vault_root=vault, bus=bus, debounce_ms=100)
        watcher.start()
        watcher_holder["w"] = watcher
        started_event.set()
        try:
            yield
        finally:
            await watcher.shutdown()

    app = FastAPI(lifespan=_lifespan)
    app.state.bus = bus
    app.include_router(wiki_ws_router)

    with TestClient(app) as client:
        assert started_event.wait(timeout=5.0)
        yield client, vault, bus


def test_write_file_then_ws_receives_event(live_stack):
    """End-to-end: write entities/test.md → /api/wiki/live forwards it."""
    client, vault, _bus = live_stack
    with client.websocket_connect("/api/wiki/live") as ws:
        target = vault / "entities" / "test.md"
        target.write_text(
            "# Test page\n\nLive reload smoke test.\n",
            encoding="utf-8",
        )
        # The watcher debounces 100 ms; on Windows the native event
        # arrives in <50 ms. We rely on the TestClient's blocking
        # receive to wait for the JSON frame.
        msg = ws.receive_json()
        assert msg["type"] == "page_changed"
        assert msg["slug"] == "test"
        assert msg["path"] == "entities/test.md"
        assert msg["kind"] in ("created", "modified")
