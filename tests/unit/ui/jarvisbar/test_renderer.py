"""Unit tests for the jarvis-bar pure renderer math + draw smoke."""

from __future__ import annotations

from jarvis.ui.jarvisbar import renderer as R

# --- thinking: the travelling sweep ------------------------------------------
# The look the mission deck's header bar uses for "working", brought onto the
# desktop bar (maintainer, 2026-08-20). It replaced the "orbital core", which
# replaced a travelling sine wave before that — so the guards below pin what
# each of those was rejected FOR, not just the current shape.


def _settled(mode, hovered, frames=40, final_t=0.1):
    r = R.JarvisBarRenderer()
    for _ in range(frames):
        r.render(0.0, mode, 0.0)  # deterministic settle
    return list(r.render(final_t, mode, 0.0, hovered=hovered).getdata())


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


# --- Slim-bar refinement: thin strokes + standby dots ---------------------


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
