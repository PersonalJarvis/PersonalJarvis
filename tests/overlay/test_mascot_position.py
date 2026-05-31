"""Mascot Position-Persistence + Monitor-Recovery — Plan §13.4 + §20.4."""

from __future__ import annotations

from pathlib import Path

import pytest

from overlay.mascot_position import (
    DEFAULT_X_RELATIVE,
    DEFAULT_Y_RELATIVE,
    MascotPosition,
    ResolvedPlacement,
    _ScreenSnapshot,
    clamp_to_work_area,
    load_position_from_toml,
    resolve_placement,
    save_position_to_toml,
    snap_to_edges,
)


def _screen(
    name: str,
    *,
    x: int = 0,
    y: int = 0,
    w: int = 1920,
    h: int = 1080,
    primary: bool = False,
) -> _ScreenSnapshot:
    return _ScreenSnapshot(
        name=name, geometry=(x, y, w, h), is_primary=primary
    )


# -------------------------------------------------------------------------
# resolve_placement — Plan §13.4 5-Schritt-Recovery
# -------------------------------------------------------------------------


def test_resolve_when_persisted_monitor_present() -> None:
    screens = [_screen("\\\\.\\DISPLAY1", primary=True)]
    persisted = MascotPosition(
        monitor="\\\\.\\DISPLAY1", x_relative=300, y_relative=100
    )
    placement = resolve_placement(persisted, screens)
    assert placement.abs_x == 300
    assert placement.abs_y == 100
    assert placement.monitor == "\\\\.\\DISPLAY1"
    assert placement.recovered is False


def test_resolve_falls_back_to_primary_when_monitor_missing() -> None:
    """Plan §13.4 step 3: primary fallback wenn persisted monitor weg."""
    screens = [_screen("\\\\.\\DISPLAY2", primary=True, x=100, y=200)]
    persisted = MascotPosition(
        monitor="\\\\.\\DISPLAY99", x_relative=500, y_relative=400
    )
    placement = resolve_placement(persisted, screens)
    # Default-Position auf primary, NICHT der gespeicherte Offset.
    assert placement.abs_x == 100 + DEFAULT_X_RELATIVE
    assert placement.abs_y == 200 + DEFAULT_Y_RELATIVE
    assert placement.monitor == "\\\\.\\DISPLAY2"
    assert placement.recovered is True


def test_resolve_with_no_persisted_uses_primary_default() -> None:
    screens = [_screen("\\\\.\\DISPLAY1", primary=True)]
    placement = resolve_placement(None, screens)
    assert placement.abs_x == DEFAULT_X_RELATIVE
    assert placement.abs_y == DEFAULT_Y_RELATIVE
    assert placement.recovered is True


def test_resolve_with_no_screens_returns_default() -> None:
    persisted = MascotPosition(monitor="X", x_relative=10, y_relative=20)
    placement = resolve_placement(persisted, [])
    assert placement.recovered is True
    assert placement.monitor == ""


def test_resolve_clamps_when_relative_offset_off_screen() -> None:
    """Resolution-Change: monitor schrumpft, persisted x_rel ist nun ausserhalb."""
    screens = [_screen("\\\\.\\DISPLAY1", primary=True, w=800, h=600)]
    persisted = MascotPosition(
        monitor="\\\\.\\DISPLAY1", x_relative=2000, y_relative=2000
    )
    placement = resolve_placement(persisted, screens, mascot_size_px=160)
    # Clamped in 800-160-16=624 max.
    assert placement.abs_x <= 800 - 160 - 16
    assert placement.abs_y <= 600 - 160 - 16
    assert placement.recovered is False  # gleicher Monitor, nur clamped


def test_resolve_picks_first_screen_when_no_primary_flagged() -> None:
    screens = [_screen("a"), _screen("b")]
    placement = resolve_placement(None, screens)
    assert placement.monitor == "a"


# -------------------------------------------------------------------------
# Snap + Clamp
# -------------------------------------------------------------------------


