"""The desktop pet's renderer (``ui.orb.pet_renderer``) — pacing and scaling.

The pet's performance budget (``docs/pets.md``) rests on three promises this
module makes to the overlay: every frame is scaled once, ``frame_key`` changes
exactly when the picture does, and ``next_frame_delay_ms`` lets the overlay
sleep until then. These tests pin all three without Tk, with a hand-written
pack, a real :class:`PetStateMachine` and a clock the test moves by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from PIL import Image

from jarvis.ui.pets.state_machine import PetStateMachine
from jarvis.ui.pets.states import PET_STATES, SLEEP_AFTER_SECONDS
from ui.orb import controls as orb_controls
from ui.orb import pet_renderer as pr


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass(frozen=True)
class _Spec:
    row: int
    frames: int
    fps: int
    loop: bool


class _Manifest:
    def __init__(self, frame_size: int, specs: dict[str, _Spec], pet_id: str = "fake") -> None:
        self.frame_size = frame_size
        self.id = pet_id
        self._specs = specs

    def spec_for(self, state: str) -> tuple[str, _Spec]:
        if state in self._specs:
            return state, self._specs[state]
        return "idle", self._specs["idle"]


class _Pack:
    def __init__(self, frame_size: int = 48, pet_id: str = "fake") -> None:
        specs = {
            "idle": _Spec(0, 4, 4, True),
            "listening": _Spec(1, 4, 8, True),
            "talking": _Spec(3, 4, 10, True),
            "success": _Spec(4, 3, 10, False),
            "sleeping": _Spec(6, 2, 2, True),
        }
        self.manifest = _Manifest(frame_size, specs, pet_id)
        self.frames: dict[str, tuple[Image.Image, ...]] = {}
        for state in PET_STATES:
            resolved, spec = self.manifest.spec_for(state)
            self.frames[state] = tuple(
                Image.new("RGBA", (frame_size, frame_size), (index * 40, row, 0, 255))
                for index, row in [(i, spec.row) for i in range(spec.frames)]
            )


class _Loader:
    def __init__(self, packs: dict[str, _Pack | None]) -> None:
        self.packs = packs
        self.calls: list[str] = []

    def __call__(self, pet_id: str) -> _Pack | None:
        self.calls.append(pet_id)
        return self.packs.get(pet_id, self.packs.get("fake"))


def _scale_calls() -> tuple[list[int], object]:
    calls: list[int] = []

    def _to_key(frame: Image.Image, scale: int, key: tuple[int, int, int]) -> Image.Image:
        calls.append(scale)
        return frame.convert("RGB").resize((frame.width * scale, frame.height * scale))

    return calls, _to_key


def _renderer(clock: _Clock, **kwargs) -> tuple[pr.PetRenderer, list[int]]:
    calls, to_key = _scale_calls()
    loader = kwargs.pop("loader", _Loader({"fake": _Pack()}))
    renderer = pr.PetRenderer(
        kwargs.pop("pet_id", "fake"),
        clock=clock,
        loader=loader,
        to_color_key=to_key,
        machine=PetStateMachine(clock),
        **kwargs,
    )
    return renderer, calls


# --- pure helpers -------------------------------------------------------------


def test_a_48_pixel_sheet_is_drawn_at_exactly_3x_at_100_percent() -> None:
    assert pr.pixel_factor(48, 1.0, 1.0) == 3


def test_other_sheet_sizes_land_near_the_same_on_screen_size() -> None:
    assert pr.pixel_factor(32, 1.0, 1.0) * 32 == pytest.approx(pr.PET_TARGET_EDGE_PX, abs=16)
    assert pr.pixel_factor(64, 1.0, 1.0) * 64 == pytest.approx(pr.PET_TARGET_EDGE_PX, abs=16)


def test_the_factor_follows_dpi_and_user_size_and_rounds_half_up() -> None:
    assert pr.pixel_factor(48, 1.5, 1.0) == 5  # 4.5 -> 5, never banker's 4
    assert pr.pixel_factor(48, 2.0, 1.0) == 6
    assert pr.pixel_factor(48, 1.0, 2.0) == 6
    assert pr.pixel_factor(48, 1.0, 0.5) == 2


def test_the_factor_never_drops_below_one_and_survives_garbage() -> None:
    assert pr.pixel_factor(64, 0.1, 0.1) == 1
    assert pr.pixel_factor(48, float("nan"), 1.0) == 3
    assert pr.pixel_factor("x", 1.0, 1.0) == 1  # type: ignore[arg-type]


def test_dpi_ratio_reads_logical_dpi_except_on_macos() -> None:
    assert pr.dpi_ratio_for(96.0, "win32") == 1.0
    assert pr.dpi_ratio_for(144.0, "linux") == 1.5
    assert pr.dpi_ratio_for(144.0, "darwin") == 1.0  # Aqua already measures in points
    assert pr.dpi_ratio_for(None, "win32") == 1.0
    assert pr.dpi_ratio_for(float("inf"), "win32") == 1.0
    assert pr.dpi_ratio_for(-3.0, "win32") == 1.0


def test_frame_index_loops_or_rests_on_the_last_frame() -> None:
    assert pr.frame_index(0.0, 4, 4, True) == 0
    assert pr.frame_index(0.26, 4, 4, True) == 1
    assert pr.frame_index(1.01, 4, 4, True) == 0  # wrapped
    assert pr.frame_index(5.0, 3, 10, False) == 2  # a one-shot rests at its end
    assert pr.frame_index(9.0, 1, 4, True) == 0


def test_seconds_to_next_frame_is_the_boundary_or_never() -> None:
    assert pr.seconds_to_next_frame(0.1, 4, 4, True) == pytest.approx(0.15)
    assert pr.seconds_to_next_frame(0.0, 1, 8, True) is None  # one frame never changes
    assert pr.seconds_to_next_frame(0.5, 3, 10, False) is None  # one-shot finished
    assert pr.seconds_to_next_frame(0.05, 3, 10, False) == pytest.approx(0.05)


# --- scaling happens once ------------------------------------------------------


def test_every_frame_is_scaled_once_at_load_and_never_while_painting() -> None:
    clock = _Clock()
    renderer, calls = _renderer(clock)
    scaled_at_load = len(calls)
    assert scaled_at_load == sum(len(v) for v in _Pack().frames.values())
    assert set(calls) == {3}
    for _ in range(50):
        clock.now += 0.07
        renderer.render()
        renderer.frame_key()
        renderer.next_frame_delay_ms()
    assert len(calls) == scaled_at_load
    assert renderer.size == (144, 144)
    assert renderer.render().size == (144, 144)


def test_a_new_size_rescales_and_reports_the_change() -> None:
    clock = _Clock()
    renderer, calls = _renderer(clock)
    calls.clear()
    assert renderer.set_look(pet_scale=2.0) is True
    assert renderer.size == (288, 288)
    assert set(calls) == {6}
    assert renderer.set_look(pet_scale=2.0) is False  # same size, no window change


# --- frame keys and pacing ------------------------------------------------------


def test_the_key_changes_only_at_frame_boundaries_while_idle() -> None:
    clock = _Clock()
    renderer, _ = _renderer(clock)
    first = renderer.frame_key()
    clock.now += 0.2  # idle is 4 fps: still frame 0
    assert renderer.frame_key() == first
    clock.now += 0.06
    assert renderer.frame_key() != first


def test_idle_sleeps_until_the_next_quarter_second() -> None:
    clock = _Clock()
    renderer, _ = _renderer(clock)
    delay = renderer.next_frame_delay_ms()
    assert 250 <= delay <= 255
    clock.now += 0.1
    assert 150 <= renderer.next_frame_delay_ms() <= 155


def test_active_states_tick_at_the_manifest_rate() -> None:
    clock = _Clock()
    renderer, _ = _renderer(clock)
    renderer.on_mode("listen")
    assert renderer.state() == "listening"
    assert 125 <= renderer.next_frame_delay_ms() <= 130  # 8 fps
    renderer.on_mode("speak")
    assert 100 <= renderer.next_frame_delay_ms() <= 105  # 10 fps


def test_asleep_the_pet_ticks_at_two_frames_a_second() -> None:
    clock = _Clock()
    renderer, _ = _renderer(clock)
    clock.now += SLEEP_AFTER_SECONDS + 0.01
    assert renderer.state() == "sleeping"
    delay = renderer.next_frame_delay_ms()
    assert 490 <= delay <= 505


def test_a_one_shot_wakes_the_loop_when_it_ends() -> None:
    clock = _Clock()
    renderer, _ = _renderer(clock)
    renderer.on_outcome("success")
    clock.now += 0.25  # the 3-frame one-shot rests on its last frame at 10 fps
    # No frame boundary is left; the next change is the one-shot ending in
    # 1.25 s, beyond the ceiling — so the loop sleeps the full second.
    assert renderer.next_frame_delay_ms() == pr.MAX_FRAME_DELAY_MS
    clock.now += 1.0
    assert 250 <= renderer.next_frame_delay_ms() <= 255  # wakes exactly at the end
    clock.now += 0.26
    assert renderer.state() == "idle"


def test_the_delay_is_clamped_to_a_sane_band() -> None:
    clock = _Clock()
    renderer, _ = _renderer(clock)
    for _ in range(200):
        clock.now += 0.013
        delay = renderer.next_frame_delay_ms()
        assert pr.MIN_FRAME_DELAY_MS <= delay <= pr.MAX_FRAME_DELAY_MS


# --- the pet "none" and unknown ids ------------------------------------------------


def test_no_pet_paints_an_invisible_strip_wide_line() -> None:
    clock = _Clock()
    loader = _Loader({"fake": _Pack()})
    renderer, calls = _renderer(clock, pet_id="none", loader=loader)
    assert loader.calls == []  # "none" never touches the disk
    assert calls == []
    assert renderer.has_figure is False
    width, height = renderer.size
    assert (width, height) == (orb_controls.pet_strip_size(1.0)[0], 1)
    frame = renderer.render()
    assert frame.getcolors() == [(width * height, (255, 0, 255))]
    # A figure-less pet has nothing to animate: it idles on the slow ceiling.
    assert renderer.next_frame_delay_ms() <= pr.MAX_FRAME_DELAY_MS


def test_the_reported_id_is_the_pack_that_actually_loaded() -> None:
    clock = _Clock()
    loader = _Loader({"fake": _Pack(pet_id="gigi")})
    renderer, _ = _renderer(clock, pet_id="u0123456789abcdef", loader=loader)
    assert renderer.pet_id == "gigi"


def test_a_crashing_loader_degrades_to_the_strip_only() -> None:
    def _broken(_pet_id: str) -> None:
        raise OSError("disk gone")

    renderer, _ = _renderer(_Clock(), loader=_broken)
    assert renderer.has_figure is False
    assert renderer.pet_id == "none"


def test_switching_pets_keeps_the_state_machine() -> None:
    clock = _Clock()
    loader = _Loader({"fake": _Pack(), "miso": _Pack(frame_size=32, pet_id="miso")})
    renderer, _ = _renderer(clock, loader=loader)
    renderer.on_mode("listen")
    renderer.load("miso")
    assert renderer.state() == "listening"
    assert renderer.size == (160, 160)  # 32 px at the nearest factor (5x)
    assert renderer.frame_key()[0] == "miso"


# --- the real engine, end to end ---------------------------------------------------


def test_the_built_in_default_pet_loads_through_the_real_engine() -> None:
    """Drift guard: the renderer's assumptions about the engine's API hold."""
    clock = _Clock()
    renderer = pr.PetRenderer("gigi", clock=clock, dpi_ratio=1.0)
    assert renderer.has_figure
    edge = renderer.size[0]
    assert renderer.size == (edge, edge)
    assert renderer.render().mode == "RGB"
    for mode in ("idle", "listen", "think", "speak", "notice"):
        renderer.on_mode(mode)
        assert renderer.state() in PET_STATES
        assert renderer.render().size == renderer.size
        assert pr.MIN_FRAME_DELAY_MS <= renderer.next_frame_delay_ms() <= pr.MAX_FRAME_DELAY_MS
