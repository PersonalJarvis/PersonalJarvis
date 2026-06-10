"""Self-documentation page tests (Wave-2 B7).

``memory.md`` is deterministic (no LLM), schema-valid as a root meta page,
created on first refresh, updated in place on later refreshes, carries no
dangling wikilinks, and never touches ``_archive/``.
"""
from __future__ import annotations

import datetime as _dt
import os
import time
from pathlib import Path

import pytest
import pytest_asyncio

from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.cleanup import dangling_link_targets
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository, parse_markdown
from jarvis.memory.wiki.self_doc import (
    PAGE_NAME,
    refresh_memory_page,
    render_memory_page,
)
from jarvis.memory.wiki.vault_index import VaultIndex


@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    vault_root = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub\n", encoding="utf-8")
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
    curator = WikiCurator(
        repo=repo,
        vault=vault,
        writer=writer,
        llm=WikiCuratorLLM.__new__(WikiCuratorLLM),
        log_writer=LogWriter(log_path=vault_root / "log.md"),
        vault_root=vault_root,
    )
    journal = CandidateJournal(tmp_path / "jarvis.db")
    yield vault_root, curator, journal
    journal.close()


def test_render_is_deterministic_and_link_clean(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    (vault_root / "entities").mkdir(parents=True)
    (vault_root / "entities" / "lena.md").write_text("x", encoding="utf-8")

    now = _dt.datetime(2026, 6, 10, 12, 0)
    body = render_memory_page(
        vault_root=vault_root,
        backlog_count=3,
        telemetry_snapshot={"wiki_consolidator_add": 2},
        now=now,
    )

    assert body.startswith("---\ntype: meta\n")
    assert "Last refreshed: 2026-06-10 12:00" in body
    assert "1 entities" in body
    assert "Candidate journal backlog: 3 pending" in body
    assert "2 added" in body
    assert "[[entities/lena]]" in body
    # Identical inputs render identical output (no hidden clock reads).
    assert body == render_memory_page(
        vault_root=vault_root,
        backlog_count=3,
        telemetry_snapshot={"wiki_consolidator_add": 2},
        now=now,
    )


@pytest.mark.asyncio
async def test_first_refresh_creates_schema_valid_meta_page(stack) -> None:
    vault_root, curator, journal = stack
    journal.append(
        [CandidateFact(fact="Lena moved to Hamburg.")],
        source_label="s", turn_hash="h",
    )

    ok = await refresh_memory_page(
        curator=curator, vault_root=vault_root, journal=journal,
    )

    assert ok is True
    page_path = vault_root / PAGE_NAME
    assert page_path.is_file()
    raw = page_path.read_text(encoding="utf-8")
    page = parse_markdown(raw, page_path)
    assert page.page_type == "meta"
    assert page.is_schema_valid is True
    assert "Candidate journal backlog: 1 pending" in raw
    # No dangling wikilinks in the rendered page.
    assert dangling_link_targets(raw, vault_root) == []
    # _archive untouched.
    assert list((vault_root / "_archive").iterdir()) == []


@pytest.mark.asyncio
async def test_second_refresh_updates_in_place(stack) -> None:
    vault_root, curator, journal = stack
    await refresh_memory_page(curator=curator, vault_root=vault_root, journal=journal)
    page_path = vault_root / PAGE_NAME
    # Age past the writer's 30s concurrent-edit lock (machine page; in
    # production consecutive runs inside 30s simply skip one refresh).
    aged = time.time() - 120.0
    os.utime(page_path, (aged, aged))

    journal.append(
        [CandidateFact(fact="New fact."), CandidateFact(fact="Another.")],
        source_label="s", turn_hash="h2",
    )
    ok = await refresh_memory_page(
        curator=curator, vault_root=vault_root, journal=journal,
    )

    assert ok is True
    raw = page_path.read_text(encoding="utf-8")
    assert "Candidate journal backlog: 2 pending" in raw
    # Sections never duplicate across refreshes.
    assert raw.count("## Live status") == 1
    assert raw.count("## How my memory works") == 1
