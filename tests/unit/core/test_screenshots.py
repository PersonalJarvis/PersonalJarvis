"""Tests for the development-screenshot folder + 10-day retention sweep.

Covers ``jarvis/core/screenshots.py``: the canonical ``screenshots/`` directory,
the stray-root-capture consolidation (self-healing — agents that drop captures
in the repo root get them swept into place), and the mtime-based retention prune.

Design note: the pure functions take explicit ``Path`` arguments (dependency
injection) so they run entirely under ``tmp_path`` — no monkeypatching of the
real repo root, so a test can never delete or move a real screenshot.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from jarvis.core import screenshots as sc


def _touch(p: Path, *, age_days: float) -> None:
    """Create a file and backdate its mtime by ``age_days`` days."""
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    past = time.time() - age_days * 24 * 60 * 60
    os.utime(p, (past, past))


def test_screenshots_dir_is_repo_root_subdir() -> None:
    d = sc.screenshots_dir()
    assert d.name == "screenshots"
    # The parent must be the actual repo root (where pyproject.toml lives).
    assert (d.parent / "pyproject.toml").exists()


def test_prune_deletes_files_older_than_cutoff(tmp_path: Path) -> None:
    old = tmp_path / "old.png"
    fresh = tmp_path / "fresh.png"
    _touch(old, age_days=11)
    _touch(fresh, age_days=1)

    stats = sc.prune_old_screenshots(tmp_path, max_age_days=10)

    assert not old.exists()
    assert fresh.exists()
    assert stats["removed"] == 1
    assert stats["scanned"] == 2


def test_prune_disabled_when_max_age_non_positive(tmp_path: Path) -> None:
    old = tmp_path / "old.png"
    _touch(old, age_days=100)

    stats = sc.prune_old_screenshots(tmp_path, max_age_days=0)

    assert old.exists()  # 0 means "off", never "delete everything"
    assert stats["removed"] == 0


def test_prune_missing_dir_returns_zero_stats(tmp_path: Path) -> None:
    stats = sc.prune_old_screenshots(tmp_path / "does-not-exist", max_age_days=10)

    assert stats["removed"] == 0
    assert stats["scanned"] == 0


def test_consolidate_moves_stray_root_images(tmp_path: Path) -> None:
    root = tmp_path
    dest = tmp_path / "screenshots"
    (root / "chat_redesign.png").write_bytes(b"a")
    (root / "shot.jpg").write_bytes(b"b")
    (root / "Screenshot 2026-05-30 225136.png").write_bytes(b"c")

    moved = sc.consolidate_stray_root_screenshots(root, dest)

    assert moved == 3
    assert (dest / "chat_redesign.png").exists()
    assert (dest / "shot.jpg").exists()
    assert not (root / "chat_redesign.png").exists()


def test_consolidate_ignores_non_images_and_does_not_recurse_dest(tmp_path: Path) -> None:
    root = tmp_path
    dest = tmp_path / "screenshots"
    dest.mkdir()
    (dest / "already.png").write_bytes(b"z")  # already in place — must be left alone
    (root / "notes.md").write_text("hi")
    (root / "data.json").write_text("{}")

    moved = sc.consolidate_stray_root_screenshots(root, dest)

    assert moved == 0
    assert (root / "notes.md").exists()
    assert (root / "data.json").exists()
    assert (dest / "already.png").exists()


def test_consolidate_never_overwrites_on_name_clash(tmp_path: Path) -> None:
    root = tmp_path
    dest = tmp_path / "screenshots"
    dest.mkdir()
    (dest / "shot.png").write_bytes(b"original")
    (root / "shot.png").write_bytes(b"new")

    moved = sc.consolidate_stray_root_screenshots(root, dest)

    assert moved == 1
    # The pre-existing file must survive untouched.
    assert (dest / "shot.png").read_bytes() == b"original"
    # The moved file landed under a unique name.
    assert len(list(dest.glob("*.png"))) == 2


def test_sweep_consolidates_then_prunes_and_never_raises(tmp_path: Path) -> None:
    root = tmp_path
    dest = tmp_path / "screenshots"
    # A stray fresh capture in the root, and an old one already in dest.
    (root / "fresh_capture.png").write_bytes(b"new")
    dest.mkdir()
    _touch(dest / "ancient.png", age_days=30)

    stats = sc.sweep_screenshots(root=root, dest=dest, max_age_days=10)

    assert isinstance(stats, dict)
    # stray capture was moved in, old one pruned out
    assert (dest / "fresh_capture.png").exists()
    assert not (dest / "ancient.png").exists()
    assert not (root / "fresh_capture.png").exists()
