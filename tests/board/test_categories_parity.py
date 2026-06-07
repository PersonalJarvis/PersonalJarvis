"""Five-layer-enum parity guard for the Board usage categories.

The six category keys are a wire-format vocabulary shared by the Python
aggregator/store, the Pydantic route models, and the TypeScript frontend. This
test fails the build the moment the Python source and the TS mirror drift —
the BUG-008 class this project has hit four times. See
``docs/anti-drift-three-layer.md``.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.board.categories import BOARD_CATEGORY_KEYS

_TS_MIRROR = (
    Path(__file__).resolve().parents[2]
    / "jarvis" / "ui" / "web" / "frontend" / "src" / "lib" / "boardCategories.ts"
)


def _parse_ts_keys(text: str) -> list[str]:
    match = re.search(
        r"BOARD_CATEGORY_KEYS\s*=\s*\[(?P<body>.*?)\]\s*as const",
        text,
        re.DOTALL,
    )
    assert match, "Could not find BOARD_CATEGORY_KEYS array in the TS mirror"
    return re.findall(r'["\']([a-z_]+)["\']', match.group("body"))


def test_ts_mirror_matches_python_source() -> None:
    assert _TS_MIRROR.exists(), f"TS mirror missing at {_TS_MIRROR}"
    ts_keys = _parse_ts_keys(_TS_MIRROR.read_text(encoding="utf-8"))
    assert tuple(ts_keys) == BOARD_CATEGORY_KEYS, (
        "boardCategories.ts BOARD_CATEGORY_KEYS drifted from "
        "jarvis/board/categories.py — keep both in lock-step."
    )


def test_every_key_has_ts_meta_and_label() -> None:
    """Each key must have a CATEGORY_META entry and an i18n label reference."""
    text = _TS_MIRROR.read_text(encoding="utf-8")
    for key in BOARD_CATEGORY_KEYS:
        assert re.search(rf"\b{key}:\s*{{", text), f"CATEGORY_META missing '{key}'"

    en_locale = (
        _TS_MIRROR.parents[1] / "i18n" / "locales" / "en.json"
    ).read_text(encoding="utf-8")
    for key in BOARD_CATEGORY_KEYS:
        assert f'"{key}"' in en_locale, f"en.json board_view.category missing '{key}'"
