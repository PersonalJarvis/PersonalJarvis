"""Unit tests for ``jarvis.memory.wiki.vault_index.VaultIndex``.

Covers the four documented contracts:
* ``scan`` walks the four page-type directories and tolerates a missing one.
* ``pages_by_type`` returns valid pages sorted by slug.
* ``find_by_slug`` returns the cached entry and refreshes it on stale mtime.
* ``backlinks_to`` returns sources sorted alphabetically and stays in sync
  when a page's wikilinks change.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from jarvis.memory.wiki.vault_index import VaultIndex

from tests.unit.memory.wiki.conftest import FakePageRepository, write_page


pytestmark = pytest.mark.asyncio


@pytest.fixture
def index(fake_repo: FakePageRepository) -> VaultIndex:
    return VaultIndex(repo=fake_repo)


async def test_scan_empty_vault_is_noop(index: VaultIndex, vault_root: Path) -> None:
    await index.scan(vault_root)
    assert index.pages_by_type("entity") == []
    assert index.pages_by_type("concept") == []
    assert index.find_by_slug("nothing") is None


async def test_scan_tolerates_missing_directory(
    index: VaultIndex, tmp_path: Path, fake_repo: FakePageRepository
) -> None:
    """A fresh vault may not have ``sessions/`` yet — scan must not raise."""
    (tmp_path / "entities").mkdir()
    write_page(tmp_path, "entity", "alex")
    # No concepts/, projects/, sessions/ directories
    await index.scan(tmp_path)
    pages = index.pages_by_type("entity")
    assert len(pages) == 1
    assert pages[0].slug == "alex"


async def test_scan_picks_up_all_four_page_types(
    index: VaultIndex, vault_root: Path
) -> None:
    write_page(vault_root, "entity", "alex")
    write_page(vault_root, "entity", "claude")
    write_page(vault_root, "concept", "awareness-layer")
    write_page(vault_root, "project", "wiki-curator")
    write_page(vault_root, "session", "2026-05-11-abc")

    await index.scan(vault_root)
    assert {p.slug for p in index.pages_by_type("entity")} == {"alex", "claude"}
    assert [p.slug for p in index.pages_by_type("concept")] == ["awareness-layer"]
    assert [p.slug for p in index.pages_by_type("project")] == ["wiki-curator"]
    assert [p.slug for p in index.pages_by_type("session")] == ["2026-05-11-abc"]


async def test_pages_by_type_is_alphabetically_sorted(
    index: VaultIndex, vault_root: Path
) -> None:
    write_page(vault_root, "entity", "zoe")
    write_page(vault_root, "entity", "alpha")
    write_page(vault_root, "entity", "mid")
    await index.scan(vault_root)
    slugs = [p.slug for p in index.pages_by_type("entity")]
    assert slugs == ["alpha", "mid", "zoe"]


async def test_pages_by_type_returns_empty_for_unknown(
    index: VaultIndex, vault_root: Path
) -> None:
    write_page(vault_root, "entity", "alex")
    await index.scan(vault_root)
    assert index.pages_by_type("nonsense") == []


async def test_find_by_slug_returns_page(
    index: VaultIndex, vault_root: Path
) -> None:
    write_page(vault_root, "entity", "alex")
    await index.scan(vault_root)
    page = index.find_by_slug("alex")
    assert page is not None
    assert page.slug == "alex"
    assert page.page_type == "entity"


async def test_find_by_slug_returns_none_on_unknown(
    index: VaultIndex, vault_root: Path
) -> None:
    await index.scan(vault_root)
    assert index.find_by_slug("never-existed") is None


async def test_backlinks_are_populated_after_scan(
    index: VaultIndex, vault_root: Path
) -> None:
    write_page(vault_root, "entity", "alex")
    write_page(
        vault_root,
        "concept",
        "voice-pipeline",
        body="The voice path runs through [[alex]]'s setup.",
    )
    write_page(
        vault_root,
        "project",
        "wiki-curator",
        body="Driven by [[alex]].",
    )
    await index.scan(vault_root)

    backlinks = index.backlinks_to("alex")
    assert len(backlinks) == 2
    assert {p.slug for p in backlinks} == {"voice-pipeline", "wiki-curator"}


async def test_backlinks_handle_prefixed_wikilink_form(
    index: VaultIndex, vault_root: Path
) -> None:
    write_page(vault_root, "entity", "alex")
    write_page(
        vault_root,
        "concept",
        "voice-pipeline",
        body="Linked via [[entities/alex]].",
    )
    await index.scan(vault_root)
    backlinks = index.backlinks_to("alex")
    assert [p.slug for p in backlinks] == ["voice-pipeline"]


async def test_backlinks_handle_aliased_form(
    index: VaultIndex, vault_root: Path
) -> None:
    write_page(vault_root, "entity", "alex")
    write_page(
        vault_root,
        "concept",
        "voice-pipeline",
        body="Linked via [[alex|the user]].",
    )
    await index.scan(vault_root)
    backlinks = index.backlinks_to("alex")
    assert [p.slug for p in backlinks] == ["voice-pipeline"]


async def test_backlinks_alphabetically_sorted(
    index: VaultIndex, vault_root: Path
) -> None:
    write_page(vault_root, "entity", "alex")
    write_page(vault_root, "concept", "zeta", body="[[alex]]")
    write_page(vault_root, "concept", "alpha", body="[[alex]]")
    write_page(vault_root, "concept", "mid", body="[[alex]]")
    await index.scan(vault_root)
    sources = [p.slug for p in index.backlinks_to("alex")]
    assert sources == ["alpha", "mid", "zeta"]


async def test_invalid_pages_are_skipped(
    index: VaultIndex, tmp_path: Path
) -> None:
    """A file in ``entities/`` with ``type: concept`` is rejected."""
    (tmp_path / "entities").mkdir()
    bad = tmp_path / "entities" / "wrong.md"
    bad.write_text(
        "---\ntype: concept\nslug: wrong\n---\n\nBody.\n",
        encoding="utf-8",
    )
    good = tmp_path / "entities" / "ok.md"
    good.write_text(
        "---\ntype: entity\nslug: ok\n---\n\nBody.\n",
        encoding="utf-8",
    )
    await index.scan(tmp_path)
    assert index.find_by_slug("wrong") is None
    assert index.find_by_slug("ok") is not None


async def test_skip_dirs_are_not_scanned(
    index: VaultIndex, vault_root: Path
) -> None:
    """``_archive/`` and ``attachments/`` are excluded from the scan."""
    archived = vault_root / "_archive" / "old.md"
    archived.write_text(
        "---\ntype: entity\nslug: old\n---\n\nBody.\n",
        encoding="utf-8",
    )
    attached = vault_root / "attachments" / "blob.md"
    attached.write_text(
        "---\ntype: entity\nslug: blob\n---\n\nBody.\n",
        encoding="utf-8",
    )
    await index.scan(vault_root)
    assert index.find_by_slug("old") is None
    assert index.find_by_slug("blob") is None


async def test_stale_rescan_picks_up_changed_file(
    index: VaultIndex, vault_root: Path
) -> None:
    """Editing a page on disk surfaces via find_by_slug on the next call."""
    path = write_page(
        vault_root, "entity", "alex", body="Initial body."
    )
    await index.scan(vault_root)
    first = index.find_by_slug("alex")
    assert first is not None
    assert "Initial body." in first.body

    # Bump mtime well into the future so the stale check sees a change
    # without needing a real sleep.
    new_content = path.read_text(encoding="utf-8").replace(
        "Initial body.", "Updated body."
    )
    path.write_text(new_content, encoding="utf-8")
    future = time.time() + 5
    os.utime(path, (future, future))

    second = index.find_by_slug("alex")
    assert second is not None
    assert "Updated body." in second.body


async def test_stale_rescan_updates_backlinks_when_links_change(
    index: VaultIndex, vault_root: Path
) -> None:
    """Re-parsing a page rebuilds the backlink table accordingly."""
    write_page(vault_root, "entity", "alex")
    write_page(vault_root, "entity", "claude")
    source_path = write_page(
        vault_root,
        "concept",
        "voice-pipeline",
        body="Initial reference to [[alex]].",
    )
    await index.scan(vault_root)
    assert {p.slug for p in index.backlinks_to("alex")} == {"voice-pipeline"}
    assert index.backlinks_to("claude") == []

    # Rewrite the page so the wikilink now points to claude instead.
    source_path.write_text(
        "---\ntype: concept\nslug: voice-pipeline\n---\n\nNow points at [[claude]].\n",
        encoding="utf-8",
    )
    future = time.time() + 5
    os.utime(source_path, (future, future))

    # Trigger a refresh via the public API.
    _ = index.find_by_slug("voice-pipeline")

    assert index.backlinks_to("alex") == []
    assert {p.slug for p in index.backlinks_to("claude")} == {"voice-pipeline"}


async def test_deleted_file_is_dropped_on_next_access(
    index: VaultIndex, vault_root: Path
) -> None:
    """If the user deletes a page in Obsidian, the index drops it."""
    path = write_page(vault_root, "entity", "alex")
    await index.scan(vault_root)
    assert index.find_by_slug("alex") is not None

    path.unlink()
    # Force the stale-refresh path to notice the missing file.
    assert index.find_by_slug("alex") is None


async def test_rescan_clears_old_state(
    index: VaultIndex, vault_root: Path
) -> None:
    """A fresh scan replaces the previous in-memory state wholesale."""
    write_page(vault_root, "entity", "alex")
    await index.scan(vault_root)
    assert index.find_by_slug("alex") is not None

    # Remove the page on disk and re-scan
    (vault_root / "entities" / "alex.md").unlink()
    write_page(vault_root, "entity", "claude")
    await index.scan(vault_root)
    assert index.find_by_slug("alex") is None
    assert index.find_by_slug("claude") is not None


async def test_duplicate_wikilinks_count_once_per_source(
    index: VaultIndex, vault_root: Path
) -> None:
    """``backlinks_to`` deduplicates the source list."""
    write_page(vault_root, "entity", "alex")
    write_page(
        vault_root,
        "concept",
        "voice-pipeline",
        body="Refs [[alex]] and again [[alex]] later.",
    )
    await index.scan(vault_root)
    sources = index.backlinks_to("alex")
    assert [p.slug for p in sources] == ["voice-pipeline"]
