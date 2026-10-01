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


@pytest.mark.parametrize("extent", [24, 30, 36, 40, 48, 64])
def test_every_figure_lands_near_the_target_size_at_100_percent(extent: int) -> None:
    on_screen = pr.pixel_factor(extent, 1.0, 1.0) * extent
    assert on_screen == pytest.approx(pr.PET_TARGET_FIGURE_PX, abs=extent / 2)


def test_the_factor_follows_dpi_and_user_size_and_rounds_half_up() -> None:
    assert pr.pixel_factor(40, 1.0, 1.0) == 5  # 4.5 -> 5, never banker's 4
    assert pr.pixel_factor(40, 1.5, 1.0) == 7
    assert pr.pixel_factor(40, 2.0, 1.0) == 9
    assert pr.pixel_factor(40, 1.0, 2.0) == 9
    assert pr.pixel_factor(40, 1.0, 0.5) == 2


def test_the_factor_never_drops_below_one_and_survives_garbage() -> None:
    assert pr.pixel_factor(64, 0.1, 0.1) == 1
    assert pr.pixel_factor(48, float("nan"), 1.0) == 4
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
    assert set(calls) == {4}
    for _ in range(50):
        clock.now += 0.07
        renderer.render()
        renderer.frame_key()
        renderer.next_frame_delay_ms()
    assert len(calls) == scaled_at_load
    # The fake frames fill their whole cell: no crop, 48 px at 4x.
    assert renderer.size == (192, 192)
    assert renderer.render().size == (192, 192)


def test_a_new_size_rescales_and_reports_the_change() -> None:
    clock = _Clock()
    renderer, calls = _renderer(clock)
    calls.clear()
    assert renderer.set_look(pet_scale=2.0) is True
    assert renderer.size == (384, 384)
    assert set(calls) == {8}
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
    assert renderer.size == (192, 192)  # 32 px at the nearest factor (6x)
    assert renderer.frame_key()[0] == "miso"


# --- the real engine, end to end ---------------------------------------------------


def test_the_built_in_default_pet_loads_through_the_real_engine() -> None:
    """Drift guard: the renderer's assumptions about the engine's API hold."""
    clock = _Clock()
    renderer = pr.PetRenderer("gigi", clock=clock, dpi_ratio=1.0)
    assert renderer.has_figure
    # Cropped to the figure, and the figure is about the target size.
    target = pr.PET_TARGET_FIGURE_PX
    assert 0.6 * target <= renderer.figure_width <= 1.25 * target
    assert renderer.size[0] >= renderer.figure_width
    assert renderer.render().mode == "RGB"
    for mode in ("idle", "listen", "think", "speak", "notice"):
        renderer.on_mode(mode)
        assert renderer.state() in PET_STATES
        assert renderer.render().size == renderer.size
        assert pr.MIN_FRAME_DELAY_MS <= renderer.next_frame_delay_ms() <= pr.MAX_FRAME_DELAY_MS


# --- memory and failure (review fixes) ------------------------------------------


class _SharedRowPack(_Pack):
    """Like the real loader: fallback states share their source row's tuple."""

    def __init__(self) -> None:
        super().__init__()
        shared = self.frames["idle"]
        for state in ("thinking", "error", "sleeping"):
            self.frames[state] = shared


def test_states_that_share_a_row_are_scaled_once() -> None:
    pack = _SharedRowPack()
    distinct = {id(sequence): sequence for sequence in pack.frames.values()}
    renderer, calls = _renderer(_Clock(), loader=_Loader({"fake": pack}))
    assert len(calls) == sum(len(sequence) for sequence in distinct.values())
    frames = renderer._frames  # noqa: SLF001 — the scaled cache under test
    assert frames["sleeping"] is frames["idle"]


def test_a_frame_that_will_not_scale_leaves_the_strip_only() -> None:
    clock = _Clock()

    def _broken(frame: Image.Image, scale: int, key: tuple[int, int, int]) -> Image.Image:
        raise ValueError("bad frame")

    renderer = pr.PetRenderer(
        "fake",
        clock=clock,
        loader=_Loader({"fake": _Pack()}),
        to_color_key=_broken,
        machine=PetStateMachine(clock),
    )
    assert renderer.has_figure is False
    assert renderer.pet_id == "none"
    assert renderer._frames == {}  # noqa: SLF001 — nothing half-filled is kept
    assert renderer.size[1] == 1
    assert renderer.render().size == renderer.size


