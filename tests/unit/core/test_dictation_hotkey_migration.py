"""One-time backfill of the dictation shortcuts (BUG-010 config drift).

``hotkey_dictate`` shipped as ``""`` for a while, so every install from that
period persisted an empty value — and a persisted empty value beats a code
default. When the default became a real combo, those installs kept reading "no
key assigned" while ``hotkey_dictate_toggle``, which was never persisted, WAS
armed from its new default. Same feature, two answers, decided by which key
happened to be in the file.

The fix cannot be "treat empty as use-the-default": that would make the Clear
button impossible, and an unbound shortcut is a state the user is entitled to.
So the two cases are told apart by a marker, and the tests below pin both
halves of that contract — the backfill happens exactly once, and a deliberate
Clear survives every restart afterwards.
"""

from __future__ import annotations

from pathlib import Path

import tomlkit

from jarvis.core import config_writer
from jarvis.core.config import TriggerConfig
from jarvis.core.config_writer import (
    DICTATION_HOTKEY_MIGRATION_KEY,
    migrate_dictation_hotkey_defaults,
)


def _trigger(path: Path) -> dict:
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    return dict(doc.get("trigger") or {})


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# The drift it exists to heal
# ----------------------------------------------------------------------


def test_a_stale_empty_shortcut_gets_the_shipped_default(tmp_path: Path) -> None:
    """The live case: an install that predates the default reads "unbound"."""
    path = _write(tmp_path / "jarvis.toml", '[trigger]\nhotkey_dictate = ""\n')

    assert migrate_dictation_hotkey_defaults(path=path) is True

    trigger = _trigger(path)
    assert trigger["hotkey_dictate"] == TriggerConfig().hotkey_dictate
    assert trigger[DICTATION_HOTKEY_MIGRATION_KEY] is True


def test_the_hands_free_shortcut_is_healed_the_same_way(tmp_path: Path) -> None:
    path = _write(tmp_path / "jarvis.toml", '[trigger]\nhotkey_dictate_toggle = ""\n')

    assert migrate_dictation_hotkey_defaults(path=path) is True

    healed = _trigger(path)["hotkey_dictate_toggle"]
    assert healed == TriggerConfig().hotkey_dictate_toggle


def test_it_never_touches_a_shortcut_the_user_already_chose(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "jarvis.toml",
        '[trigger]\nhotkey_dictate = "ctrl+shift+d"\nhotkey_dictate_toggle = ""\n',
    )

    migrate_dictation_hotkey_defaults(path=path)

    assert _trigger(path)["hotkey_dictate"] == "ctrl+shift+d"


def test_it_leaves_the_other_keybinds_alone(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "jarvis.toml",
        '[trigger]\nhotkey_call = "j+n"\nhotkey_hangup = ""\nhotkey_dictate = ""\n',
    )

    migrate_dictation_hotkey_defaults(path=path)

    trigger = _trigger(path)
    assert trigger["hotkey_call"] == "j+n"
    # A cleared HANGUP is not this migration's business — only the two
    # dictation rows carry the shipped-empty drift.
    assert trigger["hotkey_hangup"] == ""


# ----------------------------------------------------------------------
# Exactly once, and a Clear is forever
# ----------------------------------------------------------------------


def test_it_runs_exactly_once(tmp_path: Path) -> None:
    path = _write(tmp_path / "jarvis.toml", '[trigger]\nhotkey_dictate = ""\n')
    assert migrate_dictation_hotkey_defaults(path=path) is True

    # The user clears the row again, deliberately.
    _write(
        tmp_path / "jarvis.toml",
        f'[trigger]\nhotkey_dictate = ""\n{DICTATION_HOTKEY_MIGRATION_KEY} = true\n',
    )

    assert migrate_dictation_hotkey_defaults(path=path) is False
    assert _trigger(path)["hotkey_dictate"] == ""


def test_clearing_a_shortcut_stamps_the_marker_so_it_stays_cleared(
    tmp_path: Path,
) -> None:
    """The half a marker in the file alone cannot cover.

    On an install where the keys were never persisted the migration has nothing
    to consider and writes nothing — so a Clear performed there would be
    re-armed by the next boot unless the SAVE itself stamps the marker.
    """
    path = _write(tmp_path / "jarvis.toml", '[trigger]\nhotkey_call = "f3+f4"\n')

    config_writer.set_keybind("dictate", "", path=path)

    trigger = _trigger(path)
    assert trigger["hotkey_dictate"] == ""
    assert trigger[DICTATION_HOTKEY_MIGRATION_KEY] is True

    assert migrate_dictation_hotkey_defaults(path=path) is False
    assert _trigger(path)["hotkey_dictate"] == ""


def test_saving_a_non_dictation_keybind_does_not_stamp_the_marker(
    tmp_path: Path,
) -> None:
    """Saving Call says nothing about whether the dictation rows were seen."""
    path = _write(tmp_path / "jarvis.toml", "[trigger]\n")

    config_writer.set_keybind("call", "f7+f8", path=path)

    assert DICTATION_HOTKEY_MIGRATION_KEY not in _trigger(path)


