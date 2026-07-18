"""Stage-2 consolidator tests (Wave-2 B5): body-aware judge semantics.

A scripted FakeBrain plays the judge; everything else (journal, curator,
AtomicWriter, vault) is real-on-tmpfs. Pins: ADD creates a schema-valid
page; UPDATE merges in place without losing existing facts (and the judge
SAW the existing body); NOOP only closes the journal row; INVALIDATE sets
``valid_until`` + ``superseded-by`` frontmatter and deletes nothing; a
truncated judge response writes nothing and skips the batch.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    SessionRollupConfig,
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
    "- [[entities/ruben|Ruben]] — friend\n"
    "\n"
    "## Sources\n"
    "\n"
    "- conversation\n"
)


def _gpu_body(vram_gb: int, *, extra_fact: str = "") -> str:
    today = dt.date.today().isoformat()
    extra = f"- {extra_fact}\n" if extra_fact else ""
    return (
        "---\n"
        "type: entity\n"
        "entity_kind: device\n"
        "slug: nvidia-geforce-rtx-5070-ti\n"
        "aliases: [NVIDIA GeForce RTX 5070 Ti]\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        "---\n\n"
        "# NVIDIA GeForce RTX 5070 Ti\n\n"
        "## Summary\n\n"
        f"The user's graphics card has {vram_gb} GB VRAM.\n\n"
        "## Facts\n\n"
        f"- It has {vram_gb} GB VRAM.\n"
        f"{extra}\n"
        "## Relationships\n\n"
        "- Owned by the user.\n\n"
        "## Sources\n\n"
        "- conversation\n"
    )


def _san_francisco_trip_body() -> str:
    today = dt.date.today().isoformat()
    return (
        "---\n"
        "type: project\n"
        "slug: san-francisco-trip\n"
        "status: active\n"
        f"started: {today}\n"
        f"last_activity: {today}\n"
        "---\n\n"
        "# San Francisco Trip\n\n"
        "## Goal\n\n"
        "The user plans to travel to San Francisco tomorrow.\n\n"
        "## Current Status\n\n"
        "Planned.\n\n"
        "## Recent Activity\n\n"
        "- The user disclosed the travel plan.\n\n"
        "## Open Threads\n\n"
        "- None recorded.\n\n"
        "## Related\n\n"
        "- San Francisco.\n\n"
        "## Sources\n\n"
        "- conversation\n"
    )


class FakeBrain:
    """Plays the judge with a scripted response per call."""

    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(
        self,
        responses: list[str],
        *,
        finish_reason: str | list[str] = "stop",
    ) -> None:
        self._responses = list(responses)
        self._finish_reasons = (
            list(finish_reason) if isinstance(finish_reason, list) else [finish_reason]
        )
        self.received_requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.received_requests.append(req)
        text = self._responses.pop(0) if self._responses else "[]"
        yield BrainDelta(content=text)
        reason = self._finish_reasons.pop(0) if self._finish_reasons else "stop"
        yield BrainDelta(finish_reason=reason)

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class FakeRegistry:
    def __init__(self, brain: Any) -> None:
        self._brain = brain

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        return self._brain

    def available(self) -> set[str]:
        return {"gemini"}


class ScriptedProviderRegistry:
    """Expose one independently scripted brain per provider family."""

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.tried: list[str] = []

    def instantiate(self, name: str, **_kwargs: Any) -> Any:
        self.tried.append(name)
        return FakeBrain([self._responses[name]])

    def available(self) -> set[str]:
        return set(self._responses)


def _config(*, user_entity_slug: str = "") -> JarvisConfig:
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(
            wiki=WikiMemoryConfig(
                session_rollup=SessionRollupConfig(
                    user_entity_slug=user_entity_slug,
                ),
            ),
        ),
    )


@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    vault_root = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
    (vault_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (vault_root / "entities" / "ruben.md").write_text(
        "---\ntype: entity\nslug: ruben\n---\n\n# Ruben\n\n## Summary\n\nThe user.\n",
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
    stack_tuple,
    brain: FakeBrain,
    *,
    config: JarvisConfig | None = None,
    **kwargs: Any,
) -> Consolidator:
    vault_root, curator, journal = stack_tuple
    registry = kwargs.pop("registry", None)
    return Consolidator(
        config=config or _config(),
        journal=journal,
        curator=curator,
        search=None,  # slug-overlap retrieval only — deterministic in tests
        vault_root=vault_root,
        registry=registry or FakeRegistry(brain),
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


RUBEN_FULL_BODY = (
    "---\n"
    "type: entity\n"
    "entity_kind: person\n"
    "slug: ruben\n"
    "aliases: [Ruben]\n"
    "created: 2026-06-01\n"
    "updated: 2026-06-01\n"
    "---\n"
    "\n"
    "# Ruben\n"
    "\n"
    "## Summary\n"
    "\n"
    "The user.\n"
    "\n"
    "## Facts\n"
    "\n"
    "- Enjoys great coffee.\n"
    "\n"
    "## Relationships\n"
    "\n"
    "## Sources\n"
    "\n"
    "- conversation\n"
)


def _espresso_project_body() -> str:
    today = dt.date.today().isoformat()
    return (
        "---\n"
        "type: project\n"
        "slug: espresso-machine\n"
        "status: active\n"
        f"started: {today}\n"
        f"last_activity: {today}\n"
        "---\n\n"
        "# Espresso Machine\n\n"
        "## Goal\n\n"
        "Find a high-end espresso machine for the kitchen.\n\n"
        "## Current Status\n\n"
        "Researching options.\n\n"
        "## Recent Activity\n\n"
        "- The user disclosed the pursuit.\n\n"
        "## Open Threads\n\n"
        "- None recorded.\n\n"
        "## Related\n\n"
        "- [[entities/ruben]]\n\n"
        "## Sources\n\n"
        "- conversation\n"
    )


@pytest.mark.asyncio
async def test_companion_add_creates_topic_page_beside_profile_update(stack) -> None:
    """Graph-visibility rule: a profile update may CREATE the missing topic
    page as a secondary "add" in the same batch, cross-linked both ways."""
    vault_root, _curator, journal = stack
    _write_aged(vault_root / "entities" / "ruben.md", RUBEN_FULL_BODY)
    journal.append(
        [CandidateFact(
            fact="The user is pursuing a high-end espresso machine for the kitchen.",
            kind="preference", subjects=("ruben", "espresso-machine"),
        )],
        source_label="realtime-aggressive:1", turn_hash="h-espresso",
    )
    cid = journal.pending()[0].id

    updated_profile = RUBEN_FULL_BODY.replace(
        "- Enjoys great coffee.\n",
        "- Enjoys great coffee.\n"
        "- Pursuing a high-end espresso machine for the kitchen.\n",
    ).replace(
        "## Relationships\n\n",
        "## Relationships\n\n- [[projects/espresso-machine]] — active pursuit\n\n",
    )
    brain = FakeBrain([_judge_json([
        {
            "candidate_id": cid,
            "decision": "update",
            "target": "entities/ruben.md",
            "new_body": updated_profile,
            "reason": "profile note",
        },
        {
            "candidate_id": cid,
            "decision": "add",
            "target": "projects/espresso-machine.md",
            "new_body": _espresso_project_body(),
            "reason": "companion topic page (graph visibility)",
        },
    ])])
    consolidator = _consolidator(stack, brain)

    label = await consolidator.run_once()

    assert label == "journal-batch:1"
    topic = vault_root / "projects" / "espresso-machine.md"
    assert topic.is_file()
    assert "Find a high-end espresso machine" in topic.read_text(encoding="utf-8")
    profile = (vault_root / "entities" / "ruben.md").read_text(encoding="utf-8")
    assert "- Enjoys great coffee." in profile  # existing fact survived
    assert "- Pursuing a high-end espresso machine for the kitchen." in profile
    # The cross-link survives demotion because the target page is created
    # in the SAME batch.
    assert "[[projects/espresso-machine]]" in profile
    assert journal.pending() == []


@pytest.mark.asyncio
async def test_secondary_update_is_still_rejected(stack) -> None:
    """Only "add" and "invalidate" may ride as secondary actions — a
    secondary "update" of another existing page stays invalid."""
    vault_root, _curator, journal = stack
    _write_aged(vault_root / "entities" / "ruben.md", RUBEN_FULL_BODY)
    journal.append(
        [CandidateFact(
            fact="The user is pursuing a high-end espresso machine.",
            kind="preference", subjects=("ruben",),
        )],
        source_label="realtime-aggressive:2", turn_hash="h-espresso-2",
    )
    cid = journal.pending()[0].id

    brain = FakeBrain([_judge_json([
        {
            "candidate_id": cid,
            "decision": "update",
            "target": "entities/ruben.md",
            "new_body": RUBEN_FULL_BODY,
            "reason": "profile note",
        },
        {
            "candidate_id": cid,
            "decision": "update",
            "target": "entities/lena.md",
            "new_body": LENA_BODY,
            "reason": "sneaky second update",
        },
    ])])
    consolidator = _consolidator(stack, brain)

    label = await consolidator.run_once()

    assert label == "judge-unavailable"  # rejected response, chain exhausted
    assert not (vault_root / "entities" / "lena.md").exists()
    assert journal.pending() != []  # candidate stays visible for the next pass


@pytest.mark.asyncio
async def test_partial_decision_array_crosses_to_complete_provider(stack) -> None:
    _vault_root, _curator, journal = stack
    journal.append(
        [
            CandidateFact(fact="The user owns a yacht.", subjects=("user",)),
            CandidateFact(fact="The yacht is named Aurora.", subjects=("aurora",)),
        ],
        source_label="voice:partial-fallback",
        turn_hash="partial-fallback",
    )
    first, second = [row.id for row in journal.pending()]
    registry = ScriptedProviderRegistry(
        {
            "gemini": _judge_json(
                [{"candidate_id": first, "decision": "noop", "reason": "partial"}]
            ),
            "openrouter": _judge_json(
                [
                    {"candidate_id": first, "decision": "noop", "reason": "known"},
                    {"candidate_id": second, "decision": "noop", "reason": "known"},
                ]
            ),
        }
    )

    label = await _consolidator(
        stack,
        FakeBrain([]),
        registry=registry,
    ).run_once()

    assert label == "journal-batch:2"
    assert registry.tried == ["gemini", "openrouter"]
    assert journal.pending() == []


@pytest.mark.asyncio
async def test_explicit_persistence_noop_crosses_to_write_provider(stack) -> None:
    vault_root, _curator, journal = stack
    evidence = (
        "Evidence user turn [sf-turn]: Kannst du bitte hinzufügen, dass ich "  # i18n-allow
        "morgen nach San Francisco reisen möchte?"  # i18n-allow
    )
    journal.append(
        [
            CandidateFact(
                fact="The user plans to travel to San Francisco tomorrow.",
                kind="plan",
                subjects=("san-francisco-trip",),
                evidence_turn_id="sf-turn",
                evidence_excerpt=evidence,
            )
        ],
        source_label="realtime:explicit-persistence",
        turn_hash="explicit-persistence",
    )
    cid = journal.pending()[0].id
    registry = ScriptedProviderRegistry(
        {
            "gemini": _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "noop",
                        "reason": "a near-term trip is too transient",
                    }
                ]
            ),
            "openrouter": _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "add",
                        "target": "projects/san-francisco-trip.md",
                        "new_body": _san_francisco_trip_body(),
                        "reason": "the user explicitly asked to keep the dated plan",
                    }
                ]
            ),
        }
    )

    label = await _consolidator(
        stack,
        FakeBrain([]),
        registry=registry,
    ).run_once()

    assert label == "journal-batch:1"
    assert registry.tried == ["gemini", "openrouter"]
    assert journal.pending() == []
    page = vault_root / "projects" / "san-francisco-trip.md"
    assert page.is_file()
    assert "travel to San Francisco tomorrow" in page.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "utterance",
    [
        "Remember that I travel tomorrow.",
        "Notiere, dass ich morgen reise.",  # i18n-allow
        "Speichere, dass ich morgen reise.",  # i18n-allow
        "Füge bitte hinzu, dass ich morgen reise.",  # i18n-allow
        "Recuerda que viajo mañana.",  # i18n-allow
        "Añade que viajo mañana.",  # i18n-allow
    ],
)
def test_explicit_persistence_clause_detector_covers_supported_languages(
    utterance: str,
) -> None:
    candidate = CandidateFact(
        fact="The user travels tomorrow.",
        evidence_turn_id="directive-turn",
        evidence_excerpt=f"Evidence user turn [directive-turn]: {utterance}",
    )

    assert Consolidator._has_explicit_persistence_request(candidate)  # noqa: SLF001


@pytest.mark.asyncio
async def test_explicit_wiki_save_allows_exact_existing_fact_noop(stack) -> None:
    vault_root, _curator, journal = stack
    page = vault_root / "projects" / "san-francisco-trip.md"
    existing_body = _san_francisco_trip_body().replace(
        "The user plans to travel to San Francisco tomorrow.",
        "Plans to travel to San Francisco tomorrow.",
    )
    _write_aged(page, existing_body)
    journal.append(
        [
            CandidateFact(
                fact="The user plans to travel to San Francisco tomorrow.",
                kind="plan",
                subjects=("san-francisco-trip",),
                evidence_turn_id="sf-repeat",
                evidence_excerpt=(
                    "Evidence user turn [sf-repeat]: Add to my wiki that I "
                    "plan to travel to San Francisco tomorrow."
                ),
            )
        ],
        source_label="realtime:explicit-wiki-repeat",
        turn_hash="explicit-wiki-repeat",
    )
    cid = journal.pending()[0].id
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "noop",
                        "reason": "exact duplicate of an unchanged existing fact",
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-batch:1"
    assert journal.pending() == []
    assert page.read_text(encoding="utf-8") == existing_body


@pytest.mark.asyncio
async def test_explicit_save_allows_unsupported_evidence_noop(stack) -> None:
    _vault_root, _curator, journal = stack
    journal.append(
        [
            CandidateFact(
                fact="The user has already booked the San Francisco trip.",
                kind="plan",
                subjects=("san-francisco-trip",),
                evidence_turn_id="sf-unsupported",
                evidence_excerpt=(
                    "Evidence user turn [sf-unsupported]: Remember that I may "
                    "travel to San Francisco tomorrow."
                ),
            )
        ],
        source_label="realtime:unsupported-persistence-candidate",
        turn_hash="unsupported-persistence-candidate",
    )
    cid = journal.pending()[0].id
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "noop",
                        "reason": "the booking claim is unsupported by user evidence",
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-batch:1"
    assert journal.pending() == []


@pytest.mark.asyncio
async def test_one_shot_command_with_relative_clause_remains_noop(stack) -> None:
    _vault_root, _curator, journal = stack
    journal.append(
        [
            CandidateFact(
                fact="The user wants the open report saved.",
                kind="other",
                subjects=("report",),
                evidence_turn_id="save-report",
                evidence_excerpt=(
                    "Evidence user turn [save-report]: Save the report that is open."
                ),
            )
        ],
        source_label="realtime:one-shot-save-command",
        turn_hash="one-shot-save-command",
    )
    cid = journal.pending()[0].id
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "noop",
                        "reason": "one-shot command with no durable assertion",
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-batch:1"
    assert journal.pending() == []


@pytest.mark.asyncio
async def test_update_that_drops_existing_fact_falls_back_safely(stack) -> None:
    vault_root, _curator, journal = stack
    page = vault_root / "entities" / "lena.md"
    _write_aged(page, LENA_BODY)
    journal.append(
        [CandidateFact(fact="Lena works at the animal clinic.", subjects=("lena",))],
        source_label="voice:preservation-fallback",
        turn_hash="preservation-fallback",
    )
    cid = journal.pending()[0].id
    destructive = LENA_BODY.replace("- Lena lives in Hamburg.\n", "")
    preserved = LENA_BODY.replace(
        "- Lena lives in Hamburg.\n",
        "- Lena lives in Hamburg.\n- Lena works at the animal clinic.\n",
    )
    registry = ScriptedProviderRegistry(
        {
            "gemini": _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "update",
                        "target": "entities/lena.md",
                        "new_body": destructive,
                    }
                ]
            ),
            "openrouter": _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "update",
                        "target": "entities/lena.md",
                        "new_body": preserved,
                    }
                ]
            ),
        }
    )

    label = await _consolidator(
        stack,
        FakeBrain([]),
        registry=registry,
    ).run_once()

    assert label == "journal-batch:1"
    assert registry.tried == ["gemini", "openrouter"]
    content = page.read_text(encoding="utf-8")
    assert "Lena lives in Hamburg." in content
    assert "Lena works at the animal clinic." in content


@pytest.mark.asyncio
async def test_unsupported_numeric_claim_falls_through_to_grounded_provider(
    stack,
) -> None:
    vault_root, _curator, journal = stack
    journal.append(
        [
            CandidateFact(
                fact=(
                    "The user's graphics card is an NVIDIA GeForce RTX 5070 Ti."
                ),
                subjects=("nvidia-geforce-rtx-5070-ti",),
                evidence_turn_id="gpu-turn",
                evidence_excerpt=(
                    "Evidence user turn [gpu-turn]: My RTX 5070 Ti has "
                    "16 GB VRAM."
                ),
            )
        ],
        source_label="voice:numeric-grounding",
        turn_hash="numeric-grounding",
    )
    cid = journal.pending()[0].id
    registry = ScriptedProviderRegistry(
        {
            "gemini": _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "add",
                        "target": "entities/nvidia-geforce-rtx-5070-ti.md",
                        "new_body": _gpu_body(24),
                    }
                ]
            ),
            "openrouter": _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "add",
                        "target": "entities/nvidia-geforce-rtx-5070-ti.md",
                        "new_body": _gpu_body(16),
                    }
                ]
            ),
        }
    )

    label = await _consolidator(
        stack,
        FakeBrain([]),
        registry=registry,
    ).run_once()

    assert label == "journal-batch:1"
    assert registry.tried == ["gemini", "openrouter"]
    content = (
        vault_root / "entities" / "nvidia-geforce-rtx-5070-ti.md"
    ).read_text(encoding="utf-8")
    assert "16 GB VRAM" in content
    assert "24 GB VRAM" not in content


@pytest.mark.asyncio
async def test_numeric_value_already_in_existing_page_remains_valid(stack) -> None:
    vault_root, _curator, journal = stack
    page = vault_root / "entities" / "nvidia-geforce-rtx-5070-ti.md"
    _write_aged(page, _gpu_body(16))
    journal.append(
        [
            CandidateFact(
                fact="The graphics card is installed in the desktop.",
                subjects=("nvidia-geforce-rtx-5070-ti",),
            )
        ],
        source_label="voice:existing-number",
        turn_hash="existing-number",
    )
    cid = journal.pending()[0].id
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "update",
                        "target": "entities/nvidia-geforce-rtx-5070-ti.md",
                        "new_body": _gpu_body(
                            16,
                            extra_fact="It is installed in the desktop.",
                        ),
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-batch:1"
    content = page.read_text(encoding="utf-8")
    assert "16 GB VRAM" in content
    assert "installed in the desktop" in content


@pytest.mark.asyncio
async def test_range_rendering_of_grounded_endpoints_is_accepted(stack) -> None:
    """"5 to 6 million" evidence grounds a "5-6 million" page rendering.

    Live 2026-07-17: the guard parsed "5-6" as ONE unknown value and burned
    the whole provider chain on a correct answer.
    """
    vault_root, _curator, journal = stack
    journal.append(
        [
            CandidateFact(
                fact=(
                    "The user's company pays 5 to 6 million euros in "
                    "holiday pay."
                ),
                subjects=("user-company",),
                evidence_turn_id="cost-turn",
                evidence_excerpt=(
                    "Evidence user turn [cost-turn]: holiday pay costs us "
                    "5 to 6 million euros a year."
                ),
            )
        ],
        source_label="voice:range-grounding",
        turn_hash="range-grounding",
    )
    cid = journal.pending()[0].id
    today = dt.date.today().isoformat()
    body = (
        "---\n"
        "type: entity\n"
        "entity_kind: organization\n"
        "slug: user-company\n"
        "aliases: [the user's company]\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        "---\n\n"
        "# User Company\n\n"
        "## Summary\n\nThe user's company.\n\n"
        "## Facts\n\n- Holiday pay costs 5-6 million euros a year.\n\n"
        "## Relationships\n\n- Owned by the user.\n\n"
        "## Sources\n\n- conversation\n"
    )
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "add",
                        "target": "entities/user-company.md",
                        "new_body": body,
                        "reason": "new organization",
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-batch:1"
    content = (
        vault_root / "entities" / "user-company.md"
    ).read_text(encoding="utf-8")
    assert "5-6 million euros" in content


def test_numeric_guard_accepts_locale_decimal_equivalence() -> None:
    row = SimpleNamespace(
        fact="The user is 1,80 m tall.",
        evidence_excerpt="Evidence user turn [t1]: I am 1,80 m tall.",
        subjects=("user",),
    )
    unsupported = Consolidator._unsupported_numeric_values(
        "## Facts\n\n- Height: 1.80 m.\n",
        row=row,
        existing_path=None,
    )
    assert unsupported == set()


def test_numeric_guard_still_rejects_new_precision() -> None:
    row = SimpleNamespace(
        fact="Costs are 5 to 6 million euros.",
        evidence_excerpt="Evidence user turn [t1]: 5 to 6 million euros.",
        subjects=(),
    )
    unsupported = Consolidator._unsupported_numeric_values(
        "## Facts\n\n- Costs 5.6 million euros.\n",
        row=row,
        existing_path=None,
    )
    assert unsupported == {"5.6"}


@pytest.mark.asyncio
async def test_today_and_current_year_are_grounded_time_context(stack) -> None:
    vault_root, _curator, journal = stack
    today_date = dt.date.today()
    today = today_date.isoformat()
    journal.append(
        [CandidateFact(fact="The user owns a yacht.", subjects=("user-yacht",))],
        source_label="voice:today-frontmatter",
        turn_hash="today-frontmatter",
    )
    cid = journal.pending()[0].id
    body = (
        "---\n"
        "type: entity\n"
        "entity_kind: vehicle\n"
        "slug: user-yacht\n"
        "aliases: [the user's yacht]\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        "---\n\n"
        "# User Yacht\n\n"
        f"## Summary\n\nAs of {today_date.year}, the user owns this yacht.\n\n"
        f"## Facts\n\n- Ownership was current on {today}.\n\n"
        "## Relationships\n\n- Owned by the user.\n\n"
        "## Sources\n\n- conversation\n"
    )
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "add",
                        "target": "entities/user-yacht.md",
                        "new_body": body,
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-batch:1"
    content = (vault_root / "entities" / "user-yacht.md").read_text(
        encoding="utf-8"
    )
    assert f"created: {today}" in content
    assert f"updated: {today}" in content
    assert f"As of {today_date.year}" in content
    assert f"current on {today}" in content


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
async def test_topic_question_candidate_is_presented_to_stage2_as_noop(stack) -> None:
    vault_root, _curator, journal = stack
    review_key = "session:v3:topic-question:chunk:000:abc"
    assert journal.claim_capture(
        review_key,
        "realtime-session-sweep:topic-question",
        "session-sweep",
        "c" * 64,
        session_id="topic-question",
    )
    assert journal.commit_capture_candidates(
        [
            CandidateFact(
                fact="The user is interested in Vitamin D.",
                kind="preference",
                subjects=("user",),
                evidence_turn_id="vitamin-turn",
                evidence_excerpt=(
                    "Evidence user turn [vitamin-turn]: "
                    "What are the benefits of Vitamin D?"
                ),
            )
        ],
        review_key=review_key,
        source_label="realtime-session-sweep:topic-question",
        turn_hash=review_key,
    ) == 1
    cid = journal.pending()[0].id
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "noop",
                        "reason": "question does not disclose durable interest",
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once(review_keys=(review_key,))

    assert label == "journal-batch:1"
    assert journal.pending() == []
    assert not (vault_root / "concepts" / "vitamin-d.md").exists()
    request = brain.received_requests[0]
    assert '"What are the benefits of Vitamin D?"' in request.system
    assert '"Tell me about Monaco."' in request.system
    assert '"I own a yacht." and "I plan to attend Monaco."' in request.system
    assert "What are the benefits of Vitamin D?" in request.messages[0].content


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("", "user"), ("owner-profile", "owner-profile"), ("../../private", "user")],
)
@pytest.mark.asyncio
async def test_judge_receives_safe_dynamic_user_entity_binding(
    stack,
    configured: str,
    expected: str,
) -> None:
    _vault_root, _curator, journal = stack
    journal.append(
        [
            CandidateFact(
                fact="The speaker prefers dark mode.",
                kind="preference",
                subjects=(expected,),
            )
        ],
        source_label="voice-user-entity",
        turn_hash="user-entity-binding",
    )
    cid = journal.pending()[0].id
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "noop",
                        "reason": "prompt binding probe",
                    }
                ]
            )
        ]
    )
    consolidator = _consolidator(
        stack,
        brain,
        config=_config(user_entity_slug=configured),
    )

    await consolidator.run_once()

    prompt = brain.received_requests[0].messages[0].content
    assert f'subject slug ["{expected}"]' in prompt
    assert f"profile page entities/{expected}.md" in prompt
    assert "../../private" not in prompt


def test_neighbour_lookup_rejects_legacy_traversal_subject(stack) -> None:
    vault_root, _curator, journal = stack
    outside = vault_root.parent / "outside-private.md"
    outside.write_text("outside private content", encoding="utf-8")
    journal.append(
        [CandidateFact(fact="A safe durable fact.", subjects=("ruben",))],
        source_label="legacy",
        turn_hash="legacy-traversal",
    )
    journal._conn.execute(  # noqa: SLF001 - emulate a pre-guard legacy row
        "UPDATE wiki_candidate_journal SET subjects = ?",
        (json.dumps(["../../outside-private"]),),
    )
    journal._conn.commit()  # noqa: SLF001
    consolidator = _consolidator(stack, FakeBrain(["[]"]))

    rows = journal.pending()
    neighbours = consolidator._collect_neighbours(rows)  # noqa: SLF001

    assert rows[0].subjects == ()
    assert neighbours == {}
    assert "outside private content" not in str(neighbours)


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
async def test_unsafe_superseded_by_cannot_inject_frontmatter(stack) -> None:
    vault_root, _curator, journal = stack
    old_page = vault_root / "entities" / "lena.md"
    _write_aged(old_page, LENA_BODY)
    journal.append(
        [CandidateFact(fact="Lena moved to Berlin.", subjects=("lena",))],
        source_label="voice:yaml-guard",
        turn_hash="yaml-guard",
    )
    cid = journal.pending()[0].id
    malicious = 'lena-berlin"\nowned: true'
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "invalidate",
                        "target": "entities/lena.md",
                        "superseded_by": malicious,
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "judge-unavailable"
    assert old_page.read_text(encoding="utf-8") == LENA_BODY
    assert [row.id for row in journal.pending()] == [cid]


@pytest.mark.asyncio
async def test_multi_page_candidate_aborts_before_partial_write(stack) -> None:
    vault_root, _curator, journal = stack
    old_page = vault_root / "entities" / "lena.md"
    # A fresh external edit on the invalidation target must also block the
    # sibling create in this candidate-level transaction.
    old_page.write_text(LENA_BODY, encoding="utf-8")
    journal.append(
        [CandidateFact(fact="Lena moved to Berlin.", subjects=("lena",))],
        source_label="voice:transactional-contradiction",
        turn_hash="transactional-contradiction",
    )
    cid = journal.pending()[0].id
    berlin_body = LENA_BODY.replace("slug: lena\n", "slug: lena-berlin\n").replace(
        "- Lena lives in Hamburg.", "- Lena lives in Berlin.",
    )
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "add",
                        "target": "entities/lena-berlin.md",
                        "new_body": berlin_body,
                    },
                    {
                        "candidate_id": cid,
                        "decision": "invalidate",
                        "target": "entities/lena.md",
                        "superseded_by": "lena-berlin",
                    },
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-transient:1"
    assert not (vault_root / "entities" / "lena-berlin.md").exists()
    assert old_page.read_text(encoding="utf-8") == LENA_BODY
    assert [row.id for row in journal.pending()] == [cid]


@pytest.mark.asyncio
async def test_truncated_single_candidate_stays_pending_without_writes(stack) -> None:
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
    assert len(journal.pending()) == 1
    assert journal.backlog_count() == 1


@pytest.mark.asyncio
async def test_truncated_batch_bisects_and_preserves_every_candidate(stack) -> None:
    vault_root, _curator, journal = stack
    journal.append(
        [
            CandidateFact(
                fact="Lena moved to Hamburg.", kind="person", subjects=("lena",)
            ),
            CandidateFact(
                fact="Tom moved to Bremen.", kind="person", subjects=("tom",)
            ),
        ],
        source_label="voice-fact:split",
        turn_hash="split",
    )
    first, second = [row.id for row in journal.pending()]
    tom_body = LENA_BODY.replace("Lena", "Tom").replace("lena", "tom").replace(
        "Hamburg", "Bremen"
    )
    brain = FakeBrain(
        [
            _judge_json([{"candidate_id": first, "decision": "add"}]),
            _judge_json(
                [
                    {
                        "candidate_id": first,
                        "decision": "add",
                        "target": "entities/lena.md",
                        "new_body": LENA_BODY,
                    }
                ]
            ),
            _judge_json(
                [
                    {
                        "candidate_id": second,
                        "decision": "add",
                        "target": "entities/tom.md",
                        "new_body": tom_body,
                    }
                ]
            ),
        ],
        finish_reason=["length", "stop", "stop"],
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-batch:2"
    assert len(brain.received_requests) == 3
    assert (vault_root / "entities" / "lena.md").is_file()
    assert (vault_root / "entities" / "tom.md").is_file()
    assert journal.pending() == []


@pytest.mark.asyncio
async def test_same_target_candidates_are_serialized_without_overwrite(stack) -> None:
    vault_root, _curator, journal = stack
    journal.append(
        [
            CandidateFact(
                fact="Lena lives in Hamburg.", kind="person", subjects=("lena",)
            ),
            CandidateFact(
                fact="Lena works at the animal clinic.",
                kind="person",
                subjects=("lena",),
            ),
        ],
        source_label="voice-fact:same-target",
        turn_hash="same-target",
    )
    first, second = [row.id for row in journal.pending()]
    merged_body = LENA_BODY.replace(
        "- Lena lives in Hamburg.\n",
        "- Lena lives in Hamburg.\n- Lena works at the animal clinic.\n",
    )
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": first,
                        "decision": "add",
                        "target": "entities/lena.md",
                        "new_body": LENA_BODY,
                    },
                    {
                        "candidate_id": second,
                        "decision": "add",
                        "target": "entities/lena.md",
                        "new_body": merged_body,
                    },
                ]
            ),
            _judge_json(
                [
                    {
                        "candidate_id": second,
                        "decision": "update",
                        "target": "entities/lena.md",
                        "new_body": merged_body,
                    }
                ]
            ),
        ]
    )
    consolidator = _consolidator(stack, brain)

    assert await consolidator.run_once() == "journal-deferred:1"
    assert [row.id for row in journal.pending()] == [second]
    assert await consolidator.run_once() == "journal-batch:1"

    content = (vault_root / "entities" / "lena.md").read_text(encoding="utf-8")
    assert "Lena lives in Hamburg." in content
    assert "Lena works at the animal clinic." in content
    assert journal.pending() == []


@pytest.mark.asyncio
async def test_recent_human_edit_keeps_candidate_pending(stack) -> None:
    vault_root, _curator, journal = stack
    # Deliberately fresh and not writer-authored: this is an external edit.
    (vault_root / "entities" / "lena.md").write_text(LENA_BODY, encoding="utf-8")
    journal.append(
        [
            CandidateFact(
                fact="Lena works at the animal clinic.",
                kind="person",
                subjects=("lena",),
            )
        ],
        source_label="voice-fact:human-edit",
        turn_hash="human-edit",
    )
    cid = journal.pending()[0].id
    updated_body = LENA_BODY.replace(
        "- Lena lives in Hamburg.\n",
        "- Lena lives in Hamburg.\n- Lena works at the animal clinic.\n",
    )
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "update",
                        "target": "entities/lena.md",
                        "new_body": updated_body,
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-transient:1"
    assert [row.id for row in journal.pending()] == [cid]
    assert "animal clinic" not in (
        vault_root / "entities" / "lena.md"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_log_append_failure_does_not_retry_landed_page(stack) -> None:
    vault_root, curator, journal = stack

    class _BrokenLog:
        async def append_log_entry(self, **_kwargs: Any) -> None:
            raise RuntimeError("secondary log unavailable")

    curator._log = _BrokenLog()  # noqa: SLF001 - post-write failure injection
    journal.append(
        [CandidateFact(fact="Lena moved to Hamburg.", subjects=("lena",))],
        source_label="voice-fact:log-failure",
        turn_hash="log-failure",
    )
    cid = journal.pending()[0].id
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "add",
                        "target": "entities/lena.md",
                        "new_body": LENA_BODY,
                    }
                ]
            )
        ]
    )

    label = await _consolidator(stack, brain).run_once()

    assert label == "journal-batch:1"
    assert (vault_root / "entities" / "lena.md").is_file()
    assert journal.pending() == []


@pytest.mark.asyncio
async def test_stage2_receives_user_evidence_and_persists_source_marker(stack) -> None:
    vault_root, _curator, journal = stack
    review_key = "session:v3:s-grounded:chunk:000:abc"
    assert journal.claim_capture(
        review_key,
        "realtime-session-sweep:s-grounded",
        "session-sweep",
        "a" * 64,
        session_id="s-grounded",
    )
    assert journal.commit_capture_candidates(
        [
            CandidateFact(
                fact="Lena moved to Hamburg.",
                kind="person",
                subjects=("lena",),
                evidence_turn_id="turn-7",
                evidence_excerpt=(
                    "Evidence user turn [turn-7]: Lena moved to Hamburg."
                ),
            )
        ],
        review_key=review_key,
        source_label="realtime-session-sweep:s-grounded",
        turn_hash=review_key,
    ) == 1
    cid = journal.pending()[0].id
    brain = FakeBrain(
        [
            _judge_json(
                [
                    {
                        "candidate_id": cid,
                        "decision": "add",
                        "target": "entities/lena.md",
                        "new_body": LENA_BODY,
                    }
                ]
            )
        ]
    )

    await _consolidator(stack, brain).run_once(review_keys=(review_key,))

    prompt = brain.received_requests[0].messages[0].content
    assert "Evidence user turn [turn-7]: Lena moved to Hamburg." in prompt
    page = (vault_root / "entities" / "lena.md").read_text(encoding="utf-8")
    assert "Realtime transcript: session `s-grounded`, turn `turn-7`." in page


@pytest.mark.asyncio
async def test_captured_candidate_without_user_evidence_is_rejected(stack) -> None:
    _vault_root, _curator, journal = stack
    review_key = "session:v2:legacy"
    assert journal.claim_capture(
        review_key,
        "legacy",
        "session-sweep",
        "b" * 64,
        session_id="legacy",
    )
    assert journal.commit_capture_candidates(
        [CandidateFact(fact="An unsupported legacy guess.", evidence_turn_id="t1")],
        review_key=review_key,
        source_label="legacy",
        turn_hash=review_key,
    ) == 1
    brain = FakeBrain(["[]"])

    label = await _consolidator(stack, brain).run_once(review_keys=(review_key,))

    assert label == "journal-evidence-rejected:1"
    assert brain.received_requests == []
    summary = journal.capture_decision_summary((review_key,))
    assert summary["rejected"] == 1
    assert summary["pending"] == 0


@pytest.mark.asyncio
async def test_empty_journal_is_a_cheap_noop(stack) -> None:
    brain = FakeBrain([])
    consolidator = _consolidator(stack, brain)
    label = await consolidator.run_once()
    assert label == "journal-empty"
    assert brain.received_requests == []
