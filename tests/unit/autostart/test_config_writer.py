"""config_writer.set_autostart: TOML-only bool round-trip + config model default."""

from __future__ import annotations

import tomllib
from pathlib import Path

from jarvis.core import config_writer
from jarvis.core.config import AutostartConfig, JarvisConfig


def test_default_enabled_is_true() -> None:
    # Approved design spec §5 ("default ON, user mandate"): on first boot the
    # self-healing reconcile installs the entry so Jarvis launches at login.
    # The Settings toggle is the off-switch; a headless host is a graceful
    # no-op regardless (supported=False), so default-on stays cloud-safe.
    assert AutostartConfig().enabled is True
    assert JarvisConfig().autostart.enabled is True


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
