"""Unit tests for the jarvis-bar pure renderer math + draw smoke."""
from __future__ import annotations

import pytest

from jarvis.ui.jarvisbar import renderer as R


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


def test_display_level_rises_nearly_instantly():
    """The bars must move IN SYNC with the voice: from silence, one frame at
    full input already produces a clearly moving bar (user report 2026-07-21:
    the indicator reacted with a visible delay)."""
    r = R.JarvisBarRenderer()
    r.render(0.0, "listen", 0.0)
    r.render(0.016, "listen", 1.0)
    assert r._st.display_level > 0.5  # noqa: SLF001


def test_display_level_snaps_to_exact_zero_after_silence():
    """After the input level drops to 0 the eased display must reach EXACT 0.0
    within a few 60 fps frames — a sub-visible tail still animates the bars."""
    r = R.JarvisBarRenderer()
    for k in range(10):
        r.render(k * 0.016, "listen", 0.9)
    for k in range(10):
        r.render((10 + k) * 0.016, "listen", 0.0)
        if r._st.display_level == 0.0:  # noqa: SLF001
            break
    assert r._st.display_level == 0.0  # noqa: SLF001
    assert k <= 8  # ≤ ~150 ms at 60 fps


# --- thinking: the travelling sweep ------------------------------------------
# The look the mission deck's header bar uses for "working", brought onto the
# desktop bar (maintainer, 2026-08-20). It replaced the "orbital core", which
# replaced a travelling sine wave before that — so the guards below pin what
# each of those was rejected FOR, not just the current shape.


def test_rejected_thinking_looks_stay_gone():
    # The travelling sine was rejected as a generic-AI visual; the orbital
    # core was rejected on looks and on cost. Guard against a future session
    # resurrecting either by name.
    for gone in (
        "wave_points",
        "wave_width_for",
        "orbit_point",
        "orbit_trail",
        "core_radius",
        "core_drift",
        "core_ring_points",
        "ORBITS",
    ):
        assert not hasattr(R, gone), f"{gone} is back"


def test_sweep_peak_sits_where_the_phase_points():
    # phase 0 → the highlight is on the first bar, phase 1/2 → the middle.
    gains = [R.sweep_gain(i, 9, 0.0) for i in range(9)]
    assert gains.index(max(gains)) == 0
    gains = [R.sweep_gain(i, 9, 0.5) for i in range(9)]
    assert gains.index(max(gains)) == 4


def test_sweep_gain_is_bounded_and_falls_off():
    for count in (5, 7, 10, 14):
        for phase in (0.0, 0.2, 0.5, 0.83, 0.999):
            gains = [R.sweep_gain(i, count, phase) for i in range(count)]
            assert all(0.0 <= g <= 1.0 for g in gains)
            assert max(gains) > 0.5, "the highlight vanished between bars"
            assert min(gains) < 0.3, "the whole row lit up — no highlight to see"


def test_sweep_wraps_around_the_row():
    # The highlight must leave on the right and re-enter on the left without a
    # jump: just before the wrap the LAST bar is brightest, just after it the
    # first — and the two rows are near-identical mirror images.
    before = [R.sweep_gain(i, 10, 0.995) for i in range(10)]
    after = [R.sweep_gain(i, 10, 0.005) for i in range(10)]
    assert before[-1] > 0.9 and after[0] > 0.9
    assert max(abs(a - b) for a, b in zip(before, reversed(after), strict=True)) < 0.05


def test_sweep_travels_over_time():
    peak_at = []
    for k in range(6):
        gains = R.sweep_gains(k * R.THINK_SWEEP_PERIOD_S / 6.0, 10)
        peak_at.append(gains.index(max(gains)))
    assert len(set(peak_at)) >= 4, f"the sweep barely moved: {peak_at}"


def test_sweep_period_completes_one_pass():
    a = R.sweep_gains(1.0, 10)
    b = R.sweep_gains(1.0 + R.THINK_SWEEP_PERIOD_S, 10)
    assert max(abs(x - y) for x, y in zip(a, b, strict=True)) < 1e-9


def test_sweep_phase_wraps_to_unit_range():
    for t in (0.0, 0.4, 1.05, 7.3, 123.456):
        assert 0.0 <= R.sweep_phase(t) < 1.0
    # A degenerate period must not divide by zero — it just stops moving.
    assert R.sweep_phase(3.0, 0.0) == 0.0


def test_sweep_bar_heights_stay_inside_the_pill():
    for phase in (0.0, 0.25, 0.5, 0.75):
        gains = [R.sweep_gain(i, 10, phase) for i in range(10)]
        hs = R.sweep_bar_heights(gains, max_h=40.0, min_h=4.0)
        assert all(4.0 <= h <= 40.0 + 1e-9 for h in hs)


def test_sweep_bars_never_collapse_to_the_resting_height():
    # A row that drops to min_h between passes reads as a dead bar, not a
    # working one — THINK_BASE_V is what keeps the unlit strokes present.
    gains = [0.0] * 10
    hs = R.sweep_bar_heights(gains, max_h=40.0, min_h=4.0)
    assert all(h > 4.0 + 1.0 for h in hs)


