"""Tests for AtomicConfigWriter (Phase 7.2).

Plan acceptance criteria §7.2:
- Happy-path test: mutation persists, ConfigReloaded event fires
- Pre-validation reject: no write, no backup file
- Rollback test with a monkeypatched reload crash
- Comment preservation: user comment survives 100 mutations
- Concurrency test: 10 parallel mutations serialize correctly
- Backup GC: after 11 mutations the oldest >30d is deleted, the last 10 are kept
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.config import JarvisConfig
from jarvis.core.events import ConfigReloaded
from jarvis.core.self_mod import (
    AllowlistViolationError,
    AtomicConfigWriter,
    AuditActor,
    AuditSource,
    BackupError,
    MutationRequest,
    PreValidateError,
    ReloadError,
    SecretAccessError,
    SelfModAudit,
)

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_jarvis.toml"


# ----------------------------------------------------------------------
# Fixtures + Helpers
# ----------------------------------------------------------------------


def _isolated_loader(path: Path) -> JarvisConfig:
    """Loader without ENV overrides (test isolation against JARVIS__*)."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    data = tomllib.loads(raw.decode("utf-8"))
    return JarvisConfig.model_validate(data)


class CaptureBus:
    """Simple EventBus stub: collects published events in-memory."""

    def __init__(self) -> None:
        self.events: list[ConfigReloaded] = []

    async def publish(self, event: ConfigReloaded) -> None:
        self.events.append(event)


@pytest.fixture
def fixture_path(tmp_path: Path) -> Path:
    target = tmp_path / "jarvis.toml"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return target


@pytest.fixture
def audit_log(tmp_path: Path) -> SelfModAudit:
    return SelfModAudit(path=tmp_path / "audit.log")


@pytest.fixture
def bus() -> CaptureBus:
    return CaptureBus()


@pytest.fixture
def writer(
    fixture_path: Path,
    tmp_path: Path,
    audit_log: SelfModAudit,
    bus: CaptureBus,
) -> AtomicConfigWriter:
    return AtomicConfigWriter(
        config_path=fixture_path,
        backup_dir=tmp_path / "backups",
        audit=audit_log,
        bus=bus,  # type: ignore[arg-type]
        config_loader=_isolated_loader,
    )


def _make_request(path: str, new_value: Any, **kwargs: Any) -> MutationRequest:
    return MutationRequest(
        path=path,
        new_value=new_value,
        actor=kwargs.pop("actor", AuditActor.HAUPTJARVIS),
        source=kwargs.pop("source", AuditSource.VOICE),
        **kwargs,
    )


