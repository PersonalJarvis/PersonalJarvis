"""Parity guard for the persisted voice-session mode vocabulary."""
from __future__ import annotations

import json
import re
from pathlib import Path

from jarvis.sessions.constants import VOICE_MODES
from jarvis.sessions.models import KNOWN_VOICE_MODES

REPO_ROOT = Path(__file__).resolve().parents[3]
TYPES_TS = REPO_ROOT / "jarvis/ui/web/frontend/src/components/sessions/types.ts"
SCHEMA_SQL = REPO_ROOT / "jarvis/sessions/schema.sql"
LOCALES_DIR = REPO_ROOT / "jarvis/ui/web/frontend/src/i18n/locales"


def _expected() -> set[str]:
    return set(VOICE_MODES)


def test_models_known_set_matches_constants_tuple() -> None:
    assert set(KNOWN_VOICE_MODES) == _expected()


def test_types_ts_known_set_matches_constants_tuple() -> None:
    text = TYPES_TS.read_text(encoding="utf-8")
    block = re.search(
        r"export\s+const\s+KNOWN_VOICE_MODES\s*=\s*\[([\s\S]+?)\]\s*as\s+const",
        text,
    )
    assert block is not None, "could not find KNOWN_VOICE_MODES in types.ts"
    found = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert found == _expected()


def test_schema_documents_every_known_mode() -> None:
    text = SCHEMA_SQL.read_text(encoding="utf-8")
    declaration = re.search(
        r"voice_mode\s+TEXT[^\n]*--\s*([^;\n]+)",
        text,
    )
    assert declaration is not None, "voice_mode schema vocabulary is missing"
    found = {value.strip() for value in declaration.group(1).split("|")}
    assert found == _expected()


def test_every_locale_labels_every_known_mode() -> None:
    for locale in ("en", "de", "es"):
        document = json.loads(
            (LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8")
        )
        labels = document["voice_mode"]
        assert set(labels) - {"label"} == _expected(), f"{locale} locale drift"
        assert all(labels[mode].strip() for mode in _expected())
