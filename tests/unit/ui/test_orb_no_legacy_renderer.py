"""Regression tests for removing the legacy procedural orb renderer."""
from __future__ import annotations

import pytest

import ui.orb.overlay as overlay_mod
from jarvis.core.config import UIConfig
from ui.orb.overlay import OrbOverlay


def test_ui_config_default_is_whisper_bar_not_legacy_orb() -> None:
    # Default flipped to the slim whisper bar; the legacy procedural "orb" is
    # still never the default. The mascot stays explicitly selectable.
    assert UIConfig().orb_style == "whisper_bar"
    assert UIConfig().orb_style != "orb"
    assert UIConfig(orb_style="mascot").orb_style == "mascot"


def test_legacy_orb_style_is_coerced_to_mascot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_ORB_STYLE", raising=False)

    overlay = OrbOverlay(style="orb")

    assert overlay._style == "mascot"


def test_env_cannot_force_legacy_orb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_ORB_STYLE", "orb")

    overlay = OrbOverlay(style="mascot")

    assert overlay._style == "mascot"


def test_runtime_style_switch_cannot_select_legacy_orb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_ORB_STYLE", raising=False)
    overlay = OrbOverlay(style="mascot")

    overlay.set_style("orb")

    assert overlay._style == "mascot"


def test_missing_mascot_asset_does_not_fallback_to_legacy_orb(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("JARVIS_ORB_STYLE", raising=False)
    monkeypatch.setattr(
        overlay_mod,
        "DEFAULT_MASCOT_REL",
        "assets/icons/__missing_legacy_orb_regression__.png",
    )

    overlay = OrbOverlay(style="mascot", mascot_path=tmp_path / "missing.png")

    assert overlay._build_renderer("mascot") is None
