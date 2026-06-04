"""Integration tests for :class:`VoiceFactBridge` aggressive-ingest path (B8.6).

The bridge has two paths from voice turn -> wiki:

1. The B5 *ack path*: trigger when the brain's reply contains "notiert",
   "vermerkt", etc.
2. The B8 *aggressive path*: trigger on every user turn with at least
   ``min_user_chars`` characters, even when the brain did not ack. The
   curator's prompt is the salience filter -- smalltalk returns [] and
   produces no on-disk artefact.

These tests run the full bridge -> curator -> AtomicWriter -> disk
pipeline. The only mocked piece is the curator's LLM (``propose_updates``)
-- we drive it with a scripted return so we control whether the
salience filter says "fact" or "smalltalk".

Acceptance criteria from B8:

* Pad-Thai utterance + non-ack brain reply -> page lands on disk.
* "hallo, wie geht's" + non-ack brain reply -> no page lands.
* The legacy ack path (`brain says "Notiert."`) keeps working.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from jarvis.core.bus import EventBus
from jarvis.core.config import VoiceBridgeConfig
from jarvis.core.events import ResponseGenerated, TranscriptFinal
from jarvis.core.protocols import Transcript
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.protocols import PageUpdate
from jarvis.memory.wiki.vault_index import VaultIndex
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge


# ---------------------------------------------------------------------------
# Fixtures: real curator + writer + vault, mocked-out CuratorLLM
# ---------------------------------------------------------------------------


def _entity_body(slug: str, summary_line: str) -> str:
    """Minimal schema-compliant entity-page body. Mirrors the canonical
    template used in the curator-ingest E2E test."""
    return (
        "---\n"
        "type: entity\n"
        "entity_kind: person\n"
        f"slug: {slug}\n"
        "aliases: []\n"
        "created: 2026-05-14\n"
        "updated: 2026-05-14\n"
        "---\n"
        "\n"
        f"# {slug.title()}\n"
        "\n"
        "## Summary\n"
        "\n"
        f"{summary_line}\n"
        "\n"
        "## Facts\n"
        "\n"
        "- TODO\n"
        "\n"
        "## Relationships\n"
        "\n"
        "- TODO\n"
        "\n"
        "## Sources\n"
        "\n"
        "- voice-aggressive fixture\n"
    )


@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    """Build the real curator pipeline with an empty on-disk vault."""
    vault_root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
    (vault_root / "index.md").write_text(
        "# Index\n\n## Entities\n\n(empty)\n", encoding="utf-8"
    )
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    backup_dir = tmp_path / "backups"
    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)
    writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
    log_writer = LogWriter(log_path=vault_root / "log.md")

    # Curator-LLM is bypassed (we only need ``propose_updates`` which the
    # tests patch per-case). Following the E2E-test convention from
    # tests/integration/memory/wiki/test_curator_ingest_e2e.py.
    llm = WikiCuratorLLM.__new__(WikiCuratorLLM)

    curator = WikiCurator(
        repo=repo,
        vault=vault,
        writer=writer,
        llm=llm,
        log_writer=log_writer,
        vault_root=vault_root,
    )

    bus = EventBus()
    yield curator, vault_root, bus


async def _drive_voice_turn(
    bus: EventBus,
    *,
    user_text: str,
    brain_text: str,
) -> None:
    """Publish a TranscriptFinal + ResponseGenerated pair and let the
    bridge process them. The brief sleep gives the fire-and-forget
    ingest task room to run before we inspect disk."""
    await bus.publish(TranscriptFinal(
        transcript=Transcript(text=user_text, language="de", confidence=0.95),
        timestamp_ns=42,
    ))
    await bus.publish(ResponseGenerated(text=brain_text, language="de"))
    # Give the background ingest a few event-loop ticks to land. The
    # curator + AtomicWriter chain is ~100 ms in this test setup.
    for _ in range(8):
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# Aggressive path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pad_thai_lands_in_wiki_without_brain_ack(stack) -> None:
    """The headline scenario: fact-shaped user text + non-ack brain reply
    must still produce a wiki page via the aggressive path."""
    curator, vault_root, bus = stack

    bridge = VoiceFactBridge(
        bus=bus,
        curator=curator,
        config=VoiceBridgeConfig(
            aggressive_mode=True,
            min_user_chars=30,
            rate_limit_seconds=0,   # disable rate-limit for the test
        ),
    )
    bridge.start()

    # The curator-LLM acts as the salience filter. Here it produces a
    # carlos.md entity page -- the LLM has decided "Pad-Thai with Carlos"
    # is a fact worth persisting.
    carlos_update = PageUpdate(
        target_path=vault_root / "entities" / "carlos.md",
        operation="create",
        new_body=_entity_body(
            "carlos",
            "Carlos went out for Pad Thai with Alex on 2026-05-14.",
        ),
        reason="new fact about a person",
    )

    with patch.object(
        curator._llm, "propose_updates", return_value=[carlos_update],
    ):
        await _drive_voice_turn(
            bus,
            user_text="Ich war heute mit Carlos Pad-Thai essen, er hat mir von seinem Boss erzaehlt.",
            brain_text="Klingt lecker, war es scharf?",
        )

    bridge.stop()

    page = vault_root / "entities" / "carlos.md"
    assert page.is_file(), "Pad-Thai fact should have produced entities/carlos.md"
    assert "Pad Thai" in page.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_smalltalk_produces_no_page(stack) -> None:
    """Salience filter (curator prompt) decides smalltalk -> [].
    Even when the bridge fires the ingest, no page lands on disk."""
    curator, vault_root, bus = stack

    bridge = VoiceFactBridge(
        bus=bus,
        curator=curator,
        config=VoiceBridgeConfig(
            aggressive_mode=True,
            min_user_chars=30,
            rate_limit_seconds=0,
        ),
    )
    bridge.start()

    # Curator-LLM returns empty -- this is the "smalltalk, drop it"
    # decision. The bridge still calls the curator, but no page lands.
    with patch.object(
        curator._llm, "propose_updates", return_value=[],
    ) as proposer:
        await _drive_voice_turn(
            bus,
            # 38 chars > 30 threshold so the bridge actually fires.
            user_text="Hallo Jarvis, wie geht es dir denn so?",
            brain_text="Mir geht's gut, danke der Nachfrage!",
        )

    bridge.stop()

    # The curator was offered the chance to act -- and declined.
    assert proposer.called, (
        "aggressive path should have called the curator even for "
        "smalltalk-looking text; the salience filter lives inside the "
        "curator's prompt, not in the bridge"
    )
    # ... and produced no on-disk artefacts.
    assert list((vault_root / "entities").glob("*.md")) == []


@pytest.mark.asyncio
async def test_short_text_skips_aggressive_path(stack) -> None:
    """Below the min_user_chars cut-off the bridge never calls the curator."""
    curator, _vault_root, bus = stack

    bridge = VoiceFactBridge(
        bus=bus,
        curator=curator,
        config=VoiceBridgeConfig(
            aggressive_mode=True,
            min_user_chars=30,
            rate_limit_seconds=0,
        ),
    )
    bridge.start()

    with patch.object(
        curator._llm, "propose_updates", return_value=[],
    ) as proposer:
        await _drive_voice_turn(
            bus,
            user_text="hallo",         # 5 chars -- way below 30
            brain_text="hi!",
        )

    bridge.stop()
    assert not proposer.called, (
        "trivial utterances must be filtered out by the bridge BEFORE "
        "the curator/LLM is consulted -- otherwise wake-word noise burns "
        "LLM calls"
    )


# ---------------------------------------------------------------------------
# Ack path keeps working (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ack_path_still_fires_when_brain_says_notiert(stack) -> None:
    """The B5 ack path must keep working alongside the aggressive path."""
    curator, vault_root, bus = stack

    bridge = VoiceFactBridge(
        bus=bus,
        curator=curator,
        config=VoiceBridgeConfig(
            aggressive_mode=True,
            min_user_chars=30,
            rate_limit_seconds=0,
        ),
    )
    bridge.start()

    sam_update = PageUpdate(
        target_path=vault_root / "entities" / "sam.md",
        operation="create",
        new_body=_entity_body("sam", "Sam wurde 1976 geboren."),
        reason="explicit user note",
    )

    with patch.object(
        curator._llm, "propose_updates", return_value=[sam_update],
    ):
        await _drive_voice_turn(
            bus,
            user_text="Sam wurde 1976 geboren.",   # 26 chars -- below aggressive min
            brain_text="Notiert.",                     # explicit ack keyword
        )

    bridge.stop()

    page = vault_root / "entities" / "sam.md"
    assert page.is_file(), (
        "ack path must still fire for fact-shaped texts even when they "
        "are below the aggressive-path min_user_chars threshold"
    )


@pytest.mark.asyncio
async def test_ack_path_and_aggressive_path_do_not_double_ingest(stack) -> None:
    """When the brain acks AND the text is long enough for the aggressive
    path, exactly one ingest fires -- ack-path wins, _pending is cleared."""
    curator, vault_root, bus = stack

    bridge = VoiceFactBridge(
        bus=bus,
        curator=curator,
        config=VoiceBridgeConfig(
            aggressive_mode=True,
            min_user_chars=30,
            rate_limit_seconds=0,
        ),
    )
    bridge.start()

    update = PageUpdate(
        target_path=vault_root / "entities" / "alex.md",
        operation="create",
        new_body=_entity_body("alex", "Alex rebuilt the wiki system."),
        reason="ack path fact",
    )

    with patch.object(
        curator._llm, "propose_updates", return_value=[update],
    ) as proposer:
        await _drive_voice_turn(
            bus,
            user_text="Ich habe heute das gesamte Wiki-System neu aufgebaut.",
            brain_text="Notiert.",
        )

    bridge.stop()

    # Exactly one ingest call -- not two. The ack path fires first and
    # clears self._pending so the aggressive branch finds nothing.
    assert proposer.call_count == 1


# ---------------------------------------------------------------------------
# Rate-limit on the aggressive path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_drops_second_aggressive_ingest_within_window(stack) -> None:
    """With rate_limit_seconds=60 the second voice turn within a minute
    must not trigger a second aggressive ingest."""
    curator, _vault_root, bus = stack

    bridge = VoiceFactBridge(
        bus=bus,
        curator=curator,
        config=VoiceBridgeConfig(
            aggressive_mode=True,
            min_user_chars=30,
            rate_limit_seconds=60,  # the real-world default
        ),
    )
    bridge.start()

    with patch.object(
        curator._llm, "propose_updates", return_value=[],
    ) as proposer:
        await _drive_voice_turn(
            bus,
            user_text="Mein Lieblingsfilm ist Inception und ich liebe Nolan-Filme.",
            brain_text="Cool, was hat dich daran beruehrt?",
        )
        # Same window -- second aggressive ingest must NOT fire.
        await _drive_voice_turn(
            bus,
            user_text="Mein Lieblingsessen ist Pizza und am liebsten mit Salami.",
            brain_text="Klassiker. Italienisch oder amerikanisch?",
        )

    bridge.stop()
    assert proposer.call_count == 1, (
        "second aggressive ingest within the rate-limit window must be "
        "dropped to avoid burning LLM calls on consecutive turns"
    )


@pytest.mark.asyncio
async def test_aggressive_mode_off_disables_path_entirely(stack) -> None:
    """``aggressive_mode=False`` keeps the ack path on but never fires
    the aggressive path."""
    curator, _vault_root, bus = stack

    bridge = VoiceFactBridge(
        bus=bus,
        curator=curator,
        config=VoiceBridgeConfig(
            aggressive_mode=False,
            min_user_chars=30,
            rate_limit_seconds=0,
        ),
    )
    bridge.start()

    with patch.object(
        curator._llm, "propose_updates", return_value=[],
    ) as proposer:
        await _drive_voice_turn(
            bus,
            user_text="Mein Lieblingsfilm ist Inception und das ist die volle Wahrheit.",
            brain_text="Klingt nach einem guten Film!",
        )

    bridge.stop()
    assert not proposer.called, (
        "with aggressive_mode=False, a non-ack reply must NOT trigger "
        "a curator call"
    )
