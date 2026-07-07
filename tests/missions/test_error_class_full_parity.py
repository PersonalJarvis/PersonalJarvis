"""Cross-layer parity for MISSION_ERROR_CLASSES (AP-4/BUG-008 defense):
Python events <-> voice phrase table (de+en) <-> UI locale files (en/de/es).
The Python<->TS union is guarded in test_mission_error_class_parity.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from jarvis.missions.events import MISSION_ERROR_CLASSES
from jarvis.missions.voice.readback import FAILURE_REASON_PHRASES, Lang

_REPO = Path(__file__).resolve().parents[2]
_LOCALES = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "i18n" / "locales"

# The voice readback surface is scoped to the render API's ``Lang`` literal
# (de/en) — NOT every key that happens to exist in FAILURE_REASON_PHRASES.
# That dict also carries a partial ``es`` entry (two git-setup reason keys,
# unrelated to this plan) that intentionally does not cover the four
# MISSION_ERROR_CLASSES tokens; extending voice error-class phrases to ``es``
# is separate backlog (spec §6). Iterating raw dict keys would make this
# guard depend on that unrelated partial addition instead of the actual
# supported-language contract.
_VOICE_LANGS: tuple[str, ...] = get_args(Lang)


@pytest.mark.parametrize("lang", _VOICE_LANGS)
def test_voice_table_carries_every_error_class(lang: str) -> None:
    missing = MISSION_ERROR_CLASSES - set(FAILURE_REASON_PHRASES[lang])
    assert not missing, f"FAILURE_REASON_PHRASES[{lang!r}] missing {missing}"


@pytest.mark.parametrize("locale", ["en", "de", "es"])
def test_ui_locales_carry_every_error_class(locale: str) -> None:
    data = json.loads((_LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    keys = set(data.get("subagents_view", {}).get("error_class", {}))
    missing = MISSION_ERROR_CLASSES - keys
    assert not missing, f"{locale}.json subagents_view.error_class missing {missing}"
