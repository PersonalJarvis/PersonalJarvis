"""Every runtime test runs against a private user-data folder.

Runtime code derives its folders from ``jarvis.core.paths.user_data_dir`` (agent
folders, Jarvis' own OpenClaw copy, the last-good pins). A test that redirected
only ``runtimes_root`` once wrote stub binaries into the person's real
``agent_runtimes/openclaw-cli`` (2026-10-07), which the live app would then
have tried to run. The whole package is redirected here instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _private_user_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    from jarvis.core import paths

    root = tmp_path_factory.mktemp("jarvis-user-data")
    monkeypatch.setattr(paths, "user_data_dir", lambda: root)
    return root
