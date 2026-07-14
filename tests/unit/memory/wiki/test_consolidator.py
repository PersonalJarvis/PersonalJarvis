"""Stage-2 consolidator tests (Wave-2 B5): body-aware judge semantics.

A scripted FakeBrain plays the judge; everything else (journal, curator,
AtomicWriter, vault) is real-on-tmpfs. Pins: ADD creates a schema-valid
page; UPDATE merges in place without losing existing facts (and the judge
SAW the existing body); NOOP only closes the journal row; INVALIDATE sets
``valid_until`` + ``superseded-by`` frontmatter and deletes nothing; a
truncated judge response writes nothing and skips the batch.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    WikiMemoryConfig,
)
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.consolidator import Consolidator
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.vault_index import VaultIndex

LENA_BODY = (
    "---\n"
    "type: entity\n"
    "entity_kind: person\n"
    "slug: lena\n"
    "aliases: [Lena]\n"
    "created: 2026-06-01\n"
    "updated: 2026-06-01\n"
    "---\n"
    "\n"
    "# Lena\n"
    "\n"
    "## Summary\n"
    "\n"
    "A friend of the user.\n"
    "\n"
    "## Facts\n"
    "\n"
    "- Lena lives in Hamburg.\n"
    "\n"
    "## Relationships\n"
    "\n"
    "- [[entities/alex|Alex]] — friend\n"
    "\n"
    "## Sources\n"
    "\n"
    "- conversation\n"
)


class FakeBrain:
    """Plays the judge with a scripted response per call."""

    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(self, responses: list[str], *, finish_reason: str = "stop") -> None:
        self._responses = list(responses)
        self.finish_reason = finish_reason
        self.received_requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.received_requests.append(req)
        text = self._responses.pop(0) if self._responses else "[]"
        yield BrainDelta(content=text)
        yield BrainDelta(finish_reason=self.finish_reason)

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class FakeRegistry:
    def __init__(self, brain: Any) -> None:
        self._brain = brain

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        return self._brain

    def available(self) -> set[str]:
        return {"gemini"}


def _config() -> JarvisConfig:
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
    )


@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    vault_root = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
    (vault_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (vault_root / "entities" / "alex.md").write_text(
        "---\ntype: entity\nslug: alex\n---\n\n# Alex\n\n## Summary\n\nThe user.\n",
        encoding="utf-8",
    )

    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)
    writer = AtomicWriter(vault_root=vault_root, backup_dir=tmp_path / "backups")
    log_writer = LogWriter(log_path=vault_root / "log.md")
    curator = WikiCurator(
        repo=repo,
        vault=vault,
        writer=writer,
        llm=WikiCuratorLLM.__new__(WikiCuratorLLM),
        log_writer=log_writer,
        vault_root=vault_root,
    )
    journal = CandidateJournal(tmp_path / "jarvis.db")
    yield vault_root, curator, journal
    journal.close()


def _consolidator(
    stack_tuple, brain: FakeBrain, **kwargs: Any,
) -> Consolidator:
    vault_root, curator, journal = stack_tuple
    return Consolidator(
        config=_config(),
        journal=journal,
        curator=curator,
        search=None,  # slug-overlap retrieval only — deterministic in tests
        vault_root=vault_root,
        registry=FakeRegistry(brain),
        **kwargs,
    )


def _judge_json(items: list[dict[str, Any]]) -> str:
    return json.dumps(items)


def _write_aged(path: Path, content: str) -> None:
    """Write a fixture page with an mtime older than the writer's 30s
    concurrent-edit lock, so an immediate consolidator update is not
    skipped as a recent edit."""
    path.write_text(content, encoding="utf-8")
    aged = time.time() - 120.0
    os.utime(path, (aged, aged))


@pytest.mark.asyncio
async def test_add_creates_schema_valid_page(stack) -> None:
    vault_root, _curator, journal = stack
    journal.append(
        [CandidateFact(fact="Lena moved to Hamburg.", kind="person", subjects=("lena",))],
        source_label="voice-fact:1", turn_hash="h1",
    )
    cid = journal.pending()[0].id

    brain = FakeBrain([_judge_json([{
        "candidate_id": cid,
        "decision": "add",
        "target": "entities/lena.md",
        "new_body": LENA_BODY,
        "reason": "new person",
    }])])
    consolidator = _consolidator(stack, brain)

    label = await consolidator.run_once()

    assert label == "journal-batch:1"
    page = vault_root / "entities" / "lena.md"
    assert page.is_file()
    assert "Lena lives in Hamburg." in page.read_text(encoding="utf-8")
    assert journal.pending() == []
    assert journal.backlog_count() == 0


@pytest.mark.asyncio
async def test_update_merges_in_place_and_judge_saw_the_body(stack) -> None:
    vault_root, _curator, journal = stack
    _write_aged(vault_root / "entities" / "lena.md", LENA_BODY)
    journal.append(
        [CandidateFact(
            fact="Lena got a new job at the animal clinic.",
            kind="person", subjects=("lena",),
        )],
        source_label="voice-fact:2", turn_hash="h2",
    )
    cid = journal.pending()[0].id

    updated_body = LENA_BODY.replace(
        "- Lena lives in Hamburg.\n",
        "- Lena lives in Hamburg.\n- Lena works at the animal clinic.\n",
    )
    brain = FakeBrain([_judge_json([{
        "candidate_id": cid,
        "decision": "update",
        "target": "entities/lena.md",
        "new_body": updated_body,
        "reason": "merge job fact",
    }])])
    consolidator = _consolidator(stack, brain)

    await consolidator.run_once()

    # Body-awareness: the judge prompt contained the EXISTING page body.
    prompt_text = brain.received_requests[0].messages[0].content
    assert "Lena lives in Hamburg." in prompt_text
    assert "entities/lena.md" in prompt_text

    # In-place merge: ONE file, old fact retained, new fact added.
    pages = list((vault_root / "entities").glob("lena*.md"))
    assert pages == [vault_root / "entities" / "lena.md"]
    content = pages[0].read_text(encoding="utf-8")
    assert "- Lena lives in Hamburg." in content
    assert "- Lena works at the animal clinic." in content


@pytest.mark.asyncio
async def test_noop_marks_row_without_writing(stack) -> None:
    vault_root, _curator, journal = stack
    # Aged like the UPDATE/INVALIDATE fixtures for pattern consistency
    # (NOOP itself writes nothing, but copy-pasted setups should be safe).
    _write_aged(vault_root / "entities" / "lena.md", LENA_BODY)
    before = (vault_root / "entities" / "lena.md").read_text(encoding="utf-8")
    journal.append(
        [CandidateFact(fact="Lena lives in Hamburg.", kind="person", subjects=("lena",))],
        source_label="voice-fact:3", turn_hash="h3",
    )
    cid = journal.pending()[0].id

    brain = FakeBrain([_judge_json([{
        "candidate_id": cid, "decision": "noop", "reason": "already known",
    }])])
    consolidator = _consolidator(stack, brain)

    await consolidator.run_once()

    assert journal.pending() == []
    assert (vault_root / "entities" / "lena.md").read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_invalidate_sets_frontmatter_and_deletes_nothing(stack) -> None:
    """Contradiction: the replacing page is ADDed in the same batch and the
    superseded page gets valid_until + a superseded-by wikilink to it (the
    same-batch arm of the create-or-refuse rule keeps the link alive)."""
    vault_root, _curator, journal = stack
    _write_aged(vault_root / "entities" / "lena.md", LENA_BODY)
    journal.append(
        [CandidateFact(
            fact="Lena actually moved to Berlin, not Hamburg.",
            kind="person", subjects=("lena",),
        )],
        source_label="voice-fact:4", turn_hash="h4",
    )
    cid = journal.pending()[0].id

    berlin_body = LENA_BODY.replace("slug: lena\n", "slug: lena-berlin\n").replace(
        "- Lena lives in Hamburg.", "- Lena lives in Berlin.",
    )
    brain = FakeBrain([_judge_json([
        {
            "candidate_id": cid,
            "decision": "add",
            "target": "entities/lena-berlin.md",
            "new_body": berlin_body,
            "reason": "corrected location page",
        },
        {
            "candidate_id": cid,
            "decision": "invalidate",
            "target": "entities/lena.md",
            "superseded_by": "lena-berlin",
            "reason": "contradiction",
        },
    ])])
    consolidator = _consolidator(stack, brain)

    await consolidator.run_once()

    page = vault_root / "entities" / "lena.md"
    assert page.is_file(), "invalidate must never delete"
    content = page.read_text(encoding="utf-8")
    assert "valid_until: " in content
    # The superseded-by wikilink survives because the replacing page was
    # created in the SAME batch (canonicalised to the typed alias form).
    assert "superseded-by:" in content
    assert "[[entities/lena-berlin|lena-berlin]]" in content
    # The body itself is byte-preserved.
    assert "- Lena lives in Hamburg." in content
    assert (vault_root / "entities" / "lena-berlin.md").is_file()
    assert journal.pending() == []


@pytest.mark.asyncio
async def test_truncated_judge_skips_batch_without_writes(stack) -> None:
    vault_root, _curator, journal = stack
    journal.append(
        [CandidateFact(fact="Lena moved to Hamburg.", kind="person", subjects=("lena",))],
        source_label="voice-fact:5", turn_hash="h5",
    )
    brain = FakeBrain(
        [_judge_json([{"candidate_id": 1, "decision": "add"}])],
        finish_reason="length",
    )
    consolidator = _consolidator(stack, brain)

    label = await consolidator.run_once()

    assert label == "judge-truncated"
    assert not (vault_root / "entities" / "lena.md").exists()
    assert journal.pending() == []  # skipped, not retried forever
    assert journal.backlog_count() == 0


@pytest.mark.asyncio
async def test_empty_journal_is_a_cheap_noop(stack) -> None:
    brain = FakeBrain([])
    consolidator = _consolidator(stack, brain)
    label = await consolidator.run_once()
    assert label == "journal-empty"
    assert brain.received_requests == []
