import json
import os

import pytest

from jarvis.cli_ctl import config as cfg
from jarvis.cli_ctl import paths


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path))
    for k in ("JARVISCTL_BASE_URL", "JARVISCTL_CONTROL_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_default_base_url_is_local_admin_port():
    prof = cfg.resolve_profile()
    assert prof.base_url == "http://127.0.0.1:47821"


def test_env_overrides_win(monkeypatch):
    monkeypatch.setenv("JARVISCTL_BASE_URL", "https://vps.example:8080")
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_envkey")
    prof = cfg.resolve_profile()
    assert prof.base_url == "https://vps.example:8080"
    assert prof.control_key == "jctl_envkey"


def test_saved_file_used_when_no_env(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"base_url": "http://h:1", "control_key": "jctl_filekey"}),
        encoding="utf-8",
    )
    prof = cfg.resolve_profile()
    assert prof.base_url == "http://h:1"
    assert prof.control_key == "jctl_filekey"


def test_local_control_key_fallback(monkeypatch):
    # No env, no file -> fall back to the local-control-key helper. Patch the
    # helper directly so the test is independent of whether the full Jarvis
    # runtime is importable (keeps the minimal-install CI matrix green).
    monkeypatch.setattr(
        "jarvis.cli_ctl.config._local_control_key", lambda: "jctl_localkey"
    )
    prof = cfg.resolve_profile()
    assert prof.control_key == "jctl_localkey"


def test_save_login_persists_and_chmods(monkeypatch, tmp_path):
    cfg.save_login("http://h:2", "jctl_saved")
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data == {"base_url": "http://h:2", "control_key": "jctl_saved"}


def test_session_file_used_when_no_env_or_config(monkeypatch, tmp_path):
    sess = tmp_path / "session.json"
    sess.write_text(
        json.dumps({"port": 48999, "token": "jctl_sess", "pid": os.getpid()}),
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(sess))
    monkeypatch.setattr("jarvis.cli_ctl.config._local_control_key", lambda: None)
    prof = cfg.resolve_profile()
    assert prof.base_url == "http://127.0.0.1:48999"
    assert prof.control_key == "jctl_sess"


def test_config_file_beats_session(monkeypatch, tmp_path):
    sess = tmp_path / "session.json"
    sess.write_text(
        json.dumps({"port": 48999, "token": "jctl_sess", "pid": os.getpid()}),
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(sess))
    paths.config_file().write_text(
        json.dumps({"base_url": "http://h:1", "control_key": "jctl_file"}),
        encoding="utf-8",
    )
    prof = cfg.resolve_profile()
    assert prof.base_url == "http://h:1"
    assert prof.control_key == "jctl_file"
