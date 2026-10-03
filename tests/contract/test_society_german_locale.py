"""M5 German gate for the lazy Society locale chunk."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "jarvis" / "ui" / "web" / "frontend" / "src" / "i18n" / "locales" / "society"
_PLACEHOLDER = re.compile(r"\{[^{}]+\}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError(f"duplicate locale key: {key}")
        result[key] = value
    return result


def _load(name: str) -> dict[str, Any]:
    return json.loads(
        (LOCALES / name).read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
    )


def _leaves(value: Any, prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(value, dict):
        return result
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            result.update(_leaves(child, path))
        else:
            assert isinstance(child, str), f"{path} must be a string"
            result[path] = child
    return result


def test_society_german_locale_matches_english_contract() -> None:
    english = _leaves(_load("en.json"))
    german = _leaves(_load("de.json"))

    assert german.keys() == english.keys()
    for key, source in english.items():
        assert set(_PLACEHOLDER.findall(german[key])) == set(
            _PLACEHOLDER.findall(source)
        ), key
