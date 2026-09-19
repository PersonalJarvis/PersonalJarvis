"""Portable storage lifecycle preserves selected publications and fences authority."""

import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest

from jarvis.core.swarm_types import TaskSpec, TeamCreate
from jarvis.swarm.lifecycle import StorageLifecycle
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, SwarmStoreError
from tests.fakes.swarm_runtime import runtime, terminal


async def finished(service, name="Lifecycle"):
    team = await service.create_team(
        TeamCreate(
            name=name,
            goal="49",
            request_key=name,
            tasks=[TaskSpec(id="proof", title="Proof", description="49", acceptance="Return49")],
        )
    )
    await service.control(team["id"], "start")
    assert (await terminal(service, team["id"]))["state"] == "succeeded"
    artifacts = await service.records(team["id"], "artifacts")
    publication = await service.publish(team["id"], artifacts[0]["id"], "selected-publication")
    return team, publication


@pytest.mark.asyncio
async def test_export_restore_elsewhere_preserves_publication_bytes_and_scope(tmp_path):
    source, destination = runtime(tmp_path / "source"), runtime(tmp_path / "destination")
    try:
        team, publication = await finished(source)
        other, other_publication = await finished(source, "Unrelated")
        exported = await source.backup(team["id"], "snapshot")
        assert await source.backup(team["id"], "snapshot") == exported
        path = await source.backup_file(team["id"], exported["backup_id"])
        restored = await destination.restore_backup(path, "restore-here")
        assert restored["team"]["id"] == team["id"]
        assert restored["team"]["lead_id"] == team["lead_id"]
        assert await destination.publication(publication["id"]) == await source.publication(
            publication["id"]
        )
        with pytest.raises(PermissionError):
            await destination.publication(other_publication["id"])
        assert (await destination.restore_backup(path, "restore-here"))["team"]["id"] == team["id"]
        with pytest.raises(PermissionError):
            await source.backup_file(other["id"], exported["backup_id"])
    finally:
        await source.stop()
        await destination.stop()


@pytest.mark.asyncio
async def test_corrupt_same_identity_restore_quarantines_and_rotates_old_actor(tmp_path):
    service = runtime(tmp_path / "swarm")
    try:
        team, _ = await finished(service)
        store = service.registry.open(team["id"])
        controller = await service._controller(store)
        old_actor = store.actor_for(controller, team["lead_id"])
        backup = await service.backup(team["id"], "before-corruption")
        archive = await service.backup_file(team["id"], backup["backup_id"])
        with store.path.open("wb") as output:
            output.write(b"broken database")
        restored = await service.restore_backup(archive, "replace-corrupt", team["id"])
        assert restored["quarantine_id"]
        quarantined = (
            service.root
            / "lifecycle/quarantine"
            / restored["quarantine_id"]
            / team["id"]
            / "team.sqlite3"
        )
        assert quarantined.read_bytes() == b"broken database"
        assert restored["team"]["id"] == team["id"]
        with pytest.raises(SwarmAccessError):
            service.registry.open(team["id"]).discover(old_actor, "proof")
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_delete_preserves_selected_publication_and_is_idempotent(tmp_path):
    service = runtime(tmp_path / "swarm")
    try:
        team, publication = await finished(service)
        content = await service.publication(publication["id"])
        assert (await service.delete_team(team["id"], "delete-workspace"))["deleted"]
        assert (await service.delete_team(team["id"], "delete-workspace"))["deleted"]
        assert await service.publication(publication["id"]) == content
        assert not (service.root / team["id"]).exists()
        with pytest.raises(SwarmAccessError):
            await service.team(team["id"])
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_delete_stale_version_does_not_cancel_running_team(tmp_path):
    import asyncio

    gate = asyncio.Event()
    service = runtime(tmp_path / "swarm", gate=gate)
    try:
        team = await service.create_team(TeamCreate(name="Live", goal="42", request_key="live"))
        running = await service.control(team["id"], "start")
        with pytest.raises(SwarmConflictError):
            await service.delete_team(team["id"], "stale-delete", team["version"])
        assert (await service.team(team["id"]))["state"] == running["state"]
    finally:
        gate.set()
        await service.stop()


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "team/../escape", "C:/data", "team\\file"]
)
def test_restore_refuses_archive_traversal_before_writing(tmp_path, name):
    service = runtime(tmp_path / "swarm")
    upload = tmp_path / "attack.zip"
    with zipfile.ZipFile(upload, "w") as archive:
        archive.writestr("archive.json", "{}")
        archive.writestr(name, "unsafe")
    lifecycle = StorageLifecycle(service.root, service.registry)
    with pytest.raises(SwarmStoreError):
        lifecycle.prepare_restore(upload, "malicious")


