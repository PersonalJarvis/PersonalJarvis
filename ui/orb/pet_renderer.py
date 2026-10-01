"""The desktop pet's renderer for ``OrbOverlay`` (style ``pet``, ``docs/pets.md``).

The mascot and the voice orb are drawn fresh every frame at 60 fps; a pixel-art
pet does not need that and must not pay for it (the budget in ``docs/pets.md``:
an idle pet costs next to nothing). So this renderer works the other way round:

* every frame of every state is cropped to the figure and scaled ONCE, when
  the pet is loaded — an integer nearest-neighbour factor chosen so the visible
  figure is about :data:`PET_TARGET_FIGURE_PX` logical pixels at any DPI, times
  the user's ``pet_scale``, keyed to the overlay's magenta colour key;
* ``frame_key`` names the frame that is due now, so the overlay repaints only
  when that key changes and reuses its cached Tk image otherwise;
* ``next_frame_delay_ms`` says how long the overlay may sleep until the next
  frame boundary or the next state change the state machine will make on its
  own (a one-shot ending, the pet falling asleep).

Idle acts: while the pet idles, it now and then plays one of its acts (a yawn,
a stretch, a puff of fire) once and goes back to idling — after a random pause
of :data:`ACT_FIRST_DELAY_S` once it starts idling, then every
:data:`ACT_GAP_S`, never the same act twice in a row. Any other state cancels
a running act; the overlay's frame timer covers the wait, so nothing extra runs.

Talking follows the voice: the talking row is drawn closed → widest, and while
a live output level arrives the frame is picked from that level (smoothed,
bucketed — an unchanged level gives an unchanged key, so no repaint). Without a
level the row plays back and forth at the manifest's rate.

The state itself comes from :class:`jarvis.ui.pets.state_machine.PetStateMachine`,
fed by the overlay from real Jarvis events. Pet ``"none"`` loads no pack: the
renderer then paints a fully keyed (invisible, click-through) strip-wide line so
the overlay window keeps a real position for the control strip to hang from.
"""

from __future__ import annotations

import logging
import math
import random
import sys
import time
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import Any

from PIL import Image

from jarvis.ui.pets.states import NO_PET_ID
from ui.orb import controls as orb_controls

log = logging.getLogger("jarvis.orb")

#: The visible figure's larger side at 100 % display scaling and ``pet_scale``
#: 1.0, in logical pixels — a desk companion's size, big enough that the
#: control strip under it (``controls.PET_SLOT``) reads as its own.
PET_TARGET_FIGURE_PX = 180

#: Nominal window edge before a pack is loaded (the real size replaces it).
PET_TARGET_EDGE_PX = PET_TARGET_FIGURE_PX

#: Logical pixels per inch Tk reports at 100 % scaling on Windows and X11.
_BASE_PPI = 96.0

#: Bounds for the frame timer. The floor keeps a broken manifest from spinning
#: the Tk loop; the ceiling makes sure a state change the overlay did not kick
#: (it always does, this is the backstop) is picked up within a second.
MIN_FRAME_DELAY_MS = 15
MAX_FRAME_DELAY_MS = 1000

#: A level older than this no longer drives the mouth (the voice stopped).
LEVEL_FRESH_S = 0.25
#: How often the overlay looks at the level while it drives the mouth.
LEVEL_POLL_MS = 60
#: Exponential smoothing per poll: enough to stop a single loud sample from
#: flicking the jaw, little enough to follow syllables.
LEVEL_SMOOTHING = 0.55
#: Below this the mouth is closed; above, the open frames share the range up
#: to ``LEVEL_OPEN_SPAN`` above it.
LEVEL_SILENCE = 0.04
LEVEL_OPEN_SPAN = 0.45

#: Source pixels kept around the cropped figure (sparkles may touch the edge).
_CROP_MARGIN = 1

