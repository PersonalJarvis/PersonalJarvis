"""Integration test for the 30-second concurrent-edit lock.

Simulates the user editing a wiki page in Obsidian while the curator
is mid-ingest. The 30s lock in the atomic writer must skip the
recently-touched page and report it under
``WriteResult.skipped_due_to_recent_edit`` — without crashing the
whole ingest.

We override the writer's lock-window to 30s and use a clock-stub so
the test is deterministic.
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


def _entity_body(slug: str, summary: str = "Body") -> str:
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
        "\n## Summary\n\n"
        f"{summary}\n"
        "\n## Facts\n\n- TODO\n"
        "\n## Relationships\n\n- TODO\n"
        "\n## Sources\n\n- test fixture\n"
    )


@pytest_asyncio.fixture
async def stack_with_fast_clock(tmp_path: Path):
    """Real stack but with a deterministic clock for the writer."""
    vault_root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub\n", encoding="utf-8")
    (vault_root / "index.md").write_text("# Index\n\n## Entities\n\n(empty)\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    # Plant an entity page on disk *before* the writer is created so we
    # can simulate "user just edited this page" by setting its mtime.
    pre_existing = vault_root / "entities" / "ruben.md"
    pre_existing.write_text(_entity_body("ruben", "Original content."), encoding="utf-8")

    repo = MarkdownPageRepository()
    vault = VaultIndex(repo=repo)
    await vault.scan(vault_root)

    # Clock fixed at t=1000.0 ; pre_existing was touched at t=995
    # (5s ago — well inside the 30s lock).
    clock_t = [1000.0]
    backup_dir = tmp_path / "backups"
    writer = AtomicWriter(
        vault_root=vault_root,
        backup_dir=backup_dir,
        clock=lambda: clock_t[0],
    )
    # Force the pre_existing file's mtime to 5s before "now".
    import os
    os.utime(pre_existing, (clock_t[0] - 5, clock_t[0] - 5))

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
    return curator, vault_root, pre_existing


@pytest.mark.asyncio
async def test_concurrent_edit_lock_skips_recently_touched_page(
    stack_with_fast_clock,
):
    """A page touched 5s ago must be skipped; other pages still apply."""
    curator, vault_root, pre_existing = stack_with_fast_clock

    # Two updates: one targets the just-edited page (must skip), one
    # targets a fresh page (must apply).
    fake_updates = [
        PageUpdate(
            target_path=pre_existing,
            operation="update",
            new_body=_entity_body("ruben", "Updated by curator — should be skipped."),
            reason="user-edited recently",
        ),
        PageUpdate(
            target_path=vault_root / "entities" / "newpage.md",
            operation="create",
            new_body=_entity_body("newpage", "Brand-new page — should apply."),
            reason="fresh slot",
        ),
    ]

    with patch.object(curator._llm, "propose_updates", return_value=fake_updates):
        result = await curator.ingest(
            source_content="content",
            source_label="cli-ingest:concurrent.md",
        )

    # Ruben's page survived untouched.
    surviving = (vault_root / "entities" / "ruben.md").read_text(encoding="utf-8")
    assert "Original content." in surviving
    assert "should be skipped" not in surviving

    # newpage landed.
    assert (vault_root / "entities" / "newpage.md").is_file()

    # Result classification is correct.
    assert len(result.applied) == 1
    assert result.applied[0].name == "newpage.md"
    assert len(result.skipped_due_to_recent_edit) == 1
    assert result.skipped_due_to_recent_edit[0].name == "ruben.md"
    assert result.failed_validation == []
