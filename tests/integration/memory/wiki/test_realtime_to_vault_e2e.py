"""Realtime transcript review reaches real Markdown pages without manual flush."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    WikiMemoryConfig,
)
from jarvis.core.events import VoiceTurnCompleted
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.consolidator import Consolidator
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.extractor import ConversationFactExtractor
from jarvis.memory.wiki.journal import CandidateJournal
from jarvis.memory.wiki.lock import VaultLock
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.scheduler import CuratorScheduler
from jarvis.memory.wiki.vault_index import VaultIndex
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge


class _ReviewBrain:
    """Script both review stages while keeping the production pipeline real."""

    name = "review-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls += 1
        yield BrainDelta(content=self._responses.pop(0))
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, request: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class _Registry:
    def __init__(self, brain: _ReviewBrain) -> None:
        self._brain = brain

    def available(self) -> set[str]:
        return {"test-provider"}

    def instantiate(self, name: str, **kwargs: Any) -> _ReviewBrain:
        return self._brain


def _entity_page(slug: str, fact: str) -> str:
    title = slug.replace("-", " ").title()
    return (
        "---\n"
        "type: entity\n"
        "entity_kind: person\n"
        f"slug: {slug}\n"
        f"aliases: [{title}]\n"
        "created: 2026-07-12\n"
        "updated: 2026-07-12\n"
        "---\n\n"
        f"# {title}\n\n"
        f"## Summary\n\n{fact}\n\n"
        f"## Facts\n\n- {fact}\n\n"
        "## Relationships\n\n"
        "## Sources\n\n- realtime conversation\n"
    )


def _turn(*, turn_id: str, provider: str, text: str) -> VoiceTurnCompleted:
    return VoiceTurnCompleted(
        session_id="realtime-session",
        turn_id=turn_id,
        user_text=text,
        jarvis_text="Thanks for telling me.",
        tier="realtime",
        provider=provider,
        model="realtime-test-model",
    )


async def _wait_for_page(path: Path, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"reviewed realtime fact never reached {path}")


@pytest.mark.asyncio
async def test_consecutive_realtime_reviews_write_to_selected_vault(
    tmp_path: Path,
) -> None:
    vault_root = tmp_path / "selected-vault" / "Jarvis"
    for directory in ("entities", "concepts", "projects", "sessions", "_archive"):
        (vault_root / directory).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# Schema\n", encoding="utf-8")
    (vault_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    config = JarvisConfig(
        brain=BrainConfig(
            primary="test-provider",
            providers={"test-provider": BrainProviderConfig(model="test-model")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
    )
    assert config.memory.wiki.voice_bridge.rate_limit_seconds == 0
    assert config.wiki_scheduler.consolidate_after_candidates == 1

    facts = [
        "Lena moved to Hamburg last month.",
        "Noah works at the city library.",
    ]
    responses = [
        json.dumps([{"fact": facts[0], "kind": "person", "subjects": ["lena"]}]),
        json.dumps(
            [
                {
                    "candidate_id": 1,
                    "decision": "add",
                    "target": "entities/lena.md",
                    "new_body": _entity_page("lena", facts[0]),
                    "reason": "new durable person fact",
                }
            ]
        ),
        json.dumps([{"fact": facts[1], "kind": "person", "subjects": ["noah"]}]),
        json.dumps(
            [
                {
                    "candidate_id": 2,
                    "decision": "add",
                    "target": "entities/noah.md",
                    "new_body": _entity_page("noah", facts[1]),
                    "reason": "new durable person fact",
                }
            ]
        ),
    ]
    brain = _ReviewBrain(responses)
    registry = _Registry(brain)
    repository = MarkdownPageRepository()
    vault = VaultIndex(repo=repository)
    await vault.scan(vault_root)
    writer = AtomicWriter(vault_root=vault_root, backup_dir=tmp_path / "backups")
    curator = WikiCurator(
        repo=repository,
        vault=vault,
        writer=writer,
        llm=WikiCuratorLLM.__new__(WikiCuratorLLM),
        log_writer=LogWriter(log_path=vault_root / "log.md"),
        vault_root=vault_root,
    )
    journal = CandidateJournal(tmp_path / "data" / "jarvis.db")
    extractor = ConversationFactExtractor(
        config=config,
        journal=journal,
        registry=registry,
    )
    consolidator = Consolidator(
        config=config,
        journal=journal,
        curator=curator,
        search=None,
        vault_root=vault_root,
        registry=registry,
    )
    scheduler = CuratorScheduler(
        curator=curator,
        lock=VaultLock(tmp_path / "curator.lock"),
        config=config.wiki_scheduler,
        consolidator=consolidator,
    )
    extractor.attach_scheduler(
        scheduler,
        consolidate_after=config.wiki_scheduler.consolidate_after_candidates,
    )
    bus = EventBus()
    bridge = VoiceFactBridge(
        bus=bus,
        curator=curator,
        config=config.memory.wiki.voice_bridge,
        extractor=extractor,
    )
    bridge.start()

    try:
        await bus.publish(
            _turn(
                turn_id="openai-turn",
                provider="openai-realtime",
                text="My friend Lena moved to Hamburg last month and lives there now.",
            )
        )
        await _wait_for_page(vault_root / "entities" / "lena.md")
        await bus.publish(
            _turn(
                turn_id="gemini-turn",
                provider="gemini-live",
                text="My colleague Noah works at the city library during the week.",
            )
        )
        await _wait_for_page(vault_root / "entities" / "noah.md")
    finally:
        bridge.stop()
        journal.close()

    assert facts[0] in (vault_root / "entities" / "lena.md").read_text(
        encoding="utf-8"
    )
    assert facts[1] in (vault_root / "entities" / "noah.md").read_text(
        encoding="utf-8"
    )
    assert brain.calls == 4
    assert journal.backlog_count() == 0
