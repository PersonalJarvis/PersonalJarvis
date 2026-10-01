"""User-created pets: upload validation, inference, re-encoding and deletion."""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from jarvis.ui.pets import store as store_module
from jarvis.ui.pets.loader import load_pet
from jarvis.ui.pets.manifest import USER_ID_RE, PetManifestError
from jarvis.ui.pets.states import PET_FORMAT
from jarvis.ui.pets.store import PetStore

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png(image: Image.Image, **save: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", **save)
    return buffer.getvalue()


def _sheet(rows: list[int], size: int = 48, columns: int = 8) -> Image.Image:
    """A sheet whose row ``r`` has ``rows[r]`` filled cells from the left."""
    sheet = Image.new("RGBA", (size * columns, size * len(rows)), (0, 0, 0, 0))
    for row, filled in enumerate(rows):
        for col in range(filled):
            box = (col * size, row * size, (col + 1) * size, (row + 1) * size)
            sheet.paste((30 + col, 60, 90, 255), box)
    return sheet


def _add(store: PetStore, sheet: bytes, **kwargs: object):
    fields: dict = {"name": "Pet", "description": ""}
    fields.update(kwargs)
    return store.add(sheet_bytes=sheet, **fields)


@pytest.fixture
def store(tmp_path: Path) -> PetStore:
    return PetStore(tmp_path / "pets")


def test_add_infers_animations_from_the_rows(store: PetStore) -> None:
    manifest = _add(store, _png(_sheet([4, 2, 0, 3])), name=" Pixel ", description="Hi.")
    assert USER_ID_RE.match(manifest.id)
    assert manifest.name == "Pixel"
    assert manifest.frame_size == 48
    assert set(manifest.animations) == {"idle", "listening", "talking"}
    assert manifest.animations["idle"].frames == 4
    assert manifest.animations["idle"].fps == 4
    assert manifest.animations["listening"].fps == 8
    assert manifest.animations["talking"].row == 3
    # The stored pack loads through the real loader.
    folder = store.directory(manifest.id)
    assert folder is not None
    assert load_pet(folder, builtin=False).manifest == manifest


def test_frames_stop_at_the_first_empty_cell(store: PetStore) -> None:
    sheet = _sheet([2])
    sheet.paste((1, 2, 3, 255), (48 * 3, 0, 48 * 4, 48))  # a stray cell after a gap
    assert _add(store, _png(sheet)).animations["idle"].frames == 2


def test_add_with_manifest(store: PetStore) -> None:
    manifest_json = json.dumps(
        {
            "format": PET_FORMAT,
            "id": "ignored",
            "name": "From file",
            "description": "Named in the file.",
            "frame_size": 32,
            "sheet": "whatever.png",
            "animations": {"idle": {"row": 1, "frames": 2, "fps": 6}},
        }
    )
    sheet = _png(_sheet([1, 2], size=32))
    manifest = _add(store, sheet, name="", manifest_json=manifest_json)
    assert manifest.name == "From file"
    assert manifest.description == "Named in the file."
    assert manifest.frame_size == 32
    assert manifest.sheet == "sheet.png"
    assert manifest.animations["idle"].row == 1
    assert manifest.id != "ignored"


def test_form_fields_win_over_the_manifest(store: PetStore) -> None:
    manifest_json = json.dumps(
        {"name": "File name", "animations": {"idle": {"row": 0, "frames": 1, "fps": 4}}}
    )
    manifest = _add(store, _png(_sheet([1])), name="Form name", manifest_json=manifest_json)
    assert manifest.name == "Form name"


def test_upload_is_re_encoded(store: PetStore) -> None:
    info = PngInfo()
    info.add_text("Comment", "smuggled-metadata")
    upload = _png(_sheet([1]), pnginfo=info)
    assert b"smuggled-metadata" in upload
    manifest = _add(store, upload + b"trailing-junk")
    folder = store.directory(manifest.id)
    assert folder is not None
    stored = (folder / "sheet.png").read_bytes()
    assert stored.startswith(PNG_SIGNATURE)
    assert b"smuggled-metadata" not in stored
    assert b"trailing-junk" not in stored


def test_explicit_frame_size_must_fit_the_grid(store: PetStore) -> None:
    with pytest.raises(PetManifestError, match="grid"):
        _add(store, _png(_sheet([1], size=48)), frame_size=64)
    with pytest.raises(PetManifestError):
        _add(store, _png(_sheet([1])), frame_size=40)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty"),
        (b"GIF89a" + b"\0" * 20, "PNG"),
        (PNG_SIGNATURE + b"garbage", "read"),
    ],
)
def test_rejects_non_png_uploads(store: PetStore, payload: bytes, message: str) -> None:
    with pytest.raises(PetManifestError, match=message):
        _add(store, payload)


