"""The Stage-2 consolidator must never do blocking work on the event loop.

A run is awaited from the app's event loop — the same loop that serves the
Desktop UI, the WebSocket stream and the voice path. Its blocking steps
(SQLite reads/writes, FTS5 queries, whole-page vault reads) therefore have to
land on a worker thread; doing them inline stalls everything else for as long
as a batch takes, which is exactly the "the whole app freezes while the wiki
thinks" symptom (AP-9).

These tests assert the thread, not the wall clock, so they stay deterministic.
"""
from __future__ import annotations

import threading
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
from jarvis.memory.wiki.consolidator import Consolidator
from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal


def _config() -> JarvisConfig:
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
    )


class _ThreadRecordingJournal:
    """Pass-through journal that records which thread each call ran on."""

    def __init__(self, inner: CandidateJournal) -> None:
        self._inner = inner
        self.threads: dict[str, list[int]] = {}

    def _record(self, method: str) -> None:
        self.threads.setdefault(method, []).append(threading.get_ident())

    def pending(self, *args: Any, **kwargs: Any) -> Any:
        self._record("pending")
        return self._inner.pending(*args, **kwargs)

    def mark(self, *args: Any, **kwargs: Any) -> None:
        self._record("mark")
        self._inner.mark(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@pytest.fixture
def stack(tmp_path: Path):
    vault_root = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects"):
        (vault_root / sub).mkdir(parents=True)
    journal = CandidateJournal(tmp_path / "jarvis.db")
    recording = _ThreadRecordingJournal(journal)
    consolidator = Consolidator(
        config=_config(),
        journal=recording,  # type: ignore[arg-type]
        curator=object(),  # never reached in these tests
        search=None,
        vault_root=vault_root,
    )
    yield consolidator, recording, journal
    journal.close()


async def test_journal_read_runs_off_the_event_loop(stack) -> None:
    consolidator, recording, _journal = stack

    assert await consolidator.run_once() == "journal-empty"

    assert recording.threads["pending"], "the journal was never read"
    assert threading.get_ident() not in recording.threads["pending"]


async def test_journal_write_runs_off_the_event_loop(stack) -> None:
    consolidator, recording, journal = stack
    journal.append(
        [CandidateFact(fact="Lena moved to Hamburg.", kind="person", subjects=("lena",))],
        source_label="voice-fact:1",
        turn_hash="h1",
    )
    row_id = journal.pending()[0].id

    await consolidator._mark([row_id], status="skipped")

    assert recording.threads["mark"]
    assert threading.get_ident() not in recording.threads["mark"]
    assert journal.pending() == []


async def test_neighbour_retrieval_runs_off_the_event_loop(
    stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrieval reads up to ``k_nearest * batch`` full pages plus FTS5."""
    consolidator, _recording, journal = stack
    journal.append(
        [CandidateFact(fact="Lena moved to Hamburg.", kind="person", subjects=("lena",))],
        source_label="voice-fact:1",
        turn_hash="h1",
    )
    rows = journal.pending()

    seen: list[int] = []
    real = consolidator._collect_neighbours

    def recording_collect(batch):  # type: ignore[no-untyped-def]
        seen.append(threading.get_ident())
        return real(batch)

    monkeypatch.setattr(consolidator, "_collect_neighbours", recording_collect)

    # No provider needed: an unreachable judge ends the batch right after
    # retrieval, which is the step under test.
    async def no_provider(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(consolidator, "_judge", no_provider)

    outcome = await consolidator._process_rows(rows)

    assert outcome.unavailable is True
    assert seen and threading.get_ident() not in seen


def test_page_reads_are_memoised_within_one_cycle(stack) -> None:
    """Validation probes the same pages repeatedly; disk must see one read."""
    consolidator, _recording, _journal = stack
    target = "entities/lena.md"
    (consolidator._vault_root / "entities" / "lena.md").write_text(
        "# Lena\n\nLives in Hamburg.\n", encoding="utf-8"
    )

    first = consolidator._read_page(target)
    (consolidator._vault_root / "entities" / "lena.md").write_text(
        "# Lena\n\nRewritten mid-cycle.\n", encoding="utf-8"
    )
    second = consolidator._read_page(target)

    assert first == second, "the memo must serve the same body within a cycle"

    # A new judge/execute cycle always starts from disk again, so a body this
    # run has written can never be served stale.
    consolidator._page_body_cache.clear()
    assert "Rewritten mid-cycle." in (consolidator._read_page(target) or "")


def test_missing_page_is_memoised_as_absent(stack) -> None:
    consolidator, _recording, _journal = stack
    assert consolidator._read_page("entities/nobody.md") is None
    assert consolidator._page_body_cache["entities/nobody.md"] is None
