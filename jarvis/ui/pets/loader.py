"""Find, validate and decode pet packs (``docs/pets.md``).

Built-in pets ship under ``jarvis/ui/pets/builtin/<id>/``; user-created pets
live under ``<DATA_DIR>/pets/<u…id>/``. :func:`load_pet` turns a folder into a
:class:`PetPack` whose frames are ready for the overlay: RGBA at source size,
binary alpha, and no pixel in the overlay's exact colour key.

Pure Pillow, no OS-specific code: the same loader runs in the Tk overlay, the
macOS companion process and the REST routes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from PIL import Image, ImageChops

from jarvis.ui.pets.manifest import (
    BUILTIN_ID_RE,
    MAX_SHEET_BYTES,
    USER_ID_RE,
    PetManifest,
    PetManifestError,
    check_cells,
    check_sheet_size,
    parse_manifest,
)
from jarvis.ui.pets.states import DEFAULT_PET_ID, NO_PET_ID, PET_STATES

_log = logging.getLogger(__name__)

MANIFEST_NAME = "pet.json"

#: The overlay's transparent colour (pure magenta). Sprites never use it.
COLOR_KEY: tuple[int, int, int] = (255, 0, 255)

#: Alpha at or above this counts as opaque; everything else is gone.
ALPHA_THRESHOLD = 128

#: A pet.json larger than this is not a manifest.
_MAX_MANIFEST_BYTES = 64 * 1024


@dataclass(frozen=True)
class PetPack:
    """A loaded pet: its manifest plus decoded frames for every state.

    ``frames`` has an entry for EVERY state in ``PET_STATES``; a state the
    manifest does not declare shares its fallback's tuple.
    """

    manifest: PetManifest
    directory: Path
    frames: Mapping[str, tuple[Image.Image, ...]]


def builtin_root() -> Path:
    """Folder holding the built-in pets (``jarvis/ui/pets/builtin``)."""
    return Path(__file__).resolve().parent / "builtin"


def custom_root(data_dir: Path | None = None) -> Path:
    """Folder holding user-created pets: ``<DATA_DIR>/pets``. Not created here."""
    if data_dir is None:
        from jarvis.core import config  # lazy: the overlay may run without it loaded

        data_dir = config.DATA_DIR
    return Path(data_dir) / "pets"


def find_pet_dir(pet_id: str, *, data_dir: Path | None = None) -> tuple[Path, bool] | None:
    """Return ``(folder, builtin)`` for ``pet_id``, or ``None``.

    Built-in pets win over user pets. ``"none"``, an unknown id and anything
    that is not a well-formed id return ``None``; ids are matched against
    strict patterns before they touch the filesystem, so no id can escape
    the pet folders.
    """
    if not isinstance(pet_id, str) or pet_id == NO_PET_ID:
        return None
    if BUILTIN_ID_RE.match(pet_id) and not USER_ID_RE.match(pet_id):
        folder = builtin_root() / pet_id
        if (folder / MANIFEST_NAME).is_file():
            return folder, True
    if USER_ID_RE.match(pet_id):
        folder = custom_root(data_dir) / pet_id
        if (folder / MANIFEST_NAME).is_file():
            return folder, False
    return None


def read_manifest(pet_dir: Path, *, builtin: bool) -> PetManifest:
    """Parse ``pet.json`` in ``pet_dir`` and check it against its sheet's size.

    Reads only the PNG header, so listing many pets stays cheap. Raises
    :class:`PetManifestError` for any broken rule or unreadable file.
    """
    pet_dir = Path(pet_dir)
    manifest_path = pet_dir / MANIFEST_NAME
    try:
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise PetManifestError("pet.json is too large.")
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PetManifestError("The pet folder has no pet.json.") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PetManifestError("pet.json could not be read as JSON.") from exc
    manifest = parse_manifest(data, builtin=builtin)
    if manifest.id != pet_dir.name:
        raise PetManifestError(
            f"pet.json names the id '{manifest.id}' but its folder is '{pet_dir.name}'."
        )
    sheet_path = pet_dir / manifest.sheet
    try:
        if sheet_path.stat().st_size > MAX_SHEET_BYTES:
            raise PetManifestError("The sprite sheet is larger than 2 MB.")
        with Image.open(sheet_path) as probe:
            if probe.format != "PNG":
                raise PetManifestError("The sprite sheet must be a PNG image.")
            width, height = probe.size
    except FileNotFoundError as exc:
        raise PetManifestError(f"The sprite sheet '{manifest.sheet}' is missing.") from exc
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise PetManifestError("The sprite sheet could not be read as a PNG.") from exc
    check_cells(manifest, width, height)
    return manifest


def normalize_sheet(image: Image.Image) -> Image.Image:
    """Return ``image`` as RGBA with binary alpha and no exact colour-key pixel.

    Alpha at or above :data:`ALPHA_THRESHOLD` becomes 255, everything else
    becomes fully transparent black. An opaque pixel that is exactly
    ``#FF00FF`` is nudged to ``#FE00FE`` so the overlay's colour key never
    punches a hole into the sprite.
    """
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A").point(lambda v: 255 if v >= ALPHA_THRESHOLD else 0)
    clean = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    clean.paste(rgba.convert("RGB"), mask=alpha)
    clean.putalpha(alpha)
    key_mask = _exact_color_mask(clean, COLOR_KEY, alpha)
    if key_mask.getbbox() is not None:
        clean.paste(_nudged(COLOR_KEY) + (255,), mask=key_mask)
    return clean


def load_pet(pet_dir: Path, *, builtin: bool) -> PetPack:
    """Load and decode the pet in ``pet_dir``. Raises :class:`PetManifestError`."""
    pet_dir = Path(pet_dir)
    manifest = read_manifest(pet_dir, builtin=builtin)
    try:
        with Image.open(pet_dir / manifest.sheet) as raw:
            raw.load()
            sheet = normalize_sheet(raw)
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise PetManifestError("The sprite sheet could not be decoded.") from exc
    check_sheet_size(*sheet.size)

    size = manifest.frame_size
    by_row: dict[str, tuple[Image.Image, ...]] = {}
    frames: dict[str, tuple[Image.Image, ...]] = {}
    for state in PET_STATES:
        resolved, spec = manifest.spec_for(state)
        if resolved not in by_row:
            top = spec.row * size
            by_row[resolved] = tuple(
                sheet.crop((col * size, top, (col + 1) * size, top + size))
                for col in range(spec.frames)
            )
        frames[state] = by_row[resolved]
    return PetPack(manifest=manifest, directory=pet_dir, frames=MappingProxyType(frames))


def load_default_pet() -> PetPack | None:
    """The :data:`DEFAULT_PET_ID` pack, or ``None`` when even that is broken."""
    try:
        return load_pet(builtin_root() / DEFAULT_PET_ID, builtin=True)
    except PetManifestError as exc:
        # A broken default is a packaging bug; the strip without a figure
        # still works, so the overlay degrades instead of crashing.
        _log.error("default pet %r failed to load: %s", DEFAULT_PET_ID, exc)
        return None


def load_pet_by_id(pet_id: str, *, data_dir: Path | None = None) -> PetPack | None:
    """Load a pet by id.

    ``"none"`` returns ``None`` (strip without a figure). An unknown or broken
    id logs a warning and returns the :data:`DEFAULT_PET_ID` pack instead.
    """
    if pet_id == NO_PET_ID:
        return None
    found = find_pet_dir(pet_id, data_dir=data_dir)
    if found is None:
        _log.warning("pet %r not found; showing %r", pet_id, DEFAULT_PET_ID)
        return load_default_pet()
    folder, builtin = found
    try:
        return load_pet(folder, builtin=builtin)
    except PetManifestError as exc:
        _log.warning("pet %r is broken (%s); showing %r", pet_id, exc, DEFAULT_PET_ID)
        return load_default_pet()


def _builtin_manifests() -> list[PetManifest]:
    root = builtin_root()
    try:
        folders = [p for p in root.iterdir() if p.is_dir() and BUILTIN_ID_RE.match(p.name)]
    except OSError as exc:
        _log.error("built-in pets folder %s is unreadable: %s", root, exc)
        return []
    # Stable order: the default pet first, the rest alphabetically.
    folders.sort(key=lambda p: (p.name != DEFAULT_PET_ID, p.name))
    manifests: list[PetManifest] = []
    for folder in folders:
        try:
            manifests.append(read_manifest(folder, builtin=True))
        except PetManifestError as exc:
            _log.warning("built-in pet %r skipped: %s", folder.name, exc)
    return manifests


def list_custom(root: Path) -> list[PetManifest]:
    """Every valid user pet under ``root``; broken ones are skipped with a warning."""
    root = Path(root)
    try:
        folders = [p for p in root.iterdir() if p.is_dir() and USER_ID_RE.match(p.name)]
    except FileNotFoundError:  # A fresh install has no user-created pets directory yet.
        return []
    except OSError as exc:
        _log.warning("pets folder %s is unreadable: %s", root, exc)
        return []
    manifests: list[PetManifest] = []
    for folder in folders:
        try:
            manifests.append(read_manifest(folder, builtin=False))
        except PetManifestError as exc:
            _log.warning("user pet %r skipped: %s", folder.name, exc)
    manifests.sort(key=lambda m: (m.name.casefold(), m.id))
    return manifests


def list_pets(*, data_dir: Path | None = None) -> list[PetManifest]:
    """Built-in pets (default first) followed by user pets. Never raises."""
    return _builtin_manifests() + list_custom(custom_root(data_dir))


def to_color_key(
    frame: Image.Image, scale: int, key: tuple[int, int, int] = COLOR_KEY
) -> Image.Image:
    """Scale ``frame`` by an integer factor onto a solid colour-key background.

    Returns an RGB image ``scale`` times the source size, scaled with
    nearest-neighbour so no pixel blends into the key. Transparent pixels
    become exactly ``key``; an opaque pixel that happens to equal ``key`` is
    nudged by one step so it stays visible.
    """
    if isinstance(scale, bool) or not isinstance(scale, int) or scale < 1:
        raise ValueError("scale must be a positive whole number")
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A").point(lambda v: 255 if v >= ALPHA_THRESHOLD else 0)
    rgb = rgba.convert("RGB")
    key_mask = _exact_color_mask(rgb, key, alpha)
    if key_mask.getbbox() is not None:
        rgb.paste(_nudged(key), mask=key_mask)
    out = Image.new("RGB", rgba.size, key)
    out.paste(rgb, mask=alpha)
    if scale == 1:
        return out
    width, height = out.size
    return out.resize((width * scale, height * scale), Image.Resampling.NEAREST)


def _nudged(key: tuple[int, int, int]) -> tuple[int, int, int]:
    """The nearest colour that is not ``key``: every lit channel one step darker.

    ``#FF00FF`` becomes ``#FE00FE`` (docs/pets.md); a black key becomes
    ``#010101``.
    """
    nudged = tuple(v - 1 if v > 0 else 0 for v in key)
    if nudged == tuple(key):
        return (1, 1, 1)
    return nudged  # type: ignore[return-value]


def _exact_color_mask(
    image: Image.Image, color: tuple[int, int, int], alpha: Image.Image
) -> Image.Image:
    """An ``L`` mask: 255 where ``image`` is exactly ``color`` and ``alpha`` is opaque."""
    mask = alpha
    for channel, value in zip(image.convert("RGB").split(), color, strict=True):
        hit = channel.point(lambda v, value=value: 255 if v == value else 0)
        mask = ImageChops.multiply(mask, hit)
    return mask
