"""User-created pets under ``<DATA_DIR>/pets`` (``docs/pets.md``).

An upload is untrusted: the sheet is decoded with Pillow, checked against the
format's limits and RE-ENCODED, so the raw upload bytes never reach the disk.
Each pet is staged in a hidden folder and moved into place with one rename,
so a crash never leaves a half-written pet that the loader would list.
"""

from __future__ import annotations

import io
import json
import logging
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from PIL import Image

from jarvis.ui.pets.loader import MANIFEST_NAME, list_custom, normalize_sheet
from jarvis.ui.pets.manifest import (
    MAX_SHEET_BYTES,
    USER_ID_RE,
    PetManifest,
    PetManifestError,
    check_cells,
    check_sheet_size,
    parse_manifest,
)
from jarvis.ui.pets.states import (
    FRAME_SIZES,
    MAX_FRAMES_PER_STATE,
    ONE_SHOT_STATES,
    PET_FORMAT,
    PET_STATES,
)

_log = logging.getLogger(__name__)

SHEET_NAME = "sheet.png"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_MANIFEST_CHARS = 64 * 1024

#: Upper bound of user-created pets. Each one is up to 2 MB on disk, and the
#: settings grid shows them all; a cap keeps a scripted upload loop from
#: filling the data directory.
MAX_USER_PETS = 50

_STAGING_PREFIX = ".staging-"
#: A staging folder older than this is debris from a crash mid-save.
_STALE_STAGING_S = 3600.0

#: The frame size tried first when an upload does not name one (the template's).
_PREFERRED_FRAME_SIZE = 48

#: Frame rate of an inferred animation, per state; unnamed states use 8.
_DEFAULT_FPS: dict[str, int] = {"idle": 4, "sleeping": 2}
_ACTIVE_FPS = 8


class PetStore:
    """Create, list and delete user pets in one folder."""

    MAX_SHEET_BYTES = MAX_SHEET_BYTES

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def list(self) -> list[PetManifest]:
        """Every valid user pet; broken ones are skipped with a warning."""
        self._sweep_stale_staging()
        return list_custom(self.root)

    def count(self) -> int:
        """How many user pets exist (folders with a manifest), without decoding them."""
        if not self.root.is_dir():
            return 0
        try:
            return sum(
                1
                for entry in self.root.iterdir()
                if USER_ID_RE.match(entry.name) and (entry / MANIFEST_NAME).is_file()
            )
        except OSError as exc:
            _log.warning("could not count user pets in %s: %s", self.root, exc)
            return 0

    def directory(self, pet_id: str) -> Path | None:
        """The folder of an existing user pet, or ``None``."""
        if not isinstance(pet_id, str) or not USER_ID_RE.match(pet_id):
            return None
        folder = self.root / pet_id
        return folder if (folder / MANIFEST_NAME).is_file() else None

    def add(
        self,
        *,
        sheet_bytes: bytes,
        name: str,
        description: str,
        manifest_json: str | None = None,
        frame_size: int | None = None,
    ) -> PetManifest:
        """Validate an upload, store it as a new pet and return its manifest.

        Without ``manifest_json`` the rows are read in ``PET_STATES`` order and
        each row's frame count is its run of non-empty cells from the left.
        Raises :class:`PetManifestError` with a user-readable message.
        """
        if self.count() >= MAX_USER_PETS:
            raise PetManifestError(
                f"You already have {MAX_USER_PETS} pets. Delete one to add another."
            )
        sheet = _decode_sheet(sheet_bytes)
        width, height = sheet.size
        pet_id = "u" + secrets.token_hex(8)

        data: dict = {}
        if manifest_json is not None and manifest_json.strip():
            data = _manifest_data(manifest_json)
            if frame_size is None and "frame_size" in data:
                frame_size = data["frame_size"]
        size = _pick_frame_size(width, height, frame_size)
        if "animations" not in data:
            data["animations"] = _infer_animations(sheet, size)
        # The form fields win; an uploaded pet.json fills in what they leave empty.
        if isinstance(name, str) and name.strip():
            data["name"] = name
        if isinstance(description, str) and description.strip():
            data["description"] = description
        data.update(
            {
                "format": PET_FORMAT,
                "id": pet_id,
                "frame_size": size,
                "sheet": SHEET_NAME,
            }
        )
        manifest = parse_manifest(data, builtin=False)
        check_cells(manifest, width, height)
        self._write(manifest, sheet)
        return manifest

    def delete(self, pet_id: str) -> bool:
        """Remove a user pet. Only ``u…`` ids; never touches anything outside the root."""
        if not isinstance(pet_id, str) or not USER_ID_RE.match(pet_id):
            return False
        folder = self.root / pet_id
        try:
            if folder.is_symlink():
                # A link planted in the pets folder: drop the link, never its target.
                folder.unlink()
                return True
            if not folder.is_dir():
                return False
            if folder.resolve().parent != self.root.resolve():
                return False
            shutil.rmtree(folder)
        except OSError as exc:
            _log.warning("could not delete pet %r: %s", pet_id, exc)
            return False
        return True

    # -- internals ------------------------------------------------------

    def _sweep_stale_staging(self, *, now: float | None = None) -> None:
        """Remove staging folders a crash left behind (older than an hour).

        A fresh one belongs to a save in progress (another thread, another
        request), so only old ones go.
        """
        if not self.root.is_dir():
            return
        cutoff = (time.time() if now is None else now) - _STALE_STAGING_S
        try:
            entries = list(self.root.iterdir())
        except OSError as exc:
            _log.debug("staging sweep skipped for %s: %s", self.root, exc)
            return
        for entry in entries:
            if not entry.name.startswith(_STAGING_PREFIX):
                continue
            try:
                if entry.is_symlink() or not entry.is_dir():
                    continue
                if entry.stat().st_mtime >= cutoff:
                    continue
                shutil.rmtree(entry)
                _log.info("removed a stale pet staging folder %s", entry.name)
            except OSError as exc:
                _log.debug("could not remove stale staging folder %s: %s", entry, exc)

    def _write(self, manifest: PetManifest, sheet: Image.Image) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._sweep_stale_staging()
        staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=self.root))
        try:
            _write_atomic(staging / SHEET_NAME, _encode_png(sheet))
            text = json.dumps(manifest.to_json(), indent=2, ensure_ascii=False) + "\n"
            _write_atomic(staging / MANIFEST_NAME, text.encode("utf-8"))
            os.replace(staging, self.root / manifest.id)
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise PetManifestError("The pet could not be saved.") from exc


