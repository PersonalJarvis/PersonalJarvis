"""Backup publication stays atomic while brief filesystem locks recover."""

import pytest

from jarvis.swarm.distributed import snapshot
from jarvis.swarm.store import SwarmConflictError
from tests.fakes.swarm_snapshot_publish import DelayedRename


@pytest.fixture
def paths(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "object").write_bytes(b"complete snapshot")
    return staging, tmp_path / "published"


def test_snapshot_publication_recovers_without_partial_visibility(monkeypatch, paths):
    rename = DelayedRename(2)
    monkeypatch.setattr(snapshot.os, "replace", rename)
    staging, target = paths
    snapshot._publish_snapshot(staging, target)
    assert rename.calls == 3
    assert not staging.exists()
    assert (target / "object").read_bytes() == b"complete snapshot"


def test_persistent_lock_fails_without_publishing_snapshot(monkeypatch, paths):
    rename = DelayedRename(100)
    monkeypatch.setattr(snapshot.os, "replace", rename)
    with pytest.raises(PermissionError, match="filesystem lock"):
        snapshot._publish_snapshot(*paths)
    assert rename.calls == 6
    assert paths[0].is_dir() and not paths[1].exists()


def test_retry_does_not_overwrite_a_competing_export(monkeypatch, paths):
    rename = DelayedRename(1, competing_target=True)
    monkeypatch.setattr(snapshot.os, "replace", rename)
    with pytest.raises(SwarmConflictError, match="new snapshot directory"):
        snapshot._publish_snapshot(*paths)
    assert rename.calls == 1
    assert (paths[1] / "keep").read_bytes() == b"other export"
    assert paths[0].is_dir()


def test_unrelated_filesystem_errors_are_not_retried(monkeypatch, paths):
    rename = DelayedRename(1, error_type=FileNotFoundError)
    monkeypatch.setattr(snapshot.os, "replace", rename)
    with pytest.raises(FileNotFoundError):
        snapshot._publish_snapshot(*paths)
    assert rename.calls == 1
    assert paths[0].is_dir() and not paths[1].exists()
