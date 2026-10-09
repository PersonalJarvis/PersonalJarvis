"""The "connecting" look: from the wake word until the realtime provider takes the call.

The supervisor has a ``CONNECTING`` state for the realtime handshake, but every
desktop surface used to ignore it and kept showing the listening look while
the provider had not accepted a single frame. These pin the whole chain: the
bridge paints ``connect`` and greets on connecting, the shared strip (Jarvis
Bar and pet) runs a loading loop and then a one-shot "connected" flourish that
settles into the listening look, the pet figure is busy meanwhile, the voice
orb circles a comet, and the connecting call can still be hung up.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

from jarvis.core.events import SystemStateChanged, VoiceSessionStarted
from jarvis.ui.jarvisbar import interaction, modes, renderer
from jarvis.ui.pets.state_machine import PetStateMachine

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

try:  # noqa: SIM105 — intentional try-import for the discovery quirk
    from ui.orb import controls  # type: ignore[import-not-found]
    from ui.orb.bus_bridge import OrbBusBridge  # type: ignore[import-not-found]
    from ui.orb.voice_orb import VoiceOrbRenderer  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip("ui.orb not available on the pytest PYTHONPATH", allow_module_level=True)

KEY = (255, 0, 255)


class _FakeBus:
    def subscribe(self, *_args, **_kwargs) -> None:
        pass


class _FakeOrb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def show(self, mode: str = "listen") -> None:
        self.calls.append(("show", mode))

    def hide(self) -> None:
        self.calls.append(("hide", None))

    def set_level(self, level: float) -> None:
        self.calls.append(("set_level", level))

    def play_animation(self, name: str) -> None:
        self.calls.append(("play_animation", name))

    def stop_animation(self, name: str) -> None:
        self.calls.append(("stop_animation", name))

    def show_listening_transcript(self, text: str = "", duration_ms: int = 30000) -> None:
        self.calls.append(("show_listening_transcript", text))

    def hide_comment(self) -> None:
        self.calls.append(("hide_comment", None))


# -- vocabulary ---------------------------------------------------------------


def test_connect_is_a_call_mode_but_not_a_plain_voice_mode() -> None:
    assert "connect" in modes.MODES
    assert "connect" in modes.ACTIVE_VOICE_MODES
    assert "connect" not in modes.VOICE_MODES
    assert "connect" not in modes.DICTATION_MODES


def test_a_connecting_call_can_be_hung_up_from_the_talk_control_and_the_phone() -> None:
    width = controls.pet_strip_size()[0]
    for action in ("orb", "call"):
        layout = controls.pet_strip_layout()
        if action == "call":
            x = layout.call[0]
        else:
            x = next((x0 + x1) / 2 for name, x0, x1 in layout.slots if name == "orb")
        assert interaction.resolve_click(x, width, "connect") == "hangup"


# -- bridge -------------------------------------------------------------------


async def test_connecting_state_paints_the_loading_loop() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]
    await bridge._on_session_started(VoiceSessionStarted(session_id="s1"))  # noqa: SLF001
    orb.calls.clear()

    await bridge._on_state(SystemStateChanged(new_state="CONNECTING", previous="LISTENING"))  # noqa: SLF001

    assert ("show", "connect") in orb.calls
    assert bridge._current_voice_mode() == "connect"  # noqa: SLF001


async def test_connected_call_returns_to_listening_with_a_greeting() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(bus=_FakeBus(), orb=orb, idle_animations_enabled=False)  # type: ignore[arg-type]
    await bridge._on_session_started(VoiceSessionStarted(session_id="s1"))  # noqa: SLF001
    await bridge._on_state(SystemStateChanged(new_state="CONNECTING", previous="LISTENING"))  # noqa: SLF001
    orb.calls.clear()

    await bridge._on_state(SystemStateChanged(new_state="LISTENING", previous="CONNECTING"))  # noqa: SLF001

    assert ("show", "listen") in orb.calls
    assert ("play_animation", "wave") in orb.calls


async def test_a_failed_handshake_never_leaves_the_loading_loop_behind() -> None:
    orb = _FakeOrb()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(), orb=orb, idle_animations_enabled=False, hide_on_idle=False
    )
    await bridge._on_session_started(VoiceSessionStarted(session_id="s1"))  # noqa: SLF001
    await bridge._on_state(SystemStateChanged(new_state="CONNECTING", previous="LISTENING"))  # noqa: SLF001
    orb.calls.clear()

    await bridge._on_state(SystemStateChanged(new_state="IDLE", previous="CONNECTING"))  # noqa: SLF001

    assert orb.calls[-1] == ("show", "idle") or ("show", "idle") in orb.calls


# -- the shared strip (Jarvis Bar + pet) --------------------------------------


def test_no_audio_signal_can_repaint_the_loading_loop() -> None:
    """The opening words are buffered, not heard: a live level stays a loop."""
    for playback in (False, True):
        for seconds in (0.0, 99.0):
            look = renderer.visual_mode("connect", seconds, hold_s=0.4, playback_active=playback)
            assert look == "connect"


def _arc_spread(stroke: controls.MorphStroke) -> float:
    """Distance from the talk control's centre, averaged over a stroke."""
    return sum((x * x + y * y) ** 0.5 for x, y in stroke.points) / len(stroke.points)


