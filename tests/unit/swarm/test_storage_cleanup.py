"""Owner-scoped deletion remains recoverable across readers and partial cleanup."""

import errno
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PureWindowsPath

import pytest

import jarvis.swarm.store as storage
from jarvis.core.swarm_types import TeamCreate
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, SwarmStoreError, TeamRegistry
from tests.fakes.swarm_storage import running_team


@pytest.mark.parametrize(
    ("original", "resolved", "same"),
    [
        (r"C:\instance\team\state-wal", r"\\?\C:\instance\team\state-wal", True),
        (r"\\server\share\team", r"\\?\UNC\server\share\team", True),
        (r"C:\instance\team\state", r"\\?\C:\elsewhere\state", False),
        (r"C:\instance\team\state", r"\\?\D:\instance\team\state", False),
        (r"C:\instance\team\state", r"C:\instance\other\state", False),
    ],
)
def test_cleanup_normalizes_only_equivalent_windows_path_prefixes(original, resolved, same):
    assert storage._same_resolved_path(PureWindowsPath(original), PureWindowsPath(resolved)) is same


@pytest.mark.skipif(sys.platform != "win32", reason="Windows verbatim path race")
def test_cleanup_accepts_volatile_file_resolving_with_verbatim_prefix(tmp_path, monkeypatch):
    fixture = archived_team(tmp_path)
    marker = fixture.store.path.parent / "volatile-file"
    marker.write_bytes(b"retired data")
    resolve = Path.resolve

    def verbatim_if_volatile(path, *args, **kwargs):
        result = resolve(path, *args, **kwargs)
        return Path("\\\\?\\" + str(result)) if path == marker else result

    monkeypatch.setattr(Path, "resolve", verbatim_if_volatile)
    assert fixture.registry.delete(fixture.team["id"])["deleted"]
    assert not fixture.store.path.parent.exists()


def archived_team(tmp_path):
    fixture = running_team(tmp_path / "instance")
    fixture.store.user_transition("canceled")
    fixture.store.user_transition("archived")
    return fixture


def test_cleanup_hides_team_before_retry_and_allows_existing_reader_to_finish(
    tmp_path, monkeypatch
):
    fixture = archived_team(tmp_path)
    other = fixture.registry.create(TeamCreate(name="Other", goal="Keep", request_key="other"))
    retry_started = threading.Event()
    reader_released = threading.Event()
    remove = storage.shutil.rmtree

    def remove_after_reader(directory):
        assert directory == fixture.store.path.parent
        if not reader_released.is_set():
            retry_started.set()
            # POSIX permits unlinking open SQLite files. Emulate the Windows
            # sharing error so admission and retry semantics are checked on all OSes.
            raise PermissionError(errno.EACCES, "SQLite reader still holds this file")
        remove(directory)

    monkeypatch.setattr(storage.shutil, "rmtree", remove_after_reader)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with fixture.store._tx() as reader:
                before = reader.execute("SELECT record FROM team").fetchone()[0]
                cleanup = pool.submit(fixture.registry.delete, fixture.team["id"])
                assert retry_started.wait(5)
                with pytest.raises(SwarmAccessError):
                    fixture.registry.open(fixture.team["id"])
                with pytest.raises(SwarmAccessError):
                    fixture.store.get()
                assert [team["id"] for team in fixture.registry.list()] == [other["id"]]
                assert reader.execute("SELECT record FROM team").fetchone()[0] == before
        finally:
            reader_released.set()
        assert cleanup.result(timeout=5)["deleted"]
    assert not fixture.store.path.parent.exists()
    assert fixture.registry.open(other["id"]).get()["name"] == "Other"


def test_partial_cleanup_keeps_owner_authority_across_recreation(tmp_path, monkeypatch):
    fixture = archived_team(tmp_path)
    objects = fixture.store.path.parent / "objects"
    objects.mkdir(exist_ok=True)
    (objects / "leftover").write_bytes(b"remove on retry")
    retained = fixture.registry.root / "publications" / "retained"
    retained.parent.mkdir()
    retained.write_bytes(b"keep forever")
    remove = storage.shutil.rmtree

    def partially_remove(directory):
        assert directory == fixture.store.path.parent
        fixture.store.path.unlink()
        raise OSError(errno.EIO, "Injected cleanup interruption after database removal")

    monkeypatch.setattr(storage.shutil, "rmtree", partially_remove)
    with pytest.raises(SwarmStoreError, match="cleanup is pending"):
        fixture.registry.delete(fixture.team["id"])
    assert fixture.registry.list() == []
    assert not fixture.store.path.exists()
    resumed = TeamRegistry(fixture.registry.root)
    with pytest.raises(SwarmAccessError):
        resumed.delete(fixture.team["id"], "other-owner")
    with pytest.raises(SwarmAccessError):
        fixture.store.get()
    monkeypatch.setattr(storage.shutil, "rmtree", remove)
    assert resumed.delete(fixture.team["id"])["deleted"]
    assert TeamRegistry(fixture.registry.root).delete(fixture.team["id"])["deleted"]
    assert not objects.exists()
    assert retained.read_bytes() == b"keep forever"


