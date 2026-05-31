"""config_writer.set_autostart: TOML-only bool round-trip + config model default."""

from __future__ import annotations

import tomllib
from pathlib import Path

from jarvis.core import config_writer
from jarvis.core.config import AutostartConfig, JarvisConfig


def test_default_enabled_is_false() -> None:
    # Cloud-first / least-surprise default: a fresh install must not register
    # login autostart until the user opts in (wizard, Settings toggle, or an
    # explicit [autostart] enabled = true in jarvis.toml).
    assert AutostartConfig().enabled is False
    assert JarvisConfig().autostart.enabled is False


def test_set_autostart_writes_bool(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[brain]\nprimary = \"gemini\"\n", encoding="utf-8")

    config_writer.set_autostart(False, path=toml)
    data = tomllib.loads(toml.read_text(encoding="utf-8"))
    assert data["autostart"]["enabled"] is False  # a real TOML bool, not "False"

    config_writer.set_autostart(True, path=toml)
    data = tomllib.loads(toml.read_text(encoding="utf-8"))
    assert data["autostart"]["enabled"] is True
    # sibling section preserved
    assert data["brain"]["primary"] == "gemini"


def test_set_autostart_preserves_bom(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("﻿[ui]\ntray_enabled = true\n", encoding="utf-8")
    config_writer.set_autostart(True, path=toml)
    raw = toml.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM round-tripped
