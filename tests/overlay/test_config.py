"""OverlayConfig — Pydantic Round-Trip + from_toml."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from overlay.config import OverlayConfig, OverlayMascotConfig, OverlayThemeConfig


def test_defaults_round_trip() -> None:
    cfg = OverlayConfig()
    data = cfg.model_dump()
    rebuilt = OverlayConfig.model_validate(data)
    assert rebuilt == cfg


def test_theme_defaults() -> None:
    theme = OverlayThemeConfig()
    assert theme.yellow_primary == "#FFC700"
    assert theme.glow_width_px == 14


def test_invalid_hex_color() -> None:
    with pytest.raises(ValueError):
        OverlayThemeConfig(yellow_primary="not-a-color")


def test_invalid_loopback_host() -> None:
    with pytest.raises(ValueError):
        OverlayConfig(ws_host="8.8.8.8")


def test_extra_field_rejected() -> None:
    with pytest.raises(ValueError):
        OverlayConfig.model_validate({"unknown_key": True})


def test_mascot_size_clamp() -> None:
    with pytest.raises(ValueError):
        OverlayMascotConfig(size_px=10)
    with pytest.raises(ValueError):
        OverlayMascotConfig(size_px=999)


def test_from_toml_with_section(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        textwrap.dedent(
            """
            [overlay]
            enabled = false
            all_monitors = true
            ws_port = 7900

            [overlay.theme]
            glow_width_px = 20
            """
        ),
        encoding="utf-8",
    )
    cfg = OverlayConfig.from_toml(toml)
    assert cfg.enabled is False
    assert cfg.all_monitors is True
    assert cfg.ws_port == 7900
    assert cfg.theme.glow_width_px == 20


def test_from_toml_missing_section_uses_defaults(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[other]\nkey = 1\n", encoding="utf-8")
    cfg = OverlayConfig.from_toml(toml)
    assert cfg == OverlayConfig()
