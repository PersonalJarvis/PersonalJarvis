"""Integration-Test: MessageRecorder → RecallStore-Auto-Log."""
from __future__ import annotations

import pytest
import pytest_asyncio

from jarvis.core.bus import EventBus
from jarvis.core.events import MessageSent, ResponseGenerated
from jarvis.memory import MessageRecorder, RecallStore


@pytest_asyncio.fixture
async def recall(tmp_path):
    s = RecallStore(tmp_path / "recall.db")
    await s.open()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_recorder_auto_logs_on_bus(recall):
    bus = EventBus()
    MessageRecorder(recall).attach(bus)

    await bus.publish(MessageSent(text="hallo", role="user"))
    await bus.publish(ResponseGenerated(text="Hi! How can I help?"))
    await bus.publish(MessageSent(text="python programmieren"))

    # Beide geschrieben
    msgs = await recall.recent_messages(limit=10)
    assert len(msgs) == 3
    texts = [m["text"] for m in msgs]
    assert "hallo" in texts
    assert "python programmieren" in texts

    # Search funktioniert
    hits = await recall.search_messages("python", k=5)
    assert len(hits) == 1
    assert "python" in hits[0]["text"].lower()


@pytest.mark.asyncio
async def test_recorder_skips_empty_text(recall):
    bus = EventBus()
    MessageRecorder(recall).attach(bus)
    await bus.publish(MessageSent(text="", role="user"))
    await bus.publish(ResponseGenerated(text=""))
    msgs = await recall.recent_messages(limit=5)
    assert len(msgs) == 0
