"""OrbBusBridge.set_surface repoints the bridge at a new overlay surface
(live style swap), and _on_mic_level routes live mic loudness to the active
surface only while LISTENING."""
from __future__ import annotations

from ui.orb.bus_bridge import OrbBusBridge


class FakeBus:
    def subscribe(self, *a, **k):
        return None

    async def publish(self, *a, **k):
        return None


class FakeSurface:
    def __init__(self):
        self.level = None
        self.mute_cb = None
        self.fb_cb = None

    def set_level(self, lv):
        self.level = lv

    def set_on_mute_toggle(self, cb):
        self.mute_cb = cb

    def set_feedback_publisher(self, cb):
        self.fb_cb = cb


def test_set_surface_repoints_and_injects_callbacks():
    old, new = FakeSurface(), FakeSurface()
    bridge = OrbBusBridge(bus=FakeBus(), orb=old)
    bridge.set_surface(new)
    assert bridge._orb is new
    assert new.mute_cb is not None  # mute-gesture publisher injected
    assert new.fb_cb is not None    # visible-feedback publisher injected


def test_mic_level_routes_to_surface_only_during_listening():
    surface = FakeSurface()
    bridge = OrbBusBridge(bus=FakeBus(), orb=surface)

    bridge._last_state = "IDLE"
    bridge._on_mic_level(0.7)
    assert surface.level is None  # not listening → ignored

    bridge._last_state = "LISTENING"
    bridge._on_mic_level(0.7)
    assert surface.level == 0.7  # listening → forwarded to the bars

    bridge._last_state = "SPEAKING"
    bridge._on_mic_level(0.3)
    assert surface.level == 0.7  # speaking uses the TTS level, not the mic


def test_tts_owns_the_bars_and_suppresses_the_silent_mic():
    import time

    surface = FakeSurface()
    bridge = OrbBusBridge(bus=FakeBus(), orb=surface)
    bridge._last_state = "LISTENING"

    # TTS just published an output level → it is making sound → mic suppressed
    # (the state label is LISTENING because continue-listening flips it while
    # the audio is still playing — exactly the bug this guards against).
    bridge._note_tts_level(0.6)
    bridge._on_mic_level(0.0)
    assert surface.level == 0.6  # silent mic must NOT clobber Jarvis's voice

    # Once TTS has been quiet for a while, the mic drives the bars again.
    bridge._last_tts_level_t = time.monotonic() - 1.0
    bridge._on_mic_level(0.3)
    assert surface.level == 0.3


def test_mic_level_follows_surface_after_swap():
    old, new = FakeSurface(), FakeSurface()
    bridge = OrbBusBridge(bus=FakeBus(), orb=old)
    bridge._last_state = "LISTENING"
    bridge.set_surface(new)
    bridge._on_mic_level(0.5)
    assert new.level == 0.5  # the new surface now receives the mic level
    assert old.level is None
