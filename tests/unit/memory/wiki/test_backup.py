"""Unit tests for ``jarvis.memory.wiki.backup``.

The :class:`BackupManager` is a thin tar.gz helper but it is the only
recovery surface the writer trusts. The tests focus on:

* the snapshot includes the right files and excludes ``_archive`` /
  ``attachments``;
* the snapshot path uses the documented ``wiki-<ts>.tar.gz`` filename;
* restore puts a single member back, atomically, and refuses
  path-traversal;
* rotation keeps ``max_backups`` archives newest-first and deletes the
  rest.
"""
from __future__ import annotations

import datetime as _dt
import tarfile
import time
from pathlib import Path

import pytest

from jarvis.memory.wiki.backup import (
    BACKUP_FILENAME_GLOB,
    DEFAULT_MAX_BACKUPS,
    BackupError,
    BackupManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A populated vault skeleton with one file in every relevant subdir."""
    root = tmp_path / "workspace"
    for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
        (root / sub).mkdir(parents=True)
    (root / "schema.md").write_text("schema body", encoding="utf-8")
    (root / "index.md").write_text("# index", encoding="utf-8")
    (root / "log.md").write_text("# log", encoding="utf-8")
    (root / "entities" / "ruben.md").write_text("entity ruben", encoding="utf-8")
    (root / "concepts" / "wiki.md").write_text("concept wiki", encoding="utf-8")
    (root / "_archive" / "old.md").write_text("archived — should be excluded", encoding="utf-8")
    (root / "attachments" / "big.bin").write_bytes(b"\x00" * 1024)
    return root


@pytest.fixture
def manager(vault: Path, tmp_path: Path) -> BackupManager:
    return BackupManager(
        vault_root=vault,
        backup_dir=tmp_path / "backups",
    )


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def test_snapshot_creates_archive_with_expected_name(manager: BackupManager) -> None:
    fixed_now = _dt.datetime(2026, 5, 11, 19, 42, 7)
    out = manager.snapshot(now=fixed_now)
    assert out.exists()
    assert out.name.startswith("wiki-20260511194207")
    assert out.suffix == ".gz"


def test_snapshot_excludes_archive_and_attachments(manager: BackupManager) -> None:
    out = manager.snapshot()
    with tarfile.open(out, "r:gz") as tar:
        members = sorted(m.name for m in tar.getmembers() if m.isfile())
    assert "schema.md" in members
    assert "entities/ruben.md" in members
    assert "concepts/wiki.md" in members
    # Hard-negative: nothing under _archive/ or attachments/ may leak.
    assert not any(m.startswith("_archive/") for m in members)
    assert not any(m.startswith("attachments/") for m in members)


def test_snapshot_uses_vault_relative_arcnames(manager: BackupManager) -> None:
    out = manager.snapshot()
    with tarfile.open(out, "r:gz") as tar:
        names = [m.name for m in tar.getmembers() if m.isfile()]
    # No member name may contain a drive letter or absolute prefix —
    # restoring must work on any host without rewriting paths.
    for name in names:
        assert not name.startswith("/")
        assert ":" not in name
        assert not name.startswith("..")


def test_snapshot_collisions_within_one_second_disambiguate(
    manager: BackupManager,
) -> None:
    fixed = _dt.datetime(2026, 5, 11, 19, 42, 7)
    a = manager.snapshot(now=fixed)
    b = manager.snapshot(now=fixed)
    assert a != b
    assert a.exists() and b.exists()


def test_snapshot_raises_when_vault_missing(tmp_path: Path) -> None:
    mgr = BackupManager(
        vault_root=tmp_path / "does-not-exist",
        backup_dir=tmp_path / "backups",
    )
    with pytest.raises(BackupError):
        mgr.snapshot()


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def test_restore_round_trips_a_modified_page(manager: BackupManager, vault: Path) -> None:
    out = manager.snapshot()
    target = vault / "entities" / "ruben.md"
    target.write_text("local edit, must be reverted", encoding="utf-8")

    restored = manager.restore(out, "entities/ruben.md")
    assert restored == target.resolve()
    assert target.read_text(encoding="utf-8") == "entity ruben"


def test_restore_creates_missing_subdirs(manager: BackupManager, vault: Path) -> None:
    # Snapshot, then delete the file and its parent dir.
    out = manager.snapshot()
    target = vault / "entities" / "ruben.md"
    target.unlink()
    # Parent dir survives because vault still has other entities, but
    # the restore path must work when the parent is also gone.
    (vault / "entities").rmdir()

    restored = manager.restore(out, "entities/ruben.md")
    assert restored.exists()
    assert restored.read_text(encoding="utf-8") == "entity ruben"


def test_restore_refuses_path_traversal(manager: BackupManager) -> None:
    out = manager.snapshot()
    with pytest.raises(BackupError):
        manager.restore(out, "../etc/passwd")


def test_restore_rejects_missing_member(manager: BackupManager) -> None:
    out = manager.snapshot()
    with pytest.raises(BackupError):
        manager.restore(out, "entities/never-existed.md")


def test_restore_rejects_missing_archive(manager: BackupManager, tmp_path: Path) -> None:
    with pytest.raises(BackupError):
        manager.restore(tmp_path / "no-such-archive.tar.gz", "schema.md")


# ---------------------------------------------------------------------------
# rotation + listing
# ---------------------------------------------------------------------------


def test_list_backups_returns_newest_first(manager: BackupManager) -> None:
    paths: list[Path] = []
    for delta in range(3):
        ts = _dt.datetime(2026, 5, 11, 12, 0, delta)
        paths.append(manager.snapshot(now=ts))
        # Force distinct mtimes — on some filesystems the resolution is
        # only one second, and the helper is fast enough to land in the
        # same tick.
        time.sleep(0.01)

    listed = manager.list_backups()
    assert len(listed) == 3
    # Listed newest first; we created them oldest first, so the list
    # should be the reverse insertion order.
    assert listed[0] == paths[-1]
    assert listed[-1] == paths[0]


def test_rotate_keeps_max_backups_newest(vault: Path, tmp_path: Path) -> None:
    mgr = BackupManager(
        vault_root=vault,
        backup_dir=tmp_path / "backups",
        max_backups=3,
    )
    created: list[Path] = []
    for i in range(5):
        ts = _dt.datetime(2026, 5, 11, 12, 0, i)
        created.append(mgr.snapshot(now=ts))
        time.sleep(0.01)

    deleted = mgr.rotate()
    survivors = mgr.list_backups()

    assert len(survivors) == 3
    assert len(deleted) == 2
    # The two oldest must be deleted.
    deleted_names = {p.name for p in deleted}
    assert created[0].name in deleted_names
    assert created[1].name in deleted_names
    # The three newest must survive.
    survivor_names = {p.name for p in survivors}
    assert created[-1].name in survivor_names
    assert created[-2].name in survivor_names
    assert created[-3].name in survivor_names


def test_rotate_is_noop_when_under_cap(manager: BackupManager) -> None:
    manager.snapshot()
    deleted = manager.rotate()
    assert deleted == []


def test_default_cap_is_ten(vault: Path, tmp_path: Path) -> None:
    mgr = BackupManager(vault_root=vault, backup_dir=tmp_path / "backups")
    assert mgr.max_backups == DEFAULT_MAX_BACKUPS


def test_constructor_rejects_zero_max_backups(vault: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        BackupManager(
            vault_root=vault,
            backup_dir=tmp_path / "backups",
            max_backups=0,
        )


def test_glob_pattern_matches_what_snapshot_produces(manager: BackupManager) -> None:
    out = manager.snapshot()
    assert out.match(BACKUP_FILENAME_GLOB)
