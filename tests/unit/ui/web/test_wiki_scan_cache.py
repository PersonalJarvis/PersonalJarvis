"""Guards for the vault-scan hot path behind /tree, /graph and /backlinks.

All three routes project the SAME walk of the Obsidian vault, and every one
of them used to re-read and re-parse every Markdown file on every request.
Opening the Wiki tab fires all three at once and each page click fires more,
so the walk is what the user actually feels as "the wiki is laggy".

Two properties are pinned here:

* an unchanged file is never read or parsed twice, while a changed one is
  picked up immediately (correctness of the cache, not just its speed), and
* concurrent requests share ONE walk instead of queueing identical ones.

Real files in ``tmp_path`` — the filesystem is never mocked.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from jarvis.ui.web import wiki_routes
from jarvis.ui.web.wiki_routes import (
    _scan_visible_pages,
    _scan_visible_pages_sync,
    invalidate_visible_page_cache,
)


@pytest.fixture(autouse=True)
def _clear_scan_cache() -> None:
    """The parse cache is process-wide; keep it from leaking across tests."""
    invalidate_visible_page_cache()
    yield
    invalidate_visible_page_cache()


def _write_page(vault_root: Path, subdir: str, slug: str, body: str) -> Path:
    directory = vault_root / subdir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    path.write_text(
        f"---\ntype: entity\nslug: {slug}\n---\n\n# {slug.title()}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _write_page(root, "entities", "lena", "Lives in Hamburg.")
    _write_page(root, "concepts", "golf", "A sport the user practices.")
    return root


class _ParseCounter:
    """Count how often the tolerant Markdown parser actually runs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        real = wiki_routes.parse_markdown

        def counting(raw: str, path: Path):  # type: ignore[no-untyped-def]
            self.calls.append(Path(path).name)
            return real(raw, path)

        monkeypatch.setattr(wiki_routes, "parse_markdown", counting)


