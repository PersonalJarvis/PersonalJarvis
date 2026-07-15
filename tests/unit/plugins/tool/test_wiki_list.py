"""Tests for the ``wiki-list`` tool — grounded vault listings.

Forensic origin (voice session 2026-07-14 09:29): asked "what is in my
wiki", the delegated brain had no listing tool, so it probed blindly
(``wiki-recall`` → ``wiki-page-read index.md`` → not found → ``SOUL.md``
→ not found, ~14 LLM rounds, 66 s) and finally recited the *example*
directory layout from ``schema.md`` as if it were the actual vault
content — pure hallucination. A deterministic listing answers the
question in ONE round and is ground truth by construction.

Hard-negatives:
  1. ``wiki-list`` MUST be in ROUTER_TOOLS; workers may reach it only through
     the mission-scoped supervisor broker.
  2. The listing MUST reflect the real filesystem — files that exist are
     present, files that do not exist are absent.
  3. Meta/contract pages (``type: meta`` frontmatter) MUST be flagged so
     the model cannot mistake the schema contract for user content.
  4. An empty vault MUST produce an honest "empty" answer, not an error.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.plugins.tool.wiki_list import WikiListTool


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """Minimal realistic vault: content pages + a meta contract page."""
    (tmp_path / "entities").mkdir()
    (tmp_path / "entities" / "ruben.md").write_text(
        "# Ruben\n\nThe maintainer.\n", encoding="utf-8"
    )
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "personal-jarvis.md").write_text(
        "# Personal Jarvis\n\nThe project.\n", encoding="utf-8"
    )
    (tmp_path / "log.md").write_text("# Log\n", encoding="utf-8")
    (tmp_path / "schema.md").write_text(
        "---\ntype: meta\npurpose: wiki-maintenance-contract\n---\n\n"
        "# Wiki Schema\n\nExample layout: USER.md, SOUL.md, people/…\n",
        encoding="utf-8",
    )
    # Obsidian app config must never appear in the listing.
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "workspace.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_wiki_list_is_in_router_tools() -> None:
    assert "wiki-list" in ROUTER_TOOLS


def test_wiki_list_surface(vault: Path) -> None:
    tool = WikiListTool(vault_root=vault)
    assert tool.name == "wiki-list"
    assert tool.risk_tier == "safe"
    assert tool.schema["type"] == "object"


@pytest.mark.asyncio
async def test_wiki_list_lists_real_files_only(vault: Path) -> None:
    tool = WikiListTool(vault_root=vault)
    result = await tool.execute({}, ctx=None)
    assert result.success is True
    out = result.output
    assert "entities/ruben.md" in out
    assert "projects/personal-jarvis.md" in out
    assert "log.md" in out
    # The hallucinated names from the live incident must NOT appear.
    assert "USER.md" not in out.replace("schema.md", "")
    assert "SOUL.md" not in out
    # Hidden app config is excluded.
    assert ".obsidian" not in out


@pytest.mark.asyncio
async def test_wiki_list_flags_meta_pages(vault: Path) -> None:
    tool = WikiListTool(vault_root=vault)
    result = await tool.execute({}, ctx=None)
    assert result.success is True
    schema_line = next(
        line for line in result.output.splitlines() if "schema.md" in line
    )
    assert "system file" in schema_line.lower()
    content_line = next(
        line for line in result.output.splitlines() if "ruben.md" in line
    )
    assert "system file" not in content_line.lower()


@pytest.mark.asyncio
async def test_wiki_list_shows_page_titles(vault: Path) -> None:
    tool = WikiListTool(vault_root=vault)
    result = await tool.execute({}, ctx=None)
    assert "Ruben" in result.output
    assert "Personal Jarvis" in result.output


@pytest.mark.asyncio
async def test_wiki_list_empty_vault_is_honest(tmp_path: Path) -> None:
    tool = WikiListTool(vault_root=tmp_path)
    result = await tool.execute({}, ctx=None)
    assert result.success is True
    assert "empty" in result.output.lower()


@pytest.mark.asyncio
async def test_wiki_list_missing_vault_is_honest(tmp_path: Path) -> None:
    tool = WikiListTool(vault_root=tmp_path / "does-not-exist")
    result = await tool.execute({}, ctx=None)
    assert result.success is True
    assert "empty" in result.output.lower() or "not" in result.output.lower()


@pytest.mark.asyncio
async def test_wiki_list_caps_huge_vaults(tmp_path: Path) -> None:
    for i in range(520):
        (tmp_path / f"note-{i:04d}.md").write_text(f"# Note {i}\n", encoding="utf-8")
    tool = WikiListTool(vault_root=tmp_path)
    result = await tool.execute({}, ctx=None)
    assert result.success is True
    assert result.output.count(".md") <= 501
    assert "truncated" in result.output.lower()
