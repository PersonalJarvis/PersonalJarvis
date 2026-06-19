"""Regression guard for the CWD-drift bug class.

Many persistence paths (data/setup_state.json, the SQLite DBs, flight_recorder,
audit logs) are resolved relative to ``os.getcwd()`` under the historical
assumption that the desktop app always launches from the repo root. That
assumption was false — the autostart Scheduled Task sets a WorkingDirectory, but
a manual start / restart-app inherits the user home — so the same install
read/wrote a *different* ``data/`` dir per start method: the first-run guide
re-appeared on every restart and Chats/Sessions/Missions split across two
folders. ``ensure_project_root_cwd`` pins the CWD to the repo root so every
CWD-relative path is deterministic.
"""
from pathlib import Path

from jarvis.core import config


def test_ensure_project_root_cwd_pins_from_foreign_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert Path.cwd() != config.PROJECT_ROOT  # precondition: started elsewhere

    result = config.ensure_project_root_cwd()

    assert Path.cwd() == config.PROJECT_ROOT
    assert result == config.PROJECT_ROOT


def test_ensure_project_root_cwd_idempotent_when_already_at_root(monkeypatch):
    monkeypatch.chdir(config.PROJECT_ROOT)

    result = config.ensure_project_root_cwd()

    assert Path.cwd() == config.PROJECT_ROOT
    assert result == config.PROJECT_ROOT
