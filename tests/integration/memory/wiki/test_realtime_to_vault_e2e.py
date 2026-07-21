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
from jarvis.core.events import VoiceSessionEnded, VoiceTurnCompleted
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
    """Script both review stages while keeping the production pipeline real.

    Responses are routed by REQUEST KIND, not by call order: per-turn
    extraction is deferred to the session-end boundary (AP-9, 2026-07-21),
    where the completeness sweep runs concurrently with the judge rounds
    that freshly journaled candidates trigger — a strict call-order script
    would race those two. Sweeps re-read already-reviewed turns and answer
    with an empty candidate array.
    """

    name = "review-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(self, extractions: list[str], judgements: list[str]) -> None:
        self._extractions = list(extractions)
        self._judgements = list(judgements)
        self.calls = 0
        self.requests: list[BrainRequest] = []
        self.judge_requests: list[BrainRequest] = []

    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls += 1
        self.requests.append(request)
        text = "\n".join(
            (
                str(getattr(request, "system", "") or ""),
                *(
                    str(getattr(message, "content", "") or "")
                    for message in request.messages
                ),
            )
        )
        if "completeness sweep" in text:
            payload = "[]"
        elif "Evidence user turn [" in text:
            self.judge_requests.append(request)
            payload = self._judgements.pop(0)
        else:
            payload = self._extractions.pop(0)
        yield BrainDelta(content=payload)
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


def _asset_page(slug: str, fact: str, owner_slug: str) -> str:
    title = slug.replace("-", " ").title()
    owner = owner_slug.replace("-", " ").title()
    return (
        "---\n"
        "type: entity\n"
        "entity_kind: vehicle\n"
        f"slug: {slug}\n"
        f"aliases: [{title}]\n"
        "created: 2026-07-15\n"
        "updated: 2026-07-15\n"
        "---\n\n"
        f"# {title}\n\n"
        f"## Summary\n\n{fact}\n\n"
        f"## Facts\n\n- {fact}\n\n"
        f"## Relationships\n\n- Owned by [[entities/{owner_slug}|{owner}]]\n\n"
        "## Sources\n\n- realtime conversation\n"
    )


def _turn(
    *,
    turn_id: str,
    provider: str,
    text: str,
    assistant_text: str = "Thanks for telling me.",
    session_id: str = "realtime-session",
) -> VoiceTurnCompleted:
    return VoiceTurnCompleted(
        session_id=session_id,
        turn_id=turn_id,
        user_text=text,
        jarvis_text=assistant_text,
        tier="realtime",
        provider=provider,
        model="realtime-test-model",
    )


async def _wait_for_page(path: Path, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:  # noqa: ASYNC110 -- bounded test polling
        if path.is_file():  # noqa: ASYNC240 -- bounded integration-test probe
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"reviewed realtime fact never reached {path}")


