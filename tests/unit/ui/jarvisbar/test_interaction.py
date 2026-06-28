"""Unit tests for jarvis-bar click/drag classification + default placement."""
from __future__ import annotations

from jarvis.ui.jarvisbar import interaction as I
from jarvis.ui.jarvisbar import renderer as R


def test_is_drag_threshold():
    assert I.is_drag(10, 5, 16) is False   # 15 < 16
    assert I.is_drag(10, 7, 16) is True    # 17 >= 16
    assert I.is_drag(-20, 0, 16) is True


def test_classify_release():
    assert I.classify_release(moved=False) == "click"
    assert I.classify_release(moved=True) == "drag"


def test_resolve_click_idle_and_mute_zones():
    W = 100
    # right zone → the microphone → mute toggle (non-destructive), any state
    assert I.resolve_click(90, W, "idle") == "mute"
    assert I.resolve_click(90, W, "listen") == "mute"
    # idle → a click anywhere else starts a normal session
    assert I.resolve_click(10, W, "idle") == "talk"  # idle left → start normal session
    assert I.resolve_click(50, W, "idle") == "talk"
    # active middle (no control there) → nothing
    assert I.resolve_click(50, W, "speak") == "none"


def test_active_bar_body_click_does_not_hang_up():
    """REGRESSION — the silent-hangup trap (live bug 2026-06-19).

    A low-intent click on the BODY of an active bar — where the user sees only
    the equalizer / orbital-core and NO End button (it is drawn only on hover,
    renderer.py) — must NOT end the session. The old code treated the whole
    left 40% of the bar as the hang-up X, so any such click silently hung up
    ("Jarvis legt von selbst auf, ich hab nichts von Auflegen gesagt").
    """
    W, PW = 100, 100
    # Right-of-the-End-button body (centre ≈ cx - 0.32*pw = 18, hit ≈ ±16) → none,
    # even with the controls shown and across every active state.
    assert I.resolve_click(40, W, "speak", hovered=True, pill_w=PW) == "none"
    assert I.resolve_click(40, W, "listen", hovered=True, pill_w=PW) == "none"
    assert I.resolve_click(40, W, "think", hovered=True, pill_w=PW) == "none"


def test_hangup_requires_visible_end_button():
    """A hang-up fires only when the End button is actually shown (hovered) AND
    the click lands on it — so behaviour matches the visible affordance."""
    W, PW = 100, 100
    # End-button centre ≈ cx - 0.32*pw = 18 for these dimensions.
    # On the button, controls visible → hang up (the deliberate gesture works).
    assert I.resolve_click(18, W, "speak", hovered=True, pill_w=PW) == "hangup"
    # Same spot, but the controls are NOT shown (not hovered) → no hang up.
    assert I.resolve_click(18, W, "speak", hovered=False, pill_w=PW) == "none"
    # Hovered, but the click is far from the button (centre of the bar) → none.
    assert I.resolve_click(50, W, "speak", hovered=True, pill_w=PW) == "none"


def test_hangup_hitbox_at_real_bar_geometry():
    """Anchor the contract to the ACTUAL deployed pill, not just W=PW=100.

    At the real dims the End button sits at WIN_W/2 - 0.32*ACTIVE_W, the
    equalizer bars start ~24px in, and the bar window is only ~107px wide — so a
    centre click (over the bars) must NOT hang up while a click on the End does.
    """
    W, PW = R.WIN_W, R.ACTIVE_W
    x_glyph = round(W / 2.0 - 0.32 * PW)  # mirror renderer x_left (End button)
    # Deliberate click on the visible X glyph, controls shown → hang up.
    assert I.resolve_click(x_glyph, W, "speak", hovered=True, pill_w=PW) == "hangup"
    # The bar's centre (where the live equalizer is drawn) → never a hang up.
    assert I.resolve_click(round(W / 2.0), W, "speak", hovered=True, pill_w=PW) == "none"
    # Controls not shown → even the X spot is inert.
    assert I.resolve_click(x_glyph, W, "speak", hovered=False, pill_w=PW) == "none"


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
    cfg.write_text("[ui]\norb_style = \"jarvis_bar\"\n", encoding="utf-8")
    assert I.load_jarvisbar_position(cfg) is None  # not set yet
    I.save_jarvisbar_position(cfg, 640, 980)
    assert I.load_jarvisbar_position(cfg) == (640, 980)
    # overwrite + preserve the pre-existing [ui] section
    I.save_jarvisbar_position(cfg, 100, 200)
    assert I.load_jarvisbar_position(cfg) == (100, 200)
    assert "orb_style" in cfg.read_text(encoding="utf-8")


def test_load_missing_file_is_none(tmp_path):
    assert I.load_jarvisbar_position(tmp_path / "nope.toml") is None


def test_save_missing_file_is_noop(tmp_path):
    missing = tmp_path / "nope.toml"
    I.save_jarvisbar_position(missing, 1, 2)  # must not raise
    assert not missing.exists()
