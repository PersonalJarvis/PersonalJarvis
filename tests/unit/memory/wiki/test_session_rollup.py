"""Unit tests for :class:`SessionRollupWorker` (Phase B7).

The worker is a thin orchestrator over real components (RecallStore,
AtomicWriter, LogWriter, BrainProviderRegistry). We replace the brain
call with a fake (the only thing we don't want hitting the network in
CI) and keep everything else real-on-tmpfs so the tests exercise the
full write / archive / log path.
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

from jarvis.core.bus import EventBus
from jarvis.core.config import load_config
from jarvis.core.events import IdleEntered
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.recall import RecallStore
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.session_rollup import (
    SessionRollupResult,
    SessionRollupWorker,
)


# ----------------------------------------------------------------------------
# Streaming-aware FakeBrain — mirrors the protocol shape used in production.
# ``complete`` is an async generator; ``brain.complete(req)`` returns the
# generator object, which the worker hands to ``aggregate``.
# ----------------------------------------------------------------------------


class _FakeBrain:
    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(
        self,
        response_text: str,
        *,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.response_text = response_text
        self.raise_exc = raise_exc
        self.received_requests: list[BrainRequest] = []
        self.call_count = 0

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.received_requests.append(req)
        self.call_count += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        yield BrainDelta(content=self.response_text)
        yield BrainDelta(finish_reason="stop", usage={"input_tokens": 10, "output_tokens": 20})

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------

NS_PER_MIN = 60 * 1_000_000_000


@pytest_asyncio.fixture
async def worker_stack(tmp_path: Path):
    """Build a real B7 stack against an on-disk vault + SQLite + fake brain."""
    vault_root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub\n", encoding="utf-8")
    (vault_root / "index.md").write_text(
        "# Index\n\n## Entities\n\n(empty)\n", encoding="utf-8"
    )
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    # Seed the durable hub pages the session worker links into. A real vault
    # always has a user entity and at least one active project; the session
    # rollup demotes links to pages that do NOT exist, so these must be
    # present for the fake-brain's [[entities/alex]] /
    # [[projects/wiki-memory-rebuild]] references to survive as graph edges.
    (vault_root / "entities" / "alex.md").write_text(
        "---\ntype: entity\nslug: alex\naliases: [Alex, the user]\n---\n"
        "# Alex\n\n## Summary\nThe user.\n",
        encoding="utf-8",
    )
    (vault_root / "projects" / "wiki-memory-rebuild.md").write_text(
        "---\ntype: project\nslug: wiki-memory-rebuild\nstatus: active\n---\n"
        "# Wiki Memory Rebuild\n\n## Goal\nRebuild the memory tier.\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "jarvis.db"
    recall = RecallStore(db_path)
    await recall.open()

    repo = MarkdownPageRepository()
    backup_dir = tmp_path / "backups"
    writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
    log_writer = LogWriter(log_path=vault_root / "log.md")
    bus = EventBus()
    config = load_config()

    # Deterministic clock — t=base. Each test sets the value. The
    # baseline is on 2026-06-15 so the worker's rendered session pages
    # (named ``YYYY-MM-DD-<id>.md``) sort lexically newer than the
    # hand-placed older fixtures used in the archive-window test.
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

    # D2 (2026-06): the awareness-episode session-page feed is retired and
    # gated off by default (wiki_write_enabled=False). These tests exercise
    # the legacy write machinery itself, so they opt back in explicitly.
    # The default-off behaviour is pinned by test_d2_no_session_page_feed.py.
    worker._cfg = worker._cfg.model_copy(update={"wiki_write_enabled": True})  # noqa: SLF001

    # Replace the brain provider so we never hit the network. The fake
    # always streams a fixed paragraph as a BrainDelta sequence — same
    # shape as a real provider's async generator.
    fake_brain = _FakeBrain(
        "User worked across [[entities/alex]] context with several "
        "[[projects/wiki-memory-rebuild]] iterations. Main decisions: "
        "settle on Karpathy pattern, ship Wave 2 integration, defer "
        "B5 to next session. Open thread: B7 itself."
    )
    worker._registry.instantiate = MagicMock(return_value=fake_brain)    # noqa: SLF001

    yield worker, recall, vault_root, clock_holder, fake_brain

    await recall.close()


async def _seed_episode(
    recall: RecallStore,
    *,
    started_at_ns: int,
    summary: str,
    primary_app: str = "code.exe",
) -> int:
    """Insert one awareness episode and return the row id."""
    return await recall.record_episode(
        started_at_ns=started_at_ns,
        ended_at_ns=started_at_ns + NS_PER_MIN,
        trigger_kind="window_switch",
        summary=summary,
        frame_count=3,
        primary_app=primary_app,
    )


# ----------------------------------------------------------------------------
# Public-API shape
# ----------------------------------------------------------------------------


def test_initial_session_start_matches_clock(worker_stack):
    worker, _recall, _vault, clock_holder, _brain = worker_stack
    assert worker.session_start_ns == clock_holder[0]


@pytest.mark.asyncio
async def test_disabled_worker_returns_disabled_immediately(worker_stack):
    """When config.enabled=False, flush_session is a no-op."""
    worker, _recall, _vault, _clock, _brain = worker_stack
    worker._cfg = worker._cfg.model_copy(update={"enabled": False})    # noqa: SLF001
    result = await worker.flush_session()
    assert result.status == "disabled"


# ----------------------------------------------------------------------------
# Trigger logic
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_below_threshold_does_not_flush(worker_stack):
    """A 30-minute idle event must not trigger flush (threshold default 120min)."""
    worker, recall, _vault, clock_holder, _brain = worker_stack
    base = clock_holder[0]
    await _seed_episode(recall, started_at_ns=base - 50 * NS_PER_MIN, summary="a")
    await _seed_episode(recall, started_at_ns=base - 40 * NS_PER_MIN, summary="b")

    clock_holder[0] = base
    event = IdleEntered(idle_since_ns=base - 30 * NS_PER_MIN)

    await worker._on_idle_entered(event)    # noqa: SLF001

    sessions = list((worker._vault_root / "sessions").glob("*.md"))    # noqa: SLF001
    assert sessions == []


@pytest.mark.asyncio
async def test_idle_above_threshold_flushes_session(worker_stack):
    """An idle event whose idle-duration exceeds the threshold triggers a flush."""
    worker, recall, vault_root, clock_holder, brain = worker_stack
    base = clock_holder[0]
    # Simulate "the session started 4h ago" so the seeded episodes lie
    # inside the active session window.
    worker._session_start_ns = base - 240 * NS_PER_MIN    # noqa: SLF001
    await _seed_episode(recall, started_at_ns=base - 60 * NS_PER_MIN, summary="ep1 morning")
    await _seed_episode(recall, started_at_ns=base - 30 * NS_PER_MIN, summary="ep2 noon")
    await _seed_episode(recall, started_at_ns=base - 10 * NS_PER_MIN, summary="ep3 just before idle")

    # Idle started 150 min ago — well above the 120-min default.
    event = IdleEntered(idle_since_ns=base - 150 * NS_PER_MIN)
    await worker._on_idle_entered(event)    # noqa: SLF001

    sessions = list((vault_root / "sessions").glob("*.md"))
    assert len(sessions) == 1
    assert brain.call_count == 1


# ----------------------------------------------------------------------------
# Empty / too-few session handling
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_with_too_few_episodes_is_skipped(worker_stack):
    """One-episode "session" → no LLM call, no page, status reports the skip.

    Pins the SKIP branch independent of the production tuning of
    ``min_episodes_for_rollup`` (set to 1 in jarvis.toml 2026-05-17 to
    raise throughput).  The fixture's load_config() picks up that
    production value, so we override the worker's config here to keep
    the SKIP branch testable.
    """
    worker, recall, vault_root, clock_holder, brain = worker_stack
    worker._cfg.min_episodes_for_rollup = 2  # noqa: SLF001 — pin for this case
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    await _seed_episode(recall, started_at_ns=base - 10 * NS_PER_MIN, summary="lonely episode")

    result = await worker.flush_session()
    assert result.status == "skipped_too_few_episodes"
    assert result.episode_count == 1
    assert list((vault_root / "sessions").glob("*.md")) == []
    assert brain.call_count == 0


@pytest.mark.asyncio
async def test_skip_still_advances_session_start(worker_stack):
    """Skipping must reset session_start so the next batch starts fresh."""
    worker, recall, _vault, clock_holder, _brain = worker_stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    initial_start = worker.session_start_ns
    await _seed_episode(recall, started_at_ns=base - 5 * NS_PER_MIN, summary="ep")

    clock_holder[0] = base + 10 * NS_PER_MIN
    await worker.flush_session()
    assert worker.session_start_ns == clock_holder[0]
    assert worker.session_start_ns != initial_start


# ----------------------------------------------------------------------------
# Happy path: page contents
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_writes_schema_compliant_session_page(worker_stack):
    worker, recall, vault_root, clock_holder, _brain = worker_stack
    base = clock_holder[0]
    worker._session_start_ns = base - 90 * NS_PER_MIN    # noqa: SLF001
    for i in range(3):
        await _seed_episode(
            recall,
            started_at_ns=base - (50 - i * 10) * NS_PER_MIN,
            summary=f"episode {i+1}",
        )

    result = await worker.flush_session()
    assert result.status == "ok"
    assert result.episode_count == 3
    assert result.page_path is not None
    assert result.page_path.is_file()

    content = result.page_path.read_text(encoding="utf-8")
    assert "type: session" in content
    assert "session_id:" in content
    assert "episode_ids:" in content
    assert "[[projects/wiki-memory-rebuild]]" in content    # from the fake brain output


@pytest.mark.asyncio
async def test_graph_connectivity_postprocesses_links(worker_stack):
    """Session pages must join the graph, not scatter.

    The rendered page must:
      * demote a link to a non-existent ephemeral app to plain text
        (no ``[[PowerShell]]`` ghost node),
      * keep a link to an existing durable page as a resolvable edge,
      * strip a token-truncated ``[[…`` fragment,
      * carry a deterministic ``## Related`` backbone linking the user
        entity and the active project so the session is wired into the
        existing dense cluster.
    """
    worker, recall, vault_root, clock_holder, _brain = worker_stack
    # Swap in a brain whose paragraph mixes a ghost app, a resolvable hub
    # reference, and a truncated trailing fragment.
    ghost_brain = _FakeBrain(
        "Worked in [[projects/wiki-memory-rebuild]] and ran [[PowerShell]] "
        "scripts, then opened [[Snipping Tool"
    )
    worker._registry.instantiate = MagicMock(return_value=ghost_brain)    # noqa: SLF001
    worker._brain = None    # noqa: SLF001

    base = clock_holder[0]
    worker._session_start_ns = base - 90 * NS_PER_MIN    # noqa: SLF001
    for i in range(3):
        await _seed_episode(
            recall, started_at_ns=base - (50 - i * 10) * NS_PER_MIN,
            summary=f"episode {i}", primary_app="powershell.exe",
        )

    result = await worker.flush_session()
    assert result.status == "ok"
    content = result.page_path.read_text(encoding="utf-8")

    # Ghost app demoted to plain text — no orphan node.
    assert "[[PowerShell]]" not in content
    assert "PowerShell" in content
    # Truncated fragment stripped — no dangling "[[".
    assert "[[Snipping Tool" not in content
    assert "Snipping Tool" in content
    # Resolvable durable link survives as a real edge.
    assert "[[projects/wiki-memory-rebuild]]" in content
    # Deterministic backbone footer wires the session to the user hub.
    assert "## Related" in content
    assert "[[entities/alex]]" in content


@pytest.mark.asyncio
async def test_log_entry_links_durable_hubs(worker_stack):
    """The log.md entry's pages-touched must include the durable hubs the
    session linked, not just the session's own self-reference — so log-driven
    backlinks also pull the session into the network.
    """
    worker, recall, vault_root, clock_holder, _brain = worker_stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            recall, started_at_ns=base - (30 - i * 10) * NS_PER_MIN, summary=f"ep{i}",
        )

    await worker.flush_session()
    log_content = (vault_root / "log.md").read_text(encoding="utf-8")
    assert "[[entities/alex]]" in log_content


@pytest.mark.asyncio
async def test_happy_path_appends_log_entry(worker_stack):
    worker, recall, vault_root, clock_holder, _brain = worker_stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            recall,
            started_at_ns=base - (30 - i * 10) * NS_PER_MIN,
            summary=f"ep{i}",
        )

    await worker.flush_session()

    log_content = (vault_root / "log.md").read_text(encoding="utf-8")
    assert "session rollup" in log_content
    assert "[[sessions/" in log_content


# ----------------------------------------------------------------------------
# Rolling window / archive
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_active_sessions_cap_archives_oldest(worker_stack):
    """When we have 6 sessions on disk and cap=5, the oldest moves to _archive."""
    worker, recall, vault_root, clock_holder, _brain = worker_stack
    sessions_dir = vault_root / "sessions"

    # Plant five existing session files (already on disk, with date-sortable
    # filenames so the worker's archive logic picks the oldest deterministically).
    for i in range(5):
        (sessions_dir / f"2026-05-0{i+1}-old00000.md").write_text(
            f"---\ntype: session\nsession_id: old0000{i}\n---\nold\n",
            encoding="utf-8",
        )

    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            recall, started_at_ns=base - (30 - i * 10) * NS_PER_MIN, summary=f"ep{i}",
        )

    result = await worker.flush_session()
    assert result.status == "ok"
    assert len(result.archived) == 1
    # The oldest file (2026-05-01-...) is the one archived.
    assert result.archived[0].name.startswith("2026-05-01-")
    assert (vault_root / "_archive" / "sessions" / result.archived[0].name).is_file()
    # Active sessions count is now exactly the cap.
    assert len(list(sessions_dir.glob("*.md"))) == 5


# ----------------------------------------------------------------------------
# LLM failure modes
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archived_sessions_are_purged_from_fts_index(worker_stack):
    """Rolling-window archiving renames session files directly (bypassing the
    AtomicWriter), so the worker must explicitly purge their FTS rows — else
    search keeps returning ghost hits at the old ``sessions/<name>.md`` path.
    """
    worker, recall, vault_root, clock_holder, _brain = worker_stack
    sessions_dir = vault_root / "sessions"
    for i in range(5):
        (sessions_dir / f"2026-05-0{i+1}-old00000.md").write_text(
            f"---\ntype: session\nsession_id: old0000{i}\n---\nold\n",
            encoding="utf-8",
        )
    # Spy on the FTS purge so we assert the wiring without touching a real DB.
    worker._writer.forget_paths = MagicMock()    # noqa: SLF001

    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            recall, started_at_ns=base - (30 - i * 10) * NS_PER_MIN, summary=f"ep{i}",
        )

    result = await worker.flush_session()
    assert result.status == "ok"
    assert len(result.archived) == 1

    worker._writer.forget_paths.assert_called_once()    # noqa: SLF001
    purged = worker._writer.forget_paths.call_args.args[0]    # noqa: SLF001
    # The purged path is the ORIGINAL live location, not the _archive/ dest.
    assert purged == [sessions_dir / "2026-05-01-old00000.md"]


@pytest.mark.asyncio
async def test_llm_timeout_reports_failure_status(worker_stack):
    worker, recall, vault_root, clock_holder, brain = worker_stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            recall, started_at_ns=base - (30 - i * 10) * NS_PER_MIN, summary=f"ep{i}",
        )

    # Patch wait_for to raise immediately to avoid waiting cfg.timeout_s.
    # ``brain.complete`` itself is left untouched — the timeout simulates
    # the worker's outer ``asyncio.wait_for`` firing before the stream
    # completes.
    with patch("jarvis.memory.wiki.session_rollup.asyncio.wait_for", side_effect=asyncio.TimeoutError):
        result = await worker.flush_session()

    assert result.status == "llm_failure"
    assert list((vault_root / "sessions").glob("*.md")) == []


@pytest.mark.asyncio
async def test_llm_returns_empty_text_reports_failure(worker_stack):
    worker, recall, vault_root, clock_holder, brain = worker_stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    for i in range(2):
        await _seed_episode(
            recall, started_at_ns=base - (30 - i * 10) * NS_PER_MIN, summary=f"ep{i}",
        )

    # Swap in a brain that streams an empty content delta — the worker
    # must treat that as "no output" and fail closed, not write a
    # zero-byte session page.
    empty_brain = _FakeBrain("")
    worker._registry.instantiate = MagicMock(return_value=empty_brain)    # noqa: SLF001
    worker._brain = None    # noqa: SLF001 — force re-instantiation
    result = await worker.flush_session()
    assert result.status == "llm_failure"
    assert list((vault_root / "sessions").glob("*.md")) == []


# ----------------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_is_idempotent_when_subscribed(worker_stack):
    worker, _recall, _vault, _clock, _brain = worker_stack
    await worker.start()
    await worker.start()
    assert worker._subscribed is True    # noqa: SLF001


@pytest.mark.asyncio
async def test_disabled_worker_does_not_subscribe(worker_stack):
    worker, _recall, _vault, _clock, _brain = worker_stack
    worker._cfg = worker._cfg.model_copy(update={"enabled": False})    # noqa: SLF001
    await worker.start()
    assert worker._subscribed is False    # noqa: SLF001
