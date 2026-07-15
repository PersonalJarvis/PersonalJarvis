"""Offscreen smoke test: the indicator sidecar boots, acks, and exits.

Runs the real ``python -m jarvis.cu.indicator`` subprocess on Qt's
offscreen platform plugin so it works on CI boxes without a display.
Auto-skips when PySide6 is not installed (base install / headless floor).
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import pytest

from jarvis.cu.indicator import protocol

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="PySide6 not installed (indicator sidecar is a [desktop] extra)",
)


def test_sidecar_show_quit_round_trip() -> None:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("JARVIS_CU_INDICATOR_AUTOSHOW", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "jarvis.cu.indicator"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(
            protocol.encode_command(protocol.CMD_SHOW, hint="Esc to cancel")
        )
        proc.stdin.write(protocol.encode_command(protocol.CMD_BLANK))
        proc.stdin.write(protocol.encode_command(protocol.CMD_UNBLANK))
        proc.stdin.write(protocol.encode_command(protocol.CMD_QUIT))
        proc.stdin.flush()
        out, err = proc.communicate(timeout=30)
    except Exception:
        proc.kill()
        raise
    acks = [
        protocol.decode_ack(line)
        for line in out.splitlines()
        if protocol.decode_ack(line) is not None
    ]
    assert proc.returncode == 0, f"sidecar exited {proc.returncode}: {err}"
    assert acks == ["show", "blank", "unblank", "quit"], f"acks={acks} err={err}"


def test_sidecar_exits_cleanly_on_stdin_eof() -> None:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.Popen(
        [sys.executable, "-m", "jarvis.cu.indicator"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
    )
    assert proc.stdin is not None
    proc.stdin.close()  # parent "dies" — EOF must end the sidecar
    assert proc.wait(timeout=30) == 0