# --- the crop, the blink accent and the voice-driven mouth ---------------------------


def _boxed(frame_size: int, box: tuple[int, int, int, int]) -> Image.Image:
    frame = Image.new("RGBA", (frame_size, frame_size), (0, 0, 0, 0))
    frame.paste((200, 100, 50, 255), box)
    return frame


class _BoxedPack(_Pack):
    """Frames with a small opaque figure; the success row adds a far sparkle."""

    def __init__(self) -> None:
        super().__init__()
        for state in self.frames:
            self.frames[state] = tuple(_boxed(48, (14, 10, 34, 40)) for _ in range(2))
        sparkle = _boxed(48, (14, 10, 34, 40))
        sparkle.paste((255, 220, 0, 255), (40, 4, 42, 6))
        self.frames["success"] = (sparkle,)


def test_the_crop_is_symmetric_and_covers_every_effect() -> None:
    pack = _BoxedPack()
    crop, idle = pr.figure_crop(pack.frames, 48)
    assert idle == (20, 30)  # the body alone sets the size
    x0, y0, x1, y1 = crop
    assert 24 - x0 == x1 - 24  # centred on the cell's middle line
    assert x1 >= 42 and y0 <= 4  # the sparkle stays inside the window
    assert y1 == 40  # the window ends at the lowest painted row


def test_the_window_is_the_crop_at_the_figure_factor() -> None:
    clock = _Clock()
    pack = _BoxedPack()
    renderer, calls = _renderer(clock, loader=_Loader({"fake": pack}))
    crop, (idle_w, idle_h) = pr.figure_crop(pack.frames, 48)
    factor = pr.pixel_factor(max(idle_w, idle_h), 1.0, 1.0)
    assert set(calls) == {factor}
    assert renderer.size == ((crop[2] - crop[0]) * factor, (crop[3] - crop[1]) * factor)
    assert renderer.figure_width == idle_w * factor
    assert renderer.render().size == renderer.size


def test_an_accent_plays_once_every_few_loops() -> None:
    # Seven breathing frames, then the blink, once every third pass.
    seen = [
        pr.frame_index(step / 6.0 + 1e-6, 8, 6, True, accent_frames=1, accent_every=3)
        for step in range(23)
    ]
    assert seen[:21] == list(range(7)) * 3
    assert seen[21] == 7
    assert seen[22] == 0
    # Without an accent every cell plays on every loop.
    assert pr.frame_index(7 / 6.0 + 1e-6, 8, 6, True) == 7


def test_the_mouth_follows_the_level_and_swings_without_one() -> None:
    swing = [pr.ping_pong_index(step / 10.0 + 1e-6, 4, 10) for step in range(8)]
    assert swing == [0, 1, 2, 3, 2, 1, 0, 1]
    assert pr.level_frame(0.0, 4) == 0
    assert pr.level_frame(pr.LEVEL_SILENCE / 2, 4) == 0
    assert pr.level_frame(pr.LEVEL_SILENCE + 0.01, 4) == 1
    assert pr.level_frame(1.0, 4) == 3


def test_a_live_level_drives_the_talking_frame() -> None:
    clock = _Clock()
    renderer, _ = _renderer(clock)
    renderer.on_mode("speak")
    renderer.feed_level(0.9, clock.now)
    key = renderer.frame_key()
    assert key[3] == 3  # loud: the widest mouth
    assert renderer.next_frame_delay_ms() == pr.LEVEL_POLL_MS + 2
    # The same smoothed level gives the same key: no repaint.
    assert renderer.frame_key() == key
    # Quiet: the mouth closes once the smoothing catches up.
    for _ in range(8):
        clock.now += 0.05
        renderer.feed_level(0.0, clock.now)
    assert renderer.frame_key()[3] == 0
    # The voice stopped arriving: back to the swing at the manifest rate.
    clock.now += pr.LEVEL_FRESH_S + 0.05
    assert renderer.next_frame_delay_ms() <= 105


def test_garbage_levels_are_ignored() -> None:
    clock = _Clock()
    renderer, _ = _renderer(clock)
    renderer.on_mode("speak")
    renderer.feed_level(None, clock.now)
    renderer.feed_level(float("nan"), clock.now)
    renderer.feed_level("loud", clock.now)  # type: ignore[arg-type]
    assert renderer.frame_key()[3] in range(4)


