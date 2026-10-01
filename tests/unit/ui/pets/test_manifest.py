"""The ``pet.json`` rules of the ``jarvis-pet/1`` format (docs/pets.md)."""

from __future__ import annotations

import copy

import pytest

from jarvis.ui.pets.manifest import (
    MAX_DESCRIPTION_CHARS,
    MAX_NAME_CHARS,
    AnimationSpec,
    PetManifestError,
    check_cells,
    check_sheet_size,
    parse_manifest,
)
from jarvis.ui.pets.states import PET_FORMAT, PET_STATES, STATE_FALLBACKS

USER_ID = "u0123456789abcdef"


def _data(**overrides: object) -> dict:
    data: dict = {
        "format": PET_FORMAT,
        "id": "gigi",
        "name": "Gigi",
        "description": "A ghost.",
        "frame_size": 48,
        "sheet": "sheet.png",
        "animations": {
            "idle": {"row": 0, "frames": 4, "fps": 4, "loop": True},
            "talking": {"row": 3, "frames": 4, "fps": 10},
            "success": {"row": 4, "frames": 6, "fps": 10},
        },
    }
    data.update(overrides)
    return data


def _animations(**changes: object) -> dict:
    animations = copy.deepcopy(_data()["animations"])
    animations["idle"].update(changes)
    return animations


def test_valid_manifest_parses() -> None:
    manifest = parse_manifest(_data(), builtin=True)
    assert manifest.id == "gigi"
    assert manifest.name == "Gigi"
    assert manifest.frame_size == 48
    assert manifest.builtin is True
    assert manifest.animations["idle"] == AnimationSpec(row=0, frames=4, fps=4, loop=True)


def test_loop_defaults_to_false_for_one_shots_only() -> None:
    manifest = parse_manifest(_data(), builtin=True)
    assert manifest.animations["talking"].loop is True
    assert manifest.animations["success"].loop is False


def test_name_and_description_are_trimmed() -> None:
    manifest = parse_manifest(_data(name="  Gigi  ", description="  hi  "), builtin=True)
    assert (manifest.name, manifest.description) == ("Gigi", "hi")


def test_user_id_rules() -> None:
    assert parse_manifest(_data(id=USER_ID), builtin=False).id == USER_ID
    with pytest.raises(PetManifestError):
        parse_manifest(_data(id="gigi"), builtin=False)
    # A built-in may never look like a user id (the two namespaces stay apart).
    with pytest.raises(PetManifestError):
        parse_manifest(_data(id=USER_ID), builtin=True)


@pytest.mark.parametrize(
    "overrides",
    [
        {"format": "jarvis-pet/2"},
        {"id": "none"},
        {"id": "Gigi"},
        {"id": "../evil"},
        {"id": 7},
        {"name": ""},
        {"name": "x" * (MAX_NAME_CHARS + 1)},
        {"description": 5},
        {"description": "x" * (MAX_DESCRIPTION_CHARS + 1)},
        {"frame_size": 40},
        {"frame_size": True},
        {"sheet": "../sheet.png"},
        {"sheet": "sub/sheet.png"},
        {"sheet": "sheet.gif"},
        {"animations": {}},
        {"animations": {"talking": {"row": 0, "frames": 1, "fps": 4}}},
        {"animations": {"idle": {"row": 0, "frames": 1, "fps": 4}, "dancing": {}}},
    ],
)
def test_manifest_rule_rejections(overrides: dict) -> None:
    with pytest.raises(PetManifestError):
        parse_manifest(_data(**overrides), builtin=True)


@pytest.mark.parametrize(
    "changes",
    [
        {"row": -1},
        {"frames": 0},
        {"frames": 9},
        {"fps": 0},
        {"fps": 13},
        {"frames": True},
        {"loop": "yes"},
        {"row": 1.5},
    ],
)
def test_animation_rule_rejections(changes: dict) -> None:
    with pytest.raises(PetManifestError):
        parse_manifest(_data(animations=_animations(**changes)), builtin=True)


