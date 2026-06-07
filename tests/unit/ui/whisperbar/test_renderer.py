"""Unit tests for the whisper-bar pure renderer math + draw smoke."""
from __future__ import annotations

import pytest

from jarvis.ui.whisperbar import renderer as R


def test_ease_moves_toward_target():
    assert R.ease(0.0, 1.0, 0.5) == 0.5
    assert R.ease(0.5, 1.0, 0.5) == 0.75


def test_bar_heights_zero_level_is_min():
    hs = R.bar_heights(0.0, 0.0, 7, max_h=40.0, min_h=4.0)
    assert len(hs) == 7
    assert all(abs(h - 4.0) < 1e-6 for h in hs)


def test_bar_heights_grow_with_level():
    lo = sum(R.bar_heights(0.3, 0.2, 7, max_h=40.0, min_h=4.0))
    hi = sum(R.bar_heights(0.3, 0.9, 7, max_h=40.0, min_h=4.0))
    assert hi > lo
    for h in R.bar_heights(1.7, 1.0, 7, max_h=40.0, min_h=4.0):
        assert 4.0 <= h <= 40.0 + 1e-6


def test_wave_points_bounded_inside_pill():
    pts = R.wave_points(0.4, 200, 52, cx=150, cy=36, n=48)
    assert len(pts) == 49
    for x, y in pts:
        assert 50 <= x <= 250
        assert 36 - 26 <= y <= 36 + 26  # within ±height*0.5


def test_render_returns_image_for_every_mode():
    rnd = R.WhisperBarRenderer(accent="#e7c46e")
    for mode in ("idle", "listen", "speak", "think"):
        img = rnd.render(0.1, mode, 0.5)
        assert img.size == (R.WIN_W, R.WIN_H)
        assert img.mode == "RGB"


def _settled(mode, hovered, frames=40, final_t=0.1):
    r = R.WhisperBarRenderer()
    for _ in range(frames):
        r.render(0.0, mode, 0.0)  # deterministic settle
    return list(r.render(final_t, mode, 0.0, hovered=hovered).getdata())


def test_hover_reveals_controls():
    # active + hover draws the X + square → pixels differ from the animation
    assert _settled("listen", hovered=False) != _settled("listen", hovered=True)
    assert _settled("think", hovered=False) != _settled("think", hovered=True)
    # idle + hover opens the bar and shows the dictation square → differs from
    # the clean collapsed standby pill
    assert _settled("idle", hovered=False, frames=80, final_t=0.0) != _settled(
        "idle", hovered=True, frames=80, final_t=0.0
    )


def test_idle_collapses_expansion_over_frames():
    rnd = R.WhisperBarRenderer()
    for _ in range(40):
        rnd.render(0.0, "listen", 0.5)
    active_h = rnd._st.ph
    for _ in range(80):
        rnd.render(0.0, "idle", 0.0)
    assert rnd._st.ph < active_h
    assert rnd._st.ph == pytest.approx(R.COLLAPSED_H, abs=1.0)


# --- visual_mode: sound-driven look (bars while audible, wave while silent) ---


def test_visual_mode_idle_stays_idle_regardless_of_sound():
    # idle is the standby pill; sound recency must not turn it into bars.
    assert R.visual_mode("idle", 0.0, hold_s=0.5) == "idle"
    assert R.visual_mode("idle", 10.0, hold_s=0.5) == "idle"


def test_visual_mode_shows_bars_while_sound_is_recent():
    # In ANY active turn, real sound (mic OR TTS) within the hold window draws
    # the speaking equalizer — this is what the user calls the "Striche".
    assert R.visual_mode("listen", 0.0, hold_s=0.5) == "speak"
    assert R.visual_mode("think", 0.1, hold_s=0.5) == "speak"
    assert R.visual_mode("speak", 0.49, hold_s=0.5) == "speak"


def test_visual_mode_wave_only_while_thinking():
    # The wave (the "indicator") appears ONLY while actively thinking/processing
    # — coarse "think" is the THINKING state AND the silent TTS-synthesis
    # lead-in (the bridge shows "think" for SPEAKING too). That is the only
    # place an animated indicator belongs.
    assert R.visual_mode("think", 5.0, hold_s=0.5) == "think"
    assert R.visual_mode("think", 99.0, hold_s=0.5) == "think"


def test_visual_mode_listening_silence_is_still_bars_not_wave():
    # After "Hey Jarvis" with no speech yet, Jarvis is WAITING, not thinking —
    # the user explicitly does NOT want the wave there. Silence in any
    # non-thinking active state shows bars, which render flat/still at level 0.
    assert R.visual_mode("listen", 2.0, hold_s=0.5) == "speak"
    assert R.visual_mode("listen", 99.0, hold_s=0.5) == "speak"


