"""Memory triggers regression suite -- context injection plus session rollup.

Two wiki triggers must stay green together against one shared tmp-vault:

1. :class:`WikiContextInjector` -- runs on every brain turn and prepends
   matching vault snippets to the system prompt (LLM-free).
2. :class:`SessionRollupWorker` -- listens for ``IdleEntered`` and rolls
   awareness episodes into a session markdown page. Retired by default
   (``wiki_write_enabled`` off, no built-in ``IdleEntered`` publisher); this
   suite opts back in to keep the code path honest.

The former third trigger, the voice bridge's automatic review of every user
turn, was removed on 2026-09-30; explicit saves are pinned in
``tests/unit/memory/wiki/test_conversation_observer.py``.

* AP-5 from B5: no SQLite mocking. :class:`RecallStore` runs against a real
  on-disk SQLite file.
* The rollup worker's brain is a small private :class:`_FakeStreamBrain`.
* Telemetry is reset before each test so counters reflect the case.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from jarvis.brain.wiki_context import WikiContextInjector
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig, SessionRollupConfig
from jarvis.core.events import IdleEntered
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.recall import RecallStore
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.fts_index import rebuild_index
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.search import VaultSearch
from jarvis.memory.wiki.session_rollup import SessionRollupWorker
from jarvis.memory.wiki.telemetry import telemetry
from jarvis.memory.wiki.vault_index import VaultIndex

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

    bus = EventBus()
    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)
    writer = AtomicWriter(vault_root=vault_root, backup_dir=tmp_path / "backups")
    log_writer = LogWriter(log_path=vault_root / "log.md")

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
            "[[entities/ruben]] preferences and [[projects/wiki-memory]] iterations."
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

    # The injector reads the FTS index, fed in production by the boot
    # reconcile and the vault watcher; neither runs here, so tests re-run
    # rebuild_index (the boot step) before every injector expectation.
    fts_conn = sqlite3.connect(":memory:", check_same_thread=False)
    rebuild_index(vault_root, fts_conn)
    search = VaultSearch(vault_root, conn=fts_conn)
    injector = WikiContextInjector(
        search=search, max_chars=1500,
        latency_budget_ms=500,  # generous; tmpfs is fast
        min_keyword_length=4,
    )

    telemetry.reset()

    yield {
        "vault_root": vault_root,
        "bus": bus,
        "rollup": rollup,
        "registry": registry,
        "fake_rollup_brain": fake_rollup_brain,
        "recall": recall,
        "clock_holder": clock_holder,
        "injector": injector,
        "fts_conn": fts_conn,
    }

    await rollup.stop()
    await recall.close()


# ===========================================================================
# Case 1 -- Rollup-Path: episodes -> idle -> session page -> visible to injector
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
    rebuild_index(s["vault_root"], s["fts_conn"])  # the boot/watcher index step
    augmented = await s["injector"].maybe_inject(
        user_text="Was haben wir letzte Session am Wiki gemacht?",
        system_prompt="You are Personal Jarvis.",
    )
    assert "Wiki context" in augmented, (
        "the freshly-written session page should be retrievable via the injector"
    )


# ===========================================================================
# Case 2 -- A failing rollup brain is contained
# ===========================================================================


@pytest.mark.asyncio
async def test_rollup_failure_reports_llm_failure_and_writes_nothing(stack) -> None:
    s = stack
    vault_root = s["vault_root"]
    base = s["clock_holder"][0]

    s["rollup"]._brain = None        # noqa: SLF001 -- force re-instantiate
    s["rollup"]._registry = _SpyRegistry(    # noqa: SLF001
        _FakeStreamBrain(raise_exc=RuntimeError("rollup brain down")),
    )
    s["rollup"]._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            s["recall"],
            started_at_ns=base - (30 - i * 10) * NS_PER_MIN,
            summary=f"episode {i}",
        )

    result = await s["rollup"].flush_session()
    assert result.status == "llm_failure"
    assert list((vault_root / "sessions").glob("*.md")) == []


# ===========================================================================
# Case 3 -- The retired rollup can never bill a key in subscription mode
# ===========================================================================


@pytest.mark.asyncio
async def test_rollup_skips_a_keyed_provider_in_subscription_mode(
    stack, background_billing,  # noqa: ANN001 - fixture from conftest
) -> None:
    s = stack
    base = s["clock_holder"][0]
    background_billing.remember_subscription("claude-cli")
    background_billing.sign_out("claude-cli")

    s["rollup"]._brain = None        # noqa: SLF001 -- force re-instantiate
    s["rollup"]._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            s["recall"],
            started_at_ns=base - (30 - i * 10) * NS_PER_MIN,
            summary=f"episode {i}",
        )

    result = await s["rollup"].flush_session()
    assert result.status == "llm_failure"
    assert s["registry"].instantiate_calls == [], "no keyed provider may be built"
    assert s["fake_rollup_brain"].call_count == 0
    assert background_billing.calls_today == 0
