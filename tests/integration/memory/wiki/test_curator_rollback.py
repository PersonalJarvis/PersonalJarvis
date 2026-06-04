"""Integration test for the writer's validation rollback.

When the LLM produces a malformed page (missing frontmatter, wrong
``type:``, missing slug, …) the atomic writer must re-parse the
just-written file, notice it fails the schema, and roll that single
file back from the backup. Other pages in the same ``apply()`` call
stay applied — partial success is the documented mode.

We feed the curator one valid and one invalid update side by side.
The valid one must end up on disk, the invalid one must not, and the
result object must classify them correctly.
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


def _valid_entity_body(slug: str) -> str:
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
        "\n## Summary\n\nValid page.\n"
        "\n## Facts\n\n- TODO\n"
        "\n## Relationships\n\n- TODO\n"
        "\n## Sources\n\n- test fixture\n"
    )


def _malformed_body_no_frontmatter() -> str:
    """A body the schema validator must reject (no frontmatter)."""
    return "# Just a heading\n\nNo frontmatter, no slug, no type.\n"


@pytest_asyncio.fixture
async def real_stack(tmp_path: Path):
    """Real stack identical to the happy-path test."""
    vault_root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub\n", encoding="utf-8")
    (vault_root / "index.md").write_text("# Index\n\n## Entities\n\n(empty)\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    backup_dir = tmp_path / "backups"
    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)
    writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
    log_writer = LogWriter(log_path=vault_root / "log.md")
    llm = WikiCuratorLLM.__new__(WikiCuratorLLM)
    curator = WikiCurator(
        repo=repo,
        vault=vault,
        writer=writer,
        llm=llm,
        log_writer=log_writer,
        vault_root=vault_root,
    )
    return curator, vault_root, backup_dir


@pytest.mark.asyncio
async def test_invalid_page_rolls_back_while_valid_page_survives(real_stack):
    """One valid + one schema-invalid update → only the valid one persists."""
    curator, vault_root, backup_dir = real_stack

    fake_updates = [
        PageUpdate(
            target_path=vault_root / "entities" / "good.md",
            operation="create",
            new_body=_valid_entity_body("good"),
            reason="valid page",
        ),
        PageUpdate(
            target_path=vault_root / "entities" / "bad.md",
            operation="create",
            new_body=_malformed_body_no_frontmatter(),
            reason="will fail validation",
        ),
    ]

    with patch.object(curator._llm, "propose_updates", return_value=fake_updates):
        result = await curator.ingest(
            source_content="any content",
            source_label="cli-ingest:rollback.md",
        )

    # Valid page on disk.
    assert (vault_root / "entities" / "good.md").is_file()
    good_text = (vault_root / "entities" / "good.md").read_text(encoding="utf-8")
    assert "Valid page." in good_text

    # Invalid page rolled back — must not exist (it was a "create", so
    # rollback means deletion since there was nothing to restore).
    assert not (vault_root / "entities" / "bad.md").is_file()

    # Result classification: applied = [good], failed_validation = [bad].
    assert len(result.applied) == 1
    assert result.applied[0].name == "good.md"
    assert len(result.failed_validation) == 1
    assert result.failed_validation[0].name == "bad.md"
    assert result.skipped_due_to_recent_edit == []

    # Backup tarball exists.
    backups = list(backup_dir.glob("wiki-*.tar.gz"))
    assert len(backups) == 1

    # Log entry has only the surviving page.
    log_text = (vault_root / "log.md").read_text(encoding="utf-8")
    assert "[[entities/good]]" in log_text
    assert "[[entities/bad]]" not in log_text