def test_the_strokes_themselves_bend_into_the_loop() -> None:
    """No extra ring: the three strokes become the loading loop."""
    assert controls.morph_strokes(controls.PetStripState(motion="voice")) is None
    first = controls.morph_strokes(controls.PetStripState(motion="connect", phase=0))
    assert first is None  # the very first step is still the plain row
    bent = controls.morph_strokes(
        controls.PetStripState(motion="connect", phase=controls.PET_MORPH_STEPS)
    )
    assert bent is not None and len(bent) == controls.PET_INDICATOR_BARS
    for stroke in bent:
        # Every point of a fully bent stroke lies on the loop's ring.
        for x, y in stroke.points:
            assert (x * x + y * y) ** 0.5 == pytest.approx(controls.PET_LOOP_R, abs=1e-6)
    half = controls.morph_strokes(
        controls.PetStripState(motion="connect", phase=controls.PET_MORPH_STEPS // 2)
    )
    assert half is not None
    assert 0.0 < controls.morph_amount(
        controls.PetStripState(motion="connect", phase=controls.PET_MORPH_STEPS // 2)
    ) < 1.0


def test_the_bent_loop_spins_and_repeats_every_third_of_a_turn() -> None:
    frames = {
        controls.render_pet_strip(
            controls.PetStripState(
                jarvis_bar=True,
                active=True,
                motion="connect",
                phase=controls.PET_MORPH_STEPS,
                spin=step,
            )
        ).tobytes()
        for step in (0, 3, 6, 9)
    }
    assert len(frames) == 4
    period = controls.PET_SPIN_PERIOD_S
    assert controls.spin_step(0.15) == controls.spin_step(0.15 + period)


def test_a_live_voice_lengthens_the_loop_arcs_without_closing_the_ring() -> None:
    quiet = controls.morph_strokes(
        controls.PetStripState(motion="connect", phase=controls.PET_MORPH_STEPS, level=0)
    )
    loud = controls.morph_strokes(
        controls.PetStripState(
            motion="connect", phase=controls.PET_MORPH_STEPS, level=controls.PET_LEVEL_STEPS
        )
    )
    assert quiet is not None and loud is not None

    def span(stroke: controls.MorphStroke) -> float:
        """The arc's angular length in degrees, from its centreline."""
        pts = stroke.points
        length = sum(
            math.dist(pts[k], pts[k + 1]) for k in range(len(pts) - 1)
        )
        return math.degrees(length / controls.PET_LOOP_R)

    assert span(loud[0]) > span(quiet[0])
    assert span(loud[0]) < 115.0  # three arcs still read as a loop with gaps


def test_the_connected_flourish_flashes_green_and_ends_as_the_plain_row() -> None:
    mid = controls.morph_strokes(
        controls.PetStripState(
            motion="connected", phase=controls.PET_CONNECTED_PHASES // 2, spin=5
        )
    )
    assert mid is not None and max(s.green for s in mid) > 0.5
    last = controls.PetStripState(
        motion="connected", phase=controls.PET_CONNECTED_PHASES - 1, spin=5
    )
    # The last step is exactly the resting row, so nothing jumps after it.
    assert controls.morph_strokes(last) is None
    rest = controls.PetStripState(motion="voice", level=0)
    assert controls.indicator_bars(last) == controls.indicator_bars(rest)


def test_the_flourish_unbends_from_where_the_loop_stopped_spinning() -> None:
    """The arcs turn on from their handover angle; they never snap back."""
    for spin in (0, 4, 11):
        state = controls.PetStripState(motion="connected", phase=0, spin=spin)
        before = controls.morph_strokes(
            controls.PetStripState(motion="connect", phase=controls.PET_MORPH_STEPS, spin=spin)
        )
        after = controls.morph_strokes(state)
        assert before is not None and after is not None
        for a, b in zip(before, after, strict=True):
            assert _arc_spread(b) == pytest.approx(_arc_spread(a), abs=0.03)


def test_the_timeline_bends_spins_and_flourishes_on_the_surface_clock() -> None:
    tl = controls.ConnectTimeline()
    assert tl.look("listen", 0.0) is None
    tl.note_mode("listen", "connect", 1.0)
    early = tl.look("connect", 1.05)
    assert early is not None and early.motion == "connect"
    assert early.phase < controls.PET_MORPH_STEPS
    later = tl.look("connect", 2.0)
    assert later is not None and later.phase == controls.PET_MORPH_STEPS
    tl.note_mode("connect", "listen", 2.0)
    flourish = tl.look("listen", 2.1)
    assert flourish is not None and flourish.motion == "connected"
    assert flourish.spin == controls.spin_step(1.0)
    assert tl.look("listen", 2.01 + controls.PET_CONNECTED_S) is None


def test_hanging_up_while_connecting_plays_no_flourish() -> None:
    tl = controls.ConnectTimeline()
    tl.note_mode("listen", "connect", 0.0)
    tl.note_mode("connect", "idle", 0.5)
    assert tl.look("idle", 0.6) is None
    tl.note_mode("idle", "listen", 0.7)
    assert tl.look("listen", 0.8) is None


def test_the_bar_renders_the_flourish_then_the_plain_listening_look() -> None:
    bar = renderer.JarvisBarRenderer()
    tl = controls.ConnectTimeline()
    tl.note_mode("listen", "connect", 0.0)
    tl.note_mode("connect", "listen", 1.0)
    flourish = bar.render(
        0.0, "listen", 0.0, surface_mode="listen", connect_look=tl.look("listen", 1.3)
    )
    plain = bar.render(0.0, "listen", 0.0, surface_mode="listen")
    assert flourish.tobytes() != plain.tobytes()
    late = bar.render(0.0, "listen", 0.0, surface_mode="listen", connect_look=tl.look("listen", 9))
    assert late.tobytes() == plain.tobytes()


# -- pet figure + voice orb ---------------------------------------------------


def test_the_pet_is_busy_while_connecting_and_listens_once_connected() -> None:
    machine = PetStateMachine(clock=lambda: 0.0)
    machine.on_mode("connect")
    assert machine.state() == "thinking"
    machine.on_mode("listen")
    assert machine.state() == "listening"


def _outside_sphere_pixels(frame: np.ndarray, radius_share: float = 0.8) -> int:
    size = frame.shape[0]
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.hypot(xx - size / 2 + 0.5, yy - size / 2 + 0.5) / (size / 2)
    ring = (r > radius_share) & (r < 0.98)
    return int((np.any(frame != KEY, axis=-1) & ring).sum())


def test_the_voice_orb_circles_a_comet_while_connecting() -> None:
    connecting = VoiceOrbRenderer(size=160, color_key=KEY)
    idle = VoiceOrbRenderer(size=160, color_key=KEY)
    for step in range(12):
        a = np.asarray(connecting.render(step * 0.05, "connect", None))
        b = np.asarray(idle.render(step * 0.05, "idle", None))
    assert _outside_sphere_pixels(a) > _outside_sphere_pixels(b) + 40


def test_the_voice_orb_sends_one_ring_out_when_the_call_connects() -> None:
    orb = VoiceOrbRenderer(size=160, color_key=KEY)
    t = 0.0
    for _ in range(10):
        t += 0.05
        orb.render(t, "connect", None)
    t += 0.05
    orb.render(t, "listen", 0.0)
    assert orb._connected_age < controls.PET_CONNECTED_S  # noqa: SLF001
    for _ in range(30):
        t += 0.05
        orb.render(t, "listen", 0.0)
    assert orb._connected_age == float("inf")  # noqa: SLF001
