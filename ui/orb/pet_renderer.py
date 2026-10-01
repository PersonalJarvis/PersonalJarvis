"""The desktop pet's renderer for ``OrbOverlay`` (style ``pet``, ``docs/pets.md``).

The mascot and the voice orb are drawn fresh every frame at 60 fps; a pixel-art
pet does not need that and must not pay for it (the budget in ``docs/pets.md``:
an idle pet costs next to nothing). So this renderer works the other way round:

* every frame of every state is scaled ONCE, when the pet is loaded — an
  integer nearest-neighbour factor from the monitor's DPI times the user's
  ``pet_scale``, keyed to the overlay's magenta colour key;
* ``frame_key`` names the frame that is due now, so the overlay repaints only
  when that key changes and reuses its cached Tk image otherwise;
* ``next_frame_delay_ms`` says how long the overlay may sleep until the next
  frame boundary or the next state change the state machine will make on its
  own (a one-shot ending, the pet falling asleep) — 4 fps idle, 2 fps asleep,
  whatever the manifest asks for while active.

The state itself comes from :class:`jarvis.ui.pets.state_machine.PetStateMachine`,
fed by the overlay from real Jarvis events. Pet ``"none"`` loads no pack: the
renderer then paints a fully keyed (invisible, click-through) strip-wide line so
the overlay window keeps a real position for the control strip to hang from.
"""

from __future__ import annotations

import logging
import math
import sys
import time
from collections.abc import Callable, Hashable, Mapping
from typing import Any

from PIL import Image

from jarvis.ui.pets.states import NO_PET_ID
from ui.orb import controls as orb_controls

log = logging.getLogger("jarvis.orb")

#: The figure's on-screen edge at 100 % display scaling and ``pet_scale`` 1.0.
#: A 48-pixel sheet lands on exactly 3x; 32 and 64 pixel sheets get the integer
#: factor nearest to the same size, so every pet is about as tall as the next.
PET_TARGET_EDGE_PX = 144

#: Logical pixels per inch Tk reports at 100 % scaling on Windows and X11.
_BASE_PPI = 96.0

#: Bounds for the frame timer. The floor keeps a broken manifest from spinning
#: the Tk loop; the ceiling makes sure a state change the overlay did not kick
#: (it always does, this is the backstop) is picked up within a second.
MIN_FRAME_DELAY_MS = 15
MAX_FRAME_DELAY_MS = 1000

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


def pixel_factor(frame_size: int, dpi_ratio: float = 1.0, pet_scale: float = 1.0) -> int:
    """The integer nearest-neighbour factor a sprite sheet is scaled by.

    Integer on purpose: a fractional factor duplicates some source pixels and
    not others, and pixel art then looks smeared. Rounded half up (Python's
    ``round`` would send 4.5 to 4) and never below 1.
    """
    try:
        edge = max(1, int(frame_size))
        ratio = float(dpi_ratio) if math.isfinite(float(dpi_ratio)) else 1.0
        scale = float(pet_scale) if math.isfinite(float(pet_scale)) else 1.0
    except (TypeError, ValueError):
        return 1
    target = PET_TARGET_EDGE_PX * max(0.1, ratio) * max(0.1, scale)
    return max(1, int(math.floor(target / edge + 0.5)))


def frame_index(elapsed_s: float, frames: int, fps: float, loop: bool) -> int:
    """Which frame of an animation is due ``elapsed_s`` after it started."""
    count = max(1, int(frames))
    if count == 1 or fps <= 0:
        return 0
    step = int(math.floor(max(0.0, elapsed_s) * float(fps)))
    if loop:
        return step % count
    return min(step, count - 1)


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
    is its state machine's, and its figure does not follow the audio level (the
    control strip's orb does). All methods run on the Tk thread.
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
    ) -> None:
        self._clock = clock
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
        self._blank: Image.Image | None = None
        self._pet_id = NO_PET_ID
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
        self._blank = None
        pack = self._pack
        if pack is None:
            self._use_strip_only_size()
            return
        edge = int(pack.manifest.frame_size)
        factor = pixel_factor(edge, self._dpi_ratio, self._pet_scale)
        source: Mapping[str, Any] = pack.frames
        # States that borrow another row (STATE_FALLBACKS) share the SAME
        # source tuple in the pack; scale each tuple once, or a pet with only
        # an ``idle`` row would hold seven scaled copies of it.
        scaled_by_source: dict[int, tuple[Image.Image, ...]] = {}
        frames: dict[str, tuple[Image.Image, ...]] = {}
        try:
            for state, sequence in source.items():
                scaled = scaled_by_source.get(id(sequence))
                if scaled is None:
                    scaled = tuple(
                        self._to_color_key(frame, factor, self._color_key) for frame in sequence
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
        self._factor = factor
        self._size = (edge * factor, edge * factor)

    def _use_strip_only_size(self) -> None:
        """No figure: the window is a 1 px line as wide as the strip."""
        strip_w, _strip_h = orb_controls.pet_strip_size(self._dpi_ratio)
        self._factor = 1
        self._size = (max(1, strip_w), 1)

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
    def machine(self) -> Any:
        return self._machine

    # -- state feed ------------------------------------------------------

    def on_mode(self, mode: str) -> None:
        self._machine.on_mode(mode)

    def on_outcome(self, kind: str) -> None:
        self._machine.on_outcome(kind)

    def on_activity(self) -> None:
        self._machine.on_activity()

    def state(self) -> str:
        return str(self._machine.state())

    # -- frame selection -------------------------------------------------

    def _current(self) -> tuple[str, Any, int, float]:
        """(resolved state, spec, frame index, elapsed seconds) for now."""
        state = self.state()
        resolved, spec = self._pack.manifest.spec_for(state)
        resolved = str(resolved)
        sequence = self._frames.get(resolved) or self._frames.get(state) or ()
        frames = len(sequence) or max(1, int(getattr(spec, "frames", 1)))
        elapsed = max(0.0, float(self._clock()) - float(self._machine.state_started_at()))
        index = frame_index(elapsed, frames, float(spec.fps), bool(spec.loop))
        return resolved, spec, index, elapsed

    def frame_key(self, t: float = 0.0) -> Hashable:
        """A key that changes exactly when the picture on screen changes."""
        _ = t
        if self._pack is None:
            return ("none", self._size)
        resolved, _spec, index, _elapsed = self._current()
        return (self._pet_id, self._factor, resolved, index)

    def next_frame_delay_ms(self, t: float = 0.0) -> int:
        """How long the overlay may sleep before the next repaint is due."""
        _ = t
        waits: list[float] = []
        change = self._machine.next_change_in()
        if change is not None:
            waits.append(max(0.0, float(change)))
        if self._pack is not None:
            resolved, spec, _index, elapsed = self._current()
            frames = len(self._frames.get(resolved) or ()) or int(getattr(spec, "frames", 1))
            boundary = seconds_to_next_frame(elapsed, frames, float(spec.fps), bool(spec.loop))
            if boundary is not None:
                waits.append(boundary)
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
        resolved, _spec, index, _elapsed = self._current()
        sequence = self._frames.get(resolved) or self._frames.get("idle") or ()
        if not sequence:
            if self._blank is None:
                self._blank = Image.new("RGB", self._size, self._color_key)
            return self._blank
        return sequence[min(index, len(sequence) - 1)]
