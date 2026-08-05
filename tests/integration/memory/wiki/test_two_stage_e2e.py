"""Wave-2 end-to-end acceptance (spec D5): the two-stage conversation curator.

The product contract under test: *"when I tell Jarvis about a friend, a
friend page appears, links into the graph, and grows over time — zero
junk."* A scripted FakeBrain plays BOTH stages (extractor + judge); the
entire remaining stack is real: VoiceFactBridge → CandidateJournal →
CuratorScheduler(JOURNAL) → Consolidator → WikiCurator →
AtomicWriter → tmp vault.

Four conversation turns drive the scenario:

1. ExampleFriend introduced → her entity page appears, the user's profile links
   her, a concept page records the Exampleville move.
2. ExampleFriend's new job → her ONE page is updated in place; old facts survive.
3. Contradiction (Exampletown, not Exampleville) → a corrected concept page is
   added and the old one is superseded (``valid_until`` +
   ``superseded-by`` wikilink) — nothing deleted.
4. A turn carrying an API key → Stage 1 refuses it before journal storage
   (AP-2), and no page appears.

Final sweep: zero dangling wikilinks anywhere, ``memory.md`` exists and
reflects the runs, quality counters advanced.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from jarvis.core.bus import EventBus
from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    SchedulerConfig,
    WikiMemoryConfig,
)
from jarvis.core.events import ResponseGenerated, TranscriptFinal
from jarvis.core.protocols import BrainDelta, BrainRequest, Transcript
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.cleanup import dangling_link_targets
from jarvis.memory.wiki.consolidator import Consolidator
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.extractor import ConversationFactExtractor
from jarvis.memory.wiki.journal import CandidateJournal
from jarvis.memory.wiki.lock import VaultLock
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.profile import ensure_profile_skeleton
from jarvis.memory.wiki.scheduler import CuratorScheduler, TriggerSource
from jarvis.memory.wiki.self_doc import refresh_memory_page
from jarvis.memory.wiki.telemetry import telemetry
from jarvis.memory.wiki.vault_index import VaultIndex
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge

# ---------------------------------------------------------------------------
# Scripted two-role brain
# ---------------------------------------------------------------------------


class TwoRoleFakeBrain:
    """Serves the extractor and the judge from two scripted queues."""

    name = "fake-brain"
    context_window = 200_000
    supports_tools = False
    supports_vision = False

    def __init__(self) -> None:
        self.extractor_responses: list[str] = []
        self.judge_responses: list[str] = []
        self.judge_prompts: list[str] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        if (req.system or "").startswith("You extract"):
            queue = self.extractor_responses
        else:
            self.judge_prompts.append(req.messages[0].content)
            queue = self.judge_responses
        text = queue.pop(0) if queue else "[]"
        if queue is self.extractor_responses:
            focus = re.search(
                r"FOCUS USER TURN \[([^\]]+)\]", req.messages[0].content,
            )
            if focus:
                payload = json.loads(text)
                for fact in payload:
                    fact.setdefault("evidence_turn_id", focus.group(1))
                text = json.dumps(payload)
        yield BrainDelta(content=text)
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class FakeRegistry:
    def __init__(self, brain: Any) -> None:
        self._brain = brain

    def available(self) -> set[str]:
        return {"gemini"}

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        return self._brain


# ---------------------------------------------------------------------------
# Page bodies the scripted judge writes
# ---------------------------------------------------------------------------


def _entity(slug: str, facts: list[str], relationships: list[str]) -> str:
    fact_lines = "\n".join(f"- {f}" for f in facts)
    rel_lines = "\n".join(f"- {r}" for r in relationships)
    name = slug.replace("-", " ").title()
    return (
        f"---\ntype: entity\nentity_kind: person\nslug: {slug}\n"
        f"aliases: [{name}]\ncreated: 2026-06-10\nupdated: 2026-06-10\n---\n\n"
        f"# {name}\n\n## Summary\n\nA friend of the user.\n\n"
        f"## Facts\n\n{fact_lines}\n\n## Relationships\n\n{rel_lines}\n\n"
        f"## Sources\n\n- conversation\n"
    )


def _concept(slug: str, summary: str) -> str:
    name = slug.replace("-", " ").title()
    return (
        f"---\ntype: concept\nslug: {slug}\naliases: []\n"
        f"created: 2026-06-10\nupdated: 2026-06-10\n---\n\n"
        f"# {name}\n\n## Summary\n\n{summary}\n\n## Definition\n\n{summary}\n\n"
        f"## Examples\n\n- conversation\n\n## Related\n\n- [[entities/examplefriend|ExampleFriend]]\n\n"
        f"## Sources\n\n- conversation\n"
    )


PROFILE_WITH_EXAMPLEFRIEND = (
    "---\ntype: entity\nentity_kind: person\nslug: alex\n"
    "aliases: [Alex, the user]\ncreated: 2026-05-14\nupdated: 2026-06-10\n---\n\n"
    "# Alex\n\n## Summary\n\nThe project owner.\n\n## Identity\n\n"
    "## Preferences\n\n## Work style\n\n## Values\n\n"
    "## Relationships\n\n- [[entities/examplefriend|ExampleFriend]] — friend, specialist\n\n"
    "## Active projects\n\n## Decisions\n\n## Sources\n\n- seed\n- conversation\n"
)


def _facts_json(facts: list[dict[str, Any]]) -> str:
    return json.dumps(facts)


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    vault_root = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub\n", encoding="utf-8")
    (vault_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (vault_root / "entities" / "alex.md").write_text(
        "---\ntype: entity\nentity_kind: person\nslug: alex\n"
        "aliases: [Alex, the user]\ncreated: 2026-05-14\nupdated: 2026-05-30\n---\n\n"
        "# Alex\n\n## Summary\n\nThe project owner.\n\n## Sources\n\n- seed\n",
        encoding="utf-8",
    )

    cfg = JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
    )
    brain = TwoRoleFakeBrain()
    registry = FakeRegistry(brain)

    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)
    writer = AtomicWriter(vault_root=vault_root, backup_dir=tmp_path / "backups")
    curator = WikiCurator(
        repo=repo,
        vault=vault,
        writer=writer,
        llm=WikiCuratorLLM.__new__(WikiCuratorLLM),
        log_writer=LogWriter(log_path=vault_root / "log.md"),
        vault_root=vault_root,
    )
    journal = CandidateJournal(tmp_path / "jarvis.db")
    extractor = ConversationFactExtractor(
        config=cfg, journal=journal, registry=registry,
    )

    async def _refresh() -> None:
        await refresh_memory_page(
            curator=curator, vault_root=vault_root, journal=journal,
        )

    consolidator = Consolidator(
        config=cfg,
        journal=journal,
        curator=curator,
        search=None,  # slug-overlap retrieval (deterministic in tests)
        vault_root=vault_root,
        registry=registry,
        on_run_complete=_refresh,
    )
    scheduler = CuratorScheduler(
        curator=curator,
        lock=VaultLock(tmp_path / "curator.lock"),
        config=SchedulerConfig(cooldown_seconds=0),
        consolidator=consolidator,
    )

    bus = EventBus()
    bridge = VoiceFactBridge(bus=bus, curator=curator, config=None, extractor=extractor)
    bridge.start()

    # D4 skeleton, as bootstrap would do it — then age again so the first
    # conversation's profile update is not blocked by the 30s edit lock.
    _age_vault(vault_root)
    await ensure_profile_skeleton(vault_root=vault_root, slug="alex", curator=curator)
    _age_vault(vault_root)

    yield vault_root, bus, brain, journal, scheduler, bridge
    bridge.stop()
    journal.close()


def _age_vault(vault_root: Path) -> None:
    """Backdate every page past the 30s concurrent-edit lock — stands in
    for the minutes that pass between real conversations."""
    aged = time.time() - 120.0
    for p in vault_root.rglob("*.md"):
        os.utime(p, (aged, aged))


async def _turn(bus: EventBus, journal: CandidateJournal, text: str) -> None:
    """Publish one voice turn and wait for its background extraction."""
    before = journal.backlog_count()
    await bus.publish(TranscriptFinal(
        transcript=Transcript(text=text, language="en", confidence=0.95),
    ))
    await bus.publish(ResponseGenerated(text="Noted.", language="en"))
    for _ in range(100):
        if journal.backlog_count() > before:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"extraction never journaled the turn: {text!r}")


@pytest.mark.asyncio
async def test_friend_page_appears_links_grows_and_contradictions_supersede(stack) -> None:
    vault_root, bus, brain, journal, scheduler, _bridge = stack
    counters_before = telemetry.snapshot()

    # ---- Turn 1: ExampleFriend introduced ---------------------------------------
    brain.extractor_responses.append(_facts_json([
        {"fact": "ExampleFriend is a friend of the user and works as a specialist.",
         "kind": "person", "subjects": ["examplefriend"]},
        {"fact": "The user is friends with ExampleFriend.",
         "kind": "identity", "subjects": ["alex", "examplefriend"]},
        {"fact": "ExampleFriend moved to Exampleville last month.",
         "kind": "event", "subjects": ["examplefriend"]},
    ]))
    await _turn(
        bus, journal,
        "My friend ExampleFriend moved to Exampleville last month. She works as a specialist.",
    )
    rows = journal.pending()
    cid_examplefriend, cid_profile, cid_move = (r.id for r in rows)
    brain.judge_responses.append(json.dumps([
        {"candidate_id": cid_examplefriend, "decision": "add",
         "target": "entities/examplefriend.md",
         "new_body": _entity("examplefriend",
                             ["ExampleFriend works as a specialist.",
                              "ExampleFriend lives in Exampleville."],
                             ["[[entities/alex|Alex]] — friend"]),
         "reason": "new person"},
        {"candidate_id": cid_profile, "decision": "update",
         "target": "entities/alex.md",
         "new_body": PROFILE_WITH_EXAMPLEFRIEND,
         "reason": "link the friend into the profile"},
        {"candidate_id": cid_move, "decision": "add",
         "target": "concepts/examplefriend-in-exampleville.md",
         "new_body": _concept("examplefriend-in-exampleville", "ExampleFriend moved to Exampleville in May 2026."),
         "reason": "durable event"},
    ]))
    result = await scheduler.trigger(TriggerSource.JOURNAL)
    assert result.triggered is True

    examplefriend_page = vault_root / "entities" / "examplefriend.md"
    assert examplefriend_page.is_file(), "the friend page must appear"
    profile = (vault_root / "entities" / "alex.md").read_text(encoding="utf-8")
    assert "[[entities/examplefriend|ExampleFriend]]" in profile, "profile links into the graph"
    assert (vault_root / "concepts" / "examplefriend-in-exampleville.md").is_file()
    assert journal.backlog_count() == 0

    # ---- Turn 2: the page grows in place -------------------------------
    _age_vault(vault_root)
    brain.extractor_responses.append(_facts_json([
        {"fact": "ExampleFriend got a new job at the example studio in Example District.",
         "kind": "person", "subjects": ["examplefriend"]},
    ]))
    await _turn(bus, journal, "ExampleFriend got a new job at the example studio in Example District.")
    cid_job = journal.pending()[0].id
    examplefriend_with_job = examplefriend_page.read_text(encoding="utf-8").replace(
        "- ExampleFriend lives in Exampleville.\n",
        "- ExampleFriend lives in Exampleville.\n- ExampleFriend works at the example studio in Example District.\n",
    )
    brain.judge_responses.append(json.dumps([
        {"candidate_id": cid_job, "decision": "update",
         "target": "entities/examplefriend.md",
         "new_body": examplefriend_with_job,
         "reason": "merge job fact"},
    ]))
    await scheduler.trigger(TriggerSource.JOURNAL)

    pages = sorted((vault_root / "entities").glob("examplefriend*.md"))
    assert pages == [examplefriend_page], "UPDATE must stay in place — no examplefriend-2.md"
    content = examplefriend_page.read_text(encoding="utf-8")
    assert "- ExampleFriend works as a specialist." in content, "old facts survive"
    assert "- ExampleFriend works at the example studio in Example District." in content
    # Body-awareness: the judge SAW the existing page body in its prompt.
    assert "ExampleFriend works as a specialist." in brain.judge_prompts[1]

    # ---- Turn 3: contradiction → supersede, never delete ----------------
    _age_vault(vault_root)
    brain.extractor_responses.append(_facts_json([
        {"fact": "ExampleFriend actually moved to Exampletown, not Exampleville.",
         "kind": "event", "subjects": ["examplefriend"]},
    ]))
    await _turn(bus, journal, "Correction: ExampleFriend actually moved to Exampletown, not Exampleville.")
    cid_exampletown = journal.pending()[0].id
    brain.judge_responses.append(json.dumps([
        {"candidate_id": cid_exampletown, "decision": "add",
         "target": "concepts/examplefriend-in-exampletown.md",
         "new_body": _concept("examplefriend-in-exampletown", "ExampleFriend moved to Exampletown in June 2026."),
         "reason": "corrected event"},
        {"candidate_id": cid_exampletown, "decision": "invalidate",
         "target": "concepts/examplefriend-in-exampleville.md",
         "superseded_by": "examplefriend-in-exampletown",
         "reason": "contradicted by the correction"},
    ]))
    await scheduler.trigger(TriggerSource.JOURNAL)

    exampleville = (vault_root / "concepts" / "examplefriend-in-exampleville.md").read_text(encoding="utf-8")
    assert "valid_until: " in exampleville, "superseded page carries valid_until"
    assert "superseded-by:" in exampleville
    assert "examplefriend-in-exampletown" in exampleville
    assert (vault_root / "concepts" / "examplefriend-in-exampletown.md").is_file()
    assert (vault_root / "concepts" / "examplefriend-in-exampleville.md").is_file(), "never deleted"

    # ---- Turn 4: secrets never persist (AP-2) ---------------------------
    _age_vault(vault_root)
    brain.extractor_responses.append(_facts_json([
        {"fact": "The user's OpenAI key is sk-proj-AbCdEf0123456789AbCdEf0123456789.",
         "kind": "other", "subjects": ["alex"]},
    ]))
    audit_before = journal.capture_summary()
    await bus.publish(TranscriptFinal(
        transcript=Transcript(
            text=(
                "Save this: my OpenAI key is "
                "sk-proj-AbCdEf0123456789AbCdEf0123456789"
            ),
            language="en",
            confidence=0.95,
        ),
    ))
    await bus.publish(ResponseGenerated(text="Noted.", language="en"))
    for _ in range(100):
        audit_after = journal.capture_summary()
        if audit_after["failed"] > audit_before["failed"]:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError("secret-bearing turn was not rejected and audited")

    assert not (vault_root / "concepts" / "api-key-note.md").exists(), (
        "secret-shaped body must never reach disk"
    )
    assert journal.backlog_count() == 0
    assert audit_after["failed"] == audit_before["failed"] + 1

    # ---- Final sweep: zero junk -----------------------------------------
    # log.md is the append-only chronicle (it references root meta pages
    # like memory.md in [[bare]] form by design); index.md is the human
    # table of contents. Neither is curator-authored content.
    for page in vault_root.rglob("*.md"):
        if "_archive" in page.parts or page.name in ("schema.md", "log.md", "index.md"):
            continue
        raw = page.read_text(encoding="utf-8")
        assert dangling_link_targets(raw, vault_root) == [], (
            f"dangling link in {page.name}"
        )

    memory_page = vault_root / "memory.md"
    assert memory_page.is_file(), "self-documentation page exists"
    memory_raw = memory_page.read_text(encoding="utf-8")
    assert "## Live status" in memory_raw

    counters = telemetry.snapshot()

    def _delta(name: str) -> int:
        return counters.get(name, 0) - counters_before.get(name, 0)

    assert _delta("wiki_candidates_extracted") == 5
    assert _delta("wiki_candidates_blocked_secret") == 1
    assert _delta("wiki_consolidator_add") == 3       # examplefriend, exampleville, exampletown
    assert _delta("wiki_consolidator_update") == 2    # profile, examplefriend job
    assert _delta("wiki_consolidator_invalidate") == 1
    assert _delta("wiki_consolidator_runs") == 3