def test_snap_to_left_edge_when_within_tolerance() -> None:
    geo = (0, 0, 1920, 1080)
    sx, sy = snap_to_edges(8, 100, geo, mascot_size_px=160, snap_tolerance_px=16)
    assert sx == 0
    assert sy == 100


def test_snap_to_right_edge_when_within_tolerance() -> None:
    geo = (0, 0, 1920, 1080)
    # Mascot 160 wide, right edge at 1920. Position 1755 -> right gap 5px.
    sx, _sy = snap_to_edges(1755, 100, geo, mascot_size_px=160, snap_tolerance_px=16)
    assert sx == 1920 - 160


def test_snap_no_op_outside_tolerance() -> None:
    geo = (0, 0, 1920, 1080)
    sx, sy = snap_to_edges(50, 50, geo, mascot_size_px=160, snap_tolerance_px=16)
    assert (sx, sy) == (50, 50)


def test_clamp_keeps_mascot_in_work_area() -> None:
    geo = (0, 0, 1920, 1080)
    cx, cy = clamp_to_work_area(-100, -100, geo, mascot_size_px=160, margin_px=16)
    assert cx == 16
    assert cy == 16
    cx2, cy2 = clamp_to_work_area(5000, 5000, geo, mascot_size_px=160, margin_px=16)
    assert cx2 == 1920 - 160 - 16
    assert cy2 == 1080 - 160 - 16


# -------------------------------------------------------------------------
# TOML Round-Trip
# -------------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "jarvis.toml"
    pos = MascotPosition(
        monitor="\\\\.\\DISPLAY1", x_relative=300, y_relative=400
    )
    save_position_to_toml(p, pos)
    loaded = load_position_from_toml(p)
    assert loaded == pos


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "no-such.toml"
    assert load_position_from_toml(p) is None


def test_load_section_missing_returns_defaults_with_empty_monitor(
    tmp_path: Path,
) -> None:
    p = tmp_path / "jarvis.toml"
    p.write_text("[other.section]\nfoo = 1\n", encoding="utf-8")
    loaded = load_position_from_toml(p)
    assert loaded is not None
    assert loaded.monitor == ""
    assert loaded.x_relative == DEFAULT_X_RELATIVE
    assert loaded.y_relative == DEFAULT_Y_RELATIVE


def test_save_preserves_existing_other_sections(tmp_path: Path) -> None:
    p = tmp_path / "jarvis.toml"
    p.write_text(
        "# user comment\n"
        "[other.section]\n"
        'foo = "bar"\n'
        "\n"
        "[overlay.mascot]\n"
        'position_monitor = "OLD"\n'
        "position_x_relative = 1\n"
        "position_y_relative = 2\n",
        encoding="utf-8",
    )
    pos = MascotPosition(
        monitor="\\\\.\\DISPLAY3", x_relative=500, y_relative=600
    )
    save_position_to_toml(p, pos)
    text = p.read_text(encoding="utf-8")
    assert "[other.section]" in text
    assert "# user comment" in text  # Comments bleiben
    assert 'foo = "bar"' in text
    loaded = load_position_from_toml(p)
    assert loaded == pos


def test_save_appends_section_when_missing(tmp_path: Path) -> None:
    p = tmp_path / "jarvis.toml"
    p.write_text("[other]\nbaz = 3\n", encoding="utf-8")
    pos = MascotPosition(monitor="X", x_relative=10, y_relative=20)
    save_position_to_toml(p, pos)
    loaded = load_position_from_toml(p)
    assert loaded == pos
    assert "[other]" in p.read_text(encoding="utf-8")


def test_save_to_nonexistent_file_creates_minimal(tmp_path: Path) -> None:
    p = tmp_path / "fresh.toml"
    pos = MascotPosition(monitor="X", x_relative=1, y_relative=2)
    save_position_to_toml(p, pos)
    assert p.is_file()
    loaded = load_position_from_toml(p)
    assert loaded == pos