def _read_audit_lines(audit: SelfModAudit) -> list[dict]:
    if not audit.path.exists():
        return []
    return [
        json.loads(line)
        for line in audit.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ----------------------------------------------------------------------
# Happy Path
# ----------------------------------------------------------------------


class TestHappyPath:
    def test_mutation_persists(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        result = writer.mutate(_make_request("tts.provider", "elevenlabs"))
        assert result.ok is True
        assert result.old_value == "gemini-flash-tts"
        assert result.new_value == "elevenlabs"

        reloaded = _isolated_loader(fixture_path)
        assert reloaded.tts.provider == "elevenlabs"

    def test_audit_succeeded_recorded(
        self, writer: AtomicConfigWriter, audit_log: SelfModAudit
    ) -> None:
        writer.mutate(_make_request("tts.speed", 1.25))
        entries = _read_audit_lines(audit_log)
        assert len(entries) == 1
        assert entries[0]["ok"] is True
        assert entries[0]["path"] == "tts.speed"
        assert entries[0]["new_value"] == 1.25

    def test_backup_created(
        self, writer: AtomicConfigWriter, tmp_path: Path
    ) -> None:
        writer.mutate(_make_request("tts.voice_de", "Orus"))
        backups = list((tmp_path / "backups").glob("jarvis.toml.*.bak"))
        assert len(backups) == 1

    def test_config_reloaded_event_fires(
        self,
        writer: AtomicConfigWriter,
        bus: CaptureBus,
    ) -> None:
        writer.mutate(_make_request("tts.provider", "elevenlabs"))
        assert len(bus.events) == 1
        event = bus.events[0]
        assert isinstance(event, ConfigReloaded)
        assert event.changed_keys == ("tts.provider",)
        assert event.source_layer == "self_mod"

    def test_backup_path_returned(
        self, writer: AtomicConfigWriter, tmp_path: Path
    ) -> None:
        result = writer.mutate(_make_request("tts.speed", 1.5))
        assert result.backup_path is not None
        backup = Path(result.backup_path)
        assert backup.exists()
        assert backup.parent == tmp_path / "backups"

    def test_only_target_path_changed(
        self,
        writer: AtomicConfigWriter,
        fixture_path: Path,
    ) -> None:
        """All other fields remain unchanged."""
        before = _isolated_loader(fixture_path)
        writer.mutate(_make_request("tts.speed", 1.5))
        after = _isolated_loader(fixture_path)

        assert after.tts.speed == 1.5
        # Proof: all other fields identical
        assert after.tts.provider == before.tts.provider
        assert after.tts.voice_de == before.tts.voice_de
        assert after.profile.name == before.profile.name
        assert after.brain.primary == before.brain.primary


# ----------------------------------------------------------------------
# Comment & Structure Preservation
# ----------------------------------------------------------------------


class TestCommentPreservation:
    def test_user_comments_survive_one_mutation(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        writer.mutate(_make_request("tts.speed", 1.5))
        text = fixture_path.read_text(encoding="utf-8")
        # Plan fixture comments:
        assert "Personal Jarvis — test fixture" in text
        assert "Trailing comment" in text
        assert "100 mutations" in text  # header comment

    def test_user_comments_survive_100_mutations(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        """Plan-AC §7.2: user comment survives 100 mutations."""
        for i in range(100):
            value = 0.5 + (i % 10) * 0.1
            writer.mutate(_make_request("tts.speed", round(value, 2)))
        text = fixture_path.read_text(encoding="utf-8")
        assert "Personal Jarvis — test fixture" in text
        assert "Trailing comment" in text
        # File is still valid
        config = _isolated_loader(fixture_path)
        assert config.profile.name == "test-runner"


# ----------------------------------------------------------------------
# Allowlist Rejection
# ----------------------------------------------------------------------


class TestAllowlistRejection:
    def test_unknown_path_raises_allowlist_violation(
        self, writer: AtomicConfigWriter
    ) -> None:
        with pytest.raises(AllowlistViolationError):
            writer.mutate(_make_request("brain.fantasy_field", "x"))

    def test_unknown_path_does_not_modify_file(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        original = fixture_path.read_bytes()
        with pytest.raises(AllowlistViolationError):
            writer.mutate(_make_request("brain.fantasy_field", "x"))
        assert fixture_path.read_bytes() == original

    def test_unknown_path_creates_no_backup(
        self, writer: AtomicConfigWriter, tmp_path: Path
    ) -> None:
        with pytest.raises(AllowlistViolationError):
            writer.mutate(_make_request("brain.fantasy_field", "x"))
        backup_dir = tmp_path / "backups"
        if backup_dir.exists():
            assert list(backup_dir.glob("jarvis.toml.*.bak")) == []

    def test_unknown_path_writes_audit_failure(
        self, writer: AtomicConfigWriter, audit_log: SelfModAudit
    ) -> None:
        with pytest.raises(AllowlistViolationError):
            writer.mutate(_make_request("brain.fantasy_field", "x"))
        entries = _read_audit_lines(audit_log)
        assert len(entries) == 1
        assert entries[0]["ok"] is False
        # Wave 1.1 reworded the allowlist-violation message (the set is now the
        # schema minus the deny layer, no longer a literal ALLOWED tuple).
        assert "not a mutable config setting" in entries[0]["error"]

    def test_secret_path_raises_secret_access(
        self, writer: AtomicConfigWriter
    ) -> None:
        with pytest.raises(SecretAccessError):
            writer.mutate(_make_request("security.admin_password_hash", "x"))


# ----------------------------------------------------------------------
# Pre-Validate Reject (Plan-AC §7.2)
# ----------------------------------------------------------------------


class TestPreValidateReject:
    def test_invalid_type_for_provider(
        self, writer: AtomicConfigWriter
    ) -> None:
        with pytest.raises(PreValidateError):
            writer.mutate(_make_request("tts.provider", ["not", "a", "string"]))

    def test_invalid_value_does_not_modify_file(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        original = fixture_path.read_bytes()
        with pytest.raises(PreValidateError):
            writer.mutate(_make_request("tts.provider", ["x"]))
        assert fixture_path.read_bytes() == original

    def test_invalid_value_creates_no_backup(
        self, writer: AtomicConfigWriter, tmp_path: Path
    ) -> None:
        """Plan-AC §7.2: pre-validation reject — no write, no backup file."""
        with pytest.raises(PreValidateError):
            writer.mutate(_make_request("tts.provider", ["x"]))
        backup_dir = tmp_path / "backups"
        if backup_dir.exists():
            assert list(backup_dir.glob("jarvis.toml.*.bak")) == []

    def test_invalid_value_writes_audit_failure(
        self, writer: AtomicConfigWriter, audit_log: SelfModAudit
    ) -> None:
        with pytest.raises(PreValidateError):
            writer.mutate(_make_request("tts.provider", ["x"]))
        entries = _read_audit_lines(audit_log)
        assert len(entries) == 1
        assert entries[0]["ok"] is False
        assert "validate_failed" in entries[0]["error"]


# ----------------------------------------------------------------------
# Reload-Failure → Rollback (Plan-AC §7.2)
# ----------------------------------------------------------------------


class TestReloadFailRollback:
    def test_rollback_restores_original_content(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        original_bytes = fixture_path.read_bytes()

        def crashing_loader(_path: Path) -> JarvisConfig:
            raise RuntimeError("simulated reload crash")

        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            audit=audit_log,
            config_loader=crashing_loader,
        )
        with pytest.raises(ReloadError):
            writer.mutate(_make_request("tts.provider", "elevenlabs"))
        # Original restored byte-identical
        assert fixture_path.read_bytes() == original_bytes

    def test_audit_reports_post_validate_failure(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        def crashing_loader(_path: Path) -> JarvisConfig:
            raise RuntimeError("nope")

        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            audit=audit_log,
            config_loader=crashing_loader,
        )
        with pytest.raises(ReloadError):
            writer.mutate(_make_request("tts.provider", "elevenlabs"))
        entries = _read_audit_lines(audit_log)
        assert len(entries) == 1
        assert entries[0]["ok"] is False
        assert entries[0]["rolled_back"] is True
        assert "post_validate_failed_rolled_back" in entries[0]["error"]

    def test_backup_remains_after_rollback(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        """Backup is not removed, so the user can later recover manually
        (via `rollback(backup_filename)`)."""

        def crashing_loader(_path: Path) -> JarvisConfig:
            raise RuntimeError("nope")

        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            audit=audit_log,
            config_loader=crashing_loader,
        )
        with pytest.raises(ReloadError):
            writer.mutate(_make_request("tts.provider", "elevenlabs"))
        backups = list((tmp_path / "backups").glob("jarvis.toml.*.bak"))
        assert len(backups) == 1


# ----------------------------------------------------------------------
# Atomic-write safety (no orphan .tmp)
# ----------------------------------------------------------------------


class TestAtomicWrite:
    def test_no_orphan_tmp_after_failure(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If `os.replace` blows up, no `.tmp` may be left next to jarvis.toml.

        The backup uses `mkstemp` without `os.replace`; only the atomic_write
        calls `os.replace`. We let the backup path run through and
        crash only at the final replace.
        """
        original_bytes = fixture_path.read_bytes()
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            audit=audit_log,
            config_loader=_isolated_loader,
        )

        def crashing_replace(src: str, dst: str) -> None:  # noqa: ARG001
            raise OSError("simulated mid-replace crash")

        monkeypatch.setattr(os, "replace", crashing_replace)

        with pytest.raises(BackupError):
            writer.mutate(_make_request("tts.provider", "elevenlabs"))

        # Original file intact (byte-identical — replace never happened)
        assert fixture_path.read_bytes() == original_bytes
        # No orphan .tmp next to jarvis.toml
        orphans = list(fixture_path.parent.glob("jarvis.toml.*.tmp"))
        assert orphans == [], f"Orphan tmp found: {orphans}"


# ----------------------------------------------------------------------
# Backup-GC
# ----------------------------------------------------------------------


class TestBackupGC:
    def test_cap_kicks_in_at_max_backups_plus_one(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        """Prompt-AC: 51 sets with max=50 → exactly 50 retained."""
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            max_backups=50,
            backup_min_keep=10,
            audit=audit_log,
            config_loader=_isolated_loader,
        )
        for i in range(51):
            writer.mutate(_make_request("tts.speed", 1.0 + i * 0.001))
        backups = list((tmp_path / "backups").glob("jarvis.toml.*.bak"))
        assert len(backups) == 50

    def test_age_gc_keeps_min_floor(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        """Plan-AC §7.2: after 11 mutations, the oldest >30d is deleted,
        the last 10 are kept."""
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            max_backups=100,  # bypass the cap, test only age-based GC
            backup_min_keep=10,
            backup_max_age_days=30,
            audit=audit_log,
            config_loader=_isolated_loader,
        )
        # 11 mutations
        for i in range(11):
            writer.mutate(_make_request("tts.speed", 1.0 + i * 0.001))
        backups = sorted(
            (tmp_path / "backups").glob("jarvis.toml.*.bak"),
            key=lambda p: p.stat().st_mtime,
        )
        assert len(backups) == 11

        # Age the oldest one to >30 days
        ancient = time.time() - 31 * 86400
        os.utime(backups[0], (ancient, ancient))

        # Another mutation triggers GC
        writer.mutate(_make_request("tts.speed", 1.99))
        remaining = sorted(
            (tmp_path / "backups").glob("jarvis.toml.*.bak"),
            key=lambda p: p.stat().st_mtime,
        )
        # Old file (31 days) deleted because >min_keep after the cap check
        assert backups[0] not in remaining

    def test_min_keep_floor_protects_against_aggressive_gc(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        """Even if all backups were ancient, min_keep protects the last 10."""
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            max_backups=100,
            backup_min_keep=10,
            backup_max_age_days=30,
            audit=audit_log,
            config_loader=_isolated_loader,
        )
        for i in range(15):
            writer.mutate(_make_request("tts.speed", 1.0 + i * 0.001))

        backup_dir = tmp_path / "backups"
        # Artificially age all backups
        ancient = time.time() - 100 * 86400
        for path in backup_dir.glob("jarvis.toml.*.bak"):
            os.utime(path, (ancient, ancient))

        writer.mutate(_make_request("tts.speed", 9.99))
        # Min-keep: 10 + 1 (fresh) should be retained
        remaining = list(backup_dir.glob("jarvis.toml.*.bak"))
        assert len(remaining) >= 10


# ----------------------------------------------------------------------
# Concurrency (Plan-AC §7.2 + Prompt)
# ----------------------------------------------------------------------


class TestConcurrencyDifferentPaths:
    def test_ten_parallel_mutations_serialize(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            max_backups=100,
            audit=audit_log,
            config_loader=_isolated_loader,
        )

        # Plan-AC §7.2: 10 parallel mutations serialize correctly.
        # We mix ALLOWED paths together.
        targets: list[tuple[str, Any]] = [
            ("tts.speed", 1.0),
            ("tts.provider", "elevenlabs"),
            ("tts.voice_de", "Orus"),
            ("tts.voice_en", "Charon"),
            ("tts.speed", 1.5),
            ("tts.voice_de", "Iapetus"),
            ("tts.speed", 2.0),
            ("tts.provider", "gemini-flash-tts"),
            ("tts.voice_en", "Orus"),
            ("tts.speed", 0.75),
        ]

        def worker(path: str, value: Any) -> None:
            writer.mutate(_make_request(path, value))

        threads = [threading.Thread(target=worker, args=t) for t in targets]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # 10 backups, 10 audit entries, file is valid
        backups = list((tmp_path / "backups").glob("jarvis.toml.*.bak"))
        assert len(backups) == 10
        entries = _read_audit_lines(audit_log)
        assert len(entries) == 10
        assert all(e["ok"] is True for e in entries)
        # File loads without error
        _isolated_loader(fixture_path)


class TestConcurrencySamePath:
    def test_same_path_last_writer_wins(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            audit=audit_log,
            config_loader=_isolated_loader,
        )
        results: list[float] = []
        lock = threading.Lock()

        def worker(value: float) -> None:
            writer.mutate(_make_request("tts.speed", value))
            # After the mutate, tts.speed == value (or already overwritten)
            with lock:
                results.append(value)

        t1 = threading.Thread(target=worker, args=(1.25,))
        t2 = threading.Thread(target=worker, args=(1.75,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both mutations have audit + backup
        assert len(_read_audit_lines(audit_log)) == 2
        assert len(list((tmp_path / "backups").glob("jarvis.toml.*.bak"))) == 2

        # Final value is one of the two
        config = _isolated_loader(fixture_path)
        assert config.tts.speed in (1.25, 1.75)


# ----------------------------------------------------------------------
# Public API: list_backups + rollback
# ----------------------------------------------------------------------


class TestListBackups:
    def test_returns_empty_for_no_backups(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "no-backups-yet",
            audit=audit_log,
            config_loader=_isolated_loader,
        )
        assert writer.list_backups() == []

    def test_returns_newest_first(self, writer: AtomicConfigWriter) -> None:
        writer.mutate(_make_request("tts.speed", 1.1))
        time.sleep(0.01)
        writer.mutate(_make_request("tts.speed", 1.2))
        backups = writer.list_backups()
        assert len(backups) == 2
        assert backups[0].timestamp >= backups[1].timestamp

    def test_limit_respected(self, writer: AtomicConfigWriter) -> None:
        for i in range(5):
            writer.mutate(_make_request("tts.speed", 1.0 + i * 0.01))
        assert len(writer.list_backups(limit=3)) == 3


class TestRollbackPublic:
    def test_rollback_restores_named_backup(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        writer.mutate(_make_request("tts.speed", 1.5))
        backups_after_first = writer.list_backups()
        first_backup_filename = backups_after_first[0].filename

        writer.mutate(_make_request("tts.speed", 1.99))
        config = _isolated_loader(fixture_path)
        assert config.tts.speed == 1.99

        writer.rollback(first_backup_filename)
        config = _isolated_loader(fixture_path)
        # First backup points to the state BEFORE the first mutation (i.e. 1.0)
        assert config.tts.speed == 1.0

    def test_rollback_path_traversal_blocked(
        self, writer: AtomicConfigWriter
    ) -> None:
        with pytest.raises(BackupError):
            writer.rollback("../jarvis.toml")

    def test_rollback_unknown_file_raises(
        self, writer: AtomicConfigWriter
    ) -> None:
        with pytest.raises(BackupError):
            writer.rollback("nonexistent.bak")


# ----------------------------------------------------------------------
# Async-Bus-Dispatch in Live-Loop (asyncio_mode = auto)
# ----------------------------------------------------------------------


class TestBusDispatchInRunningLoop:
    @pytest.mark.asyncio
    async def test_dispatch_works_in_running_loop(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        bus = CaptureBus()
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            audit=audit_log,
            bus=bus,  # type: ignore[arg-type]
            config_loader=_isolated_loader,
        )
        # mutate() is sync. The dispatch path must detect `get_running_loop`
        # and schedule via create_task.
        writer.mutate(_make_request("tts.speed", 1.42))
        # Give create_task one tick to run
        await asyncio.sleep(0.05)
        assert len(bus.events) == 1
        assert bus.events[0].changed_keys == ("tts.speed",)