async def _wait_for_idle(
    brain: _ReviewBrain,
    journal: CandidateJournal,
    *,
    calls: int,
    timeout_s: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:  # noqa: ASYNC110 -- bounded test polling
        if brain.calls >= calls and journal.backlog_count() == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("realtime wiki review did not become idle")


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
    (vault_root / "entities" / "ruben.md").write_text(
        _entity_page("ruben", "Ruben is the user."),
        encoding="utf-8",
    )

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
        "Ruben owns a yacht named Aurora.",
        "Ruben needs to use the bathroom right now.",
        "Ruben owns an aircraft.",
    ]
    extractions = [
        json.dumps(
            [
                {
                    "fact": facts[0],
                    "kind": "person",
                    "subjects": ["lena"],
                    "evidence_turn_id": "openai-turn",
                }
            ]
        ),
        json.dumps(
            [
                {
                    "fact": facts[1],
                    "kind": "person",
                    "subjects": ["noah"],
                    "evidence_turn_id": "gemini-turn",
                }
            ]
        ),
        json.dumps(
            [
                {
                    "fact": facts[2],
                    "kind": "asset",
                    "subjects": ["aurora"],
                    "evidence_turn_id": "short-asset-turn",
                }
            ]
        ),
        # Stage 1 may over-capture; Stage 2 remains the binding slop filter.
        json.dumps(
            [
                {
                    "fact": facts[3],
                    "kind": "other",
                    "subjects": ["ruben"],
                    "evidence_turn_id": "transient-turn",
                }
            ]
        ),
        # Hostile Stage 1 copies an assistant guess while citing the valid user
        # turn. Stage 2 sees the user-only evidence and rejects the claim.
        json.dumps(
            [
                {
                    "fact": facts[4],
                    "kind": "asset",
                    "subjects": ["ruben", "aircraft"],
                    "evidence_turn_id": "assistant-guess-turn",
                }
            ]
        ),
    ]
    judgements = [
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
        json.dumps(
            [
                {
                    "candidate_id": 3,
                    "decision": "add",
                    "target": "entities/aurora.md",
                    "new_body": _asset_page("aurora", facts[2], "ruben"),
                    "reason": "durable owned asset and relationship",
                }
            ]
        ),
        json.dumps(
            [
                {
                    "candidate_id": 4,
                    "decision": "noop",
                    "reason": "transient bodily need has no durable value",
                }
            ]
        ),
        json.dumps(
            [
                {
                    "candidate_id": 5,
                    "decision": "noop",
                    "reason": "the user asked a question and did not assert ownership",
                }
            ]
        ),
    ]
    brain = _ReviewBrain(extractions, judgements)
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

    async def _speak_and_hang_up(turn: VoiceTurnCompleted) -> None:
        """One short call per fact: extraction is deferred to session end
        (AP-9, 2026-07-21), so the turn only reaches the extractor once its
        session closes."""
        await bus.publish(turn)
        await bus.publish(
            VoiceSessionEnded(
                session_id=turn.session_id, hangup_reason="hotkey"
            )
        )

    try:
        await _speak_and_hang_up(
            _turn(
                turn_id="openai-turn",
                provider="openai-realtime",
                text="My friend Lena moved to Hamburg last month and lives there now.",
                session_id="call-1",
            )
        )
        await _wait_for_page(vault_root / "entities" / "lena.md")
        await _speak_and_hang_up(
            _turn(
                turn_id="gemini-turn",
                provider="gemini-live",
                text="My colleague Noah works at the city library during the week.",
                session_id="call-2",
            )
        )
        await _wait_for_page(vault_root / "entities" / "noah.md")
        await _speak_and_hang_up(
            _turn(
                turn_id="short-asset-turn",
                provider="openai-realtime",
                text="I own a yacht named Aurora.",
                session_id="call-3",
            )
        )
        await _wait_for_page(vault_root / "entities" / "aurora.md")
        await _speak_and_hang_up(
            _turn(
                turn_id="transient-turn",
                provider="gemini-live",
                text="I need to use the bathroom right now.",
                session_id="call-4",
            )
        )
        await _wait_for_idle(brain, journal, calls=12)
        await _speak_and_hang_up(
            _turn(
                turn_id="assistant-guess-turn",
                provider="openai-realtime",
                text="What do you think I own?",
                assistant_text="Perhaps you own an aircraft.",
                session_id="call-5",
            )
        )
        await _wait_for_idle(brain, journal, calls=15)
    finally:
        bridge.stop()
        journal.close()

    assert facts[0] in (vault_root / "entities" / "lena.md").read_text(
        encoding="utf-8"
    )
    assert facts[1] in (vault_root / "entities" / "noah.md").read_text(
        encoding="utf-8"
    )
    asset_body = (vault_root / "entities" / "aurora.md").read_text(encoding="utf-8")
    assert facts[2] in asset_body
    assert "[[entities/ruben|Ruben]]" in asset_body
    fresh_index = VaultIndex(repo=MarkdownPageRepository())
    await fresh_index.scan(vault_root)
    assert [page.slug for page in fresh_index.backlinks_to("ruben")] == ["aurora"]
    assert all(
        "bathroom" not in path.read_text(encoding="utf-8").lower()
        for path in vault_root.rglob("*.md")
    )
    assert all(
        "aircraft" not in path.read_text(encoding="utf-8").lower()
        for path in vault_root.rglob("*.md")
    )
    final_judge_prompt = brain.judge_requests[-1].messages[0].content
    assert "user_evidence_excerpt=" in final_judge_prompt
    assert "Evidence user turn [assistant-guess-turn]" in final_judge_prompt
    assert "What do you think I own?" in final_judge_prompt
    assert "Perhaps you own an aircraft." not in final_judge_prompt
    # 5 deferred turn extractions + 5 judge rounds + 5 session sweeps.
    assert brain.calls == 15
    assert journal.backlog_count() == 0
