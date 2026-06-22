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
import sys
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


def test_ensure_project_root_cwd_adds_root_to_sys_path(monkeypatch, tmp_path):
    """The repo root must land in ``sys.path``, not just the CWD.

    Root packages ``ui`` and ``conductor`` live OUTSIDE the editable-installed
    ``jarvis`` package, so they import only when the repo root is on
    ``sys.path``. ``python -m`` seeds ``sys.path[0]`` from the *start-time* CWD;
    a later ``os.chdir`` does NOT fix it. A manual start / restart-app inheriting
    the user home therefore left ``ui`` unimportable, and the on-screen overlay
    (whisper-bar) silently failed to start ("No module named 'ui'"). Pinning the
    CWD is not enough — the root must be put on the import path too.
    """
    root = str(config.PROJECT_ROOT)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != root])
    monkeypatch.chdir(tmp_path)
    assert root not in sys.path  # precondition: started from a foreign sys.path

    config.ensure_project_root_cwd()

    assert root in sys.path


def test_ensure_project_root_cwd_sys_path_idempotent(monkeypatch):
    """A second call must not duplicate the repo root on ``sys.path``."""
    root = str(config.PROJECT_ROOT)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != root])

    config.ensure_project_root_cwd()
    config.ensure_project_root_cwd()

    assert sys.path.count(root) == 1