#: Seconds of idling before the first idle act, as a random ``(low, high)`` range.
#: Long on purpose: a pet that is mostly still feels calm, not hyperactive.
ACT_FIRST_DELAY_S = (40.0, 100.0)
#: Seconds between the end of one idle act and the next, as a random range.
ACT_GAP_S = (90.0, 240.0)
#: Frame-key prefix of an idle act (an act and a state can share a name).
_ACT_KEY = "act:"

_ColorKey = tuple[int, int, int]


def dpi_ratio_for(points_per_inch: float | None, platform: str = sys.platform) -> float:
    """The display scale factor from what Tk reports as one inch, in pixels.

    Windows and X11 report logical DPI (96 at 100 %, 144 at 150 %). Aqua-Tk on
    macOS measures in points, which the system already scales for Retina, so
    the ratio there is 1.0 whatever the number says. Anything implausible
    degrades to 1.0 — the pet is then simply drawn at its base size.
    """
    if platform == "darwin" or points_per_inch is None:
        return 1.0
    try:
        ppi = float(points_per_inch)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(ppi) or ppi <= 0:
        return 1.0
    return max(0.75, min(4.0, ppi / _BASE_PPI))


def pixel_factor(extent: int, dpi_ratio: float = 1.0, pet_scale: float = 1.0) -> int:
    """The integer nearest-neighbour factor for a figure ``extent`` source pixels big.

    ``extent`` is the visible figure's larger side (or a whole cell when the
    figure is unknown). Integer on purpose: a fractional factor duplicates some
    source pixels and not others, and pixel art then looks smeared. Rounded
    half up (Python's ``round`` would send 4.5 to 4) and never below 1.
    """
    try:
        edge = max(1, int(extent))
        ratio = float(dpi_ratio) if math.isfinite(float(dpi_ratio)) else 1.0
        scale = float(pet_scale) if math.isfinite(float(pet_scale)) else 1.0
    except (TypeError, ValueError):
        return 1
    target = PET_TARGET_FIGURE_PX * max(0.1, ratio) * max(0.1, scale)
    return max(1, int(math.floor(target / edge + 0.5)))


def frame_index(
    elapsed_s: float,
    frames: int,
    fps: float,
    loop: bool,
    *,
    accent_frames: int = 0,
    accent_every: int = 1,
) -> int:
    """Which frame of an animation is due ``elapsed_s`` after it started.

    With an accent (``accent_frames`` > 0, looping rows only) the last
    ``accent_frames`` cells play once every ``accent_every`` passes over the
    others — a blink every few seconds instead of on every loop.
    """
    count = max(1, int(frames))
    if count == 1 or fps <= 0:
        return 0
    step = int(math.floor(max(0.0, elapsed_s) * float(fps)))
    if not loop:
        return min(step, count - 1)
    accent = max(0, min(count - 1, int(accent_frames)))
    if accent == 0:
        return step % count
    base = count - accent
    every = max(1, int(accent_every))
    cycle = base * every + accent
    position = step % cycle
    if position < base * every:
        return position % base
    return base + (position - base * every)


def seconds_to_next_frame(elapsed_s: float, frames: int, fps: float, loop: bool) -> float | None:
    """Seconds until the frame index changes, or ``None`` if it never will.

    ``None`` covers a single-frame animation and a one-shot resting on its
    last frame — nothing on screen changes until the state does.
    """
    count = max(1, int(frames))
    if count == 1 or fps <= 0:
        return None
    elapsed = max(0.0, elapsed_s)
    step = int(math.floor(elapsed * float(fps)))
    if not loop and step >= count - 1:
        return None
    boundary = (step + 1) / float(fps)
    return max(0.0, boundary - elapsed)


def ping_pong_index(elapsed_s: float, frames: int, fps: float) -> int:
    """Back and forth over a row (0, 1, …, n-1, …, 1): a mouth that opens and closes."""
    count = max(1, int(frames))
    if count == 1 or fps <= 0:
        return 0
    period = 2 * (count - 1)
    step = int(math.floor(max(0.0, elapsed_s) * float(fps))) % period
    return step if step < count else period - step