def _decode_sheet(sheet_bytes: bytes) -> Image.Image:
    if not isinstance(sheet_bytes, bytes | bytearray) or not sheet_bytes:
        raise PetManifestError("The sprite sheet is empty.")
    if len(sheet_bytes) > MAX_SHEET_BYTES:
        raise PetManifestError("The sprite sheet is larger than 2 MB.")
    if not bytes(sheet_bytes[:8]) == _PNG_SIGNATURE:
        raise PetManifestError("The sprite sheet must be a PNG image.")
    try:
        with Image.open(io.BytesIO(sheet_bytes)) as probe:
            if probe.format != "PNG":
                raise PetManifestError("The sprite sheet must be a PNG image.")
            # Size comes from the header: reject a huge sheet before decoding it.
            check_sheet_size(*probe.size)
            probe.load()
            return normalize_sheet(probe)
    except PetManifestError:
        raise
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise PetManifestError("The sprite sheet could not be read as a PNG.") from exc


def _manifest_data(manifest_json: str) -> dict:
    if len(manifest_json) > _MAX_MANIFEST_CHARS:
        raise PetManifestError("pet.json is too large.")
    try:
        data = json.loads(manifest_json)
    except json.JSONDecodeError as exc:
        raise PetManifestError("pet.json is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise PetManifestError("pet.json must be a JSON object.")
    return data


def _pick_frame_size(width: int, height: int, requested: object) -> int:
    """The cell size for a ``width x height`` sheet; the grid must divide it exactly."""
    if requested is not None:
        if (
            not isinstance(requested, int)
            or isinstance(requested, bool)
            or requested not in FRAME_SIZES
        ):
            sizes = ", ".join(str(size) for size in FRAME_SIZES)
            raise PetManifestError(f"'frame_size' must be one of {sizes}.")
        if width % requested or height % requested:
            raise PetManifestError(
                f"The sheet is not a grid of {requested} x {requested} pixel cells."
            )
        return requested
    fitting = [
        size
        for size in FRAME_SIZES
        if width % size == 0
        and height % size == 0
        and width // size <= MAX_FRAMES_PER_STATE
        and height // size <= len(PET_STATES)
    ]
    if not fitting:
        sizes = ", ".join(str(size) for size in FRAME_SIZES)
        raise PetManifestError(
            f"The sheet is not a grid of {sizes} pixel cells with at most "
            f"{MAX_FRAMES_PER_STATE} columns and {len(PET_STATES)} rows."
        )
    return _PREFERRED_FRAME_SIZE if _PREFERRED_FRAME_SIZE in fitting else max(fitting)


def _infer_animations(sheet: Image.Image, size: int) -> dict[str, dict]:
    """Read rows in ``PET_STATES`` order; frames = leading run of non-empty cells."""
    alpha = sheet.getchannel("A")
    columns = min(sheet.width // size, MAX_FRAMES_PER_STATE)
    rows = min(sheet.height // size, len(PET_STATES))
    animations: dict[str, dict] = {}
    for row, state in enumerate(PET_STATES[:rows]):
        frames = 0
        for col in range(columns):
            box = (col * size, row * size, (col + 1) * size, (row + 1) * size)
            if alpha.crop(box).getbbox() is None:
                break
            frames += 1
        if frames:
            animations[state] = {
                "row": row,
                "frames": frames,
                "fps": _DEFAULT_FPS.get(state, _ACTIVE_FPS),
                "loop": state not in ONE_SHOT_STATES,
            }
    if "idle" not in animations:
        raise PetManifestError("The first row (idle) is empty.")
    return animations


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _write_atomic(path: Path, payload: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
