"""Living user profile skeleton (Wave-2 B6, D4).

``ensure_profile_skeleton`` makes the user entity page carry the structured
sections the consolidator maintains (Identity, Preferences, Work style,
Values, Relationships, Active projects, Decisions) — existing content is
byte-preserved, the call is idempotent, and a missing page is created.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import pytest_asyncio

from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.curator import WikiCurator
from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
from jarvis.memory.wiki.log_writer import LogWriter
from jarvis.memory.wiki.page import MarkdownPageRepository
from jarvis.memory.wiki.profile import PROFILE_SECTIONS, ensure_profile_skeleton
from jarvis.memory.wiki.vault_index import VaultIndex

SEED_BODY = (
    "---\n"
    "type: entity\n"
    "entity_kind: person\n"
    "slug: ruben\n"
    "aliases: [Ruben, the user]\n"
    "created: 2026-05-14\n"
    "updated: 2026-05-30\n"
    "---\n"
    "\n"
    "# Ruben\n"
    "\n"
    "## Summary\n"
    "\n"
    "The project owner.\n"
    "\n"
    "## Facts\n"
    "\n"
    "- Prefers a multi-provider brain.\n"
    "\n"
    "## Relationships\n"
    "\n"
    "- [[personal-jarvis]] — owner\n"
    "\n"
    "## Sources\n"
    "\n"
    "- seed\n"
)


@pytest_asyncio.fixture
async def stack(tmp_path: Path):
    vault_root = tmp_path / "vault"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive"):
        (vault_root / sub).mkdir(parents=True)
    (vault_root / "schema.md").write_text("# stub\n", encoding="utf-8")
    (vault_root / "index.md").write_text("# Index\n", encoding="utf-8")
    (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (vault_root / "projects" / "personal-jarvis.md").write_text(
        "---\ntype: project\nslug: personal-jarvis\nstatus: active\n---\n"
        "# Personal Jarvis\n\n## Goal\n\nBuild it.\n",
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
    return vault_root, curator


def _age(path: Path) -> None:
    aged = time.time() - 120.0
    os.utime(path, (aged, aged))


@pytest.mark.asyncio
async def test_skeleton_added_to_existing_profile_preserving_content(stack) -> None:
    vault_root, curator = stack
    page = vault_root / "entities" / "ruben.md"
    page.write_text(SEED_BODY, encoding="utf-8")
    _age(page)

    changed = await ensure_profile_skeleton(
        vault_root=vault_root, slug="ruben", curator=curator,
    )

    assert changed is True
    content = page.read_text(encoding="utf-8")
    # Every structured section exists exactly once.
    for section in PROFILE_SECTIONS:
        assert content.count(f"## {section}") == 1, section
    # Existing content byte-preserved.
    assert "- Prefers a multi-provider brain." in content
    assert "The project owner." in content
    # The pre-existing Relationships section is NOT duplicated.
    assert content.count("## Relationships") == 1


@pytest.mark.asyncio
async def test_skeleton_is_idempotent(stack) -> None:
    vault_root, curator = stack
    page = vault_root / "entities" / "ruben.md"
    page.write_text(SEED_BODY, encoding="utf-8")
    _age(page)

    await ensure_profile_skeleton(vault_root=vault_root, slug="ruben", curator=curator)
    _age(page)
    first = page.read_text(encoding="utf-8")
    changed = await ensure_profile_skeleton(
        vault_root=vault_root, slug="ruben", curator=curator,
    )

    assert changed is False
    assert page.read_text(encoding="utf-8") == first


@pytest.mark.asyncio
async def test_missing_profile_page_is_created(stack) -> None:
    vault_root, curator = stack

    changed = await ensure_profile_skeleton(
        vault_root=vault_root, slug="ruben", curator=curator,
    )

    assert changed is True
    page = vault_root / "entities" / "ruben.md"
    assert page.is_file()
    content = page.read_text(encoding="utf-8")
    assert "type: entity" in content
    assert "slug: ruben" in content
    for section in PROFILE_SECTIONS:
        assert f"## {section}" in content
