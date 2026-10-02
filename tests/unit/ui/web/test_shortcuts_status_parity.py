"""AP-4 parity for ``shortcuts_status`` between Python and its TypeScript twin.

The state vocabulary crosses: ``jarvis/trigger/shortcuts_status.py`` (the tuple
and the Pydantic schema), ``GET /api/settings/keybinds`` (the payload), the
``shortcuts_status`` field of ``KeybindsConfig`` in ``hooks/useHotkey.ts`` (the
TS twin) and the i18n copy per state. A state spelled differently on the two
sides renders as a blank row with no error, so the TS union is regex-read here,
asserted non-empty and compared with the Python tuple.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from jarvis.trigger.shortcuts_status import SHORTCUTS_STATES, ShortcutsStatus

_HOTKEY_TS = (
    Path(__file__).resolve().parents[4]
    / "jarvis"
    / "ui"
    / "web"
    / "frontend"
    / "src"
    / "hooks"
    / "useHotkey.ts"
)


def _ts_field() -> str:
    assert _HOTKEY_TS.exists(), f"frontend hook missing: {_HOTKEY_TS}"
    source = _HOTKEY_TS.read_text(encoding="utf-8")
    match = re.search(r"export interface KeybindsConfig\s*\{(?P<body>.*?)\n\}", source, re.DOTALL)
    assert match is not None, "KeybindsConfig interface not found in useHotkey.ts"
    field = re.search(r"shortcuts_status\?:\s*\{(?P<inner>.*?)\};", match.group("body"), re.DOTALL)
    assert field is not None, "shortcuts_status field not found on KeybindsConfig"
    return field.group("inner")


def test_ts_state_union_mirrors_the_python_states() -> None:
    inner = _ts_field()
    union = re.search(r"state:\s*(?P<union>[^;]+);", inner)
    assert union is not None, "state union not found in the shortcuts_status field"
    members = re.findall(r'"([a-z_]+)"', union.group("union"))
    assert members, "parsed no state members from the TS union"
    assert len(members) == len(set(members)), members
    assert tuple(members) == SHORTCUTS_STATES


def test_ts_payload_carries_exactly_the_python_fields() -> None:
    inner = _ts_field()
    ts_fields = set(re.findall(r"^\s*([a-z_]+)\??:", inner, re.MULTILINE))
    assert ts_fields, "parsed no fields from the TS shortcuts_status object"
    assert ts_fields == set(ShortcutsStatus.model_fields)


def test_pydantic_literal_mirrors_the_tuple() -> None:
    assert get_args(ShortcutsStatus.model_fields["state"].annotation) == SHORTCUTS_STATES


def test_the_field_is_optional_in_ts_because_the_two_ends_ship_separately() -> None:
    source = _HOTKEY_TS.read_text(encoding="utf-8")
    assert re.search(r"\bshortcuts_status\?:", source), "the field must stay optional"
