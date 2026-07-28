"""Cross-layer parity guard for the voice keybind action vocabulary (AP-4).

A keybind action is a value that crosses five layers: the ``TriggerConfig``
field that stores the combo, the ``KEYBIND_ACTIONS`` / ``KEYBIND_TOML_KEY``
tables in ``config_writer``, the ``/api/settings/keybinds`` payload built from
them, the TypeScript ``KeybindAction`` union the frontend is written against,
and the ``settings_view.keybinds.<action>_label`` string every locale has to
carry.

Every one of those layers fails SILENTLY on drift, which is what makes this
the enum-drift class that has recurred four times here (BUG-008):

* an action missing from the TS union is simply never rendered — the row for
  the new shortcut does not appear and nothing errors;
* an action whose ``KEYBIND_TOML_KEY`` value is not a real ``TriggerConfig``
  field makes the derived ``defaults`` map fall back to ``""``, so the UI
  offers "Reset to default" and resets to *unbound*;
* a missing locale key renders the raw key on screen
  ("settings_view.keybinds.dictate_toggle_label").

Every parsed set is asserted NON-EMPTY before it is compared, so a regex that
stops matching (a reformat, a rename, a move to another file) fails loudly
instead of passing against an empty set.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jarvis.core.config import TriggerConfig
from jarvis.core.config_writer import KEYBIND_ACTIONS, KEYBIND_TOML_KEY

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FRONTEND = _REPO_ROOT / "jarvis" / "ui" / "web" / "frontend" / "src"
_HOTKEY_TS = _FRONTEND / "hooks" / "useHotkey.ts"
_LOCALES = _FRONTEND / "i18n" / "locales"

#: The locales the product ships. All of them are equal (CLAUDE.md §1) — a key
#: that exists only in English is a bug for every other user, not a nicety.
SUPPORTED_LOCALES = ("de", "en", "es")


def _ts_keybind_actions() -> set[str]:
    """Members of ``export type KeybindAction = "a" | "b";`` in useHotkey.ts."""
    assert _HOTKEY_TS.exists(), f"frontend hook missing: {_HOTKEY_TS}"
    source = _HOTKEY_TS.read_text(encoding="utf-8")
    match = re.search(r"export type KeybindAction\s*=\s*(.+?);", source, re.DOTALL)
    assert match is not None, f"KeybindAction union not found in {_HOTKEY_TS.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def _locale(name: str) -> dict:
    path = _LOCALES / f"{name}.json"
    assert path.exists(), f"locale file missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _nested(data: dict, dotted: str) -> object | None:
    node: object = data
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)  # type: ignore[assignment]
        if node is None:
            return None
    return node


def test_python_keybind_tables_agree_with_each_other() -> None:
    """``KEYBIND_ACTIONS`` and ``KEYBIND_TOML_KEY`` are one vocabulary, not two."""
    assert KEYBIND_ACTIONS, "KEYBIND_ACTIONS is empty"
    assert len(set(KEYBIND_ACTIONS)) == len(KEYBIND_ACTIONS), KEYBIND_ACTIONS
    assert set(KEYBIND_TOML_KEY) == set(KEYBIND_ACTIONS)


def test_every_action_maps_to_a_real_trigger_config_field() -> None:
    """The derived ``defaults`` map is only honest if every field exists.

    ``settings_routes`` builds the defaults with ``getattr(TriggerConfig(), f,
    "")``. A typo'd or removed field would silently degrade to ``""``, so the
    "reset to default" button would unbind the shortcut instead of restoring
    it — a wrong answer that looks like a working feature.
    """
    defaults = TriggerConfig()
    assert KEYBIND_TOML_KEY, "KEYBIND_TOML_KEY is empty"
    for action, field in KEYBIND_TOML_KEY.items():
        assert hasattr(defaults, field), f"{action} -> TriggerConfig.{field} missing"
        assert isinstance(getattr(defaults, field), str), action


def test_ts_keybind_action_union_mirrors_the_python_vocabulary() -> None:
    members = _ts_keybind_actions()
    # Guard against a trivially-green empty/partial parse.
    assert members, f"parsed no KeybindAction members from {_HOTKEY_TS.name}"
    assert len(members) == len(KEYBIND_ACTIONS), members
    assert members == set(KEYBIND_ACTIONS)


def test_every_action_has_a_label_in_every_locale() -> None:
    """A missing label renders the raw i18n key at the user."""
    assert KEYBIND_ACTIONS, "KEYBIND_ACTIONS is empty"
    for name in SUPPORTED_LOCALES:
        data = _locale(name)
        for action in KEYBIND_ACTIONS:
            key = f"settings_view.keybinds.{action}_label"
            value = _nested(data, key)
            assert isinstance(value, str) and value.strip(), f"{name}.json: {key}"
