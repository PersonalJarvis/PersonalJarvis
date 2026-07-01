"""Three-trigger regression suite -- the whole memory pipeline at once (B8.10).

The wiki memory has three independent triggers that must all stay green
for the system to be considered intact:

1. :class:`WikiContextInjector` -- runs on every brain turn and
   prepends matching vault snippets to the system prompt.
2. :class:`VoiceFactBridge` -- listens for ``TranscriptFinal`` +
   ``ResponseGenerated`` and feeds the user text to the curator on
   either the ack path (B5) or the aggressive path (B8).
3. :class:`SessionRollupWorker` -- listens for ``IdleEntered`` and
   rolls awareness episodes into a session markdown page.

Each trigger already has its own focused suite. This file is the
*cross-trigger* one: ten cases that exercise the three together
against a single shared tmp-vault. A green run is the strongest
single signal that the wiki memory subsystem is intact.

Design notes
------------
* AP-5 from B5: no SQLite mocking. :class:`RecallStore` runs against a
  real on-disk SQLite file.
* AP-6 from B5: every test uses a single shared :class:`EventBus`.
* The curator's LLM is the only stubbed-out component (we do not want
  a live brain call in CI). Stubbed via ``patch.object(curator._llm,
  "propose_updates", ...)``.
* The rollup worker's brain is bypassed via a small private
  :class:`_FakeStreamBrain` -- it satisfies the
  ``BrainRequest -> AsyncIterator[BrainDelta]`` contract that
  :func:`session_rollup._call_brain` consumes.
* Telemetry is reset before each test so counters reflect the
  individual case.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from jarvis.brain.wiki_context import WikiContextInjector
from jarvis.core.bus import EventBus
from jarvis.core.config import (
    JarvisConfig,
    SessionRollupConfig,
    VoiceBridgeConfig,
)
from jarvis.core.events import IdleEntered, ResponseGenerated, TranscriptFinal
from jarvis.core.protocols import BrainDelta, BrainRequest, Transcript
from jarvis.memory.recall import RecallStore
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.protocols import PageUpdate
from jarvis.memory.wiki.search import VaultSearch
from jarvis.memory.wiki.session_rollup import SessionRollupWorker
from jarvis.memory.wiki.telemetry import telemetry
from jarvis.memory.wiki.vault_index import VaultIndex
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge


NS_PER_MIN = 60 * 1_000_000_000


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeStreamBrain:
    """Async-generator brain matching the production contract.

    The rollup worker reads ``brain.complete(BrainRequest) ->
    AsyncIterator[BrainDelta]`` via ``aggregate``. Tests build a
    ``_FakeStreamBrain`` with a fixed paragraph and optional failure
    mode.
    """

    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(
        self,
        text: str = "Default rollup paragraph for tests.",
        *,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._text = text
        self._raise_exc = raise_exc
        self.call_count = 0

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.call_count += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        yield BrainDelta(content=self._text)
        yield BrainDelta(finish_reason="stop", usage={"output_tokens": 12})

    def estimate_cost(self, req: BrainRequest) -> float:    # pragma: no cover
        return 0.0


class _SpyRegistry:
    """``BrainProviderRegistry`` stand-in for the rollup worker."""

    def __init__(self, brain: Any) -> None:
        self._brain = brain
        self.instantiate_calls: list[tuple[str, dict[str, Any]]] = []

    def available(self) -> set[str]:
        return {"gemini", "claude-api", "openrouter", "openai"}

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.instantiate_calls.append((name, dict(kwargs)))
        return self._brain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity_body(slug: str, summary: str, facts: list[str] | None = None) -> str:
    """Schema-compliant entity page body."""
    fact_lines = "\n".join(f"- {f}" for f in (facts or ["TODO"]))
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
        f"{summary}\n"
        "\n"
        "## Facts\n"
        "\n"
        f"{fact_lines}\n"
        "\n"
        "## Relationships\n"
        "\n"
        "- TODO\n"
        "\n"
        "## Sources\n"
        "\n"
        "- three-trigger test\n"
    )


async def _drive_voice_turn(
    bus: EventBus, *, user_text: str, brain_text: str,
) -> None:
    """Publish one voice-turn pair and let the bridge react."""
    await bus.publish(TranscriptFinal(
        transcript=Transcript(text=user_text, language="de", confidence=0.95),
        timestamp_ns=int(time.time_ns()),
    ))
    await bus.publish(ResponseGenerated(text=brain_text, language="de"))
    # The bridge spawns fire-and-forget ingest tasks; give them a few
    # event-loop ticks to land in this single-process test setup.
    for _ in range(8):
        await asyncio.sleep(0.05)


async def _seed_episode(
    recall: RecallStore, *, started_at_ns: int, summary: str, app: str = "code.exe",
) -> int:
    return await recall.record_episode(
        started_at_ns=started_at_ns,
        ended_at_ns=started_at_ns + NS_PER_MIN,
        trigger_kind="window_switch",
        summary=summary,
        frame_count=3,
        primary_app=app,
    )


# ---------------------------------------------------------------------------
# Big shared fixture: real curator + writer + recall + bridge + rollup +
# injector. All three triggers wired against one tmp-vault on disk.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    vault_root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
    (vault_root / "index.md").write_text(
        "# Index\n\n## Entities\n\n(empty)\n", encoding="utf-8"
    )
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    backup_dir = tmp_path / "backups"
    bus = EventBus()
    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)
    writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
    log_writer = LogWriter(log_path=vault_root / "log.md")

    # Curator-LLM is bypassed -- tests patch ``propose_updates`` per case.
    llm = WikiCuratorLLM.__new__(WikiCuratorLLM)
    curator = WikiCurator(
        repo=repo, vault=vault, writer=writer, llm=llm,
        log_writer=log_writer, vault_root=vault_root,
    )

    # Voice bridge.
    bridge = VoiceFactBridge(
        bus=bus, curator=curator,
        config=VoiceBridgeConfig(
            aggressive_mode=True, min_user_chars=30, rate_limit_seconds=0,
        ),
    )
    bridge.start()

    # Rollup worker. We craft a small ``JarvisConfig``-like surface for
    # the worker -- only the bits the worker actually reads are populated.
    full_config = JarvisConfig()
    full_config.memory.wiki.session_rollup = SessionRollupConfig(
        enabled=True,
        session_idle_threshold_minutes=2,
        min_episodes_for_rollup=2,
        max_active_sessions=5,
        max_output_tokens=600,
        timeout_s=5.0,
    )

    recall = RecallStore(tmp_path / "recall.db")
    await recall.open()

    clock_holder = [int(time.time_ns())]
    fake_rollup_brain = _FakeStreamBrain(
        text=(
            "User worked on the wiki memory rebuild, with focus on "
            "[[entities/alex]] preferences and [[projects/wiki-memory]] iterations."
        ),
    )
    registry = _SpyRegistry(fake_rollup_brain)
    rollup = SessionRollupWorker(
        config=full_config, recall_store=recall, vault_root=vault_root,
        atomic_writer=writer, page_repo=repo, log_writer=log_writer,
        bus=bus, clock=lambda: clock_holder[0], registry=registry,
    )
    # D2 (2026-06): the session-page feed is gated off by default; this
    # integration suite exercises the legacy rollup trigger, so opt back in.
    rollup._cfg = rollup._cfg.model_copy(update={"wiki_write_enabled": True})  # noqa: SLF001
    await rollup.start()

    # Context injector consumes the same on-disk vault.
    search = VaultSearch(vault_root)
    injector = WikiContextInjector(
        search=search, max_chars=1500,
        latency_budget_ms=500,  # generous; tmpfs is fast
        min_keyword_length=4,
    )

    # Reset telemetry so counters reflect just this test.
    telemetry.reset()

    yield {
        "vault_root": vault_root,
        "bus": bus,
        "curator": curator,
        "bridge": bridge,
        "rollup": rollup,
        "fake_rollup_brain": fake_rollup_brain,
        "recall": recall,
        "clock_holder": clock_holder,
        "injector": injector,
        "search": search,
    }

    bridge.stop()
    await rollup.stop()
    await recall.close()


# ===========================================================================
# Case 1 -- Happy-Path: voice fact lands AND becomes context next turn
# ===========================================================================


@pytest.mark.asyncio
async def test_voice_fact_becomes_context_on_next_turn(stack) -> None:
    """One spoken fact must (a) land in the wiki via the bridge and
    (b) be retrievable by the injector on the next brain turn."""
    s = stack
    vault_root = s["vault_root"]

    update = PageUpdate(
        target_path=vault_root / "entities" / "alex.md",
        operation="create",
        new_body=_entity_body(
            "alex",
            "Alex's favourite movie is Inception by Christopher Nolan.",
            facts=["Lieblingsfilm: Inception", "Nolan-Fan since 2010"],
        ),
        reason="user identity fact",
    )

    with patch.object(s["curator"]._llm, "propose_updates", return_value=[update]):
        await _drive_voice_turn(
            s["bus"],
            user_text="Mein Lieblingsfilm ist Inception und Nolan ist der beste Regisseur.",
            brain_text="Cool, Nolan ist wirklich stark.",  # no ack -> aggressive path
        )

    page = vault_root / "entities" / "alex.md"
    assert page.is_file(), "voice fact should have produced a wiki page"

    # Next turn: the injector finds the freshly-written page and
    # prepends it to the system prompt.
    base_prompt = "You are Personal Jarvis."
    augmented = await s["injector"].maybe_inject(
        user_text="Was war nochmal mein Lieblingsfilm?",
        system_prompt=base_prompt,
    )
    assert "Wiki context" in augmented, "injector should have prepended a context block"
    assert "Inception" in augmented, "the just-ingested fact must be in the context"


# ===========================================================================
# Case 2 -- Rollup-Path: episodes -> idle -> session page -> visible to injector
# ===========================================================================


@pytest.mark.asyncio
async def test_rollup_produces_session_page_visible_to_injector(stack) -> None:
    """Three awareness episodes + an idle event must produce a session
    page that the injector can subsequently retrieve."""
    s = stack
    vault_root = s["vault_root"]
    base = s["clock_holder"][0]

    # Seed three episodes and pretend the session started 90 min ago.
    s["rollup"]._session_start_ns = base - 90 * NS_PER_MIN    # noqa: SLF001
    for i in range(3):
        await _seed_episode(
            s["recall"],
            started_at_ns=base - (60 - i * 15) * NS_PER_MIN,
            summary=f"window-switch episode {i} about wiki memory rebuild",
            app="code.exe",
        )

    # Fire an IdleEntered well above the 2-min threshold.
    await s["bus"].publish(IdleEntered(idle_since_ns=base - 5 * NS_PER_MIN))
    # The handler awaits flush_session() synchronously, so it has landed
    # by the time publish() returns -- one short sleep is enough for
    # any file-system flush noise.
    await asyncio.sleep(0.05)

    sessions = list((vault_root / "sessions").glob("*.md"))
    assert len(sessions) == 1, "rollup should have produced exactly one session page"

    # The injector finds the session page (the rollup paragraph mentions
    # "wiki memory rebuild" via wikilinks).
    augmented = await s["injector"].maybe_inject(
        user_text="Was haben wir letzte Session am Wiki gemacht?",
        system_prompt="You are Personal Jarvis.",
    )
    assert "Wiki context" in augmented, (
        "the freshly-written session page should be retrievable via the injector"
    )


# ===========================================================================
# Case 3 -- Smalltalk: 5 hallo-turns produce no wiki pages
# ===========================================================================


@pytest.mark.asyncio
async def test_smalltalk_burst_produces_no_pages(stack) -> None:
    """Five short greeting turns must NOT leak into the wiki."""
    s = stack
    vault_root = s["vault_root"]

    # The curator's LLM is patched but we expect it never to be called
    # on these too-short inputs.
    with patch.object(
        s["curator"]._llm, "propose_updates", return_value=[],
    ) as proposer:
        for _ in range(5):
            await _drive_voice_turn(
                s["bus"],
                user_text="hallo",                    # 5 chars -- below 30
                brain_text="hi!",
            )

    assert list((vault_root / "entities").glob("*.md")) == []
    assert not proposer.called, (
        "trivial greetings must never reach the curator (LLM-call protection)"
    )


# ===========================================================================
# Case 4 -- Ack path isolated
# ===========================================================================


@pytest.mark.asyncio
async def test_ack_path_alone_creates_page(stack) -> None:
    """Brain explicitly acks -> ack path fires regardless of utterance length."""
    s = stack
    vault_root = s["vault_root"]

    sam_update = PageUpdate(
        target_path=vault_root / "entities" / "sam.md",
        operation="create",
        new_body=_entity_body("sam", "Sam wurde 1976 geboren."),
        reason="ack-path fact",
    )

    with patch.object(
        s["curator"]._llm, "propose_updates", return_value=[sam_update],
    ):
        await _drive_voice_turn(
            s["bus"],
            user_text="Sam wurde 1976 geboren.",   # 26 chars -- below aggressive min
            brain_text="Notiert.",                     # explicit ack keyword
        )

    assert (vault_root / "entities" / "sam.md").is_file()
    assert telemetry.get("voice_turns_ingested_ack") == 1
    assert telemetry.get("voice_turns_ingested_aggressive") == 0


# ===========================================================================
# Case 5 -- Aggressive path isolated
# ===========================================================================


@pytest.mark.asyncio
async def test_aggressive_path_alone_creates_page(stack) -> None:
    """Brain replies conversationally -> aggressive path fires on long-enough text."""
    s = stack
    vault_root = s["vault_root"]

    carlos_update = PageUpdate(
        target_path=vault_root / "entities" / "carlos.md",
        operation="create",
        new_body=_entity_body(
            "carlos",
            "Carlos went out for Pad Thai with Alex on 2026-05-14.",
        ),
        reason="aggressive-path fact",
    )

    with patch.object(
        s["curator"]._llm, "propose_updates", return_value=[carlos_update],
    ):
        await _drive_voice_turn(
            s["bus"],
            user_text="Ich war heute mit Carlos Pad-Thai essen, war richtig lecker.",
            brain_text="Klingt gut, wo wart ihr?",
        )

    assert (vault_root / "entities" / "carlos.md").is_file()
    assert telemetry.get("voice_turns_ingested_aggressive") == 1
    assert telemetry.get("voice_turns_ingested_ack") == 0


# ===========================================================================
# Case 6 -- Voice-path latency: bridge handlers must return promptly
# ===========================================================================


@pytest.mark.asyncio
async def test_voice_path_handlers_do_not_block_voice(stack) -> None:
    """``_on_transcript_final`` + ``_on_response_generated`` together must
    return well within the voice-path budget. Ingest itself can take
    seconds; the handlers must just spawn a background task and exit."""
    s = stack
    vault_root = s["vault_root"]

    update = PageUpdate(
        target_path=vault_root / "entities" / "alex.md",
        operation="create",
        new_body=_entity_body("alex", "Test latency fact."),
        reason="latency test",
    )

    async def _slow_propose(*_args, **_kwargs):
        # Pretend the curator-LLM takes 300 ms. This must NOT show up
        # in the bridge handler latency budget.
        await asyncio.sleep(0.3)
        return [update]

    with patch.object(
        s["curator"]._llm, "propose_updates", side_effect=_slow_propose,
    ):
        t0 = time.monotonic_ns()
        await s["bus"].publish(TranscriptFinal(
            transcript=Transcript(
                text="Mein Lieblings-Test ist dieser hier mit ausreichend Zeichen.",
                language="de",
                confidence=0.95,
            ),
            timestamp_ns=int(time.time_ns()),
        ))
        await s["bus"].publish(ResponseGenerated(
            text="Klingt spannend, erzaehl mehr.", language="de",
        ))
        elapsed_ms = (time.monotonic_ns() - t0) / 1_000_000

    assert elapsed_ms < 50, (
        f"voice-bridge bus handlers blocked for {elapsed_ms:.1f}ms "
        f"(budget 50ms) -- this is the contract for the voice path"
    )

    # And to prove the ingest still runs eventually:
    for _ in range(20):
        if (vault_root / "entities" / "alex.md").exists():
            break
        await asyncio.sleep(0.05)
    assert (vault_root / "entities" / "alex.md").exists(), (
        "the slow ingest must still complete in the background"
    )


# ===========================================================================
# Case 7 -- Curator failure path
# ===========================================================================


@pytest.mark.asyncio
async def test_curator_failure_does_not_break_voice_path(stack) -> None:
    """A raising curator-LLM must NOT propagate to the voice handlers."""
    s = stack
    vault_root = s["vault_root"]

    with patch.object(
        s["curator"]._llm, "propose_updates",
        side_effect=RuntimeError("synthetic curator failure"),
    ):
        # The voice turn must complete without raising.
        await _drive_voice_turn(
            s["bus"],
            user_text="Mein Lieblingsfilm ist Inception und das ist ein langer Satz.",
            brain_text="Klingt cool, mag ich auch.",
        )

    # No page landed (the failure was real)...
    assert list((vault_root / "entities").glob("*.md")) == []
    # ...but the bridge handlers returned cleanly. We prove that by
    # firing another turn and asserting the bridge still works.
    rescue_update = PageUpdate(
        target_path=vault_root / "entities" / "alex.md",
        operation="create",
        new_body=_entity_body("alex", "Recovery turn after curator crash."),
        reason="recovery",
    )
    with patch.object(
        s["curator"]._llm, "propose_updates", return_value=[rescue_update],
    ):
        await _drive_voice_turn(
            s["bus"],
            user_text="Test ob das System nach dem Crash noch funktioniert.",
            brain_text="Klar, alles laeuft.",
        )
    assert (vault_root / "entities" / "alex.md").is_file()


# ===========================================================================
# Case 8 -- Rollup failure path
# ===========================================================================


@pytest.mark.asyncio
async def test_rollup_failure_does_not_break_voice_path(stack) -> None:
    """A raising rollup brain must NOT prevent the voice bridge from working."""
    s = stack
    vault_root = s["vault_root"]
    base = s["clock_holder"][0]

    # Swap the rollup's brain for one that always raises.
    s["rollup"]._brain = None        # noqa: SLF001 -- force re-instantiate
    s["rollup"]._registry = _SpyRegistry(    # noqa: SLF001
        _FakeStreamBrain(raise_exc=RuntimeError("rollup brain down")),
    )

    # Seed two episodes so the rollup tries to run.
    s["rollup"]._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            s["recall"],
            started_at_ns=base - (30 - i * 10) * NS_PER_MIN,
            summary=f"episode {i}",
        )
    # Trigger the rollup. Expect status=llm_failure, no session md.
    result = await s["rollup"].flush_session()
    assert result.status == "llm_failure"
    assert list((vault_root / "sessions").glob("*.md")) == []

    # Voice path still works:
    fact_update = PageUpdate(
        target_path=vault_root / "entities" / "alex.md",
        operation="create",
        new_body=_entity_body("alex", "Voice path survives rollup crash."),
        reason="resilience proof",
    )
    with patch.object(
        s["curator"]._llm, "propose_updates", return_value=[fact_update],
    ):
        await _drive_voice_turn(
            s["bus"],
            user_text="Test ob die Voice-Pipeline trotz Rollup-Crash noch laeuft.",
            brain_text="Klar, antworte ich.",
        )
    assert (vault_root / "entities" / "alex.md").is_file()


# ===========================================================================
# Case 9 -- Race: voice turn while rollup is running
# ===========================================================================


@pytest.mark.asyncio
async def test_voice_turn_during_rollup_both_finish(stack) -> None:
    """Driving a voice turn concurrently with a rollup must let both finish."""
    s = stack
    vault_root = s["vault_root"]
    base = s["clock_holder"][0]

    # Seed rollup episodes.
    s["rollup"]._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            s["recall"],
            started_at_ns=base - (30 - i * 10) * NS_PER_MIN,
            summary=f"episode {i} about rebuild work",
        )

    voice_update = PageUpdate(
        target_path=vault_root / "entities" / "alex.md",
        operation="create",
        new_body=_entity_body("alex", "Concurrent voice fact during rollup."),
        reason="race test",
    )

    with patch.object(
        s["curator"]._llm, "propose_updates", return_value=[voice_update],
    ):
        # Run both at the same time. Each AtomicWriter call is async-locked
        # internally so they serialise -- the assertion is that BOTH
        # complete cleanly without raising.
        rollup_task = asyncio.create_task(s["rollup"].flush_session())
        voice_task = asyncio.create_task(_drive_voice_turn(
            s["bus"],
            user_text="Ein Fakt mitten im Rollup, gleichzeitig ausgeloest.",
            brain_text="Verstanden, bemerkt.",
        ))
        rollup_result, _ = await asyncio.gather(rollup_task, voice_task)

    assert rollup_result.status == "ok", (
        f"rollup must finish cleanly under concurrent voice load, "
        f"got status={rollup_result.status}"
    )
    # Both pages on disk:
    assert (vault_root / "entities" / "alex.md").is_file(), (
        "voice fact must land even when a rollup is running"
    )
    assert list((vault_root / "sessions").glob("*.md")), (
        "rollup must produce its session page despite concurrent voice ingest"
    )


# ===========================================================================
# Case 10 -- Recovery: fresh triggers see persisted state on restart
# ===========================================================================


@pytest.mark.asyncio
async def test_fresh_triggers_recover_existing_vault_state(stack) -> None:
    """After tearing down + reconstructing the triggers, the vault state
    persists -- the injector finds pages a previous run wrote."""
    s = stack
    vault_root = s["vault_root"]

    # 1) First "run" writes a page through the bridge.
    update = PageUpdate(
        target_path=vault_root / "entities" / "alex.md",
        operation="create",
        new_body=_entity_body(
            "alex",
            "Alex built the three-trigger regression suite. Coffee with milk.",
            facts=["Lieblingsgetraenk: Cafe Latte"],
        ),
        reason="pre-restart fact",
    )
    with patch.object(s["curator"]._llm, "propose_updates", return_value=[update]):
        await _drive_voice_turn(
            s["bus"],
            user_text="Mein Lieblingsgetraenk ist Cafe Latte mit Hafermilch.",
            brain_text="Schoen zu wissen.",
        )
    assert (vault_root / "entities" / "alex.md").is_file()

    # 2) "Restart": stop the bridge / rollup, build fresh equivalents
    #    against the SAME tmp vault.
    s["bridge"].stop()
    await s["rollup"].stop()

    fresh_search = VaultSearch(vault_root)
    fresh_injector = WikiContextInjector(
        search=fresh_search, max_chars=1500,
        latency_budget_ms=500, min_keyword_length=4,
    )

    # 3) Fresh injector must surface the pre-restart fact -- that is the
    #    operational contract of "restart-safe memory".
    augmented = await fresh_injector.maybe_inject(
        user_text="Was war nochmal mein Lieblingsgetraenk?",
        system_prompt="You are Personal Jarvis.",
    )
    assert "Wiki context" in augmented, "fresh injector must see persisted pages"
    assert "Cafe Latte" in augmented or "Latte" in augmented, (
        "the pre-restart fact must round-trip through disk"
    )
