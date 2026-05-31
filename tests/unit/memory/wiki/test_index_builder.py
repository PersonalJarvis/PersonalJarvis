"""Unit tests for ``jarvis.memory.wiki.index_builder.IndexBuilder``.

Covers:
* Render produces the four category sections in the documented order.
* Stable alphabetical order within each section.
* Human preamble above the first ``## Entities`` heading is preserved.
* The 200-line cap from ``schema.md`` is honored (lists get a
  ``(... N more)`` marker once they exceed the budget).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.memory.wiki.index_builder import IndexBuilder
from jarvis.memory.wiki.vault_index import VaultIndex

from tests.unit.memory.wiki.conftest import FakePageRepository, write_page


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def populated_index(
    vault_root: Path, fake_repo: FakePageRepository
) -> VaultIndex:
    write_page(vault_root, "entity", "the maintainer")
    write_page(vault_root, "entity", "claude")
    write_page(vault_root, "concept", "awareness-layer")
    write_page(vault_root, "concept", "voice-pipeline")
    write_page(vault_root, "project", "wiki-curator")
    write_page(vault_root, "session", "2026-05-11-abc")
    idx = VaultIndex(repo=fake_repo)
    await idx.scan(vault_root)
    return idx


async def test_render_contains_all_four_categories(
    populated_index: VaultIndex,
) -> None:
    builder = IndexBuilder(vault=populated_index)
    output = await builder.render_index_md()
    for heading in ("## Entities", "## Concepts", "## Projects", "## Sessions"):
        assert heading in output


async def test_render_lists_pages_alphabetically(
    populated_index: VaultIndex,
) -> None:
    builder = IndexBuilder(vault=populated_index)
    output = await builder.render_index_md()
    # Anchor on the boundary heading (newline-prefixed) to avoid the
    # backticked ``## Entities`` reference inside the preamble.
    entities_block = output.split("\n## Entities\n", 1)[1].split("\n## ", 1)[0]
    claude_idx = entities_block.index("[[claude]]")
    ruben_idx = entities_block.index("[[the maintainer]]")
    assert claude_idx < ruben_idx


async def test_render_uses_short_wikilink_form(
    populated_index: VaultIndex,
) -> None:
    builder = IndexBuilder(vault=populated_index)
    output = await builder.render_index_md()
    assert "[[the maintainer]]" in output
    assert "[[awareness-layer]]" in output
    # Not the prefixed form
    assert "[[entities/the maintainer]]" not in output


async def test_render_handles_empty_categories(
    vault_root: Path, fake_repo: FakePageRepository
) -> None:
    write_page(vault_root, "entity", "the maintainer")
    # No concepts, projects, or sessions on disk
    idx = VaultIndex(repo=fake_repo)
    await idx.scan(vault_root)
    builder = IndexBuilder(vault=idx)
    output = await builder.render_index_md()
    assert "[[the maintainer]]" in output
    # Empty categories are rendered with a placeholder
    concepts_block = output.split("\n## Concepts\n", 1)[1].split("\n## ", 1)[0]
    assert "(empty)" in concepts_block


async def test_render_preserves_human_preamble(
    populated_index: VaultIndex, tmp_path: Path
) -> None:
    existing = tmp_path / "index.md"
    existing.write_text(
        "---\n"
        "type: index\n"
        "purpose: table-of-contents\n"
        "---\n"
        "\n"
        "# Knowledge Vault — Index\n"
        "\n"
        "Custom human-written intro that must survive a regeneration.\n"
        "Multiple paragraphs OK.\n"
        "\n"
        "## Entities\n"
        "\n"
        "*old auto-generated content*\n"
        "\n"
        "- [[stale]]\n",
        encoding="utf-8",
    )
    builder = IndexBuilder(vault=populated_index)
    output = await builder.render_index_md(existing_path=existing)
    # Preamble survives verbatim.
    assert (
        "Custom human-written intro that must survive a regeneration."
        in output
    )
    assert "Multiple paragraphs OK." in output
    # Old auto-content is gone.
    assert "[[stale]]" not in output
    # New content is present
    assert "[[the maintainer]]" in output


async def test_render_handles_missing_existing_file(
    populated_index: VaultIndex, tmp_path: Path
) -> None:
    """An existing-path that does not exist returns the default preamble."""
    builder = IndexBuilder(vault=populated_index)
    output = await builder.render_index_md(
        existing_path=tmp_path / "no_such_file.md"
    )
    assert "Knowledge Vault — Index" in output
    assert "[[the maintainer]]" in output


async def test_render_handles_existing_without_boundary(
    populated_index: VaultIndex, tmp_path: Path
) -> None:
    """An existing index that has no ``## Entities`` heading is treated as
    pure preamble — the new sections are appended below it."""
    existing = tmp_path / "index.md"
    existing.write_text(
        "# Knowledge Vault — Index\n\nAll content lives above the heading.\n",
        encoding="utf-8",
    )
    builder = IndexBuilder(vault=populated_index)
    output = await builder.render_index_md(existing_path=existing)
    assert "All content lives above the heading." in output
    assert "## Entities" in output
    assert "[[the maintainer]]" in output


async def test_render_is_deterministic(populated_index: VaultIndex) -> None:
    builder = IndexBuilder(vault=populated_index)
    first = await builder.render_index_md()
    second = await builder.render_index_md()
    assert first == second


async def test_line_cap_triggers_truncation(
    vault_root: Path, fake_repo: FakePageRepository
) -> None:
    """Far past the soft cap, lists must be truncated with a marker."""
    # 80 entities — easily blows the default 200-line cap once all four
    # categories are rendered.
    for i in range(80):
        write_page(vault_root, "entity", f"e-{i:03d}")
    for i in range(20):
        write_page(vault_root, "concept", f"c-{i:03d}")
    idx = VaultIndex(repo=fake_repo)
    await idx.scan(vault_root)
    builder = IndexBuilder(vault=idx, line_cap=80)
    output = await builder.render_index_md()
    assert output.count("\n") <= 80 + 5  # small overhead tolerated
    assert "more)" in output


async def test_small_vault_does_not_trigger_truncation(
    populated_index: VaultIndex,
) -> None:
    builder = IndexBuilder(vault=populated_index)
    output = await builder.render_index_md()
    assert "more)" not in output


async def test_section_order_is_fixed(populated_index: VaultIndex) -> None:
    builder = IndexBuilder(vault=populated_index)
    output = await builder.render_index_md()
    indices = [
        output.index(h)
        for h in ("## Entities", "## Concepts", "## Projects", "## Sessions")
    ]
    assert indices == sorted(indices)


async def test_render_includes_blurbs(populated_index: VaultIndex) -> None:
    builder = IndexBuilder(vault=populated_index)
    output = await builder.render_index_md()
    assert "*People, tools, repositories, services, devices.*" in output
    assert "*Abstract recurring ideas, patterns, methodologies.*" in output
    assert "*Active or recently-active workstreams.*" in output