# ----------------------------------------------------------------------
# It must never rewrite a file that has nothing to heal
# ----------------------------------------------------------------------


def test_a_config_with_nothing_to_heal_is_left_byte_identical(
    tmp_path: Path,
) -> None:
    """``load_config`` reaches this. A plain read must not mutate the file."""
    body = '[trigger]\nhotkey_call = "f3+f4"\n\n[brain]\nprimary = "gemini"\n'
    path = _write(tmp_path / "jarvis.toml", body)

    assert migrate_dictation_hotkey_defaults(path=path) is False
    assert path.read_text(encoding="utf-8") == body


def test_a_config_without_a_trigger_table_is_left_alone(tmp_path: Path) -> None:
    body = '[brain]\nprimary = "gemini"\n'
    path = _write(tmp_path / "jarvis.toml", body)

    assert migrate_dictation_hotkey_defaults(path=path) is False
    assert path.read_text(encoding="utf-8") == body


def test_a_missing_file_is_a_quiet_no_op(tmp_path: Path) -> None:
    assert migrate_dictation_hotkey_defaults(path=tmp_path / "absent.toml") is False


def test_unparsable_toml_never_raises(tmp_path: Path) -> None:
    """A boot heal that can raise is a boot that can fail."""
    path = _write(tmp_path / "jarvis.toml", "[trigger\nhotkey_dictate = ")

    assert migrate_dictation_hotkey_defaults(path=path) is False


def test_a_bom_survives_the_rewrite(tmp_path: Path) -> None:
    """AP-7: a stripped BOM is a config the backend will not boot from."""
    path = tmp_path / "jarvis.toml"
    path.write_text('﻿[trigger]\nhotkey_dictate = ""\n', encoding="utf-8")

    assert migrate_dictation_hotkey_defaults(path=path) is True
    assert path.read_text(encoding="utf-8").startswith("﻿")


def test_comments_and_unrelated_sections_survive(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "jarvis.toml",
        '# keep me\n[trigger]\nhotkey_dictate = ""\n\n[brain]\nprimary = "gemini"\n',
    )

    migrate_dictation_hotkey_defaults(path=path)

    written = path.read_text(encoding="utf-8")
    assert "# keep me" in written
    assert 'primary = "gemini"' in written


# ----------------------------------------------------------------------
# It must not create a collision the keybind route would refuse
# ----------------------------------------------------------------------


def test_a_default_that_would_collide_is_skipped_not_forced(tmp_path: Path) -> None:
    """The polling backend fires on subsets, so a backfilled default that
    overlaps an existing shortcut would arm two actions on one press — and the
    keybind route would refuse to save the same pair by hand."""
    collides = TriggerConfig().hotkey_dictate
    path = _write(
        tmp_path / "jarvis.toml",
        f'[trigger]\nhotkey_call = "{collides}"\nhotkey_dictate = ""\n',
    )

    assert migrate_dictation_hotkey_defaults(path=path) is True

    trigger = _trigger(path)
    assert trigger["hotkey_dictate"] == ""
    # Still marked done: retrying it every boot would only re-skip it.
    assert trigger[DICTATION_HOTKEY_MIGRATION_KEY] is True


def test_an_unbound_other_action_is_not_treated_as_a_collision(
    tmp_path: Path,
) -> None:
    """An empty key set is a subset of everything — the same false positive the
    keybind route had to be fixed for."""
    path = _write(
        tmp_path / "jarvis.toml",
        '[trigger]\nhotkey_call = ""\nhotkey_hangup = ""\nhotkey_dictate = ""\n',
    )

    migrate_dictation_hotkey_defaults(path=path)

    assert _trigger(path)["hotkey_dictate"] == TriggerConfig().hotkey_dictate


# ----------------------------------------------------------------------
# The boot hook
# ----------------------------------------------------------------------


def test_load_config_heals_the_resolved_path(
    tmp_path: Path, monkeypatch
) -> None:
    from jarvis.core import config as config_module

    path = _write(tmp_path / "jarvis.toml", '[trigger]\nhotkey_dictate = ""\n')
    monkeypatch.setenv("JARVIS_CONFIG", str(path))
    monkeypatch.setattr(config_module, "_DICTATION_HOTKEY_HEALED", set())

    cfg = config_module.load_config()

    assert cfg.trigger.hotkey_dictate == TriggerConfig().hotkey_dictate
    assert _trigger(path)[DICTATION_HOTKEY_MIGRATION_KEY] is True


def test_load_config_never_rewrites_an_explicitly_named_file(
    tmp_path: Path, monkeypatch
) -> None:
    """A doctor script or a test that names a file gets it READ, not healed."""
    from jarvis.core import config as config_module

    body = '[trigger]\nhotkey_dictate = ""\n'
    path = _write(tmp_path / "jarvis.toml", body)
    monkeypatch.setattr(config_module, "_DICTATION_HOTKEY_HEALED", set())

    config_module.load_config(config_file=path)

    assert path.read_text(encoding="utf-8") == body
