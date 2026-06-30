"""Robust cross-platform primary-monitor resolution (audit G8a).

``resolve_primary_monitor`` picks which physical monitor is "the main one" Jarvis
works on. Default is the OS primary (identified robustly, NOT by assuming origin
(0,0)); ``largest`` and an explicit id/index are config overrides. The native
primary origin is a best-effort hint (Win MONITORINFOF_PRIMARY etc.); the pure
decision logic is what these pin — the native query is monkeypatched.
"""
from __future__ import annotations

from jarvis.platform import monitors as mon

# mss-style: monitors[0] = virtual bounding box, [1:] = physical screens.
_VIRTUAL = {"left": -2560, "top": 0, "width": 6400, "height": 2160}
_LEFT = {"left": -2560, "top": 0, "width": 2560, "height": 1440, "name": "DELL-left"}
_MAIN = {"left": 0, "top": 0, "width": 3840, "height": 2160, "name": "LG-main"}


def _rig() -> list[dict]:
    return [_VIRTUAL, dict(_LEFT), dict(_MAIN)]


def test_single_monitor_returns_the_only_one(monkeypatch):
    monkeypatch.setattr(mon, "native_primary_origin", lambda: None)
    only = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    assert mon.resolve_primary_monitor([only]) is only


def test_is_primary_flag_wins(monkeypatch):
    monkeypatch.setattr(mon, "native_primary_origin", lambda: None)
    rig = _rig()
    rig[1]["is_primary"] = True  # mark the LEFT one primary explicitly
    assert mon.resolve_primary_monitor(rig)["name"] == "DELL-left"


def test_native_origin_identifies_primary_when_not_at_zero(monkeypatch):
    # A rig whose primary is NOT at (0,0) — the native origin must still find it
    # (this is the "do not assume (0,0)" requirement).
    virtual = {"left": 0, "top": 0, "width": 5760, "height": 2160}
    left = {"left": 0, "top": 0, "width": 1920, "height": 1080, "name": "sec"}
    main = {"left": 1920, "top": 0, "width": 3840, "height": 2160, "name": "primary"}
    monkeypatch.setattr(mon, "native_primary_origin", lambda: (1920, 0))
    assert mon.resolve_primary_monitor([virtual, left, main])["name"] == "primary"


def test_falls_back_to_origin_when_no_native(monkeypatch):
    monkeypatch.setattr(mon, "native_primary_origin", lambda: None)
    assert mon.resolve_primary_monitor(_rig())["name"] == "LG-main"  # the (0,0) one


def test_largest_override_picks_biggest_area(monkeypatch):
    monkeypatch.setattr(mon, "native_primary_origin", lambda: (-2560, 0))  # would be LEFT
    # ...but override=largest must pick MAIN (3840x2160 > 2560x1440).
    assert mon.resolve_primary_monitor(_rig(), override="largest")["name"] == "LG-main"


def test_explicit_name_override(monkeypatch):
    monkeypatch.setattr(mon, "native_primary_origin", lambda: None)
    assert mon.resolve_primary_monitor(_rig(), override="dell")["name"] == "DELL-left"


def test_explicit_index_override(monkeypatch):
    monkeypatch.setattr(mon, "native_primary_origin", lambda: None)
    # 1-based physical index: "1" = LEFT, "2" = MAIN.
    assert mon.resolve_primary_monitor(_rig(), override="2")["name"] == "LG-main"


def test_unknown_explicit_id_falls_back_to_primary_not_wrong(monkeypatch):
    # An unmatched id must NOT silently pick a wrong screen — fall back to primary.
    monkeypatch.setattr(mon, "native_primary_origin", lambda: None)
    assert mon.resolve_primary_monitor(_rig(), override="nonexistent")["name"] == "LG-main"


def test_native_primary_origin_never_raises():
    # Whatever the host, the best-effort native query returns a tuple or None.
    result = mon.native_primary_origin()
    assert result is None or (
        isinstance(result, tuple) and len(result) == 2
        and all(isinstance(v, int) for v in result)
    )
