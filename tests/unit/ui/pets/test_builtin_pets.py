"""The shipped pets: complete, loadable, and exactly what the generator draws."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from PIL import Image

from jarvis.ui.pets.loader import COLOR_KEY, builtin_root, list_pets, load_pet
from jarvis.ui.pets.states import DEFAULT_PET_ID, PET_STATES

REPO_ROOT = Path(__file__).resolve().parents[4]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "pets" / "build_pets.py"
PACKAGE_DIR = REPO_ROOT / "jarvis" / "ui" / "pets"
EXPECTED_PETS = ("gigi", "miso", "brew", "bolt", "mochi", "shelly", "ember")


def _builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("jarvis_build_pets", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("pets-build")
    _builder().build_all(out)
    return out


def test_the_builtin_pets_ship() -> None:
    ids = [m.id for m in list_pets() if m.builtin]
    assert ids[0] == DEFAULT_PET_ID
    assert sorted(ids) == sorted(EXPECTED_PETS)


@pytest.mark.parametrize("pet_id", EXPECTED_PETS)
def test_builtin_pet_is_complete(pet_id: str) -> None:
    pack = load_pet(builtin_root() / pet_id, builtin=True)
    manifest = pack.manifest
    assert manifest.frame_size == 48
    assert set(manifest.animations) == set(PET_STATES), "every state is drawn, none borrowed"
    assert manifest.name and manifest.description.endswith(".")
    assert len(manifest.description) <= 140
    for state in PET_STATES:
        assert pack.frames[state], state
        for frame in pack.frames[state]:
            assert frame.getchannel("A").getbbox() is not None, f"{state} has an empty frame"
            raw = frame.tobytes()
            opaque = {raw[i : i + 3] for i in range(0, len(raw), 4) if raw[i + 3] == 255}
            assert bytes(COLOR_KEY) not in opaque


@pytest.mark.parametrize("pet_id", EXPECTED_PETS)
def test_states_look_different(pet_id: str) -> None:
    """Every state's first frame differs from idle's, so each reads on its own."""
    pack = load_pet(builtin_root() / pet_id, builtin=True)
    idle = pack.frames["idle"][0].tobytes()
    for state in PET_STATES[1:]:
        assert pack.frames[state][0].tobytes() != idle, state


def _pixels(path: Path) -> tuple[tuple[int, int], bytes]:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        return rgba.size, rgba.tobytes()


@pytest.mark.parametrize("pet_id", EXPECTED_PETS)
def test_committed_pet_matches_the_generator(generated: Path, pet_id: str) -> None:
    # Pixels and manifest text, not PNG bytes: the encoder's output may differ
    # across Pillow / zlib versions while the art stays identical.
    ours = PACKAGE_DIR / "builtin" / pet_id
    fresh = generated / "builtin" / pet_id
    assert _pixels(ours / "sheet.png") == _pixels(fresh / "sheet.png"), (
        f"{pet_id}: run `python scripts/pets/build_pets.py` and commit the result"
    )
    assert (ours / "pet.json").read_text(encoding="utf-8") == (fresh / "pet.json").read_text(
        encoding="utf-8"
    )
    if (fresh / "acts.png").exists():
        assert _pixels(ours / "acts.png") == _pixels(fresh / "acts.png"), (
            f"{pet_id}: run `python scripts/pets/build_pets.py` and commit the result"
        )


def test_committed_template_matches_the_generator(generated: Path) -> None:
    ours = PACKAGE_DIR / "template"
    fresh = generated / "template"
    assert _pixels(ours / "template.png") == _pixels(fresh / "template.png")
    ours_manifest = json.loads((ours / "template.json").read_text(encoding="utf-8"))
    assert ours_manifest == json.loads((fresh / "template.json").read_text(encoding="utf-8"))


def test_template_cells_read_as_empty() -> None:
    """An untouched template cell must not count as a drawn frame on upload."""
    with Image.open(PACKAGE_DIR / "template" / "template.png") as image:
        alpha = image.convert("RGBA").getchannel("A")
    assert max(alpha.tobytes()) < 128


@pytest.mark.parametrize("pet_id", EXPECTED_PETS)
def test_the_renderer_conventions_hold(pet_id: str) -> None:
    """Idle blinks on its last cell every few breaths; talking opens up a row."""
    pack = load_pet(builtin_root() / pet_id, builtin=True)
    idle = pack.manifest.animations["idle"]
    assert idle.loop and idle.accent_frames == 1 and idle.accent_every >= 2
    assert pack.manifest.animations["talking"].frames == 4
    # Closed to widest: every talking frame differs from the one before it.
    talking = pack.frames["talking"]
    for before, after in zip(talking, talking[1:], strict=False):
        assert before.tobytes() != after.tobytes()


@pytest.mark.parametrize("pet_id", EXPECTED_PETS)
def test_every_builtin_pet_has_idle_acts(pet_id: str) -> None:
    """Now and then an idle pet does something on its own (docs/pets.md)."""
    pack = load_pet(builtin_root() / pet_id, builtin=True)
    assert 4 <= len(pack.acts) <= 12
    idle = pack.frames["idle"][0].tobytes()
    for name, spec in pack.manifest.acts.items():
        assert not spec.loop
        frames = pack.acts[name]
        assert len(frames) == spec.frames >= 2
        for frame in frames:
            assert frame.getchannel("A").getbbox() is not None, f"{name} has an empty frame"
        assert any(frame.tobytes() != idle for frame in frames), f"{name} never moves"
