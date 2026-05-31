"""End-to-end ingest test for :class:`WikiCurator`.

The real curator stack against a temporary on-disk vault: the only
mocked-out piece is the brain call inside the curator-LLM (we don't
want a live Gemini hit in CI).

What this test really catches:

- The DI plumbing is wired correctly (every component reaches the
  right collaborator).
- ``PageUpdate`` objects from the LLM round-trip through the writer
  and produce on-disk files matching the schema.
- The log entry lands with wikilink-formatted page references.
- The backup tarball exists.

This is the wave-2 "happy path" — if this passes, the four B1
components compose into a working ingest pipeline.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.protocols import PageUpdate
from jarvis.memory.wiki.vault_index import VaultIndex


@pytest_asyncio.fixture
async def real_stack(tmp_path: Path):
    """Build the four real B1 components against a fresh on-disk vault."""
    vault_root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    # Minimal schema marker so the curator's preflight does not fail.
    (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
    (vault_root / "index.md").write_text(
        "# Index\n\n## Entities\n\n(empty)\n",
        encoding="utf-8",
    )
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    backup_dir = tmp_path / "backups"
    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)
    writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
    log_writer = LogWriter(log_path=vault_root / "log.md")

    # Curator-LLM is real, but its brain call is patched out per-test.
    llm = WikiCuratorLLM.__new__(WikiCuratorLLM)
    # We bypass __init__ so we don't try to instantiate a real brain;
    # the patched method below is the only entry point we exercise.

    curator = WikiCurator(
        repo=repo,
        vault=vault,
        writer=writer,
        llm=llm,
        log_writer=log_writer,
        vault_root=vault_root,
    )
    return curator, vault_root, backup_dir


def _entity_body(slug: str, summary_line: str) -> str:
    return (
        "---\n"
        "type: entity\n"
        "entity_kind: person\n"
        f"slug: {slug}\n"
        "aliases: []\n"
        "created: 2026-05-12\n"
        "updated: 2026-05-12\n"
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
        "- cli-ingest fixture\n"
    )


@pytest.mark.asyncio
async def test_ingest_creates_pages_writes_log_and_backup(real_stack):
    """One ingest call → two entity pages + one log entry + one backup."""
    curator, vault_root, backup_dir = real_stack

    fake_updates = [
        PageUpdate(
            target_path=vault_root / "entities" / "alex.md",
            operation="create",
            new_body=_entity_body("alex", "Profile body for Alex."),
            reason="new fact about the user",
        ),
        PageUpdate(
            target_path=vault_root / "entities" / "wiki-project.md",
            operation="create",
            new_body=_entity_body("wiki-project", "Karpathy wiki rebuild project."),
            reason="new project context",
        ),
    ]

    with patch.object(curator._llm, "propose_updates", return_value=fake_updates):
        result = await curator.ingest(
            source_content="The user is rebuilding the wiki system from scratch.",
            source_label="cli-ingest:demo.md",
        )

    # Pages written.
    assert len(result.applied) == 2
    written = {p.name for p in result.applied}
    assert written == {"alex.md", "wiki-project.md"}

    # Files actually on disk + parseable.
    assert (vault_root / "entities" / "alex.md").is_file()
    assert (vault_root / "entities" / "wiki-project.md").is_file()
    alex_content = (vault_root / "entities" / "alex.md").read_text(encoding="utf-8")
    assert "Profile body for Alex." in alex_content
    assert "type: entity" in alex_content

    # Log entry appended with wikilink-formatted page refs.
    log_content = (vault_root / "log.md").read_text(encoding="utf-8")
    assert "ingest | cli-ingest:demo.md" in log_content
    assert "[[entities/alex]]" in log_content
    assert "[[entities/wiki-project]]" in log_content

    # Backup tarball exists (one per apply, not per page).
    backups = list(backup_dir.glob("wiki-*.tar.gz"))
    assert len(backups) == 1


@pytest.mark.asyncio
async def test_ingest_with_no_proposed_updates_writes_nothing(real_stack):
    """LLM returns ``[]`` → no writes, no backup, no log entry."""
    curator, vault_root, backup_dir = real_stack

    with patch.object(curator._llm, "propose_updates", return_value=[]):
        result = await curator.ingest(
            source_content="hallo",
            source_label="cli-ingest:smalltalk.md",
        )

    assert result.applied == []
    assert result.skipped_due_to_recent_edit == []
    assert result.failed_validation == []

    # Vault stays empty of entity pages.
    assert list((vault_root / "entities").glob("*.md")) == []

    # Log unchanged (only contains the seeded header).
    log_content = (vault_root / "log.md").read_text(encoding="utf-8")
    assert "ingest" not in log_content

    # No backup taken.
    assert list(backup_dir.glob("*.tar.gz")) == []
