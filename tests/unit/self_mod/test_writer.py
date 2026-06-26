"""Tests für AtomicConfigWriter (Phase 7.2).

Plan-Akzeptanzkriterien §7.2:
- Happy-Path-Test: Mutation persistiert, ConfigReloaded-Event feuert
- Pre-Validation-Reject: kein Schreiben, keine Backup-Datei
- Rollback-Test mit Monkeypatched Reload-Crash
- Kommentar-Preservation: User-Kommentar überlebt 100 Mutationen
- Concurrent-Test: 10 parallele Mutationen serialisieren korrekt
- Backup-GC: nach 11 Mutationen wird älteste >30d gelöscht, letzte 10 erhalten
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
    """Loader ohne ENV-Overrides (Test-Isolation gegen JARVIS__*)."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    data = tomllib.loads(raw.decode("utf-8"))
    return JarvisConfig.model_validate(data)


class CaptureBus:
    """Einfacher EventBus-Stub: sammelt publishe Events in-memory."""

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
        """Alle anderen Felder bleiben unverändert."""
        before = _isolated_loader(fixture_path)
        writer.mutate(_make_request("tts.speed", 1.5))
        after = _isolated_loader(fixture_path)

        assert after.tts.speed == 1.5
        # Beweis: alle anderen Felder identisch
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
        # Plan-Fixture-Kommentare:
        assert "Personal Jarvis — Test-Fixture" in text
        assert "Trailing-Kommentar" in text
        assert "100 Mutationen" in text  # Header-Kommentar

    def test_user_comments_survive_100_mutations(
        self, writer: AtomicConfigWriter, fixture_path: Path
    ) -> None:
        """Plan-AC §7.2: User-Kommentar überlebt 100 Mutationen."""
        for i in range(100):
            value = 0.5 + (i % 10) * 0.1
            writer.mutate(_make_request("tts.speed", round(value, 2)))
        text = fixture_path.read_text(encoding="utf-8")
        assert "Personal Jarvis — Test-Fixture" in text
        assert "Trailing-Kommentar" in text
        # Datei ist immer noch valide
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
        """Plan-AC §7.2: Pre-Validation-Reject — kein Schreiben, keine Backup-Datei."""
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
        # Original byte-identisch wiederhergestellt
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
        """Backup wird nicht entfernt, damit der User später manuell
        recovern kann (über `rollback(backup_filename)`)."""

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
# Atomic-Write-Safety (kein orphan .tmp)
# ----------------------------------------------------------------------


class TestAtomicWrite:
    def test_no_orphan_tmp_after_failure(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Wenn `os.replace` knallt, darf kein `.tmp` neben jarvis.toml liegen.

        Das Backup nutzt `mkstemp` ohne `os.replace`; nur der atomic_write
        ruft `os.replace`. Wir lassen den Backup-Pfad durchlaufen und
        crashen erst beim Final-Replace.
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

        # Original-Datei intakt (byte-identisch — replace nie passiert)
        assert fixture_path.read_bytes() == original_bytes
        # Kein orphan .tmp neben jarvis.toml
        orphans = list(fixture_path.parent.glob("jarvis.toml.*.tmp"))
        assert orphans == [], f"Orphan-tmp gefunden: {orphans}"


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
        """Prompt-AC: 51 Sets mit max=50 → genau 50 erhalten."""
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
        """Plan-AC §7.2: nach 11 Mutationen ist die älteste >30d gelöscht,
        die letzten 10 erhalten."""
        writer = AtomicConfigWriter(
            config_path=fixture_path,
            backup_dir=tmp_path / "backups",
            max_backups=100,  # Cap aushebeln, nur Age-GC testen
            backup_min_keep=10,
            backup_max_age_days=30,
            audit=audit_log,
            config_loader=_isolated_loader,
        )
        # 11 Mutationen
        for i in range(11):
            writer.mutate(_make_request("tts.speed", 1.0 + i * 0.001))
        backups = sorted(
            (tmp_path / "backups").glob("jarvis.toml.*.bak"),
            key=lambda p: p.stat().st_mtime,
        )
        assert len(backups) == 11

        # Älteste auf >30 Tage altern
        ancient = time.time() - 31 * 86400
        os.utime(backups[0], (ancient, ancient))

        # Erneute Mutation triggert GC
        writer.mutate(_make_request("tts.speed", 1.99))
        remaining = sorted(
            (tmp_path / "backups").glob("jarvis.toml.*.bak"),
            key=lambda p: p.stat().st_mtime,
        )
        # Alte Datei (31 Tage) gelöscht, weil >min_keep nach Cap-Check
        assert backups[0] not in remaining

    def test_min_keep_floor_protects_against_aggressive_gc(
        self,
        fixture_path: Path,
        tmp_path: Path,
        audit_log: SelfModAudit,
    ) -> None:
        """Selbst wenn alle Backups ancient wären, min_keep schützt die letzten 10."""
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
        # Alle Backups künstlich altern
        ancient = time.time() - 100 * 86400
        for path in backup_dir.glob("jarvis.toml.*.bak"):
            os.utime(path, (ancient, ancient))

        writer.mutate(_make_request("tts.speed", 9.99))
        # Min-Keep: 10 + 1 (frische) sollte erhalten bleiben
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

        # Plan-AC §7.2: 10 parallele Mutationen serialisieren korrekt.
        # Wir mischen ALLOWED-Pfade durch.
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

        # 10 Backups, 10 Audit-Einträge, Datei ist valide
        backups = list((tmp_path / "backups").glob("jarvis.toml.*.bak"))
        assert len(backups) == 10
        entries = _read_audit_lines(audit_log)
        assert len(entries) == 10
        assert all(e["ok"] is True for e in entries)
        # Datei lädt ohne Fehler
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
            # Nach dem mutate ist tts.speed == value (oder bereits überschrieben)
            with lock:
                results.append(value)

        t1 = threading.Thread(target=worker, args=(1.25,))
        t2 = threading.Thread(target=worker, args=(1.75,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Beide Mutationen sind Audit + Backup
        assert len(_read_audit_lines(audit_log)) == 2
        assert len(list((tmp_path / "backups").glob("jarvis.toml.*.bak"))) == 2

        # Final-Wert ist einer der beiden
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
        # Erstes Backup zeigt auf den Stand VOR der ersten Mutation (also 1.0)
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
        # mutate() ist sync. Der Dispatch-Pfad muss `get_running_loop`
        # erkennen und via create_task scheduln.
        writer.mutate(_make_request("tts.speed", 1.42))
        # Dem create_task einen Tick zum Laufen geben
        await asyncio.sleep(0.05)
        assert len(bus.events) == 1
        assert bus.events[0].changed_keys == ("tts.speed",)