def test_rejects_oversize_uploads(store: PetStore) -> None:
    with pytest.raises(PetManifestError, match="2 MB"):
        _add(store, PNG_SIGNATURE + b"\0" * PetStore.MAX_SHEET_BYTES)
    with pytest.raises(PetManifestError, match="512"):
        _add(store, _png(Image.new("RGBA", (576, 48))))


def test_rejects_a_bad_grid_and_an_empty_idle_row(store: PetStore) -> None:
    with pytest.raises(PetManifestError, match="grid"):
        _add(store, _png(Image.new("RGBA", (50, 50), (1, 1, 1, 255))))
    with pytest.raises(PetManifestError, match="idle"):
        _add(store, _png(_sheet([0, 2])))


def test_rejects_bad_manifest_text(store: PetStore) -> None:
    for text in ("{not json", "[1, 2]"):
        with pytest.raises(PetManifestError):
            _add(store, _png(_sheet([1])), manifest_json=text)


def test_rejects_a_missing_name(store: PetStore) -> None:
    with pytest.raises(PetManifestError, match="name"):
        _add(store, _png(_sheet([1])), name="  ")


def test_list_and_delete(store: PetStore) -> None:
    first = _add(store, _png(_sheet([1])), name="B pet")
    second = _add(store, _png(_sheet([1])), name="A pet")
    assert [m.id for m in store.list()] == [second.id, first.id]
    assert store.delete(first.id) is True
    assert store.directory(first.id) is None
    assert [m.id for m in store.list()] == [second.id]
    assert store.delete(first.id) is False
    # No staging folder is left behind.
    assert sorted(p.name for p in store.root.iterdir()) == [second.id]


def test_delete_never_leaves_the_root(store: PetStore, tmp_path: Path) -> None:
    outside = tmp_path / "keep"
    outside.mkdir()
    (outside / "file.txt").write_text("x", encoding="utf-8")
    for bad in ("..", "../keep", "gigi", "", "u0123", "u0123456789ABCDEF", None):
        assert store.delete(bad) is False  # type: ignore[arg-type]
    assert (outside / "file.txt").exists()


def test_list_of_a_missing_root_is_empty(tmp_path: Path) -> None:
    assert PetStore(tmp_path / "nothing").list() == []


def test_the_number_of_user_pets_is_capped(
    store: PetStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "MAX_USER_PETS", 2)
    sheet = _png(_sheet([1], columns=1))
    _add(store, sheet)
    _add(store, sheet)
    assert store.count() == 2
    with pytest.raises(PetManifestError, match="already have 2 pets"):
        _add(store, sheet)
    assert store.count() == 2


def test_stale_staging_folders_are_swept_and_fresh_ones_kept(store: PetStore) -> None:
    store.root.mkdir(parents=True)
    stale = store.root / ".staging-crashed"
    fresh = store.root / ".staging-in-progress"
    stale.mkdir()
    (stale / "sheet.png").write_bytes(b"half")
    fresh.mkdir()
    old = time.time() - 2 * 3600
    os.utime(stale, (old, old))

    assert store.list() == []

    assert not stale.exists()
    assert fresh.is_dir()
