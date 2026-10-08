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


def test_the_loading_loop_turns() -> None:
    frames = {
        controls.render_pet_strip(
            controls.PetStripState(
                jarvis_bar=True,
                active=True,
                motion="connect",
                phase=controls.indicator_phase("connect", t),
            )
        ).tobytes()
        for t in (0.0, 0.3, 0.6, 0.9)
    }
    assert len(frames) == 4
    assert controls.connect_ring(controls.PetStripState(motion="connect")) is not None
    assert controls.connect_ring(controls.PetStripState(motion="voice")) is None


def test_the_loop_is_a_closed_cycle_of_cached_frames() -> None:
    period = controls.PET_CONNECT_PERIOD_S
    assert controls.indicator_phase("connect", 0.1) == controls.indicator_phase(
        "connect", 0.1 + period
    )


def test_the_connected_flourish_settles_into_the_resting_strokes() -> None:
    """Its last step stands where the listening look starts, so nothing jumps."""
    last = controls.PetStripState(motion="connected", phase=controls.PET_CONNECTED_PHASES - 1)
    rest = controls.PetStripState(motion="voice", level=0)
    for (h_last, _g), (h_rest, _r) in zip(
        controls.indicator_bars(last), controls.indicator_bars(rest), strict=True
    ):
        assert h_last == pytest.approx(h_rest, abs=1e-6)
    _head, tail, flare = controls.connect_ring(last)
    assert tail == pytest.approx(360.0)
    assert flare < 0.2


def test_the_flourish_starts_only_when_connect_hands_over_to_the_call() -> None:
    stamp = controls.connected_stamp
    assert stamp("connect", "listen", 5.0, None) == 5.0
    assert stamp("connect", "idle", 5.0, None) is None  # hung up while connecting
    assert stamp("listen", "connect", 5.0, 4.0) is None  # a rebuild reconnects
    assert stamp("listen", "think", 5.0, 4.0) == 4.0  # a running flourish keeps its clock
    assert stamp("idle", "listen", 5.0, None) is None  # no handshake, no flourish
    assert controls.connected_elapsed(4.0, 4.3) == pytest.approx(0.3)
    assert controls.connected_elapsed(4.0, 4.01 + controls.PET_CONNECTED_S) is None


def test_the_bar_renders_the_flourish_then_the_plain_listening_look() -> None:
    bar = renderer.JarvisBarRenderer()
    flourish = bar.render(0.0, "listen", 0.0, surface_mode="listen", connected_elapsed=0.2)
    plain = bar.render(0.0, "listen", 0.0, surface_mode="listen")
    assert flourish.tobytes() != plain.tobytes()
    late = bar.render(
        0.0, "listen", 0.0, surface_mode="listen", connected_elapsed=controls.PET_CONNECTED_S
    )
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


def test_a_live_voice_still_moves_the_strokes_inside_the_loading_loop() -> None:
    quiet = controls.PetStripState(motion="connect", phase=0, level=0)
    loud = controls.PetStripState(motion="connect", phase=0, level=controls.PET_LEVEL_STEPS)
    for (h_quiet, _g), (h_loud, _l) in zip(
        controls.indicator_bars(quiet), controls.indicator_bars(loud), strict=True
    ):
        assert h_loud >= h_quiet
    assert max(h for h, _ in controls.indicator_bars(loud)) == pytest.approx(
        controls.PET_INDICATOR_MAX_H
    )
