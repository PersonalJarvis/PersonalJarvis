"""An inherited Electron "run as Node" switch never reaches programs the app opens."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from jarvis.core.process_utils import drop_inherited_electron_node_mode


def test_drop_removes_the_switch_for_every_later_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELECTRON_RUN_AS_NODE", "1")
    drop_inherited_electron_node_mode()
    assert "ELECTRON_RUN_AS_NODE" not in os.environ
    probe = "import os; print(os.environ.get('ELECTRON_RUN_AS_NODE', 'absent'))"
    child = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.strip() == "absent"


def test_drop_is_harmless_when_the_switch_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELECTRON_RUN_AS_NODE", raising=False)
    monkeypatch.setenv("JARVIS_TEST_KEEP", "1")
    drop_inherited_electron_node_mode()
    assert os.environ["JARVIS_TEST_KEEP"] == "1"
