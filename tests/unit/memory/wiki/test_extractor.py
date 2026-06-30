"""Unit tests for ``jarvis.memory.wiki.extractor`` — Stage-1 fact extraction.

The extractor is ADD-only: one cheap LLM call per eligible conversation turn,
0..N atomic candidate facts appended to the journal, never a vault write.
Provider/model resolve through the same hook as the curator (the Wiki
settings card drives both stages).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    WikiMemoryConfig,
)
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.extractor import ConversationFactExtractor
from jarvis.memory.wiki.journal import CandidateJournal


class FakeBrain:
    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(
        self,
        response_text: str,
        *,
        finish_reason: str = "stop",
        sleep_s: float = 0.0,
    ) -> None:
        self.response_text = response_text
        self.finish_reason = finish_reason
        self.sleep_s = sleep_s
        self.received_requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.received_requests.append(req)
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        yield BrainDelta(content=self.response_text)
        yield BrainDelta(finish_reason=self.finish_reason)

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class FakeRegistry:
    def __init__(self, brain: Any) -> None:
        self._brain = brain
        self.instantiate_calls: list[tuple[str, dict[str, Any]]] = []

    def available(self) -> set[str]:
        # Only the configured primary is reachable, so the key-aware fallback
        # chain is a single hop — the existing assertions on the first (only)
        # instantiated provider still hold.
        return {"gemini"}

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.instantiate_calls.append((name, dict(kwargs)))
        return self._brain


def _config() -> JarvisConfig:
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
    )


def _ok_facts_json() -> str:
    return json.dumps(
        [
            {"fact": "Lena moved to Hamburg.", "kind": "person", "subjects": ["lena"]},
            {"fact": "User prefers dark mode.", "kind": "preference", "subjects": ["ruben"]},
        ]
    )


@pytest.fixture
def journal(tmp_path: Path) -> CandidateJournal:
    j = CandidateJournal(tmp_path / "jarvis.db")
    yield j
    j.close()


@pytest.mark.asyncio
async def test_happy_path_appends_parsed_facts(journal: CandidateJournal) -> None:
    brain = FakeBrain(_ok_facts_json())
    registry = FakeRegistry(brain)
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=registry,
    )

    n = await extractor.extract_and_journal(
        "My friend Lena moved to Hamburg and I prefer dark mode.",
        "Noted - Lena is in Hamburg now.",
        source_label="voice-fact:1",
        turn_hash="h1",
    )

    assert n == 2
    rows = journal.pending()
    assert [r.fact for r in rows] == [
        "Lena moved to Hamburg.",
        "User prefers dark mode.",
    ]
    assert rows[0].kind == "person"
    assert rows[0].subjects == ("lena",)
    # The cheap router-tier model was requested, not the frontier chat model.
    assert registry.instantiate_calls
    name, kwargs = registry.instantiate_calls[0]
    assert name == "gemini"
    assert kwargs.get("model") == "gemini-3-flash-preview"


@pytest.mark.asyncio
async def test_short_input_skips_brain_entirely(journal: CandidateJournal) -> None:
    brain = FakeBrain(_ok_facts_json())
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "ok", "sure", source_label="voice-fact:2", turn_hash="h2",
    )
    assert n == 0
    assert brain.received_requests == []
    assert journal.backlog_count() == 0


@pytest.mark.asyncio
async def test_truncated_response_is_discarded(journal: CandidateJournal) -> None:
    brain = FakeBrain(_ok_facts_json(), finish_reason="length")
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "My friend Lena moved to Hamburg today.",
        "Noted.",
        source_label="voice-fact:3",
        turn_hash="h3",
    )
    assert n == 0
    assert journal.backlog_count() == 0


@pytest.mark.asyncio
async def test_malformed_json_yields_nothing(journal: CandidateJournal) -> None:
    brain = FakeBrain("I think the user likes dark mode but no JSON here.")
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "My friend Lena moved to Hamburg today.",
        "Noted.",
        source_label="voice-fact:4",
        turn_hash="h4",
    )
    assert n == 0
    assert journal.backlog_count() == 0


@pytest.mark.asyncio
async def test_code_fenced_json_is_tolerated(journal: CandidateJournal) -> None:
    fenced = "```json\n" + _ok_facts_json() + "\n```"
    brain = FakeBrain(fenced)
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "My friend Lena moved to Hamburg and I prefer dark mode.",
        "Noted.",
        source_label="voice-fact:5",
        turn_hash="h5",
    )
    assert n == 2


@pytest.mark.asyncio
async def test_empty_array_is_a_clean_zero(journal: CandidateJournal) -> None:
    brain = FakeBrain("[]")
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "It is a bit cloudy today, is it not?",
        "Indeed.",
        source_label="voice-fact:6",
        turn_hash="h6",
    )
    assert n == 0
    assert journal.backlog_count() == 0
