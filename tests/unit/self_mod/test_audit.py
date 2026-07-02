"""Tests for SelfModAudit (Phase 7.1).

Plan acceptance criteria §7.1:
- Audit-log round-trip with concurrent writes
- Format fidelity to the plan example (10 fields, ISO-Z, UUID4)
- Sensitive paths get redacted (Plan-§AP-2)
- I/O errors don't crash the caller (Plan-§AP-5)
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from uuid import UUID

import pytest

from jarvis.core.self_mod import (
    AuditActor,
    AuditEvent,
    AuditSource,
    SelfModAudit,
)


@pytest.fixture
def audit(tmp_path: Path) -> SelfModAudit:
    return SelfModAudit(path=tmp_path / "audit.log")


@pytest.fixture
def sample_event() -> AuditEvent:
    return AuditEvent(
        source=AuditSource.VOICE,
        requested_by=AuditActor.HAUPTJARVIS,
        path="tts.provider",
        old_value="elevenlabs",
        new_value="gemini-flash-tts",
        ok=True,
    )


# --- Round trip ---


class TestRecord:
    def test_writes_valid_json_line(
        self, audit: SelfModAudit, sample_event: AuditEvent
    ) -> None:
        audit.record(sample_event)
        content = audit.path.read_text(encoding="utf-8")
        assert content.endswith("\n")
        assert content.count("\n") == 1
        parsed = json.loads(content.strip())
        assert parsed["path"] == "tts.provider"
        assert parsed["ok"] is True

    def test_creates_parent_directory(
        self, tmp_path: Path, sample_event: AuditEvent
    ) -> None:
        deep_path = tmp_path / "a" / "b" / "c" / "audit.log"
        audit = SelfModAudit(path=deep_path)
        audit.record(sample_event)
        assert deep_path.exists()

    def test_appends_does_not_truncate(
        self, audit: SelfModAudit, sample_event: AuditEvent
    ) -> None:
        for _ in range(3):
            audit.record(sample_event)
        lines = audit.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        for line in lines:
            assert json.loads(line)["path"] == "tts.provider"

    def test_format_matches_plan_spec(
        self, audit: SelfModAudit, sample_event: AuditEvent
    ) -> None:
        """Plan-§7.1: 10 audit fields."""
        audit.record(sample_event)
        parsed = json.loads(audit.path.read_text(encoding="utf-8").strip())
        for key in (
            "ts",
            "audit_id",
            "source",
            "requested_by",
            "path",
            "old_value",
            "new_value",
            "ok",
            "rolled_back",
            "error",
        ):
            assert key in parsed, f"Plan field '{key}' missing from audit JSON"

    def test_iso_z_timestamp(
        self, audit: SelfModAudit, sample_event: AuditEvent
    ) -> None:
        audit.record(sample_event)
        parsed = json.loads(audit.path.read_text(encoding="utf-8").strip())
        assert parsed["ts"].endswith("Z"), f"ts without Z suffix: {parsed['ts']}"
        assert "+00:00" not in parsed["ts"]

    def test_uuid4_audit_id(
        self, audit: SelfModAudit, sample_event: AuditEvent
    ) -> None:
        audit.record(sample_event)
        parsed = json.loads(audit.path.read_text(encoding="utf-8").strip())
        assert UUID(parsed["audit_id"]).version == 4

    def test_default_path_is_data_self_mod_log(self) -> None:
        """Plan-§AD-6: `data/self_mod.log`."""
        assert SelfModAudit.DEFAULT_PATH == Path("data") / "self_mod.log"


# --- Concurrent writes (Plan-AC §7.1) ---


class TestConcurrent:
    def test_ten_threads_no_corruption(
        self, audit: SelfModAudit, sample_event: AuditEvent
    ) -> None:
        n_threads = 10
        per_thread = 10

        def writer() -> None:
            for _ in range(per_thread):
                audit.record(sample_event)

        threads = [threading.Thread(target=writer) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = audit.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == n_threads * per_thread, (
            f"Expected {n_threads * per_thread} lines, found {len(lines)} — "
            "race condition or lock defect."
        )
        # Every line must be valid JSON (no mid-line tearing).
        for line in lines:
            parsed = json.loads(line)
            assert parsed["path"] == "tts.provider"


# --- Redaction (Plan-§AP-2) ---


class TestRedaction:
    @pytest.mark.parametrize(
        "sensitive_path",
        [
            "anthropic_api_key",
            "openai.api_key",
            "user.password",
            "spotify.token",
            "deepgram.secret",
            "auth.credential",
            "API_KEY",
            "SECRET_TOKEN",
        ],
    )
    def test_redacts_sensitive_paths(
        self, audit: SelfModAudit, sensitive_path: str
    ) -> None:
        secret = "sk-1234567890abcdef"  # noqa: S105 — test fixture, not a real token
        new_secret = "new-" + secret
        event = AuditEvent(
            source=AuditSource.UI,
            requested_by=AuditActor.USER,
            path=sensitive_path,
            old_value=secret,
            new_value=new_secret,
            ok=True,
        )
        audit.record(event)
        content = audit.path.read_text(encoding="utf-8")
        assert secret not in content, (
            f"Plaintext secret ended up in the log (path={sensitive_path}): "
            f"{content!r}"
        )
        parsed = json.loads(content.strip())
        # Length is preserved for telemetry ("16-character token")
        assert parsed["old_value"] == "*" * len(secret)
        assert parsed["new_value"] == "*" * len(new_secret)

    def test_does_not_redact_normal_paths(
        self, audit: SelfModAudit
    ) -> None:
        event = AuditEvent(
            source=AuditSource.VOICE,
            requested_by=AuditActor.HAUPTJARVIS,
            path="tts.provider",
            old_value="elevenlabs",
            new_value="gemini-flash-tts",
            ok=True,
        )
        audit.record(event)
        parsed = json.loads(audit.path.read_text(encoding="utf-8").strip())
        assert parsed["old_value"] == "elevenlabs"
        assert parsed["new_value"] == "gemini-flash-tts"

    def test_redacts_empty_string_to_empty(
        self, audit: SelfModAudit
    ) -> None:
        event = AuditEvent(
            source=AuditSource.UI,
            requested_by=AuditActor.USER,
            path="user_password",
            old_value="",
            new_value=None,
            ok=True,
        )
        audit.record(event)
        parsed = json.loads(audit.path.read_text(encoding="utf-8").strip())
        assert parsed["old_value"] == ""
        assert parsed["new_value"] is None


# --- Robustness (Plan-§AP-5) ---


class TestRobustness:
    def test_silent_on_open_failure(
        self,
        sample_event: AuditEvent,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Plan-§AP-5: I/O error does NOT crash the caller."""
        audit = SelfModAudit(path=tmp_path / "audit.log")

        def failing_open(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError("simulated: no write permission")

        monkeypatch.setattr(Path, "open", failing_open)

        # Must not propagate.
        audit.record(sample_event)

    def test_silent_on_mkdir_failure(
        self,
        sample_event: AuditEvent,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A disk-full error while creating the directory must also not
        propagate."""
        audit = SelfModAudit(path=tmp_path / "new-subdir" / "audit.log")

        def failing_mkdir(self: Path, *args: object, **kwargs: object) -> None:
            raise OSError("simulated: disk full")

        monkeypatch.setattr(Path, "mkdir", failing_mkdir)

        audit.record(sample_event)

    def test_logs_warning_on_io_failure(
        self,
        sample_event: AuditEvent,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        audit = SelfModAudit(path=tmp_path / "audit.log")

        def failing_open(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError("nope")

        monkeypatch.setattr(Path, "open", failing_open)

        with caplog.at_level(
            logging.WARNING, logger="jarvis.core.self_mod.audit"
        ):
            audit.record(sample_event)

        assert any(
            "failed" in rec.getMessage().lower()
            for rec in caplog.records
        ), "expected warning log missing"


# --- tail() ---


class TestTail:
    def test_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        audit = SelfModAudit(path=tmp_path / "missing.log")
        assert audit.tail() == []

    def test_returns_recent_n(
        self, audit: SelfModAudit, sample_event: AuditEvent
    ) -> None:
        for _ in range(20):
            audit.record(sample_event)
        recent = audit.tail(n=5)
        assert len(recent) == 5
        for entry in recent:
            assert entry["path"] == "tts.provider"

    def test_tail_zero_returns_empty(
        self, audit: SelfModAudit, sample_event: AuditEvent
    ) -> None:
        audit.record(sample_event)
        assert audit.tail(n=0) == []

    def test_tail_negative_returns_empty(
        self, audit: SelfModAudit, sample_event: AuditEvent
    ) -> None:
        audit.record(sample_event)
        assert audit.tail(n=-5) == []

    def test_handles_corrupt_lines_gracefully(
        self,
        audit: SelfModAudit,
        sample_event: AuditEvent,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        audit.record(sample_event)
        # Manually append a corrupt line.
        with audit.path.open("a", encoding="utf-8") as fh:
            fh.write("not-json-at-all\n")
        audit.record(sample_event)

        with caplog.at_level(
            logging.WARNING, logger="jarvis.core.self_mod.audit"
        ):
            entries = audit.tail()

        # The two valid entries remain, the corrupt line is
        # skipped — no crash.
        assert len(entries) == 2
        for entry in entries:
            assert entry["path"] == "tts.provider"
