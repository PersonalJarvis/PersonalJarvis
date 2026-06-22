"""Unit tests for live-instance discovery (jarvis.cli_ctl.discovery)."""
from __future__ import annotations

import json
import os

from jarvis.cli_ctl import discovery


def _write_session(path, **data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_missing_file_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(tmp_path / "absent.json"))
    assert discovery.discover() is None


def test_empty_override_disables_discovery(monkeypatch):
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", "")
    assert discovery.session_file() is None
    assert discovery.discover() is None


def test_reads_port_token_pid(monkeypatch, tmp_path):
    f = tmp_path / "session.json"
    _write_session(f, port=48999, token="jctl_live", pid=os.getpid())
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(f))
    info = discovery.discover()
    assert info is not None
    assert info.base_url == "http://127.0.0.1:48999"
    assert info.token == "jctl_live"
    assert info.pid == os.getpid()


def test_malformed_json_returns_none(monkeypatch, tmp_path):
    f = tmp_path / "session.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(f))
    assert discovery.discover() is None


def test_missing_port_returns_none(monkeypatch, tmp_path):
    f = tmp_path / "session.json"
    _write_session(f, token="jctl_x", pid=os.getpid())
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(f))
    assert discovery.discover() is None


def test_non_dict_returns_none(monkeypatch, tmp_path):
    f = tmp_path / "session.json"
    f.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(f))
    assert discovery.discover() is None


def test_dead_pid_is_ignored(monkeypatch, tmp_path):
    f = tmp_path / "session.json"
    _write_session(f, port=48999, token="jctl_x", pid=1234567)
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(f))
    monkeypatch.setattr(discovery, "_pid_alive", lambda pid: False)
    assert discovery.discover() is None
    # …but with the pid check disabled the same file resolves.
    assert discovery.discover(check_pid=False) is not None


def test_token_optional(monkeypatch, tmp_path):
    f = tmp_path / "session.json"
    _write_session(f, port=48999, pid=os.getpid())
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(f))
    info = discovery.discover()
    assert info is not None and info.token is None


def test_current_process_is_alive():
    assert discovery._pid_alive(os.getpid()) is True


def test_filename_and_dir_parity_with_writer(monkeypatch):
    """discovery must target the exact file the desktop app writes."""
    from jarvis.ui.shell import single_instance

    assert discovery.SESSION_FILENAME == single_instance.SESSION_FILENAME
    # With the test override cleared, the resolved path must match the writer's.
    monkeypatch.delenv("JARVIS_CLI_SESSION_FILE", raising=False)
    si = single_instance.SingleInstance()
    assert discovery.session_file() == si.session_file
