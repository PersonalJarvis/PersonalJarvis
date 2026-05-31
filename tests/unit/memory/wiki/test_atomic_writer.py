"""Unit tests for ``jarvis.memory.wiki.atomic_writer``.

The :class:`AtomicWriter` is the only path that writes wiki pages to
disk. The five-step pipeline is:

1. 30-second concurrent-edit lock (skip recently-touched files).
2. Single tar.gz vault snapshot per ``apply()`` call.
3. Tempfile + ``os.replace`` for every surviving update.
4. Re-parse via ``PageRepository``; roll back invalid pages from the
   snapshot, leave valid ones alone.
5. Backup rotation as hygiene at the end.

These tests cover the critical paths called out in
``docs/phase-b1-wiki-curator/README.md`` Part 5 → Instance C plus
several edge cases (rename, archive, brand-new pages, mid-write crash).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import os
import time
from pathlib import Path
from unittest import mock

import pytest

from jarvis.memory.wiki.atomic_writer import (
    ALLOWED_OPERATIONS,
    AtomicWriteError,
    AtomicWriter,
)
from jarvis.memory.wiki.backup import BackupManager
from jarvis.memory.wiki.protocols import PageUpdate

from .conftest import FakePageRepository, write_page


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def writer(vault_root: Path, tmp_path: Path) -> AtomicWriter:
    return AtomicWriter(
        vault_root=vault_root,
        backup_dir=tmp_path / "backups",
        max_backups=10,
    )


def _valid_entity_body(slug: str, body: str = "fresh body") -> str:
    """Build a minimal frontmatter + body string the FakePageRepository
    will accept as ``is_schema_valid=True``."""
    return (
        "---\n"
        "type: entity\n"
        f"slug: {slug}\n"
        "---\n"
        "\n"
        f"{body}\n"
    )


def _broken_entity_body(slug: str) -> str:
    """A body the FakePageRepository will mark as invalid.

    Strategy: declare ``type: garbage`` — the fake parser only accepts
    type values from a known set, so this trips ``is_schema_valid=False``
    even though the directory is ``entities/``. Going via an explicit
    bogus type is more deterministic than omitting the key (the fake
    falls back to inferring the type from the parent directory, which
    would resurrect validity).
    """
    return (
        "---\n"
        "type: garbage_type_value\n"
        f"slug: {slug}\n"
        "---\n"
        "\n"
        "fake parser rejects unknown types\n"
    )


# ---------------------------------------------------------------------------
# Step 1 — 30-second concurrent-edit lock
# ---------------------------------------------------------------------------


def test_recently_touched_page_is_skipped(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    """A page whose mtime is within 30s of the apply call is left alone.

    This is the "user is editing in Obsidian" guard. The skipped path
    surfaces in ``WriteResult.skipped_due_to_recent_edit`` and the file
    contents on disk stay unchanged.
    """
    target = write_page(vault_root, "entity", "the maintainer", body="user just typed this")
    original = target.read_text(encoding="utf-8")

    # Force the file mtime to "5 seconds ago" — well inside the lock.
    five_seconds_ago = time.time() - 5.0
    os.utime(target, (five_seconds_ago, five_seconds_ago))

    update = PageUpdate(
        target_path=target,
        operation="update",
        new_body=_valid_entity_body("the maintainer", body="curator-overwritten body"),
        reason="should be skipped",
    )

    result = asyncio.run(writer.apply([update], repo=fake_repo))

    assert result.skipped_due_to_recent_edit == [target.resolve()]
    assert result.applied == []
    assert result.failed_validation == []
    # Disk is untouched.
    assert target.read_text(encoding="utf-8") == original
    # No backup is taken when nothing survives the lock.
    assert result.backup_path == Path()


def test_old_mtime_passes_lock(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    target = write_page(vault_root, "entity", "the maintainer", body="old content")
    # Make the file 5 minutes old — well past the lock.
    five_minutes_ago = time.time() - 300.0
    os.utime(target, (five_minutes_ago, five_minutes_ago))

    update = PageUpdate(
        target_path=target,
        operation="update",
        new_body=_valid_entity_body("the maintainer", body="updated"),
    )
    result = asyncio.run(writer.apply([update], repo=fake_repo))

    assert result.applied == [target.resolve()]
    assert result.skipped_due_to_recent_edit == []
    assert "updated" in target.read_text(encoding="utf-8")


def test_brand_new_page_is_never_lock_skipped(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    target = vault_root / "entities" / "newbie.md"
    assert not target.exists()
    update = PageUpdate(
        target_path=target,
        operation="create",
        new_body=_valid_entity_body("newbie", body="hello"),
    )
    result = asyncio.run(writer.apply([update], repo=fake_repo))
    assert target.exists()
    assert result.applied == [target.resolve()]


# ---------------------------------------------------------------------------
# Step 2 — single backup per apply()
# ---------------------------------------------------------------------------


def test_single_backup_per_apply_call(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository, tmp_path: Path
) -> None:
    # Pre-create three pages so we have something to back up.
    for slug in ("a", "b", "c"):
        p = write_page(vault_root, "entity", slug, body="initial")
        # Age them past the lock.
        os.utime(p, (time.time() - 600, time.time() - 600))

    updates = [
        PageUpdate(
            target_path=vault_root / "entities" / f"{slug}.md",
            operation="update",
            new_body=_valid_entity_body(slug, body=f"new {slug}"),
        )
        for slug in ("a", "b", "c")
    ]
    result = asyncio.run(writer.apply(updates, repo=fake_repo))

    assert len(result.applied) == 3
    backups = list((tmp_path / "backups").glob("wiki-*.tar.gz"))
    assert len(backups) == 1, f"expected exactly one backup, got {len(backups)}"
    assert result.backup_path == backups[0]


# ---------------------------------------------------------------------------
# Step 4 — validation rollback
# ---------------------------------------------------------------------------


def test_validation_rollback_restores_only_invalid_page(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    """A broken update is reverted from the snapshot; valid neighbours stay applied."""
    good = write_page(vault_root, "entity", "good", body="initial good")
    bad = write_page(vault_root, "entity", "bad", body="initial bad")
    os.utime(good, (time.time() - 600, time.time() - 600))
    os.utime(bad, (time.time() - 600, time.time() - 600))

    updates = [
        PageUpdate(
            target_path=good,
            operation="update",
            new_body=_valid_entity_body("good", body="good — updated"),
        ),
        PageUpdate(
            target_path=bad,
            operation="update",
            new_body=_broken_entity_body("bad"),  # missing type → invalid
        ),
    ]

    result = asyncio.run(writer.apply(updates, repo=fake_repo))

    assert result.applied == [good.resolve()]
    assert result.failed_validation == [bad.resolve()]
    # Good page reflects the new content.
    assert "good — updated" in good.read_text(encoding="utf-8")
    # Bad page rolled back to the pre-apply text.
    assert bad.read_text(encoding="utf-8").startswith("---\ntype: entity")
    assert "initial bad" in bad.read_text(encoding="utf-8")


def test_validation_rollback_deletes_brand_new_invalid_page(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    """A page that was *created* in this call and then fails validation
    must be removed from disk, not restored from a (non-existent) snapshot
    member."""
    target = vault_root / "entities" / "ghost.md"
    assert not target.exists()

    update = PageUpdate(
        target_path=target,
        operation="create",
        new_body=_broken_entity_body("ghost"),
    )
    result = asyncio.run(writer.apply([update], repo=fake_repo))

    assert result.failed_validation == [target.resolve()]
    assert result.applied == []
    assert not target.exists()


# ---------------------------------------------------------------------------
# Step 3 — write atomicity / mid-write crash
# ---------------------------------------------------------------------------


def test_crash_mid_write_does_not_corrupt_other_pages(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    """Simulate ``os.replace`` raising on the second of three updates.

    The first write must have landed cleanly. The second write must not
    leave a half-written file (the tempfile must be cleaned up). The
    third update must still happen — sequential failures do not abort
    the loop.
    """
    targets = []
    for slug in ("first", "second", "third"):
        p = write_page(vault_root, "entity", slug, body=f"initial {slug}")
        os.utime(p, (time.time() - 600, time.time() - 600))
        targets.append(p)

    updates = [
        PageUpdate(
            target_path=targets[i],
            operation="update",
            new_body=_valid_entity_body(slug, body=f"new {slug}"),
        )
        for i, slug in enumerate(("first", "second", "third"))
    ]

    real_replace = os.replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        # Second .md write fails; backup write (which uses tempfile too,
        # but a different filename suffix) must not be affected.
        if call_count["n"] == 2 and str(dst).endswith("second.md"):
            raise OSError("simulated mid-write crash")
        return real_replace(src, dst)

    with mock.patch("jarvis.memory.wiki.atomic_writer.os.replace", side_effect=flaky_replace):
        result = asyncio.run(writer.apply(updates, repo=fake_repo))

    # First and third applied; second skipped due to write failure.
    applied_names = {p.name for p in result.applied}
    assert "first.md" in applied_names
    assert "third.md" in applied_names
    assert "second.md" not in applied_names
    # The crash victim is unchanged on disk (still the initial content).
    assert "initial second" in targets[1].read_text(encoding="utf-8")
    # No leftover .tmp files in the entities directory.
    leftovers = list((vault_root / "entities").glob("*.tmp"))
    assert leftovers == [], f"tempfile leak: {leftovers}"


# ---------------------------------------------------------------------------
# Step 5 — backup rotation
# ---------------------------------------------------------------------------


def test_backup_rotation_keeps_max_backups(
    vault_root: Path, tmp_path: Path, fake_repo: FakePageRepository
) -> None:
    """After 11 applies with max_backups=10, exactly 10 archives remain."""
    write_page(vault_root, "entity", "the maintainer", body="seed")
    target = vault_root / "entities" / "the maintainer.md"

    writer_small = AtomicWriter(
        vault_root=vault_root,
        backup_dir=tmp_path / "backups",
        max_backups=10,
    )

    for i in range(11):
        # Bump mtime back so the lock never fires.
        os.utime(target, (time.time() - 600, time.time() - 600))
        update = PageUpdate(
            target_path=target,
            operation="update",
            new_body=_valid_entity_body("the maintainer", body=f"iteration {i}"),
        )
        asyncio.run(writer_small.apply([update], repo=fake_repo))
        time.sleep(0.02)  # keep snapshot mtimes distinct on coarse FS

    backups = list((tmp_path / "backups").glob("wiki-*.tar.gz"))
    assert len(backups) == 10


# ---------------------------------------------------------------------------
# Defensive checks (path traversal, unknown operations, drive guard)
# ---------------------------------------------------------------------------


def test_target_outside_vault_is_rejected(
    vault_root: Path, fake_repo: FakePageRepository, tmp_path: Path
) -> None:
    """The shared ``vault_root`` fixture pins the vault to ``tmp_path``;
    we put the writer's vault one level deeper so the parent of
    ``vault_root`` is a known outside region."""
    deeper_vault = vault_root / "entities"
    backup_dir = tmp_path / "_writer-backups"
    writer_local = AtomicWriter(
        vault_root=deeper_vault,
        backup_dir=backup_dir,
    )
    # vault_root itself is now outside ``deeper_vault``.
    outside = vault_root / "concepts" / "page.md"
    update = PageUpdate(
        target_path=outside,
        operation="create",
        new_body=_valid_entity_body("rogue"),
    )
    with pytest.raises(AtomicWriteError):
        asyncio.run(writer_local.apply([update], repo=fake_repo))


def test_unknown_operation_is_rejected(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    target = vault_root / "entities" / "x.md"
    update = PageUpdate(
        target_path=target,
        operation="explode",  # not in ALLOWED_OPERATIONS
        new_body="whatever",
    )
    with pytest.raises(AtomicWriteError):
        asyncio.run(writer.apply([update], repo=fake_repo))


def test_rename_without_source_is_rejected(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    target = vault_root / "entities" / "renamed.md"
    update = PageUpdate(
        target_path=target,
        operation="rename",
        new_body=_valid_entity_body("renamed"),
        rename_from=None,
    )
    with pytest.raises(AtomicWriteError):
        asyncio.run(writer.apply([update], repo=fake_repo))


def test_allowed_operations_set_matches_protocol(
) -> None:
    assert ALLOWED_OPERATIONS == frozenset(
        {"create", "update", "rename", "archive"}
    )


# ---------------------------------------------------------------------------
# Operation: rename
# ---------------------------------------------------------------------------


def test_rename_writes_new_path_and_unlinks_old(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    old = write_page(vault_root, "entity", "luetke", body="old slug")
    os.utime(old, (time.time() - 600, time.time() - 600))
    new = vault_root / "entities" / "the maintainer-luetke.md"

    update = PageUpdate(
        target_path=new,
        operation="rename",
        new_body=_valid_entity_body("the maintainer-luetke", body="new slug"),
        rename_from=old,
    )
    result = asyncio.run(writer.apply([update], repo=fake_repo))

    assert result.applied == [new.resolve()]
    assert new.exists()
    assert not old.exists()


# ---------------------------------------------------------------------------
# Operation: archive
# ---------------------------------------------------------------------------


def test_archive_moves_page_into_archive_dir(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    target = write_page(vault_root, "entity", "deprecated", body="bye")
    os.utime(target, (time.time() - 600, time.time() - 600))

    update = PageUpdate(
        target_path=target,
        operation="archive",
        new_body="",  # ignored for archive
    )
    result = asyncio.run(writer.apply([update], repo=fake_repo))

    assert result.applied == [target.resolve()]
    assert not target.exists()
    archived = vault_root / "_archive" / "entities" / "deprecated.md"
    assert archived.exists()
    assert "bye" in archived.read_text(encoding="utf-8")


def test_archive_of_missing_page_is_noop(
    writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
) -> None:
    target = vault_root / "entities" / "never.md"
    update = PageUpdate(target_path=target, operation="archive", new_body="")
    result = asyncio.run(writer.apply([update], repo=fake_repo))
    # Reported as applied (the operation succeeded, even if it was a no-op),
    # but nothing on disk changed.
    assert result.applied == [target.resolve()]
    assert not target.exists()
    assert not (vault_root / "_archive" / "entities" / "never.md").exists()


# ---------------------------------------------------------------------------
# Result shape sanity
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_result(
    writer: AtomicWriter, fake_repo: FakePageRepository
) -> None:
    result = asyncio.run(writer.apply([], repo=fake_repo))
    assert result.applied == []
    assert result.skipped_due_to_recent_edit == []
    assert result.failed_validation == []
    assert result.backup_path == Path()


def test_writer_uses_provided_backup_manager(
    vault_root: Path, tmp_path: Path, fake_repo: FakePageRepository
) -> None:
    """Injecting a BackupManager replaces the auto-built one."""
    custom = BackupManager(
        vault_root=vault_root,
        backup_dir=tmp_path / "custom-backups",
        max_backups=3,
    )
    w = AtomicWriter(
        vault_root=vault_root,
        backup_dir=tmp_path / "default-backups",
        backup_manager=custom,
    )
    assert w.backup_manager is custom

    target = vault_root / "entities" / "x.md"
    update = PageUpdate(
        target_path=target,
        operation="create",
        new_body=_valid_entity_body("x"),
    )
    asyncio.run(w.apply([update], repo=fake_repo))
    # Backup landed in the custom dir, not the default one.
    assert any((tmp_path / "custom-backups").glob("wiki-*.tar.gz"))
    assert not (tmp_path / "default-backups").exists()
