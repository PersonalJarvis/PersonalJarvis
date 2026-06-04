"""Unit tests for whisper-bar click/drag classification + default placement."""
from __future__ import annotations

from jarvis.ui.whisperbar import interaction as I


def test_is_drag_threshold():
    assert I.is_drag(10, 5, 16) is False   # 15 < 16
    assert I.is_drag(10, 7, 16) is True    # 17 >= 16
    assert I.is_drag(-20, 0, 16) is True


def test_classify_release():
    assert I.classify_release(moved=False) == "click"
    assert I.classify_release(moved=True) == "drag"


def test_click_action_talk_when_idle_hangup_when_active():
    assert I.click_action("idle") == "talk"
    assert I.click_action("listen") == "hangup"
    assert I.click_action("think") == "hangup"
    assert I.click_action("speak") == "hangup"
    assert I.click_action("bogus") == "talk"  # unknown → safe default (talk)


def test_resolve_click_zones():
    W = 100
    # right third → the square → dictate (toggle), regardless of state
    assert I.resolve_click(90, W, "idle") == "dictate"
    assert I.resolve_click(90, W, "listen") == "dictate"
    # left third → the X → hangup ONLY while a session is active
    assert I.resolve_click(10, W, "listen") == "hangup"
    assert I.resolve_click(10, W, "idle") == "talk"  # idle left → start normal session
    # middle → start a normal session when idle, nothing when active
    assert I.resolve_click(50, W, "idle") == "talk"
    assert I.resolve_click(50, W, "speak") == "none"


def test_default_bottom_center_placement():
    x, y = I.default_bottom_center(
        screen_w=1920, screen_h=1080, bar_w=300, bar_h=72, margin=12
    )
    assert x == (1920 - 300) // 2
    assert y == 1080 - 72 - 12


def test_clamp_to_screen_keeps_bar_visible():
    # off-screen right/bottom → pulled back inside
    x, y = I.clamp_to_screen(
        5000, 5000, screen_w=1920, screen_h=1080, bar_w=300, bar_h=72, margin=12
    )
    assert x == 1920 - 300 - 12
    assert y == 1080 - 72 - 12
    # negative → pushed to margin
    x, y = I.clamp_to_screen(
        -50, -50, screen_w=1920, screen_h=1080, bar_w=300, bar_h=72, margin=12
    )
    assert x == 12 and y == 12


def test_position_round_trip(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text("[ui]\norb_style = \"whisper_bar\"\n", encoding="utf-8")
    assert I.load_whisperbar_position(cfg) is None  # not set yet
    I.save_whisperbar_position(cfg, 640, 980)
    assert I.load_whisperbar_position(cfg) == (640, 980)
    # overwrite + preserve the pre-existing [ui] section
    I.save_whisperbar_position(cfg, 100, 200)
    assert I.load_whisperbar_position(cfg) == (100, 200)
    assert "orb_style" in cfg.read_text(encoding="utf-8")


def test_load_missing_file_is_none(tmp_path):
    assert I.load_whisperbar_position(tmp_path / "nope.toml") is None


def test_save_missing_file_is_noop(tmp_path):
    missing = tmp_path / "nope.toml"
    I.save_whisperbar_position(missing, 1, 2)  # must not raise
    assert not missing.exists()
