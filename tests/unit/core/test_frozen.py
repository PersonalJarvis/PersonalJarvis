"""``jarvis.core.frozen`` — the install-kind probe, exercised by patching ``sys``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis.core import frozen


def test_not_frozen_in_a_plain_interpreter() -> None:
    assert frozen.is_frozen() is False
    assert frozen.bundle_root() is None
    assert frozen.resources_root() is None


def test_frozen_needs_both_pyinstaller_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``sys.frozen`` alone (cx_Freeze, py2exe) is not a PyInstaller bundle.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert frozen.is_frozen() is False

    monkeypatch.setattr(sys, "_MEIPASS", "/somewhere/_internal", raising=False)
    assert frozen.is_frozen() is True


def test_roots_follow_the_onedir_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = tmp_path / "Jarvis"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    exe = bundle / ("Jarvis.exe" if sys.platform == "win32" else "Jarvis")
    exe.write_bytes(b"")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    assert frozen.bundle_root() == bundle.resolve()
    assert frozen.resources_root() == internal.resolve()