def test_not_an_object_is_rejected() -> None:
    with pytest.raises(PetManifestError):
        parse_manifest(["not", "a", "manifest"], builtin=True)


def test_errors_are_readable_sentences() -> None:
    with pytest.raises(PetManifestError) as info:
        parse_manifest(_data(frame_size=40), builtin=True)
    assert str(info.value).endswith(".")
    assert "32" in str(info.value) and "64" in str(info.value)


def test_spec_for_resolves_fallbacks() -> None:
    manifest = parse_manifest(_data(), builtin=True)
    assert manifest.spec_for("idle")[0] == "idle"
    assert manifest.spec_for("talking")[0] == "talking"
    # thinking -> listening -> idle (neither declared)
    assert manifest.spec_for("thinking")[0] == "idle"
    assert manifest.spec_for("sleeping")[0] == STATE_FALLBACKS["sleeping"]
    for state in PET_STATES:
        resolved, spec = manifest.spec_for(state)
        assert resolved in manifest.animations
        assert spec is manifest.animations[resolved]


def test_spec_for_rejects_unknown_state() -> None:
    with pytest.raises(KeyError):
        parse_manifest(_data(), builtin=True).spec_for("dancing")


def test_to_json_round_trips() -> None:
    manifest = parse_manifest(_data(), builtin=True)
    again = parse_manifest(manifest.to_json(), builtin=True)
    assert again == manifest
    assert "builtin" not in manifest.to_json()


def test_check_cells_rejects_rows_outside_the_sheet() -> None:
    manifest = parse_manifest(_data(), builtin=True)
    check_cells(manifest, 48 * 6, 48 * 5)  # rows 0..4, six columns: fits
    with pytest.raises(PetManifestError):
        check_cells(manifest, 48 * 6, 48 * 4)  # success is row 4
    with pytest.raises(PetManifestError):
        check_cells(manifest, 48 * 5, 48 * 5)  # success has six frames


def test_check_sheet_size_limits() -> None:
    check_sheet_size(512, 512)
    for width, height in ((0, 10), (513, 10), (10, 513)):
        with pytest.raises(PetManifestError):
            check_sheet_size(width, height)


# --- the optional accent (a blink every few loops) -------------------------------


def test_an_accent_parses_and_round_trips() -> None:
    animations = _animations(frames=8, accent_frames=1, accent_every=3)
    manifest = parse_manifest(_data(animations=animations), builtin=True)
    idle = manifest.animations["idle"]
    assert (idle.accent_frames, idle.accent_every) == (1, 3)
    assert manifest.to_json()["animations"]["idle"]["accent_frames"] == 1
    assert parse_manifest(manifest.to_json(), builtin=True) == manifest


def test_no_accent_keeps_the_file_shape() -> None:
    manifest = parse_manifest(_data(), builtin=True)
    assert manifest.animations["idle"].accent_frames == 0
    assert "accent_frames" not in manifest.to_json()["animations"]["idle"]


@pytest.mark.parametrize(
    "changes",
    [
        {"accent_frames": 4},  # the whole row: nothing ordinary left
        {"accent_frames": -1, "accent_every": 2},
        {"accent_frames": 1, "accent_every": 0},
        {"accent_frames": 1, "accent_every": 13},
        {"accent_frames": 1},  # needs both keys
        {"accent_frames": True, "accent_every": 2},
    ],
)
def test_accent_rule_rejections(changes: dict) -> None:
    with pytest.raises(PetManifestError):
        parse_manifest(_data(animations=_animations(**changes)), builtin=True)


def test_an_accent_needs_a_looping_row() -> None:
    animations = _animations()
    animations["success"].update({"accent_frames": 1, "accent_every": 2})
    with pytest.raises(PetManifestError):
        parse_manifest(_data(animations=animations), builtin=True)
