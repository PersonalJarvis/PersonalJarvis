"""Watchdog ownership-marker failures remain diagnosable without exposing paths."""

import logging
import os

from jarvis.speech import watchdog


def test_pid_write_failure_reports_limited_protection_without_private_path(tmp_path, caplog):
    blocker = tmp_path / "private-marker-location"
    blocker.write_text("file prevents directory creation", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="jarvis.watchdog"):
        watchdog._write_pid_file(blocker / "watchdog.pid")

    assert caplog.messages == [
        "Could not persist watchdog ownership marker; duplicate-start protection is limited"
    ]
    assert str(blocker) not in caplog.text
    assert caplog.records[0].exc_info is None


def test_pid_read_failure_reports_limited_protection_without_private_path(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="jarvis.watchdog"):
        assert watchdog._read_pid_file(tmp_path) is None

    assert caplog.messages == [
        "Could not read watchdog ownership marker; duplicate-start protection is limited"
    ]
    assert str(tmp_path) not in caplog.text
    assert caplog.records[0].exc_info is None


def test_missing_and_malformed_pid_markers_are_normal_absence(tmp_path, caplog):
    marker = tmp_path / "watchdog.pid"
    with caplog.at_level(logging.WARNING, logger="jarvis.watchdog"):
        assert watchdog._read_pid_file(marker) is None
        marker.write_text("stale nonnumeric contents", encoding="utf-8")
        assert watchdog._read_pid_file(marker) is None
    assert not caplog.records


def test_written_pid_marker_is_readable_and_removed_only_by_owner(tmp_path):
    marker = tmp_path / "data" / "watchdog.pid"
    watchdog._write_pid_file(marker)
    assert watchdog._read_pid_file(marker) == os.getpid()
    marker.write_text(str(os.getpid() + 1), encoding="utf-8")
    watchdog._clear_pid_file(marker)
    assert marker.exists()
    marker.write_text(str(os.getpid()), encoding="utf-8")
    watchdog._clear_pid_file(marker)
    assert not marker.exists()
