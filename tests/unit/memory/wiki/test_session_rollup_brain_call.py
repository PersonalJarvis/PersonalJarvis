"""Regression tests for the SessionRollupWorker Brain-API call shape (B8.1+B8.2).

These tests pin the **call shape** between the rollup worker and the Brain
protocol. They fail loudly the moment a future refactor reverts to the
old positional / kwarg-based signature that broke production from
2026-05-13 onward (``GeminiBrain.complete() got an unexpected keyword
argument 'max_tokens'`` — see BUGS.md B8).

The fake matches the production contract from ``jarvis/core/protocols.py``:

* ``complete`` is an ``async def`` generator yielding ``BrainDelta`` objects.
* The worker is required to pass exactly one positional ``BrainRequest``,
  no kwargs.

Test surface:

1. Happy path — worker passes one ``BrainRequest`` and aggregates the
   stream into the rendered session page.
2. Empty stream — the brain finishes without yielding any text content,
   the worker returns ``llm_failure`` and writes no page.
3. Outer timeout — ``asyncio.wait_for`` fires before the stream
   completes, the worker maps it to ``llm_failure``.
4. Stream-internal exception — the brain raises mid-stream, the worker
   logs and returns ``llm_failure``.
5. Provider instantiation failure — the registry raises, the worker
   never reaches the request.
6. ``max_tokens`` propagation — the worker copies
   ``cfg.max_output_tokens`` into ``BrainRequest.max_tokens`` (the field
   that historically swallowed the wrong-shaped call).
7. Multi-chunk stream — text deltas concatenate cleanly in the
   aggregated summary.

The fixtures intentionally avoid ``unittest.mock.AsyncMock``: the
old-call-shape bug was masked for hours because an AsyncMock cheerfully
accepted any kwargs without raising. A real class with a typed
signature is what we want under us.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from jarvis.core.bus import EventBus
from jarvis.core.config import load_config
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.memory.recall import RecallStore
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.session_rollup import SessionRollupWorker


NS_PER_MIN = 60 * 1_000_000_000


# ----------------------------------------------------------------------
# Streaming-aware FakeBrain — strict signature, real async generator.
# Mirrors the FakeBrain in tests/unit/memory/wiki/test_curator_llm.py so
# the two B1/B7 worker test suites stay drift-free.
# ----------------------------------------------------------------------


class FakeBrain:
    """Records every ``BrainRequest`` the worker passes.

    The ``async def`` + ``yield`` shape produces a real async generator
    so calls without ``await`` (the production pattern is
    ``aggregate(brain.complete(req))``, no intermediate await) work as
    they do against a real provider.
    """

    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(
        self,
        deltas: list[BrainDelta] | None = None,
        *,
        raise_exc: BaseException | None = None,
        raise_after: int | None = None,
        sleep_s: float = 0.0,
    ) -> None:
        self._deltas = deltas if deltas is not None else [
            BrainDelta(content="default fake summary"),
        ]
        self._raise_exc = raise_exc
        self._raise_after = raise_after
        self._sleep_s = sleep_s
        self.received_requests: list[BrainRequest] = []
        self.call_count: int = 0

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        # Strict positional-only signature: a kwargs-based legacy call
        # like ``complete(prompt, max_tokens=...)`` raises TypeError
        # before any stream starts. This is the regression guard.
        self.received_requests.append(req)
        self.call_count += 1

        if self._sleep_s:
            await asyncio.sleep(self._sleep_s)

        if self._raise_exc is not None and self._raise_after is None:
            raise self._raise_exc

        for i, delta in enumerate(self._deltas):
            if self._raise_after is not None and i == self._raise_after:
                raise self._raise_exc or RuntimeError("synthetic stream failure")
            yield delta

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


# ----------------------------------------------------------------------
# Shared on-disk stack — same approach as the broader B7 test suite,
# stripped to what these call-shape tests need.
# ----------------------------------------------------------------------


class _SpyRegistry:
    """Lightweight stand-in for ``BrainProviderRegistry`` that hands out
    a pre-built brain and records every ``instantiate`` invocation.
    """

    def __init__(self, brain: Any, *, raise_exc: BaseException | None = None) -> None:
        self._brain = brain
        self._raise_exc = raise_exc
        self.instantiate_calls: list[tuple[str, dict[str, Any]]] = []

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.instantiate_calls.append((name, dict(kwargs)))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._brain


@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    """Build a real B7 stack against tmp_path with a swappable brain."""

    vault_root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    # Seed the user entity so the rollup's [[entities/the maintainer]] reference
    # resolves and survives post-processing as a real graph edge (links to
    # non-existent pages are demoted to plain text).
    (vault_root / "entities" / "the maintainer.md").write_text(
        "---\ntype: entity\nslug: the maintainer\naliases: [the maintainer]\n---\n# the maintainer\n\n## Summary\nThe user.\n",
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

    # Deterministic clock anchored on 2026-06-15 14:00 local time so the
    # rollup's filename sorts lexicographically newer than the canned
    # fixtures in the broader suite.
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

    yield worker, recall, vault_root, clock_holder

    await recall.close()


async def _seed_two_episodes(recall: RecallStore, base_ns: int) -> None:
    for i in range(2):
        await recall.record_episode(
            started_at_ns=base_ns - (30 - i * 10) * NS_PER_MIN,
            ended_at_ns=base_ns - (30 - i * 10) * NS_PER_MIN + NS_PER_MIN,
            trigger_kind="window_switch",
            summary=f"episode {i}",
            frame_count=2,
            primary_app="code.exe",
        )


def _attach_brain(worker: SessionRollupWorker, brain: Any, *, registry: _SpyRegistry | None = None) -> _SpyRegistry:
    reg = registry or _SpyRegistry(brain)
    worker._registry = reg    # noqa: SLF001 — test seam
    worker._brain = None      # noqa: SLF001 — force a fresh instantiate
    return reg


# ----------------------------------------------------------------------
# Case 1 — Happy path: one BrainRequest, stream aggregated into the page
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brain_called_with_single_brainrequest_argument(stack):
    """The worker must pass exactly one positional ``BrainRequest`` and
    no kwargs. Regression guard against the
    ``complete(prompt, max_tokens=...)`` pattern from BUG-B8.
    """

    worker, recall, vault_root, clock_holder = stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    await _seed_two_episodes(recall, base)

    brain = FakeBrain([BrainDelta(content="Productive session about [[concepts/wiki-memory]] iterations.")])
    _attach_brain(worker, brain)

    result = await worker.flush_session()

    assert result.status == "ok"
    assert brain.call_count == 1
    assert len(brain.received_requests) == 1
    req = brain.received_requests[0]
    assert isinstance(req, BrainRequest)
    assert isinstance(req.messages, tuple)
    assert len(req.messages) == 1
    assert isinstance(req.messages[0], BrainMessage)
    assert req.messages[0].role == "user"
    assert "Episodes (chronological)" in req.messages[0].content


# ----------------------------------------------------------------------
# Case 2 — Empty stream → llm_failure, no page written
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_stream_yields_llm_failure(stack):
    """Brain finishes without emitting any text content. The worker
    must NOT write a zero-content session page; it must report
    ``llm_failure`` instead.
    """

    worker, recall, vault_root, clock_holder = stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    await _seed_two_episodes(recall, base)

    # Only a finish_reason delta — no text content at all.
    brain = FakeBrain([BrainDelta(finish_reason="stop", usage={"output_tokens": 0})])
    _attach_brain(worker, brain)

    result = await worker.flush_session()

    assert result.status == "llm_failure"
    assert list((vault_root / "sessions").glob("*.md")) == []
    assert brain.call_count == 1


# ----------------------------------------------------------------------
# Case 3 — Outer asyncio.wait_for timeout
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outer_timeout_maps_to_llm_failure(stack, monkeypatch):
    """The worker's outer ``asyncio.wait_for`` cap fires before the
    stream finishes. The session_rollup module must catch that and
    return ``llm_failure``, not crash the caller.
    """

    worker, recall, vault_root, clock_holder = stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    await _seed_two_episodes(recall, base)

    brain = FakeBrain([BrainDelta(content="too slow")])
    _attach_brain(worker, brain)

    async def _instant_timeout(*_args, **_kwargs):
        raise asyncio.TimeoutError("synthetic timeout")

    monkeypatch.setattr(
        "jarvis.memory.wiki.session_rollup.asyncio.wait_for", _instant_timeout
    )

    result = await worker.flush_session()

    assert result.status == "llm_failure"
    assert list((vault_root / "sessions").glob("*.md")) == []


# ----------------------------------------------------------------------
# Case 4 — Stream-internal exception
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_exception_maps_to_llm_failure(stack):
    """The brain yields one chunk, then raises. The worker must catch
    the exception inside its ``except Exception`` arm and return
    ``llm_failure`` — never propagate to the caller, never write a
    half-baked page.
    """

    worker, recall, vault_root, clock_holder = stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    await _seed_two_episodes(recall, base)

    brain = FakeBrain(
        [BrainDelta(content="partial output before "), BrainDelta(content="(unreachable)")],
        raise_after=1,
        raise_exc=RuntimeError("provider blew up mid-stream"),
    )
    _attach_brain(worker, brain)

    result = await worker.flush_session()

    assert result.status == "llm_failure"
    assert list((vault_root / "sessions").glob("*.md")) == []


# ----------------------------------------------------------------------
# Case 5 — Provider instantiation failure
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_instantiate_failure_short_circuits(stack):
    """The registry raises on ``instantiate``. The worker logs and
    returns ``llm_failure`` without ever calling ``brain.complete``.
    """

    worker, recall, vault_root, clock_holder = stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    await _seed_two_episodes(recall, base)

    placeholder = FakeBrain([BrainDelta(content="never called")])
    failing_registry = _SpyRegistry(placeholder, raise_exc=KeyError("gemini not configured"))
    _attach_brain(worker, placeholder, registry=failing_registry)

    result = await worker.flush_session()

    assert result.status == "llm_failure"
    assert placeholder.call_count == 0
    assert len(failing_registry.instantiate_calls) == 1


# ----------------------------------------------------------------------
# Case 6 — max_tokens propagation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_tokens_propagates_into_brainrequest(stack):
    """``cfg.max_output_tokens`` must land on
    ``BrainRequest.max_tokens``. The historical bug shipped
    ``complete(prompt, max_tokens=cfg.max_output_tokens)`` which Gemini
    rejected — this guard pins the *field* the value belongs in now.
    """

    worker, recall, vault_root, clock_holder = stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    await _seed_two_episodes(recall, base)

    # Bump max_output_tokens to a recognisable non-default value.
    worker._cfg = worker._cfg.model_copy(update={"max_output_tokens": 1234})    # noqa: SLF001

    brain = FakeBrain([BrainDelta(content="ok")])
    _attach_brain(worker, brain)

    result = await worker.flush_session()
    assert result.status == "ok"
    assert brain.received_requests[0].max_tokens == 1234


# ----------------------------------------------------------------------
# Case 7 — Multi-chunk stream concatenation
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_chunk_stream_concatenates(stack):
    """Three text deltas plus a finish-reason delta must aggregate into
    one continuous summary string written to the page.
    """

    worker, recall, vault_root, clock_holder = stack
    base = clock_holder[0]
    worker._session_start_ns = base - 60 * NS_PER_MIN    # noqa: SLF001
    await _seed_two_episodes(recall, base)

    brain = FakeBrain([
        BrainDelta(content="First part of "),
        BrainDelta(content="the rollup paragraph "),
        BrainDelta(content="with [[entities/the maintainer]] reference."),
        BrainDelta(finish_reason="stop", usage={"output_tokens": 14}),
    ])
    _attach_brain(worker, brain)

    result = await worker.flush_session()
    assert result.status == "ok"
    assert result.page_path is not None
    body = result.page_path.read_text(encoding="utf-8")
    assert "First part of the rollup paragraph with [[entities/the maintainer]] reference." in body
