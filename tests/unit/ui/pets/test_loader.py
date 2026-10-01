"""Finding and decoding pet packs, and the colour-key conversion the overlay uses."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PIL import Image

from jarvis.ui.pets import loader
from jarvis.ui.pets.loader import (
    COLOR_KEY,
    builtin_root,
    custom_root,
    find_pet_dir,
    list_pets,
    load_pet,
    load_pet_by_id,
    normalize_sheet,
    to_color_key,
)
from jarvis.ui.pets.manifest import PetManifestError
from jarvis.ui.pets.states import DEFAULT_PET_ID, PET_FORMAT, PET_STATES

USER_ID = "u0123456789abcdef"


def _write_pet(
    folder: Path,
    *,
    pet_id: str,
    sheet: Image.Image,
    animations: dict | None = None,
    frame_size: int = 32,
) -> Path:
    folder.mkdir(parents=True)
    sheet.save(folder / "sheet.png")
    manifest = {
        "format": PET_FORMAT,
        "id": pet_id,
        "name": "Test pet",
        "description": "",
        "frame_size": frame_size,
        "sheet": "sheet.png",
        "animations": animations
        or {
            "idle": {"row": 0, "frames": 2, "fps": 4},
            "talking": {"row": 1, "frames": 1, "fps": 8},
        },
    }
    (folder / "pet.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def _two_row_sheet() -> Image.Image:
    sheet = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    sheet.paste((10, 20, 30, 255), (0, 0, 32, 32))  # idle frame 0
    sheet.paste((40, 50, 60, 255), (32, 0, 64, 32))  # idle frame 1
    sheet.paste((70, 80, 90, 255), (0, 32, 32, 64))  # talking frame 0
    return sheet


@pytest.fixture
def user_pet(tmp_path: Path) -> Path:
    return _write_pet(tmp_path / "pets" / USER_ID, pet_id=USER_ID, sheet=_two_row_sheet())


def test_load_pet_has_frames_for_every_state(user_pet: Path) -> None:
    pack = load_pet(user_pet, builtin=False)
    assert set(pack.frames) == set(PET_STATES)
    assert len(pack.frames["idle"]) == 2
    assert len(pack.frames["talking"]) == 1
    # Undeclared states share their fallback's frames (listening -> idle).
    assert pack.frames["listening"] is pack.frames["idle"]
    assert pack.frames["thinking"] is pack.frames["idle"]
    for frames in pack.frames.values():
        for frame in frames:
            assert frame.mode == "RGBA"
            assert frame.size == (32, 32)
    assert pack.frames["idle"][1].getpixel((5, 5)) == (40, 50, 60, 255)


def test_normalize_sheet_makes_alpha_binary_and_avoids_the_key() -> None:
    image = Image.new("RGBA", (4, 1))
    image.putpixel((0, 0), (1, 2, 3, 127))  # below threshold: gone
    image.putpixel((1, 0), (4, 5, 6, 128))  # at threshold: opaque
    image.putpixel((2, 0), (*COLOR_KEY, 255))  # exact key: nudged
    image.putpixel((3, 0), (*COLOR_KEY, 0))  # transparent key: just gone
    clean = normalize_sheet(image)
    assert clean.getpixel((0, 0)) == (0, 0, 0, 0)
    assert clean.getpixel((1, 0)) == (4, 5, 6, 255)
    assert clean.getpixel((2, 0)) == (0xFE, 0x00, 0xFE, 255)
    assert clean.getpixel((3, 0)) == (0, 0, 0, 0)
    assert set(clean.getchannel("A").tobytes()) <= {0, 255}


def test_to_color_key_scales_with_hard_edges() -> None:
    frame = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
    frame.putpixel((0, 0), (200, 10, 10, 255))
    frame.putpixel((1, 1), (*COLOR_KEY, 255))
    frame.putpixel((1, 0), (9, 9, 9, 100))  # half-transparent: keyed out
    keyed = to_color_key(frame, 3)
    assert keyed.mode == "RGB"
    assert keyed.size == (6, 6)
    colors = {keyed.getpixel((x, y)) for x in range(6) for y in range(6)}
    # Only the sprite's own colours and the exact key; no blends.
    assert colors == {(200, 10, 10), COLOR_KEY, (0xFE, 0x00, 0xFE)}
    assert keyed.getpixel((2, 2)) == (200, 10, 10)
    assert keyed.getpixel((3, 0)) == COLOR_KEY
    assert keyed.getpixel((5, 5)) == (0xFE, 0x00, 0xFE)


def test_to_color_key_honours_another_key() -> None:
    frame = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    assert to_color_key(frame, 1, key=(0, 255, 0)).getpixel((0, 0)) == (0, 255, 0)


@pytest.mark.parametrize("scale", [0, -2, 1.5, True])
def test_to_color_key_rejects_bad_scale(scale: object) -> None:
    with pytest.raises(ValueError):
        to_color_key(Image.new("RGBA", (1, 1)), scale)  # type: ignore[arg-type]


def test_find_pet_dir(tmp_path: Path, user_pet: Path) -> None:
    folder, builtin = find_pet_dir(DEFAULT_PET_ID, data_dir=tmp_path)  # type: ignore[misc]
    assert builtin is True
    assert folder == builtin_root() / DEFAULT_PET_ID
    assert find_pet_dir(USER_ID, data_dir=tmp_path) == (user_pet, False)
    for bad in ("none", "nope", "../gigi", "..", "u0123456789ABCDEF", "", 3):
        assert find_pet_dir(bad, data_dir=tmp_path) is None  # type: ignore[arg-type]


def test_custom_root_uses_data_dir(tmp_path: Path) -> None:
    assert custom_root(tmp_path) == tmp_path / "pets"
    assert not (tmp_path / "pets").exists()


def test_load_pet_by_id(tmp_path: Path, user_pet: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert load_pet_by_id("none", data_dir=tmp_path) is None
    assert load_pet_by_id(USER_ID, data_dir=tmp_path).manifest.id == USER_ID  # type: ignore[union-attr]
    with caplog.at_level(logging.WARNING, logger=loader.__name__):
        fallback = load_pet_by_id("u" + "f" * 16, data_dir=tmp_path)
    assert fallback is not None and fallback.manifest.id == DEFAULT_PET_ID
    assert "not found" in caplog.text


def test_broken_pet_falls_back_to_default(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    folder = _write_pet(
        tmp_path / "pets" / USER_ID,
        pet_id=USER_ID,
        sheet=Image.new("RGBA", (32, 32)),
        animations={"idle": {"row": 3, "frames": 1, "fps": 4}},  # row outside the sheet
    )
    with pytest.raises(PetManifestError):
        load_pet(folder, builtin=False)
    with caplog.at_level(logging.WARNING, logger=loader.__name__):
        pack = load_pet_by_id(USER_ID, data_dir=tmp_path)
    assert pack is not None and pack.manifest.id == DEFAULT_PET_ID
    assert "broken" in caplog.text


def test_folder_name_must_match_id(tmp_path: Path) -> None:
    folder = _write_pet(tmp_path / "u1111111111111111", pet_id=USER_ID, sheet=_two_row_sheet())
    with pytest.raises(PetManifestError, match="folder"):
        load_pet(folder, builtin=False)


def test_non_png_sheet_is_rejected(tmp_path: Path) -> None:
    folder = _write_pet(tmp_path / USER_ID, pet_id=USER_ID, sheet=_two_row_sheet())
    Image.new("RGB", (64, 64)).save(folder / "sheet.png", format="BMP")
    with pytest.raises(PetManifestError, match="PNG"):
        load_pet(folder, builtin=False)


def test_bad_json_is_rejected(tmp_path: Path) -> None:
    folder = _write_pet(tmp_path / USER_ID, pet_id=USER_ID, sheet=_two_row_sheet())
    (folder / "pet.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(PetManifestError, match="JSON"):
        load_pet(folder, builtin=False)


def test_list_pets_orders_default_first_and_skips_broken(tmp_path: Path, user_pet: Path) -> None:
    broken = tmp_path / "pets" / "u2222222222222222"
    broken.mkdir()
    (broken / "pet.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pets" / "not-a-user-id").mkdir()
    pets = list_pets(data_dir=tmp_path)
    ids = [m.id for m in pets]
    assert ids[0] == DEFAULT_PET_ID
    assert USER_ID in ids
    assert "u2222222222222222" not in ids
    assert all(m.builtin for m in pets if m.id != USER_ID)
    assert ids[-1] == USER_ID


def test_list_pets_without_custom_folder(tmp_path: Path) -> None:
    assert all(m.builtin for m in list_pets(data_dir=tmp_path / "missing"))