def test_visual_mode_shows_bars_while_tts_playback_is_active():
    # The TTS player only feeds a level at buffer-write time (a brief instant),
    # then blocks for the whole multi-second playback with NO further feed. So
    # `seconds_since_audible` goes stale mid-sentence. `playback_active` is the
    # player's authoritative "audio is on the device right now" signal — while
    # it's True the bar MUST show bars even though the last level is stale.
    assert R.visual_mode("listen", 4.0, hold_s=0.5, playback_active=True) == "speak"
    assert R.visual_mode("speak", 99.0, hold_s=0.5, playback_active=True) == "speak"
    # idle is still idle even if a stray playback flag lingers.
    assert R.visual_mode("idle", 0.0, hold_s=0.5, playback_active=True) == "idle"
    # Playback over + stale level: a THINKING turn falls back to the wave, but a
    # LISTENING turn falls back to still bars (waiting, not thinking).
    assert R.visual_mode("think", 4.0, hold_s=0.5, playback_active=False) == "think"
    assert R.visual_mode("listen", 4.0, hold_s=0.5, playback_active=False) == "speak"


# --- conversation growth: the bar gets ~2x bigger while a session is live ----


def _grow_settle(mode, *, hovered=False, frames=120):
    """Run enough frames that the eased pill size has converged on its target."""
    r = R.WhisperBarRenderer()
    for _ in range(frames):
        r.render(0.0, mode, 0.0, hovered=hovered)
    return r


def test_active_conversation_pill_size_vs_open_pill():
    # During a conversation the pill is 2x the hover-open pill, then trimmed to
    # feel less bulky: 15% off each side (→ 0.70 of 2x width) and 5% off top and
    # bottom (→ 0.90 of 2x height). Centred, so the idle bar stays in the middle.
    active = _grow_settle("speak")
    open_pill = _grow_settle("idle", hovered=True)
    assert active._st.pw == pytest.approx(2 * 0.70 * open_pill._st.pw, rel=0.05)
    assert active._st.ph == pytest.approx(2 * 0.90 * open_pill._st.ph, rel=0.05)


def test_window_is_large_enough_for_the_active_pill():
    # The Tk window is fixed-size; a pill bigger than the window gets clipped.
    # The window must hold the largest pill plus its 2px outline on each side.
    assert R.WIN_W >= R.ACTIVE_W + 4
    assert R.WIN_H >= R.ACTIVE_H + 4


def test_pill_bottom_edge_is_anchored_so_growth_goes_upward():
    # The bottom edge sits at a constant offset regardless of pill height, so
    # the idle pill stays put and the conversation pill grows UPWARD (never into
    # the taskbar).
    bottom_idle = R.pill_center_y(R.COLLAPSED_H) + R.COLLAPSED_H / 2.0
    bottom_active = R.pill_center_y(R.ACTIVE_H) + R.ACTIVE_H / 2.0
    assert bottom_idle == pytest.approx(bottom_active)


def test_pill_size_target_per_state():
    # idle stays collapsed; hover opens to the medium pill; a live session goes
    # to the large pill. "Only while in the conversation" → only active is 2x.
    assert _grow_settle("idle")._st.ph == pytest.approx(R.COLLAPSED_H, abs=1.0)
    assert _grow_settle("idle", hovered=True)._st.ph == pytest.approx(R.OPEN_H, abs=1.0)
    assert _grow_settle("speak")._st.ph == pytest.approx(R.ACTIVE_H, abs=1.0)


def test_equalizer_bars_scale_with_pill_height():
    # Bars are derived from the live pill height, so they grow with it — they
    # must not look lost in the big active bar.
    assert R.bar_max_for(R.ACTIVE_H) > R.bar_max_for(R.OPEN_H)


# --- Wispr-style refinement: thin strokes + standby dots ---------------------


def test_evenly_spaced_is_centered_and_symmetric():
    xs = R.evenly_spaced(cx=50.0, span=60.0, n=7)
    assert len(xs) == 7
    assert xs[0] == 20.0 and xs[-1] == 80.0  # span/2 either side of cx
    assert xs[3] == 50.0  # middle item sits exactly on cx
    assert xs[0] + xs[-1] == 2 * 50.0  # symmetric around cx


def test_evenly_spaced_single_item_sits_at_center():
    assert R.evenly_spaced(cx=10.0, span=40.0, n=1) == [10.0]


def test_idle_pill_is_empty_no_standby_dots():
    # When nothing is happening the standby pill is CLEAN — no dots, no bars.
    # (User: "when nothing is happening, nothing is in the bar.")
    r = R.WhisperBarRenderer()
    img = None
    for _ in range(150):  # settle to the collapsed idle pill
        img = r.render(0.0, "idle", 0.0)
    dr, dg, db = R.DOT_COLOR
    near = [
        1
        for (pr, pg, pb) in img.getdata()
        if abs(pr - dr) + abs(pg - dg) + abs(pb - db) < 40
    ]
    assert not near, "idle pill must be empty — no standby dots/indicators"


def test_active_bars_are_slim_not_chunky():
    # Wispr-style: the equalizer strokes are thin, not the old chunky ~6px bars.
    assert R.bar_half_w_for(R.ACTIVE_W) <= 2.0