def test_unchanged_vault_is_not_reparsed(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    counter = _ParseCounter(monkeypatch)

    first = _scan_visible_pages_sync(vault)
    assert len(first) == 2
    assert sorted(counter.calls) == ["golf.md", "lena.md"]

    counter.calls.clear()
    second = _scan_visible_pages_sync(vault)

    # Same projection, zero re-parses: a warm scan costs one stat per file.
    assert [p.relative_path for p in second] == [p.relative_path for p in first]
    assert counter.calls == []


def test_edited_page_is_reparsed_and_others_are_not(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_visible_pages_sync(vault)
    counter = _ParseCounter(monkeypatch)

    edited = vault / "entities" / "lena.md"
    edited.write_text(
        "---\ntype: entity\nslug: lena\n---\n\n# Lena\n\nMoved to Berlin.\n",
        encoding="utf-8",
    )
    # Make the change unmistakable even on a coarse-grained clock.
    future = time.time() + 5
    os.utime(edited, (future, future))

    pages = _scan_visible_pages_sync(vault)

    assert counter.calls == ["lena.md"]
    lena = next(p for p in pages if p.relative_path.name == "lena.md")
    assert "Moved to Berlin." in lena.page.body


def test_new_and_deleted_pages_are_picked_up(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_visible_pages_sync(vault)
    counter = _ParseCounter(monkeypatch)

    _write_page(vault, "projects", "cabin", "Weekend build.")
    (vault / "concepts" / "golf.md").unlink()

    names = {p.relative_path.name for p in _scan_visible_pages_sync(vault)}

    assert names == {"lena.md", "cabin.md"}
    assert counter.calls == ["cabin.md"]


def test_explicit_invalidation_forces_a_full_reparse(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scan_visible_pages_sync(vault)
    counter = _ParseCounter(monkeypatch)

    invalidate_visible_page_cache(vault)
    _scan_visible_pages_sync(vault)

    assert sorted(counter.calls) == ["golf.md", "lena.md"]


def test_cache_does_not_grow_without_bound(tmp_path: Path) -> None:
    """Only the few most recent vault roots stay resident."""
    roots = []
    for index in range(wiki_routes._PARSE_CACHE_MAX_ROOTS + 3):
        root = tmp_path / f"vault{index}"
        _write_page(root, "entities", f"page{index}", "body")
        roots.append(root)
        _scan_visible_pages_sync(root)

    assert len(wiki_routes._PARSE_CACHE) == wiki_routes._PARSE_CACHE_MAX_ROOTS


def _symlinks_supported(tmp_path: Path) -> bool:
    """Windows needs developer mode or elevation to create symlinks."""
    probe = tmp_path / "probe"
    target = tmp_path / "probe-target"
    target.write_text("x", encoding="utf-8")
    try:
        os.symlink(target, probe)
    except (OSError, NotImplementedError, AttributeError):
        return False
    probe.unlink()
    return True


def test_symlink_escaping_the_vault_is_not_exposed(vault: Path, tmp_path: Path) -> None:
    """The walk resolves links only — but it must still resolve them."""
    if not _symlinks_supported(tmp_path):
        pytest.skip("symlink creation unavailable on this host")

    outside = tmp_path / "outside" / "secret.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("# Secret\n\nNot part of the vault.\n", encoding="utf-8")
    os.symlink(outside, vault / "entities" / "secret.md")

    names = {p.relative_path.name for p in _scan_visible_pages_sync(vault)}

    assert "secret.md" not in names
    assert names == {"lena.md", "golf.md"}


def test_symlink_inside_the_vault_is_still_listed(vault: Path, tmp_path: Path) -> None:
    if not _symlinks_supported(tmp_path):
        pytest.skip("symlink creation unavailable on this host")

    os.symlink(vault / "entities" / "lena.md", vault / "concepts" / "lena-alias.md")

    names = {p.relative_path.name for p in _scan_visible_pages_sync(vault)}

    assert names == {"lena.md", "golf.md", "lena-alias.md"}


def test_symlinked_directory_is_not_descended(vault: Path, tmp_path: Path) -> None:
    """Matches the old ``os.walk(followlinks=False)`` contract."""
    if not _symlinks_supported(tmp_path):
        pytest.skip("symlink creation unavailable on this host")

    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    (outside_dir / "leaked.md").write_text("# Leaked\n", encoding="utf-8")
    os.symlink(outside_dir, vault / "linked", target_is_directory=True)

    names = {p.relative_path.name for p in _scan_visible_pages_sync(vault)}

    assert "leaked.md" not in names


async def test_concurrent_requests_share_one_walk(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mounting the Wiki tab fires /tree, /graph and /backlinks together."""
    started: list[Path] = []
    real = wiki_routes._scan_visible_pages_sync

    def slow(root: Path):  # type: ignore[no-untyped-def]
        started.append(root)
        time.sleep(0.05)
        return real(root)

    monkeypatch.setattr(wiki_routes, "_scan_visible_pages_sync", slow)

    results = await asyncio.gather(
        _scan_visible_pages(vault),
        _scan_visible_pages(vault),
        _scan_visible_pages(vault),
    )

    assert len(started) == 1
    assert all(len(pages) == 2 for pages in results)
    # All three requests got the same projection object graph.
    assert results[0] is results[1] is results[2]


async def test_a_cancelled_request_does_not_abort_the_shared_walk(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client that disconnects mid-scan must not take the others down."""
    real = wiki_routes._scan_visible_pages_sync

    def slow(root: Path):  # type: ignore[no-untyped-def]
        time.sleep(0.1)
        return real(root)

    monkeypatch.setattr(wiki_routes, "_scan_visible_pages_sync", slow)

    leaving = asyncio.ensure_future(_scan_visible_pages(vault))
    staying = asyncio.ensure_future(_scan_visible_pages(vault))
    await asyncio.sleep(0)
    leaving.cancel()

    pages = await staying
    assert len(pages) == 2
