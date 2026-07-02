"""Actuator contract tests — landed-position verification and pure mapping.

No test here dispatches real input (clicks/keys would hit the developer's
desktop); real-input behaviour is exercised by scripts/cu_test_rig.py.
"""
from __future__ import annotations

import os

import pytest

from jarvis.cu.actuate.base import (
    ActResult,
    ActuationUnavailable,
    Actuator,
    verified_move,
)
from jarvis.cu.actuate.windows import (
    expand_combo_keys,
    normalize_virtualdesk,
    resolve_vk,
)


class FakeActuator(Actuator):
    """Scriptable fake: lands where told to, records calls."""

    name = "fake"

    def __init__(self, landings: list[tuple[int, int] | None]) -> None:
        self.landings = list(landings)
        self.moves: list[tuple[int, int]] = []

    def cursor_pos(self):
        return self.landings.pop(0) if self.landings else None

    def move(self, x: int, y: int) -> None:
        self.moves.append((x, y))

    def click(self, x, y, *, button="left", double=False):  # pragma: no cover
        raise NotImplementedError

    def drag(self, x1, y1, x2, y2, *, duration_s=0.4):  # pragma: no cover
        raise NotImplementedError

    def scroll(self, direction, notches, *, x=None, y=None):  # pragma: no cover
        raise NotImplementedError

    def key_combo(self, keys):  # pragma: no cover
        raise NotImplementedError

    def type_text(self, text, *, delay_s=0.02):  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# verified_move — the anti-silent-miss contract
# ---------------------------------------------------------------------------

def test_verified_move_accepts_exact_landing():
    fake = FakeActuator(landings=[(100, 200)])
    res = verified_move(fake, 100, 200)
    assert res.ok and res.landed == (100, 200)
    assert fake.moves == [(100, 200)]


def test_verified_move_accepts_within_tolerance():
    fake = FakeActuator(landings=[(101, 198)])
    res = verified_move(fake, 100, 200)
    assert res.ok


def test_verified_move_retries_once_then_fails_loudly():
    # Both attempts land 500px off (a DPI mis-mapping) -> refuse to act.
    fake = FakeActuator(landings=[(600, 200), (600, 200)])
    res = verified_move(fake, 100, 200)
    assert not res.ok
    assert len(fake.moves) == 2
    assert "coordinate-space mismatch" in res.detail
    assert res.landed == (600, 200)


def test_verified_move_retry_recovers():
    fake = FakeActuator(landings=[(600, 200), (100, 200)])
    res = verified_move(fake, 100, 200)
    assert res.ok and len(fake.moves) == 2


def test_verified_move_trusts_unreadable_cursor():
    fake = FakeActuator(landings=[None])
    res = verified_move(fake, 50, 60)
    assert res.ok and res.landed is None
    assert "unverified" in res.detail


def test_act_result_is_frozen():
    res = ActResult(ok=True)
    with pytest.raises(Exception):
        res.ok = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Windows pure mapping helpers (importable and testable on every OS)
# ---------------------------------------------------------------------------

def test_normalize_virtualdesk_folds_negative_origin():
    # Virtual desktop: left monitor at -3840, total 6400x2160.
    vx, vy, vw, vh = -3840, 0, 6400, 2160
    assert normalize_virtualdesk(-3840, 0, vx, vy, vw, vh) == (0, 0)
    nx, ny = normalize_virtualdesk(vx + vw - 1, vh - 1, vx, vy, vw, vh)
    assert (nx, ny) == (65535, 65535)
    # The live-log click target (-1398, 1147) maps strictly inside.
    nx, ny = normalize_virtualdesk(-1398, 1147, vx, vy, vw, vh)
    assert 0 < nx < 65535 and 0 < ny < 65535


def test_normalize_virtualdesk_clamps_outside_points():
    assert normalize_virtualdesk(-9999, -9999, 0, 0, 1920, 1080) == (0, 0)
    assert normalize_virtualdesk(99999, 99999, 0, 0, 1920, 1080) == (65535, 65535)


def test_resolve_vk_letters_digits_and_names():
    assert resolve_vk("a") == ord("A")
    assert resolve_vk("Z") == ord("Z")
    assert resolve_vk("7") == ord("7")
    assert resolve_vk("enter") == 0x0D
    assert resolve_vk("Ctrl") == 0x11
    assert resolve_vk("f12") == 0x7B
    assert resolve_vk("bogus") is None
    assert resolve_vk("") is None


def test_expand_combo_keys_splits_known_combos_only():
    assert expand_combo_keys(["ctrl+v"]) == ["ctrl", "v"]
    assert expand_combo_keys(["ctrl+shift+t"]) == ["ctrl", "shift", "t"]
    assert expand_combo_keys(["ctrl", "v"]) == ["ctrl", "v"]
    # Unknown part -> token kept verbatim so it errors loudly downstream.
    assert expand_combo_keys(["ctrl+bogus"]) == ["ctrl+bogus"]
    assert expand_combo_keys(["+"]) == ["+"]


# ---------------------------------------------------------------------------
# Backend selection on the current host (read-only construction)
# ---------------------------------------------------------------------------

def test_get_actuator_on_this_host():
    from jarvis.cu.actuate import get_actuator

    if os.name == "nt":
        actuator = get_actuator()
        assert actuator.name == "windows-sendinput"
        # Read-only: cursor read-back must work on a real desktop session.
        pos = actuator.cursor_pos()
        assert pos is None or (isinstance(pos[0], int) and isinstance(pos[1], int))
    else:
        # Non-Windows CI can be headless/Wayland — both must surface the
        # honest refusal instead of a broken backend.
        try:
            actuator = get_actuator()
        except ActuationUnavailable as exc:
            assert str(exc)
        else:
            assert actuator.name.startswith("posix-")


def test_pynput_key_table_maps_core_vocabulary():
    keyboard = pytest.importorskip("pynput.keyboard", reason="pynput not installed")
    from jarvis.cu.actuate.posix import _pynput_key_table

    table = _pynput_key_table(keyboard)
    for name in ("ctrl", "shift", "alt", "enter", "tab", "esc", "left", "f5"):
        assert name in table
    # Off-Windows "win" must resolve to the platform super/command key.
    assert "win" in table
