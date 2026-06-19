"""Curator enforces the schema.md:148 create-or-refuse wikilink rule.

An unresolvable ``[[App]]`` in a proposed body must be written with the
link demoted to plain text and ``wiki_links_refused_dangling`` bumped; a
link that resolves to an existing durable page must survive verbatim.
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
from jarvis.memory.wiki.telemetry import get_telemetry
from jarvis.memory.wiki.vault_index import VaultIndex


def _entity_body(slug: str, body_line: str) -> str:
    return (
        "---\n"
        "type: entity\n"
        "entity_kind: person\n"
        f"slug: {slug}\n"
        "aliases: []\n"
        "created: 2026-06-09\n"
        "updated: 2026-06-09\n"
        "---\n"
        "\n"
        f"# {slug.title()}\n"
        "\n"
        "## Summary\n"
        "\n"
        f"{body_line}\n"
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
        "- dangling-link fixture\n"
    )


@pytest_asyncio.fixture
async def real_stack(tmp_path: Path):
    vault_root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
    (vault_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

    # A durable page that a resolvable link can point at.
    (vault_root / "entities" / "ruben.md").write_text(
        _entity_body("ruben", "Profile body for Ruben."),
        encoding="utf-8",
    )

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
    return curator, vault_root


@pytest.mark.asyncio
async def test_unresolvable_link_is_demoted_and_counter_bumped(real_stack):
    """``[[App]]`` (no page) → plain text ``App``; counter incremented."""
    curator, vault_root = real_stack
    telemetry = get_telemetry()
    before = telemetry.get("wiki_links_refused_dangling")

    proposed = [
        PageUpdate(
            target_path=vault_root / "concepts" / "morning-routine.md",
            operation="create",
            new_body=(
                "---\n"
                "type: concept\n"
                "slug: morning-routine\n"
                "aliases: []\n"
                "created: 2026-06-09\n"
                "updated: 2026-06-09\n"
                "---\n"
                "\n"
                "# Morning Routine\n"
                "\n"
                "## Summary\n"
                "\n"
                "The routine opens [[App]] every day to start work.\n"
            ),
            reason="new concept with a ghost link",
        ),
    ]

    with patch.object(curator._llm, "propose_updates", return_value=proposed):
        result = await curator.ingest(
            source_content="The morning routine opens an app.",
            source_label="cli-ingest:routine.md",
        )

    assert len(result.applied) == 1
    content = (vault_root / "concepts" / "morning-routine.md").read_text(
        encoding="utf-8"
    )
    # The bracket form is gone; the display word survives as plain text.
    assert "[[App]]" not in content
    assert "opens App every day" in content
    # Exactly one link refused.
    assert telemetry.get("wiki_links_refused_dangling") == before + 1


@pytest.mark.asyncio
async def test_resolvable_link_is_preserved(real_stack):
    """A link to an existing durable page survives; counter untouched."""
    curator, vault_root = real_stack
    telemetry = get_telemetry()
    before = telemetry.get("wiki_links_refused_dangling")

    proposed = [
        PageUpdate(
            target_path=vault_root / "concepts" / "user-context.md",
            operation="create",
            new_body=(
                "---\n"
                "type: concept\n"
                "slug: user-context\n"
                "aliases: []\n"
                "created: 2026-06-09\n"
                "updated: 2026-06-09\n"
                "---\n"
                "\n"
                "# User Context\n"
                "\n"
                "## Summary\n"
                "\n"
                "This concept concerns [[ruben]] directly.\n"
            ),
            reason="concept linking the existing user entity",
        ),
    ]

    with patch.object(curator._llm, "propose_updates", return_value=proposed):
        result = await curator.ingest(
            source_content="A concept about the user.",
            source_label="cli-ingest:context.md",
        )

    assert len(result.applied) == 1
    content = (vault_root / "concepts" / "user-context.md").read_text(
        encoding="utf-8"
    )
    # Resolvable link is canonicalised to the typed form (display kept as
    # an alias), never demoted to plain text.
    assert "[[entities/ruben|ruben]]" in content
    assert telemetry.get("wiki_links_refused_dangling") == before
