"""D2 regression: the awareness-episode -> session-page wiki feed is retired.

Two invariants:

1. With ``wiki_write_enabled`` off (the default), an ``IdleEntered`` event
   that would previously have triggered a session-page write produces NO
   page on disk and reports ``disabled_wiki_write`` — while the awareness
   episodes themselves are untouched (read, not deleted).
2. The conversation path (``VoiceFactBridge`` -> curator) still reaches the
   curator, so retiring the session feed does not silence the wiki.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from jarvis.core.bus import EventBus
from jarvis.core.config import load_config
from jarvis.core.events import IdleEntered, ResponseGenerated, TranscriptFinal
from jarvis.core.protocols import BrainDelta, BrainRequest, Transcript
from jarvis.memory.recall import RecallStore
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.session_rollup import SessionRollupWorker
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge

NS_PER_MIN = 60 * 1_000_000_000


class _FakeBrain:
    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(self, text: str = "A session paragraph.") -> None:
        self.text = text
        self.call_count = 0

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.call_count += 1
        yield BrainDelta(content=self.text)
        yield BrainDelta(finish_reason="stop", usage={"input_tokens": 1, "output_tokens": 1})

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


@pytest_asyncio.fixture
async def worker_stack(tmp_path: Path):
    vault_root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub\n", encoding="utf-8")
    (vault_root / "index.md").write_text(
        "# Index\n\n## Entities\n\n(empty)\n", encoding="utf-8"
    )
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    db_path = tmp_path / "jarvis.db"
    recall = RecallStore(db_path)
    await recall.open()

    repo = MarkdownPageRepository()
    writer = AtomicWriter(vault_root=vault_root, backup_dir=tmp_path / "backups")
    log_writer = LogWriter(log_path=vault_root / "log.md")
    bus = EventBus()
    config = load_config()

    clock_holder = [int(time.mktime((2026, 6, 15, 14, 0, 0, 0, 0, -1)) * 1_000_000_000)]
    worker = SessionRollupWorker(
        config=config,
        recall_store=recall,
        vault_root=vault_root,
        atomic_writer=writer,
        page_repo=repo,
        log_writer=log_writer,
        bus=bus,
        clock=lambda: clock_holder[0],
    )
    fake_brain = _FakeBrain()
    worker._registry.instantiate = MagicMock(return_value=fake_brain)  # noqa: SLF001

    yield worker, recall, vault_root, clock_holder, fake_brain
    await recall.close()


async def _seed_episode(recall: RecallStore, *, started_at_ns: int, summary: str) -> int:
    return await recall.record_episode(
        started_at_ns=started_at_ns,
        ended_at_ns=started_at_ns + NS_PER_MIN,
        trigger_kind="window_switch",
        summary=summary,
        frame_count=3,
        primary_app="code.exe",
    )


@pytest.mark.asyncio
async def test_idle_writes_no_session_page_when_feed_retired(worker_stack):
    """Default (wiki_write_enabled off): no page, no LLM call, status reports it."""
    worker, recall, vault_root, clock_holder, brain = worker_stack
    # Default must be OFF — assert it rather than mutate, so a regression of
    # the default flip is caught here too.
    assert worker._cfg.wiki_write_enabled is False  # noqa: SLF001

    base = clock_holder[0]
    worker._session_start_ns = base - 240 * NS_PER_MIN  # noqa: SLF001
    await _seed_episode(recall, started_at_ns=base - 60 * NS_PER_MIN, summary="ep1")
    await _seed_episode(recall, started_at_ns=base - 30 * NS_PER_MIN, summary="ep2")
    await _seed_episode(recall, started_at_ns=base - 10 * NS_PER_MIN, summary="ep3")

    event = IdleEntered(idle_since_ns=base - 150 * NS_PER_MIN)
    await worker._on_idle_entered(event)  # noqa: SLF001

    pages = list((vault_root / "sessions").glob("*.md"))
    assert pages == [], "D2: no durable session page may be written from awareness episodes"
    assert brain.call_count == 0, "D2: the retired feed must not even call the brain"

    # Awareness episodes are untouched (read, not consumed/deleted).
    remaining = await recall.recent_episodes(limit=1000, since_ns=base - 240 * NS_PER_MIN)
    assert len(remaining) == 3, "awareness L1/L2 episodes must remain intact"


@pytest.mark.asyncio
async def test_flush_returns_disabled_wiki_write_status(worker_stack):
    worker, _recall, _vault, _clock, brain = worker_stack
    result = await worker.flush_session()
    assert result.status == "disabled_wiki_write"
    assert result.page_path is None
    assert brain.call_count == 0


@pytest.mark.asyncio
async def test_reenabling_flag_restores_the_page_write(worker_stack):
    """Sanity: flipping wiki_write_enabled back on writes a page again."""
    worker, recall, vault_root, clock_holder, brain = worker_stack
    worker._cfg = worker._cfg.model_copy(update={"wiki_write_enabled": True})  # noqa: SLF001
    base = clock_holder[0]
    worker._session_start_ns = base - 240 * NS_PER_MIN  # noqa: SLF001
    await _seed_episode(recall, started_at_ns=base - 60 * NS_PER_MIN, summary="ep1")
    await _seed_episode(recall, started_at_ns=base - 30 * NS_PER_MIN, summary="ep2")

    result = await worker.flush_session()
    assert result.status == "ok"
    assert brain.call_count == 1
    assert list((vault_root / "sessions").glob("*.md"))


@pytest.mark.asyncio
async def test_voice_bridge_conversation_path_still_reaches_curator(tmp_path: Path):
    """Retiring the session feed must NOT silence the conversation -> curator path."""
    bus = EventBus()
    ingested: list[str] = []

    class _FakeCurator:
        async def ingest(self, source_content: str, source_label: str) -> Any:
            ingested.append(source_content)
            # Minimal WriteResult-shaped object the bridge tolerates.
            return MagicMock(applied=[], skipped_due_to_recent_edit=[], failed_validation=[])

    bridge = VoiceFactBridge(bus=bus, curator=_FakeCurator(), config=None)
    bridge.start()
    try:
        user_text = "Remember that my dentist appointment is on Friday at 3pm in Munich."
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text=user_text, language="en", confidence=0.95),
        ))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        # The bridge fires fire-and-forget tasks; let them run.
        for _ in range(20):
            await asyncio.sleep(0.02)
            if ingested:
                break
    finally:
        bridge.stop()

    assert ingested, "VoiceFactBridge must still forward conversation turns to the curator"
    assert any("dentist" in text for text in ingested)
