"""The ``pet.json`` manifest: parsing, validation and fallback resolution.

A pet is a folder with ``pet.json`` and a sprite sheet (``docs/pets.md``,
format ``jarvis-pet/1``). This module owns every rule a manifest must satisfy
on its own; the rules that need the decoded sheet (cells inside the image)
live in :func:`check_cells`, which the loader and the store call once they
know the sheet's size.

Every rejection raises :class:`PetManifestError` with a short, user-readable
English sentence: the message reaches the settings page verbatim when a
user uploads a broken pet.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from jarvis.ui.pets.states import (
    CALL_STATES,
    FRAME_SIZES,
    MAX_FRAMES_PER_STATE,
    NO_PET_ID,
    ONE_SHOT_STATES,
    PET_FORMAT,
    PET_STATES,
    STATE_FALLBACKS,
)

#: Id of a built-in pet (the folder name under ``jarvis/ui/pets/builtin``).
BUILTIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

#: Id of a user-created pet: ``u`` plus 16 hex digits (``PetStore.add``).
USER_ID_RE = re.compile(r"^u[0-9a-f]{16}$")

#: A sheet is a plain PNG file name inside the pet folder, never a path.
_SHEET_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}\.png$")

#: Name of an idle act: a short lowercase slug (``"fire"``, ``"yawn"``).
ACT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")

#: Most idle acts one pet may declare.
MAX_ACTS = 12

MAX_NAME_CHARS = 40
MAX_DESCRIPTION_CHARS = 140
MIN_FPS = 1
MAX_FPS = 12
#: Upper bound of ``accent_every``: an accent (a blink) at least once per this
#: many loops, so it never disappears for good.
MAX_ACCENT_EVERY = 12

#: Largest sheet edge in pixels, and the largest sheet file in bytes.
MAX_SHEET_EDGE = 512
MAX_SHEET_BYTES = 2 * 1024 * 1024


class PetManifestError(ValueError):
    """A pet pack breaks a rule of the ``jarvis-pet/1`` format."""


@dataclass(frozen=True)
class AnimationSpec:
    """One state's animation: a row of ``frames`` cells played at ``fps``.

    ``accent_frames`` (optional) marks the LAST cells of a looping row as an
    accent — a blink, a wink — that plays only once every ``accent_every``
    loops instead of on every pass. Without it an eight-frame idle at six
    frames per second would blink every 1.3 s; with ``accent_frames: 1`` and
    ``accent_every: 3`` the pet breathes on the first seven cells and blinks
    every few seconds. ``0`` (the default) plays every cell on every loop.
    """

    row: int
    frames: int
    fps: int
    loop: bool
    accent_frames: int = 0
    accent_every: int = 1


@dataclass(frozen=True)
class PetManifest:
    """A validated ``pet.json``.

    ``animations`` holds only the states the file declares; use
    :meth:`spec_for` to resolve a missing state through ``STATE_FALLBACKS``.
    """

    id: str
    name: str
    description: str
    frame_size: int
    sheet: str
    animations: Mapping[str, AnimationSpec]
    builtin: bool = False
    #: Idle acts: short one-shot animations the pet plays now and then while
    #: nothing happens (a yawn, a stretch, a puff of fire), each a row of
    #: ``acts_sheet``. Optional; without them the pet just idles.
    acts: Mapping[str, AnimationSpec] = field(default_factory=lambda: MappingProxyType({}))
    acts_sheet: str | None = None
    #: The pet on the phone: rows for the ``CALL_STATES`` in ``phone_sheet``,
    #: shown instead of the plain rows while a voice conversation runs.
    #: Optional; without them the pet just listens and talks as usual.
    phone: Mapping[str, AnimationSpec] = field(default_factory=lambda: MappingProxyType({}))
    phone_sheet: str | None = None

    def spec_for(self, state: str) -> tuple[str, AnimationSpec]:
        """Return ``(resolved_state, spec)`` for ``state``.

        A state the manifest does not declare borrows its fallback's row;
        every fallback chain ends at ``idle``, which a valid manifest always
        has. Raises ``KeyError`` for a name outside ``PET_STATES``.
        """
        if state not in PET_STATES:
            raise KeyError(f"unknown pet state {state!r}")
        resolved = state
        seen: set[str] = set()
        while resolved not in self.animations:
            seen.add(resolved)
            resolved = STATE_FALLBACKS.get(resolved, "idle")
            if resolved in seen:  # defensive: a cyclic table must not hang
                resolved = "idle"
                break
        return resolved, self.animations[resolved]

    def to_json(self) -> dict:
        """The ``pet.json`` shape (``builtin`` is not part of the file)."""
        out = {
            "format": PET_FORMAT,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "frame_size": self.frame_size,
            "sheet": self.sheet,
            "animations": {
                state: spec_json(spec)
                for state in PET_STATES
                if (spec := self.animations.get(state)) is not None
            },
        }
        if self.acts and self.acts_sheet is not None:
            out["acts_sheet"] = self.acts_sheet
            out["acts"] = {name: spec_json(spec) for name, spec in self.acts.items()}
        if self.phone and self.phone_sheet is not None:
            out["phone_sheet"] = self.phone_sheet
            out["phone"] = {
                state: spec_json(spec)
                for state in CALL_STATES
                if (spec := self.phone.get(state)) is not None
            }
        return out


def spec_json(spec: AnimationSpec) -> dict:
    """One animation as it is written to ``pet.json`` (accent keys only when used)."""
    out: dict = {"row": spec.row, "frames": spec.frames, "fps": spec.fps, "loop": spec.loop}
    if spec.accent_frames > 0:
        out["accent_frames"] = spec.accent_frames
        out["accent_every"] = spec.accent_every
    return out


def _int_field(data: Mapping, key: str, where: str) -> int:
    value = data.get(key)
    # bool is an int subclass; "frames": true is a typo, not a count.
    if not isinstance(value, int) or isinstance(value, bool):
        raise PetManifestError(f"{where}: '{key}' must be a whole number.")
    return value


def _parse_animation(state: str, raw: object) -> AnimationSpec:
    where = f"Animation '{state}'"
    if not isinstance(raw, Mapping):
        raise PetManifestError(f"{where} must be an object with row, frames and fps.")
    row = _int_field(raw, "row", where)
    frames = _int_field(raw, "frames", where)
    fps = _int_field(raw, "fps", where)
    if row < 0:
        raise PetManifestError(f"{where}: 'row' cannot be negative.")
    if not 1 <= frames <= MAX_FRAMES_PER_STATE:
        raise PetManifestError(f"{where}: 'frames' must be between 1 and {MAX_FRAMES_PER_STATE}.")
    if not MIN_FPS <= fps <= MAX_FPS:
        raise PetManifestError(f"{where}: 'fps' must be between {MIN_FPS} and {MAX_FPS}.")
    loop = raw.get("loop", state not in ONE_SHOT_STATES)
    if not isinstance(loop, bool):
        raise PetManifestError(f"{where}: 'loop' must be true or false.")
    accent_frames = 0
    accent_every = 1
    if "accent_frames" in raw or "accent_every" in raw:
        accent_frames = _int_field(raw, "accent_frames", where)
        accent_every = _int_field(raw, "accent_every", where)
        if not 0 <= accent_frames < frames:
            raise PetManifestError(
                f"{where}: 'accent_frames' must leave at least one ordinary frame."
            )
        if not 1 <= accent_every <= MAX_ACCENT_EVERY:
            raise PetManifestError(
                f"{where}: 'accent_every' must be between 1 and {MAX_ACCENT_EVERY}."
            )
        if not loop and accent_frames:
            raise PetManifestError(f"{where}: an accent needs a looping animation.")
    return AnimationSpec(
        row=row,
        frames=frames,
        fps=fps,
        loop=loop,
        accent_frames=accent_frames,
        accent_every=accent_every,
    )


def parse_manifest(data: object, *, builtin: bool) -> PetManifest:
    """Validate a decoded ``pet.json`` and return the manifest.

    ``builtin`` selects the id rule: a built-in id is a short slug, a
    user-created one is ``u`` plus 16 hex digits. Raises
    :class:`PetManifestError` on the first broken rule.
    """
    if not isinstance(data, Mapping):
        raise PetManifestError("pet.json must be a JSON object.")
    if data.get("format") != PET_FORMAT:
        raise PetManifestError(f'pet.json must declare "format": "{PET_FORMAT}".')

    pet_id = data.get("id")
    if not isinstance(pet_id, str) or pet_id == NO_PET_ID:
        raise PetManifestError("The pet id is missing or reserved.")
    if builtin:
        if not BUILTIN_ID_RE.match(pet_id) or USER_ID_RE.match(pet_id):
            raise PetManifestError(
                "A built-in pet id uses 1-32 lowercase letters, digits and dashes."
            )
    elif not USER_ID_RE.match(pet_id):
        raise PetManifestError("A user pet id is 'u' followed by 16 hex digits.")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PetManifestError("The pet needs a name.")
    name = name.strip()
    if len(name) > MAX_NAME_CHARS:
        raise PetManifestError(f"The name is longer than {MAX_NAME_CHARS} characters.")

    description = data.get("description", "")
    if not isinstance(description, str):
        raise PetManifestError("The description must be text.")
    description = description.strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise PetManifestError(
            f"The description is longer than {MAX_DESCRIPTION_CHARS} characters."
        )

    frame_size = data.get("frame_size")
    if (
        not isinstance(frame_size, int)
        or isinstance(frame_size, bool)
        or frame_size not in FRAME_SIZES
    ):
        sizes = ", ".join(str(size) for size in FRAME_SIZES)
        raise PetManifestError(f"'frame_size' must be one of {sizes}.")

    sheet = data.get("sheet")
    if not isinstance(sheet, str) or not _SHEET_NAME_RE.match(sheet) or ".." in sheet:
        raise PetManifestError("'sheet' must be a PNG file name inside the pet folder.")

    raw_animations = data.get("animations")
    if not isinstance(raw_animations, Mapping) or not raw_animations:
        raise PetManifestError("pet.json needs an 'animations' object.")
    unknown = sorted(str(key) for key in raw_animations if key not in PET_STATES)
    if unknown:
        raise PetManifestError(f"Unknown animation state '{unknown[0]}'.")
    if "idle" not in raw_animations:
        raise PetManifestError("The 'idle' animation is required.")
    animations = {
        state: _parse_animation(state, raw_animations[state])
        for state in PET_STATES
        if state in raw_animations
    }

    acts, acts_sheet = _parse_acts(data, sheet)
    phone, phone_sheet = _parse_phone(data, sheet, acts_sheet)

    return PetManifest(
        id=pet_id,
        name=name,
        description=description,
        frame_size=frame_size,
        sheet=sheet,
        animations=MappingProxyType(animations),
        builtin=builtin,
        acts=MappingProxyType(acts),
        acts_sheet=acts_sheet,
        phone=MappingProxyType(phone),
        phone_sheet=phone_sheet,
    )


def _parse_acts(data: Mapping, sheet: str) -> tuple[dict[str, AnimationSpec], str | None]:
    """The optional idle acts and their sheet; both keys or neither."""
    raw_acts = data.get("acts")
    acts_sheet = data.get("acts_sheet")
    if raw_acts is None and acts_sheet is None:
        return {}, None
    if not isinstance(raw_acts, Mapping) or not raw_acts:
        raise PetManifestError("'acts' must be an object with at least one act.")
    if (
        not isinstance(acts_sheet, str)
        or not _SHEET_NAME_RE.match(acts_sheet)
        or ".." in acts_sheet
        or acts_sheet == sheet
    ):
        raise PetManifestError("'acts_sheet' must be its own PNG file name inside the pet folder.")
    if len(raw_acts) > MAX_ACTS:
        raise PetManifestError(f"A pet can have at most {MAX_ACTS} idle acts.")
    acts: dict[str, AnimationSpec] = {}
    for name, raw in raw_acts.items():
        if not isinstance(name, str) or not ACT_NAME_RE.match(name):
            raise PetManifestError(
                "An act name uses 1-24 lowercase letters, digits and underscores."
            )
        if isinstance(raw, Mapping) and "loop" not in raw:
            raw = {**raw, "loop": False}  # an act plays once unless it says otherwise
        spec = _parse_animation(f"act {name}", raw)
        if spec.loop or spec.accent_frames:
            raise PetManifestError(f"Act '{name}' plays once: 'loop' must be false.")
        acts[name] = spec
    return acts, acts_sheet


def _parse_phone(
    data: Mapping, sheet: str, acts_sheet: str | None
) -> tuple[dict[str, AnimationSpec], str | None]:
    """The optional on-the-phone rows and their sheet; both keys or neither."""
    raw_phone = data.get("phone")
    phone_sheet = data.get("phone_sheet")
    if raw_phone is None and phone_sheet is None:
        return {}, None
    if not isinstance(raw_phone, Mapping) or not raw_phone:
        raise PetManifestError("'phone' must be an object with at least one state.")
    if (
        not isinstance(phone_sheet, str)
        or not _SHEET_NAME_RE.match(phone_sheet)
        or ".." in phone_sheet
        or phone_sheet in (sheet, acts_sheet)
    ):
        raise PetManifestError("'phone_sheet' must be its own PNG file name inside the pet folder.")
    unknown = sorted(str(key) for key in raw_phone if key not in CALL_STATES)
    if unknown:
        raise PetManifestError(f"'{unknown[0]}' has no phone row; use one of the call states.")
    phone = {
        state: _parse_animation(f"phone {state}", raw_phone[state])
        for state in CALL_STATES
        if state in raw_phone
    }
    return phone, phone_sheet


def check_sheet_size(width: int, height: int) -> None:
    """Reject a sheet larger than ``MAX_SHEET_EDGE`` on either side."""
    if width < 1 or height < 1:
        raise PetManifestError("The sprite sheet is empty.")
    if width > MAX_SHEET_EDGE or height > MAX_SHEET_EDGE:
        raise PetManifestError(
            f"The sprite sheet is larger than {MAX_SHEET_EDGE} x {MAX_SHEET_EDGE} pixels."
        )


def check_cells(manifest: PetManifest, width: int, height: int) -> None:
    """Reject a manifest whose animations reach outside a ``width x height`` sheet."""
    check_sheet_size(width, height)
    size = manifest.frame_size
    for state, spec in manifest.animations.items():
        if (spec.row + 1) * size > height or spec.frames * size > width:
            raise PetManifestError(f"Animation '{state}' reaches outside the sprite sheet.")


def check_act_cells(manifest: PetManifest, width: int, height: int) -> None:
    """Reject idle acts that reach outside a ``width x height`` acts sheet."""
    check_sheet_size(width, height)
    size = manifest.frame_size
    for name, spec in manifest.acts.items():
        if (spec.row + 1) * size > height or spec.frames * size > width:
            raise PetManifestError(f"Act '{name}' reaches outside the acts sheet.")


def check_phone_cells(manifest: PetManifest, width: int, height: int) -> None:
    """Reject phone rows that reach outside a ``width x height`` phone sheet."""
    check_sheet_size(width, height)
    size = manifest.frame_size
    for state, spec in manifest.phone.items():
        if (spec.row + 1) * size > height or spec.frames * size > width:
            raise PetManifestError(f"Phone row '{state}' reaches outside the phone sheet.")