# --- idle acts ----------------------------------------------------------------


class _ActManifest(_Manifest):
    def __init__(self) -> None:
        super().__init__(48, _Pack().manifest._specs)
        self.acts = {"wave": _Spec(0, 3, 10, False), "hop": _Spec(1, 2, 10, False)}


class _ActPack(_Pack):
    def __init__(self) -> None:
        super().__init__()
        self.manifest = _ActManifest()
        self.acts = {
            name: tuple(
                Image.new("RGBA", (48, 48), (200, 40 * i, spec.row, 255))
                for i in range(spec.frames)
            )
            for name, spec in self.manifest.acts.items()
        }


class _Rng:
    """Picks the low end of every range and the first allowed act."""

    def uniform(self, low: float, high: float) -> float:
        return low

    def choice(self, options: list[str]) -> str:
        return options[0]


def _act_renderer(clock: _Clock) -> pr.PetRenderer:
    renderer, _ = _renderer(clock, loader=_Loader({"fake": _ActPack()}), rng=_Rng())
    return renderer


def test_an_idle_pet_plays_an_act_after_a_while_and_returns_to_idle() -> None:
    clock = _Clock()
    renderer = _act_renderer(clock)
    first_delay, gap = pr.ACT_FIRST_DELAY_S[0], pr.ACT_GAP_S[0]
    assert renderer.frame_key()[2] == "idle"
    clock.now += first_delay - 0.01
    assert renderer.frame_key()[2] == "idle"
    clock.now += 0.02
    assert renderer.frame_key()[2:4] == ("act:wave", 0)
    clock.now += 0.11
    assert renderer.frame_key()[2:4] == ("act:wave", 1)
    clock.now += 0.25  # past the act's 0.3 s
    assert renderer.frame_key()[2] == "idle"
    clock.now += gap + 0.01
    # Never the same act twice in a row.
    assert renderer.frame_key()[2:4] == ("act:hop", 0)


def test_any_other_state_cancels_the_act() -> None:
    clock = _Clock()
    renderer = _act_renderer(clock)
    clock.now += pr.ACT_FIRST_DELAY_S[0] + 0.01
    assert renderer.frame_key()[2] == "act:wave"
    renderer.on_mode("listen")
    assert renderer.frame_key()[2] == "listening"


def test_the_overlay_sleeps_until_the_act_is_due_and_through_its_end() -> None:
    clock = _Clock()
    renderer = _act_renderer(clock)
    clock.now += pr.ACT_FIRST_DELAY_S[0] - 0.1
    assert renderer.next_frame_delay_ms() <= 105  # the act is due in 0.1 s
    clock.now += 0.1
    renderer.frame_key()
    clock.now += 0.25  # on the act's last frame
    assert renderer.next_frame_delay_ms() <= 52  # until the act ends, not "never"


def test_act_frames_are_scaled_with_the_states() -> None:
    clock = _Clock()
    renderer = _act_renderer(clock)
    clock.now += pr.ACT_FIRST_DELAY_S[0] + 0.01
    frame = renderer.render()
    assert frame.size == renderer.size
    assert frame.getpixel((0, 0))[0] == 200


def test_a_pet_without_acts_just_idles() -> None:
    clock = _Clock()
    renderer, _ = _renderer(clock)
    clock.now += 120.0
    assert renderer.frame_key()[2] == "idle"


# --- on the phone ------------------------------------------------------------


def test_a_voice_call_shows_the_pets_phone_rows_and_hanging_up_puts_them_away() -> None:
    renderer = pr.PetRenderer("gigi")
    renderer.on_mode("speak")
    renderer.on_mode("listen")
    assert renderer.on_call
    assert renderer.frame_key()[2] == "phone:listening"
    on_call = renderer.render()
    renderer.on_mode("idle")
    assert not renderer.on_call
    assert not str(renderer.frame_key()[2]).startswith("phone:")
    assert renderer.render() is not on_call


def test_a_pet_without_phone_rows_keeps_its_plain_rows_in_a_call() -> None:
    renderer = pr.PetRenderer("fake", loader=_Loader({"fake": _Pack()}))
    renderer.on_mode("listen")
    assert renderer.on_call
    assert renderer.frame_key()[2] == "listening"
