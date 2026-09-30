"""Config contract of the desktop pet (docs/pets.md).

Pins the four keys the pet adds — ``[ui] pet_id / pet_scale / pet_bubble`` and
``[trigger] hotkey_pet_toggle`` — their sanitizing on load, their TOML writers,
and the claims the shipped shortcut's comment makes: it validates on every
platform and collides with no other shortcut the app ships.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from jarvis.appshot.hotkey import BOTH_ALT, normalize_hotkey
from jarvis.control.wiring import DEFAULT_KILL_HOTKEY
from jarvis.core import config_writer
from jarvis.core.config import (
    AppshotConfig,
    TriggerConfig,
    UIConfig,
    clamp_pet_scale,
    normalize_pet_id,
)
from jarvis.trigger.hotkey import combos_collide, normalized_combo_tokens, validate_hotkey
from jarvis.ui.pets.manifest import BUILTIN_ID_RE, USER_ID_RE
from jarvis.ui.pets.states import DEFAULT_PET_ID, NO_PET_ID

#: The Jarvis X keys (one per pane) — not configurable here, but a pet
#: shortcut that fired inside one of them would be a collision all the same.
_JARVIS_X_KEYS = tuple(f"ctrl+shift+{n}" for n in range(1, 7))


# ---------------------------------------------------------------------------
# [ui] pet_id / pet_scale / pet_bubble
# ---------------------------------------------------------------------------


def test_defaults() -> None:
    ui = UIConfig()
    assert ui.pet_id == DEFAULT_PET_ID
    assert ui.pet_scale == 1.0
    assert ui.pet_bubble is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("gigi", "gigi"),
        ("  Miso ", "miso"),
        ("none", NO_PET_ID),
        ("NONE", NO_PET_ID),
        ("u0123456789abcdef", "u0123456789abcdef"),
        # Anything that is not id-shaped falls back to the default pet: a
        # corrupt jarvis.toml must never brick the load.
        ("../../etc", DEFAULT_PET_ID),
        ("C:\\pets\\gigi", DEFAULT_PET_ID),
        ("", DEFAULT_PET_ID),
        ("a" * 33, DEFAULT_PET_ID),
        (7, DEFAULT_PET_ID),
        (None, DEFAULT_PET_ID),
    ],
)
def test_pet_id_is_sanitized_on_load(raw: object, expected: str) -> None:
    assert UIConfig(pet_id=raw).pet_id == expected


def test_pet_id_shape_matches_the_manifest_rules() -> None:
    """One id shape across the config, the loader and the routes (AP-4)."""
    for candidate in ("gigi", "bolt-2", "u0123456789abcdef", "none", "a" * 32):
        assert normalize_pet_id(candidate) == candidate
        assert BUILTIN_ID_RE.fullmatch(candidate)
    for candidate in ("-gigi", "gi gi", "gigi.png", "a" * 33, "../x", "Gigi/"):
        assert normalize_pet_id(candidate) is None
        assert not BUILTIN_ID_RE.fullmatch(candidate.lower())
    assert USER_ID_RE.fullmatch("u0123456789abcdef")


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1.0, 1.0),
        ("1.5", 1.5),
        (0.1, 0.5),
        (9, 2.0),
        ("big", 1.0),
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (None, 1.0),
    ],
)
def test_pet_scale_is_clamped(raw: object, expected: float) -> None:
    assert UIConfig(pet_scale=raw).pet_scale == expected
    assert clamp_pet_scale(raw) == expected


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _ui_table(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("ui", {})


def test_writers_persist_the_three_ui_keys(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text('[ui]\norb_style = "pet"  # kept\n', encoding="utf-8")

    config_writer.set_pet_id("Miso", path=toml)
    config_writer.set_pet_scale(3.0, path=toml)
    config_writer.set_pet_bubble(False, path=toml)

    ui = _ui_table(toml)
    assert ui["pet_id"] == "miso"
    assert ui["pet_scale"] == 2.0  # clamped before it reaches the disk
    assert ui["pet_bubble"] is False
    assert ui["orb_style"] == "pet"
    assert "# kept" in toml.read_text(encoding="utf-8")  # comments survive


def test_set_pet_scale_never_writes_a_non_finite_value(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("", encoding="utf-8")
    config_writer.set_pet_scale(float("nan"), path=toml)
    assert _ui_table(toml)["pet_scale"] == 1.0


def test_set_pet_id_refuses_a_value_that_is_not_an_id(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        config_writer.set_pet_id("../../somewhere", path=toml)
    assert "pet_id" not in _ui_table(toml)


# ---------------------------------------------------------------------------
# [trigger] hotkey_pet_toggle
# ---------------------------------------------------------------------------


def test_pet_toggle_is_a_keybind_action() -> None:
    assert "pet_toggle" in config_writer.KEYBIND_ACTIONS
    assert config_writer.KEYBIND_TOML_KEY["pet_toggle"] == "hotkey_pet_toggle"
    assert TriggerConfig().hotkey_pet_toggle == "alt+win+p"


def test_set_keybind_persists_the_pet_toggle(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("", encoding="utf-8")
    config_writer.set_keybind("pet_toggle", "ctrl+alt+shift+p", path=toml)
    trigger = tomllib.loads(toml.read_text(encoding="utf-8"))["trigger"]
    assert trigger["hotkey_pet_toggle"] == "ctrl+alt+shift+p"


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_shipped_pet_toggle_validates_without_caution(platform: str) -> None:
    verdict = validate_hotkey(TriggerConfig().hotkey_pet_toggle, platform=platform)
    assert verdict.ok is True, verdict.reason
    assert verdict.cautions == (), verdict.cautions


def _other_shipped_shortcuts() -> dict[str, str]:
    trigger = TriggerConfig()
    shortcuts = {
        field: getattr(trigger, field)
        for field in config_writer.KEYBIND_TOML_KEY.values()
        if field != "hotkey_pet_toggle"
    }
    shortcuts["kill_switch"] = DEFAULT_KILL_HOTKEY
    shortcuts.update({f"jarvis_x_{n}": combo for n, combo in enumerate(_JARVIS_X_KEYS, 1)})
    return shortcuts


def test_shipped_pet_toggle_collides_with_no_other_shortcut() -> None:
    """Neither the same key set nor a subset / superset of any shipped combo."""
    pet = TriggerConfig().hotkey_pet_toggle
    others = _other_shipped_shortcuts()
    assert "hotkey_call" in others and "hotkey_paste_last" in others
    for name, combo in others.items():
        assert combo, name
        assert not combos_collide(pet, combo), f"{pet} collides with {name}={combo}"


def test_shipped_pet_toggle_cannot_fire_the_both_alt_appshot_gesture() -> None:
    """``alt+alt`` is watched as BOTH Alt keys down; the pet holds only one.

    ``combos_collide`` cannot judge this pair (the shared backends fold both
    Alt keys into one token), so the gesture's own rule is checked instead.
    """
    pet = TriggerConfig().hotkey_pet_toggle
    assert normalize_hotkey(AppshotConfig().hotkey) == BOTH_ALT
    assert normalize_hotkey(pet) != BOTH_ALT
    assert [part for part in pet.split("+") if "alt" in part] == ["alt"]
    assert "window" in normalized_combo_tokens(pet)  # win → Command on macOS


def test_example_config_documents_the_pet_keys() -> None:
    example = Path(__file__).resolve().parents[3] / "jarvis.toml.example"
    data = tomllib.loads(example.read_text(encoding="utf-8"))
    assert data["ui"]["pet_id"] == UIConfig().pet_id
    assert data["ui"]["pet_scale"] == UIConfig().pet_scale
    assert data["ui"]["pet_bubble"] == UIConfig().pet_bubble
    assert data["trigger"]["hotkey_pet_toggle"] == TriggerConfig().hotkey_pet_toggle