@pytest.mark.parametrize("attack", ["symlink", "compression-bomb", "duplicate"])
def test_restore_refuses_link_bomb_and_duplicate_members(tmp_path, attack):
    import stat

    service = runtime(tmp_path / "swarm")
    upload = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(upload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("archive.json", "{}")
        if attack == "symlink":
            member = zipfile.ZipInfo("team/manifest.json")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, "outside")
        elif attack == "compression-bomb":
            archive.writestr("team/manifest.json", "x" * 200_000)
        else:
            with pytest.warns(UserWarning):
                archive.writestr("archive.json", "{}")
    with pytest.raises(SwarmStoreError):
        StorageLifecycle(service.root, service.registry).prepare_restore(upload, "unsafe")


@pytest.mark.asyncio
async def test_restore_rejects_trigger_even_with_recomputed_archive_hashes(tmp_path):
    service = runtime(tmp_path / "swarm")
    try:
        team, _ = await finished(service)
        exported = await service.backup(team["id"], "export")
        path = await service.backup_file(team["id"], exported["backup_id"])
        unpacked = tmp_path / "unpacked"
        with zipfile.ZipFile(path) as archive:
            archive.extractall(unpacked)
        database = unpacked / "team/team.sqlite3"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER unsafe AFTER UPDATE ON team BEGIN DELETE FROM tasks; END"
            )
        envelope = json.loads((unpacked / "archive.json").read_text())
        envelope["files"]["team/team.sqlite3"] = {
            "size": str(database.stat().st_size),
            "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        }
        attack = tmp_path / "trigger.zip"
        with zipfile.ZipFile(attack, "w") as archive:
            archive.writestr("archive.json", json.dumps(envelope))
            for name in envelope["files"]:
                archive.write(unpacked / name, name)
        with pytest.raises(SwarmStoreError, match="schema object"):
            await service.restore_backup(attack, "reject-trigger", team["id"])
        assert (await service.team(team["id"]))["state"] == "succeeded"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_interrupted_replace_resumes_from_durable_quarantine(tmp_path, monkeypatch):
    from jarvis.swarm import lifecycle

    service = runtime(tmp_path / "swarm")
    try:
        team, _ = await finished(service)
        export = await service.backup(team["id"], "before-replace")
        archive = await service.backup_file(team["id"], export["backup_id"])
        original = lifecycle._move

        def fail_once(source, target):
            if source.parent.name == "prepared":
                monkeypatch.setattr(lifecycle, "_move", original)
                raise SwarmStoreError("Injected interrupted install move")
            original(source, target)

        monkeypatch.setattr(lifecycle, "_move", fail_once)
        with pytest.raises(SwarmStoreError, match="interrupted"):
            await service.restore_backup(archive, "replace-interrupted", team["id"])
        pending = (await service.storage_status(team["id"]))["pending_restores"]
        assert len(pending) == 1
        restored = await service.resume_restore(pending[0]["id"])
        assert restored["team"]["lead_id"] == team["lead_id"]
        assert restored["quarantine_id"] == pending[0]["id"]
        assert not (await service.storage_status(team["id"]))["pending_restores"]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_retention_removes_only_old_unreferenced_objects_and_exports(tmp_path):
    import os
    import time

    service = runtime(tmp_path / "swarm")
    try:
        team, publication = await finished(service)
        store = service.registry.open(team["id"])
        orphan = store.path.parent / "objects" / ("f" * 64)
        orphan.write_bytes(b"old-unreferenced")
        old = time.time() - 60 * 86400
        os.utime(orphan, (old, old))
        export = await service.backup(team["id"], "old-export")
        manager = service._storage_lifecycle()
        with closing(manager._journal()) as connection, connection:
            row = connection.execute(
                "SELECT record FROM operations WHERE id=?", (export["backup_id"],)
            ).fetchone()
            operation = json.loads(row[0])
            operation["created_at"] = old
            connection.execute(
                "UPDATE operations SET record=? WHERE id=?",
                (json.dumps(operation), export["backup_id"]),
            )
        result = await service.retain_team(team["id"], 30)
        assert result["orphan_objects"] == "1" and result["expired_backups"] == "1"
        assert await service.publication(publication["id"])
        assert len(await service.records(team["id"], "artifacts")) > 0
        assert not orphan.exists()
        assert (await service.storage_status(team["id"]))["backups"] == []
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_retry_after_catalog_commit_does_not_cancel_restored_generation(
    tmp_path, monkeypatch
):
    service = runtime(tmp_path / "swarm")
    try:
        team = await service.create_team(
            TeamCreate(name="Pending", goal="42", request_key="pending")
        )
        backup = await service.backup(team["id"], "before-install")
        path = await service.backup_file(team["id"], backup["backup_id"])
        manager = service._storage_lifecycle()
        save = manager._save

        def fail_journal_once(operation):
            if operation["kind"] == "restore" and operation["phase"] == "done":
                monkeypatch.setattr(manager, "_save", save)
                raise SwarmStoreError("Injected post-catalog journal interruption")
            save(operation)

        monkeypatch.setattr(manager, "_save", fail_journal_once)
        with pytest.raises(SwarmStoreError, match="post-catalog"):
            await service.restore_backup(path, "post-commit", team["id"])
        assert (await service.team(team["id"]))["state"] == "paused"
        operation = (await service.storage_status(team["id"]))["pending_restores"][0]
        assert (await service.resume_restore(operation["id"]))["team"]["state"] == "paused"
        assert (await service.restore_backup(path, "post-commit", team["id"]))["team"][
            "state"
        ] == "paused"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_deleted_identity_stays_reserved_and_backup_remains_portable(tmp_path):
    service = runtime(tmp_path / "swarm")
    try:
        team = await service.create_team(
            TeamCreate(name="Roundtrip", goal="42", request_key="roundtrip")
        )
        backup = await service.backup(team["id"], "save-before-delete")
        path = await service.backup_file(team["id"], backup["backup_id"])
        await service.delete_team(team["id"], "old-delete")
        with pytest.raises(SwarmConflictError, match="permanently deleted"):
            await service.restore_backup(path, "restore-after-delete")
        assert (await service.delete_team(team["id"], "old-delete"))["deleted"]
        service.registry.local.delete(team["id"])
        assert path.is_file()
        with pytest.raises(SwarmAccessError):
            await service.team(team["id"])
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_stale_delete_epoch_cannot_delete_a_restored_team_even_with_matching_version(
    tmp_path,
):
    service = runtime(tmp_path / "swarm")
    try:
        team = await service.create_team(TeamCreate(name="Epoch", goal="42", request_key="epoch"))
        backup = await service.backup(team["id"], "save-epoch")
        path = await service.backup_file(team["id"], backup["backup_id"])
        restored = (await service.restore_backup(path, "replace-epoch", team["id"]))["team"]
        assert restored["storage_generation"] and restored["storage_generation"] != team.get(
            "storage_generation", ""
        )
        with pytest.raises(SwarmConflictError, match="restored"):
            await service.delete_team(
                team["id"], "stale-epoch", restored["version"], team.get("storage_generation", "")
            )
        assert (await service.team(team["id"]))["state"] == "paused"
        assert (await service.restore_backup(path, "replace-epoch", team["id"]))["team"][
            "storage_generation"
        ] == restored["storage_generation"]
    finally:
        await service.stop()