def test_cleanup_retry_is_bounded_and_remains_resumable(tmp_path, monkeypatch):
    fixture = archived_team(tmp_path)
    attempts = []
    remove = storage.shutil.rmtree

    def busy(directory):
        attempts.append(directory)
        raise PermissionError(errno.EACCES, "File remains open")

    monkeypatch.setattr(storage, "_CLEANUP_TIMEOUT", 0.04)
    monkeypatch.setattr(storage.shutil, "rmtree", busy)
    with pytest.raises(SwarmStoreError, match="cleanup is pending"):
        fixture.registry.delete(fixture.team["id"])
    assert 2 <= len(attempts) <= 10
    assert fixture.registry.list() == []
    monkeypatch.setattr(storage.shutil, "rmtree", remove)
    assert TeamRegistry(fixture.registry.root).delete(fixture.team["id"])["deleted"]


def test_open_admitted_before_deletion_cannot_recreate_the_database(tmp_path, monkeypatch):
    fixture = archived_team(tmp_path)
    admitted = threading.Event()
    resume = threading.Event()
    original = fixture.registry._assert_visible
    remove = storage.shutil.rmtree
    operation_thread = []

    def pause_after_admission(team_id, owner):
        original(team_id, owner)
        if operation_thread and threading.get_ident() == operation_thread[0]:
            admitted.set()
            assert resume.wait(5)

    def stale_open():
        operation_thread.append(threading.get_ident())
        return fixture.store.get()

    def remove_database_only(directory):
        assert directory == fixture.store.path.parent
        fixture.store.path.unlink()
        raise OSError(errno.EIO, "Interrupted with the workspace directory still present")

    monkeypatch.setattr(fixture.registry, "_assert_visible", pause_after_admission)
    monkeypatch.setattr(storage.shutil, "rmtree", remove_database_only)
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale = pool.submit(stale_open)
        try:
            assert admitted.wait(5)
            with pytest.raises(SwarmStoreError, match="cleanup is pending"):
                fixture.registry.delete(fixture.team["id"])
        finally:
            resume.set()
        with pytest.raises(SwarmAccessError):
            stale.result(timeout=5)
    assert fixture.store.path.parent.is_dir()
    assert not fixture.store.path.exists()
    monkeypatch.setattr(storage.shutil, "rmtree", remove)
    fixture.registry.delete(fixture.team["id"])
    assert not fixture.store.path.parent.exists()


def test_list_skips_a_team_deleted_after_catalog_snapshot(tmp_path, monkeypatch):
    fixture = archived_team(tmp_path)
    original = fixture.registry.open
    snapshot_read = False

    def delete_before_open(team_id, owner="local-user"):
        nonlocal snapshot_read
        if not snapshot_read:
            snapshot_read = True
            fixture.registry.delete(team_id, owner)
        return original(team_id, owner)

    monkeypatch.setattr(fixture.registry, "open", delete_before_open)
    assert fixture.registry.list() == []


def test_cleanup_refuses_linked_paths_and_noncanonical_identities(tmp_path):
    fixture = archived_team(tmp_path)
    for invalid in ("../escape", "A" * 32, "publications", "f" * 31, None):
        with pytest.raises(SwarmAccessError):
            fixture.registry.delete(invalid)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"untouched")
    link = fixture.store.path.parent / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Symlink creation is unavailable: {error}")
    with pytest.raises(SwarmAccessError, match="linked paths"):
        fixture.registry.delete(fixture.team["id"])
    assert outside.read_bytes() == b"untouched"
    assert fixture.registry.open(fixture.team["id"]).get()["state"] == "archived"


def test_completed_cleanup_cannot_target_a_restored_namespace(tmp_path):
    fixture = archived_team(tmp_path)
    backup = tmp_path / "backup"
    fixture.store.backup(backup)
    fixture.registry.delete(fixture.team["id"])
    with pytest.raises(SwarmConflictError):
        fixture.registry.restore(backup, request_key="restore")
    with sqlite3.connect(fixture.registry.root / "catalog.sqlite3") as catalog:
        tombstone = catalog.execute(
            "SELECT owner,completed_at FROM team_cleanup WHERE id=?", (fixture.team["id"],)
        ).fetchone()
    assert tombstone[0] == "local-user"
    assert tombstone[1] is not None