def test_sweep_lifts_the_bar_under_the_highlight():
    gains = [R.sweep_gain(i, 10, 0.5) for i in range(10)]
    hs = R.sweep_bar_heights(gains, max_h=40.0, min_h=4.0)
    assert max(hs) > 2.0 * min(hs)


def test_sweep_matches_the_web_surfaces_numbers():
    """The deck's header bar draws the SAME motion from
    ``frontend/src/components/overlay/voiceBars.ts``. If either side's
    constants move, the two bars stop reading as one product — so they are
    pinned here, next to a pointer at the file to change with them."""
    assert R.THINK_SWEEP_WIDTH == 0.16       # SWEEP_WIDTH
    assert R.THINK_SWEEP_PERIOD_S == 1.05    # SWEEP_PERIOD_S.working


def test_render_think_mode_animates_over_time():
    r = R.JarvisBarRenderer()
    for _ in range(60):  # settle pill size
        r.render(0.0, "think", 0.0)
    a = list(r.render(1.0, "think", 0.0).getdata())
    b = list(r.render(1.25, "think", 0.0).getdata())
    assert a != b


def test_render_returns_image_for_every_mode():
    rnd = R.JarvisBarRenderer(accent="#e7c46e")
    for mode in ("idle", "listen", "speak", "think"):
        img = rnd.render(0.1, mode, 0.5)
        assert img.size == (R.WIN_W, R.WIN_H)
        assert img.mode == "RGB"


def _settled(mode, hovered, frames=40, final_t=0.1):
    r = R.JarvisBarRenderer()
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
    rnd = R.JarvisBarRenderer()
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


def test_visual_mode_indicator_only_while_thinking():
    # The sweep (the "indicator") appears ONLY while actively
    # thinking/processing — coarse "think" is the THINKING state AND the
    # silent TTS-synthesis lead-in (the bridge shows "think" for SPEAKING
    # too). That is the only place an animated indicator belongs.
    assert R.visual_mode("think", 5.0, hold_s=0.5) == "think"
    assert R.visual_mode("think", 99.0, hold_s=0.5) == "think"


def test_visual_mode_listening_silence_is_still_bars_not_indicator():
    # After "Hey Jarvis" with no speech yet, Jarvis is WAITING, not thinking —
    # the user explicitly does NOT want the thinking indicator there. Silence
    # in any non-thinking active state shows bars, which render flat/still at
    # level 0.
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
    # Playback over + stale level: a THINKING turn falls back to the orbital
    # core, but a LISTENING turn falls back to still bars (waiting, not
    # thinking).
    assert R.visual_mode("think", 4.0, hold_s=0.5, playback_active=False) == "think"
    assert R.visual_mode("listen", 4.0, hold_s=0.5, playback_active=False) == "speak"


# --- conversation growth: the bar gets ~2x bigger while a session is live ----


def _grow_settle(mode, *, hovered=False, frames=120):
    """Run enough frames that the eased pill size has converged on its target."""
    r = R.JarvisBarRenderer()
    for _ in range(frames):
        r.render(0.0, mode, 0.0, hovered=hovered)
    return r


def test_active_conversation_pill_size_vs_open_pill():
    # During a conversation the pill is 2x the hover-open pill, then trimmed to
    # feel less bulky: width keeps 0.518 of 2x, height keeps 0.56 of 2x (two
    # maintainer calibration rounds 2026-07-21 — slim AND narrow). Centred, so
    # the idle bar stays in the middle.
    active = _grow_settle("speak")
    open_pill = _grow_settle("idle", hovered=True)
    assert active._st.pw == pytest.approx(2 * 0.518 * open_pill._st.pw, rel=0.05)
    assert active._st.ph == pytest.approx(2 * 0.56 * open_pill._st.ph, rel=0.05)


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


# --- Slim-bar refinement: thin strokes + standby dots ---------------------


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
    r = R.JarvisBarRenderer()
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
    # Slim-bar style: the equalizer strokes are thin, not the old chunky ~6px bars.
    assert R.bar_half_w_for(R.ACTIVE_W) <= 2.0


def test_effective_ext_level_passes_fresh_samples_through():
    # A sample younger than the stale window renders as-is: live sound moves
    # the bars with no attenuation.
    assert R.effective_ext_level(0.7, 0.0) == 0.7
    assert R.effective_ext_level(0.7, R.LEVEL_STALE_S) == 0.7


def test_effective_ext_level_decays_a_stopped_feed_to_silence():
    # When a feeder stops without sending zero (bridge state gate, echo
    # suppression, turn commit), the last sample must NOT keep animating the
    # bars — the 2026-07-21 "still shows me speaking for 3-4 s after I
    # stopped" defect. Past the stale window the level reads as dead silence.
    assert R.effective_ext_level(0.7, R.LEVEL_STALE_S + 0.01) == 0.0
    assert R.effective_ext_level(1.0, 5.0) == 0.0


def test_level_stale_window_covers_the_slowest_healthy_feed_cadence():
    # Mic feeds arrive per captured chunk (~30-100 ms), TTS per ~60 ms write
    # block. The stale window must sit clearly above both so live sound can
    # never flicker stale between samples, yet far below one second so a
    # stopped feed collapses promptly.
    assert 0.2 <= R.LEVEL_STALE_S <= 0.6