def level_frame(level: float, frames: int) -> int:
    """The talking frame for a smoothed 0..1 output level (row is closed → widest)."""
    count = max(1, int(frames))
    if count == 1 or level < LEVEL_SILENCE:
        return 0
    span = max(1e-6, LEVEL_OPEN_SPAN)
    index = 1 + int(math.floor((level - LEVEL_SILENCE) / span * (count - 1)))
    return max(1, min(count - 1, index))


def _alpha_box(frame: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of the opaque pixels of one RGBA frame (``None`` when empty)."""
    if frame.mode == "RGBA":
        return frame.getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox()
    return frame.getbbox()


def _union(boxes: Sequence[tuple[int, int, int, int] | None]) -> tuple[int, int, int, int] | None:
    real = [box for box in boxes if box is not None]
    if not real:
        return None
    return (
        min(b[0] for b in real),
        min(b[1] for b in real),
        max(b[2] for b in real),
        max(b[3] for b in real),
    )


def figure_crop(
    frames: Mapping[str, Sequence[Image.Image]], cell: int
) -> tuple[tuple[int, int, int, int], tuple[int, int]]:
    """The crop every frame shares, and the idle figure's visible ``(w, h)``.

    The crop covers every pixel any frame of any state paints (effects
    included), symmetric around the cell's vertical centre line so the figure
    stays centred over the control strip; its bottom is the lowest painted
    row, so the strip hangs right under the feet. The idle size is what the
    on-screen size is chosen by — sparkles and sound arcs must not shrink the
    pet.
    """
    every = _union([_alpha_box(frame) for sequence in frames.values() for frame in sequence])
    idle_frames = frames.get("idle") or next(iter(frames.values()), ())
    idle = _union([_alpha_box(frame) for frame in idle_frames]) or every
    if every is None:
        return (0, 0, cell, cell), (cell, cell)
    centre = cell / 2.0
    half = max(centre - every[0], every[2] - centre) + _CROP_MARGIN
    x0 = max(0, int(math.floor(centre - half)))
    x1 = min(cell, int(math.ceil(centre + half)))
    y0 = max(0, every[1] - _CROP_MARGIN)
    y1 = min(cell, every[3])
    assert idle is not None  # noqa: S101 — ``every`` is not None, so neither is ``idle``
    return (x0, y0, x1, y1), (idle[2] - idle[0], idle[3] - idle[1])


def _default_loader(pet_id: str) -> Any:
    from jarvis.ui.pets.loader import load_pet_by_id  # noqa: PLC0415 — engine is lazy

    return load_pet_by_id(pet_id)


def _default_to_color_key(frame: Image.Image, scale: int, key: _ColorKey) -> Image.Image:
    from jarvis.ui.pets.loader import to_color_key  # noqa: PLC0415

    return to_color_key(frame, scale, key=key)


def _default_machine(clock: Callable[[], float]) -> Any:
    from jarvis.ui.pets.state_machine import PetStateMachine  # noqa: PLC0415

    return PetStateMachine(clock)


class PetRenderer:
    """Renderer protocol for ``OrbOverlay`` plus frame-pacing hints.

    ``render(t, mode, level)`` matches the mascot / voice-orb renderers; ``t``
    and ``level`` are accepted for that protocol and ignored — the pet's clock
    is its state machine's, and the output level reaches it through
    :meth:`feed_level`. All methods run on the Tk thread.
    """

    def __init__(
        self,
        pet_id: str,
        *,
        pet_scale: float = 1.0,
        dpi_ratio: float = 1.0,
        color_key: _ColorKey = (255, 0, 255),
        clock: Callable[[], float] = time.monotonic,
        loader: Callable[[str], Any] | None = None,
        to_color_key: Callable[[Image.Image, int, _ColorKey], Image.Image] | None = None,
        machine: Any | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._clock = clock
        self._rng = rng or random.Random()  # noqa: S311 — a pet's whim, not crypto
        #: The idle stretch the act schedule belongs to (its start time).
        self._idle_epoch: float | None = None
        #: The running act ``(name, started_at)``, and when the next one is due.
        self._act: tuple[str, float] | None = None
        self._act_due = math.inf
        self._last_act: str | None = None
        self._color_key = tuple(int(c) for c in color_key)
        self._dpi_ratio = float(dpi_ratio)
        self._pet_scale = float(pet_scale)
        self._loader = loader or _default_loader
        self._to_color_key = to_color_key or _default_to_color_key
        self._machine = machine if machine is not None else _default_machine(clock)
        self._pack: Any = None
        self._frames: dict[str, tuple[Image.Image, ...]] = {}
        self._factor = 1
        self._size = (1, 1)
        self._figure_width = 0
        self._blank: Image.Image | None = None
        self._pet_id = NO_PET_ID
        self._level = 0.0
        self._level_at = -math.inf
        self._smoothed = 0.0
        self.load(pet_id)

    # -- pack ------------------------------------------------------------

    def load(self, pet_id: str) -> None:
        """(Re)load the pet and pre-scale every frame. Keeps the state machine."""
        requested = str(pet_id or "").strip() or NO_PET_ID
        pack = None
        if requested != NO_PET_ID:
            try:
                pack = self._loader(requested)
            except Exception:  # noqa: BLE001 — a broken pack must not kill the overlay
                log.warning(
                    "pet %r failed to load; showing the strip only", requested, exc_info=True
                )
                pack = None
        self._pack = pack
        # The id of what actually loaded: an unknown id falls back to the
        # default pet, and the frame keys must say so.
        manifest_id = getattr(getattr(pack, "manifest", None), "id", None)
        self._pet_id = NO_PET_ID if pack is None else str(manifest_id or requested)
        self._rescale()

    def set_look(self, *, pet_scale: float | None = None, dpi_ratio: float | None = None) -> bool:
        """Apply a new size. Returns True when the window size changed."""
        before = self._size
        if pet_scale is not None:
            self._pet_scale = float(pet_scale)
        if dpi_ratio is not None:
            self._dpi_ratio = float(dpi_ratio)
        self._rescale()
        return self._size != before

    def _rescale(self) -> None:
        self._frames = {}
        self._act_names: tuple[str, ...] = ()
        self._act = None
        self._blank = None
        pack = self._pack
        if pack is None:
            self._use_strip_only_size()
            return
        edge = int(pack.manifest.frame_size)
        acts: Mapping[str, Any] = getattr(pack, "acts", None) or {}
        # Act frames join the crop (a flame may reach past the idle figure)
        # and are scaled with the states, under a prefixed key.
        source: Mapping[str, Any] = {
            **pack.frames,
            **{_ACT_KEY + str(name): seq for name, seq in acts.items()},
        }
        try:
            crop, (idle_w, idle_h) = figure_crop(source, edge)
            factor = pixel_factor(max(idle_w, idle_h), self._dpi_ratio, self._pet_scale)
            # States that borrow another row (STATE_FALLBACKS) share the SAME
            # source tuple in the pack; scale each tuple once, or a pet with
            # only an ``idle`` row would hold seven scaled copies of it.
            scaled_by_source: dict[int, tuple[Image.Image, ...]] = {}
            frames: dict[str, tuple[Image.Image, ...]] = {}
            for state, sequence in source.items():
                scaled = scaled_by_source.get(id(sequence))
                if scaled is None:
                    scaled = tuple(
                        self._to_color_key(frame.crop(crop), factor, self._color_key)
                        for frame in sequence
                    )
                    scaled_by_source[id(sequence)] = scaled
                frames[str(state)] = scaled
        except Exception:  # noqa: BLE001 — a frame that will not scale must not kill the overlay
            log.warning(
                "pet %r could not be scaled; showing the strip only", self._pet_id, exc_info=True
            )
            self._pack = None
            self._pet_id = NO_PET_ID
            self._use_strip_only_size()
            return
        self._frames = frames
        self._act_names = tuple(name for name in acts if frames.get(_ACT_KEY + name))
        self._factor = factor
        self._size = ((crop[2] - crop[0]) * factor, (crop[3] - crop[1]) * factor)
        self._figure_width = idle_w * factor

    def _use_strip_only_size(self) -> None:
        """No figure: the window is a 1 px line as wide as the strip."""
        strip_w, _strip_h = orb_controls.pet_strip_size(self.strip_scale)
        self._factor = 1
        self._size = (max(1, strip_w), 1)
        self._figure_width = 0

    @property
    def pet_id(self) -> str:
        """The pet actually shown (``"none"`` when nothing loaded)."""
        return self._pet_id

    @property
    def has_figure(self) -> bool:
        return self._pack is not None

    @property
    def size(self) -> tuple[int, int]:
        """The window size this pet needs, in pixels."""
        return self._size

    @property
    def factor(self) -> int:
        return self._factor

    @property
    def figure_width(self) -> int:
        """The idle figure's visible width on screen, in pixels (0 without a figure)."""
        return self._figure_width

    @property
    def strip_scale(self) -> float:
        """The control strip's scale: the monitor's DPI ratio times ``pet_scale``."""
        return max(0.25, self._dpi_ratio * self._pet_scale)

    @property
    def machine(self) -> Any:
        return self._machine

    # -- state feed ------------------------------------------------------

    def on_mode(self, mode: str) -> None:
        self._machine.on_mode(mode)

    def on_outcome(self, kind: str) -> None:
        self._machine.on_outcome(kind)

    def on_activity(self) -> None:
        self._machine.on_activity()

    def on_action(self, kind: str | None) -> None:
        self._machine.on_action(kind)

    def on_busy(self, busy: bool) -> None:
        self._machine.on_busy(busy)

    def on_held(self, held: bool) -> None:
        self._machine.on_held(held)

    def state(self) -> str:
        return str(self._machine.state())

    def feed_level(self, level: float | None, at: float | None) -> None:
        """The latest output level (0..1) and when it arrived, on this renderer's clock."""
        if level is None or at is None:
            return
        try:
            value = float(level)
            stamp = float(at)
        except (TypeError, ValueError):
            return
        if not (math.isfinite(value) and math.isfinite(stamp)):
            return
        if stamp - self._level_at > LEVEL_FRESH_S:
            self._smoothed = 0.0  # the voice paused: open from closed again
        self._level = max(0.0, min(1.0, value))
        self._level_at = stamp
        # Smoothed once per feed (the overlay feeds once per tick), not once
        # per read — frame_key, render and the pacing all read the same value.
        self._smoothed += LEVEL_SMOOTHING * (self._level - self._smoothed)

    def _level_fresh(self) -> bool:
        return float(self._clock()) - self._level_at <= LEVEL_FRESH_S

    # -- frame selection -------------------------------------------------

    def _idle_act(self, state: str) -> tuple[str, Any, int, float] | None:
        """The idle act on screen now, starting or ending one as its time comes.

        Returns ``(frame key, spec, frame index, elapsed)`` while an act plays,
        else ``None``. Only an idling pet with acts ever plays one.
        """
        if state != "idle" or not self._act_names:
            self._idle_epoch = None
            self._act = None
            return None
        now = float(self._clock())
        epoch = float(self._machine.state_started_at())
        if epoch != self._idle_epoch:
            self._idle_epoch = epoch
            self._act = None
            self._act_due = epoch + self._rng.uniform(*ACT_FIRST_DELAY_S)
        acts = self._pack.manifest.acts
        if self._act is not None:
            name, started = self._act
            spec = acts[name]
            if now - started >= spec.frames / float(spec.fps):
                self._act = None
                self._act_due = now + self._rng.uniform(*ACT_GAP_S)
        if self._act is None and now >= self._act_due:
            choices = [n for n in self._act_names if n != self._last_act] or list(self._act_names)
            name = self._rng.choice(choices)
            self._act = (name, now)
            self._last_act = name
        if self._act is None:
            return None
        name, started = self._act
        spec = acts[name]
        elapsed = max(0.0, now - started)
        return _ACT_KEY + name, spec, frame_index(elapsed, spec.frames, spec.fps, False), elapsed

    def _current(self) -> tuple[str, Any, int, float, bool]:
        """(resolved state, spec, frame index, elapsed seconds, level-driven) for now."""
        state = self.state()
        act = self._idle_act(state)
        if act is not None:
            key, spec, index, elapsed = act
            return key, spec, index, elapsed, False
        resolved, spec = self._pack.manifest.spec_for(state)
        resolved = str(resolved)
        sequence = self._frames.get(resolved) or self._frames.get(state) or ()
        frames = len(sequence) or max(1, int(getattr(spec, "frames", 1)))
        elapsed = max(0.0, float(self._clock()) - float(self._machine.state_started_at()))
        if state == "talking" and frames > 1:
            if self._level_fresh():
                return resolved, spec, level_frame(self._smoothed, frames), elapsed, True
            return resolved, spec, ping_pong_index(elapsed, frames, float(spec.fps)), elapsed, False
        index = frame_index(
            elapsed,
            frames,
            float(spec.fps),
            bool(spec.loop),
            accent_frames=int(getattr(spec, "accent_frames", 0)),
            accent_every=int(getattr(spec, "accent_every", 1)),
        )
        return resolved, spec, index, elapsed, False

    def frame_key(self, t: float = 0.0) -> Hashable:
        """A key that changes exactly when the picture on screen changes."""
        _ = t
        if self._pack is None:
            return ("none", self._size)
        resolved, _spec, index, _elapsed, _driven = self._current()
        return (self._pet_id, self._factor, resolved, index)

    def next_frame_delay_ms(self, t: float = 0.0) -> int:
        """How long the overlay may sleep before the next repaint is due."""
        _ = t
        waits: list[float] = []
        change = self._machine.next_change_in()
        if change is not None:
            waits.append(max(0.0, float(change)))
        if self._pack is not None:
            resolved, spec, _index, elapsed, driven = self._current()
            if driven:
                waits.append(LEVEL_POLL_MS / 1000.0)
            else:
                frames = len(self._frames.get(resolved) or ()) or int(getattr(spec, "frames", 1))
                loop = bool(spec.loop) or self.state() == "talking"
                boundary = seconds_to_next_frame(elapsed, frames, float(spec.fps), loop)
                if boundary is not None:
                    waits.append(boundary)
            if resolved.startswith(_ACT_KEY):
                # The act's last frame holds until the act is over.
                waits.append(max(0.0, spec.frames / float(spec.fps) - elapsed))
            elif self._idle_epoch is not None and math.isfinite(self._act_due):
                waits.append(max(0.0, self._act_due - float(self._clock())))
        if not waits:
            return MAX_FRAME_DELAY_MS
        # A hair past the boundary, so the tick lands on the new frame rather
        # than a microsecond before it (which would repaint the old one again).
        delay_ms = int(math.ceil(min(waits) * 1000.0)) + 2
        return max(MIN_FRAME_DELAY_MS, min(MAX_FRAME_DELAY_MS, delay_ms))

    def render(
        self, t: float = 0.0, mode: str = "idle", ext_level: float | None = None
    ) -> Image.Image:
        """The frame due now, colour-keyed at window size (shared; never mutate)."""
        _ = t, mode, ext_level
        if self._pack is None:
            if self._blank is None:
                self._blank = Image.new("RGB", self._size, self._color_key)
            return self._blank
        resolved, _spec, index, _elapsed, _driven = self._current()
        sequence = self._frames.get(resolved) or self._frames.get("idle") or ()
        if not sequence:
            if self._blank is None:
                self._blank = Image.new("RGB", self._size, self._color_key)
            return self._blank
        return sequence[min(index, len(sequence) - 1)]
