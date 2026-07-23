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

    A low-intent click on the BODY of an active bar — between the End button and
    the mic, where NO control is drawn — must NOT end the session. The old code
    treated the whole left 40% of the bar as the hang-up X, so any such click
    silently hung up ("Jarvis hangs up on its own, I never said anything about
    hanging up").
    """
    W, PW = 100, 100
    # Body gap between the End X (centre 20, hit ±16 → up to 36) and the mic zone
    # (frac ≥ 0.60 → x ≥ 60): a click at 48 lands on neither → none.
    assert I.resolve_click(48, W, "speak", hovered=True, pill_w=PW) == "none"
    assert I.resolve_click(48, W, "listen", hovered=True, pill_w=PW) == "none"
    assert I.resolve_click(48, W, "think", hovered=True, pill_w=PW) == "none"


def test_hangup_requires_visible_end_button():
    """A hang-up fires only when the End X is actually shown (hovered) AND the
    click lands on it — so behaviour matches the visible affordance."""
    W, PW = 100, 100
    # End-X centre ≈ cx - 0.30*pw = 20 for these dimensions.
    # On the X, controls visible → hang up (the deliberate gesture works).
    assert I.resolve_click(20, W, "speak", hovered=True, pill_w=PW) == "hangup"
    # Same spot, but the controls are NOT shown (not hovered) → no hang up.
    assert I.resolve_click(20, W, "speak", hovered=False, pill_w=PW) == "none"
    # Hovered, but the click is far from the X (centre of the bar) → none.
    assert I.resolve_click(50, W, "speak", hovered=True, pill_w=PW) == "none"


def test_hangup_hitbox_at_real_bar_geometry():
    """Anchor the contract to the ACTUAL deployed pill, not just W=PW=100.

    At the real dims the End X sits at WIN_W/2 - 0.30*ACTIVE_W and the bar window
    is ~107px wide — so a centre click (over the equalizer) must NOT hang up
    while a click on the End X does.
    """
    W, PW = R.WIN_W, R.ACTIVE_W
    x_glyph = round(W / 2.0 - 0.30 * PW)  # mirror renderer x_left (End X)
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


# --------------------------------------------------------------------------- #
# Multi-monitor relative placement                                            #
# --------------------------------------------------------------------------- #
def test_relative_within_center_and_edges():
    work = (0, 0, 1920, 1080)
    bw, bh = 300, 72
    # Bottom-centre → rel_x 0.5, rel_y ~1.0 (flush to the bottom of the free
    # space at the very bottom edge).
    x = (1920 - bw) // 2
    rel = I.relative_within(x, 1080 - bh, work=work, bar_w=bw, bar_h=bh)
    assert abs(rel[0] - 0.5) < 1e-6
    assert abs(rel[1] - 1.0) < 1e-6
    # Flush top-left → (0, 0).
    assert I.relative_within(0, 0, work=work, bar_w=bw, bar_h=bh) == (0.0, 0.0)
    # A degenerate axis (bar wider than the work area) yields 0.0, never a crash.
    assert I.relative_within(0, 0, work=(0, 0, 100, 1080), bar_w=300, bar_h=bh)[0] == 0.0


def test_project_relative_preserves_placement_across_monitor_sizes():
    bw, bh = 300, 72
    small = (0, 0, 1920, 1080)
    big = (1920, 0, 3840, 2160)  # a second monitor to the right, larger + offset
    # Bottom-centre on the small monitor…
    rel = I.relative_within((1920 - bw) // 2, 1080 - bh, work=small, bar_w=bw, bar_h=bh)
    # …reprojects to bottom-centre on the big monitor (same RELATIVE spot).
    x, y = I.project_relative(rel[0], rel[1], work=big, bar_w=bw, bar_h=bh)
    assert x == 1920 + (3840 - bw) // 2  # centred on the offset monitor
    assert y == 2160 - bh  # flush to the bottom of its free space


def test_relative_round_trip_is_identity_on_the_same_monitor():
    work = (100, 200, 2560, 1440)
    bw, bh = 284, 60
    for x, y in [(100, 200), (640, 980), (100 + 2560 - bw, 200 + 1440 - bh)]:
        rel = I.relative_within(x, y, work=work, bar_w=bw, bar_h=bh)
        assert I.project_relative(rel[0], rel[1], work=work, bar_w=bw, bar_h=bh) == (x, y)


def test_clamp_to_work_area_pins_to_a_secondary_monitor():
    # A secondary monitor to the right: origin (1920, 0). A drop past its right
    # edge is pulled back INSIDE that monitor, not snapped to the primary — the
    # regression the cross-monitor drag fix targets.
    work = (1920, 0, 1920, 1080)
    bw, bh, margin = 300, 72, 12
    x, y = I.clamp_to_work_area(9000, 9000, work=work, bar_w=bw, bar_h=bh, margin=margin)
    assert x == 1920 + 1920 - bw - margin
    assert y == 1080 - bh - margin
    # A point already inside is unchanged.
    assert I.clamp_to_work_area(
        2000, 500, work=work, bar_w=bw, bar_h=bh, margin=margin
    ) == (2000, 500)


def test_relative_position_persists_and_round_trips(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[ui]\norb_style = "jarvis_bar"\n', encoding="utf-8")
    assert I.load_jarvisbar_relative(cfg) is None  # not set yet
    I.save_jarvisbar_position(cfg, 640, 980, rel=(0.5, 1.0))
    assert I.load_jarvisbar_position(cfg) == (640, 980)
    rel = I.load_jarvisbar_relative(cfg)
    assert rel is not None
    assert abs(rel[0] - 0.5) < 1e-6 and abs(rel[1] - 1.0) < 1e-6
    # A save WITHOUT rel leaves the absolute position but does not require rel.
    I.save_jarvisbar_position(cfg, 100, 200)
    assert I.load_jarvisbar_position(cfg) == (100, 200)
