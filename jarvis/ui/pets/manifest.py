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
from dataclasses import dataclass
from types import MappingProxyType

from jarvis.ui.pets.states import (
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

MAX_NAME_CHARS = 40
MAX_DESCRIPTION_CHARS = 140
MIN_FPS = 1
MAX_FPS = 12

#: Largest sheet edge in pixels, and the largest sheet file in bytes.
MAX_SHEET_EDGE = 512
MAX_SHEET_BYTES = 2 * 1024 * 1024


class PetManifestError(ValueError):
    """A pet pack breaks a rule of the ``jarvis-pet/1`` format."""


@dataclass(frozen=True)
class AnimationSpec:
    """One state's animation: a row of ``frames`` cells played at ``fps``."""

    row: int
    frames: int
    fps: int
    loop: bool


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
        return {
            "format": PET_FORMAT,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "frame_size": self.frame_size,
            "sheet": self.sheet,
            "animations": {
                state: {
                    "row": spec.row,
                    "frames": spec.frames,
                    "fps": spec.fps,
                    "loop": spec.loop,
                }
                for state in PET_STATES
                if (spec := self.animations.get(state)) is not None
            },
        }


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
    return AnimationSpec(row=row, frames=frames, fps=fps, loop=loop)


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

    return PetManifest(
        id=pet_id,
        name=name,
        description=description,
        frame_size=frame_size,
        sheet=sheet,
        animations=MappingProxyType(animations),
        builtin=builtin,
    )


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
