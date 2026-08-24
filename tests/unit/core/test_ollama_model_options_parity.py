"""Parity test for the layers that share the per-model Ollama option vocabulary
(five-layer pattern, AP-4).

1. ``jarvis/core/config.py::OLLAMA_MODEL_OPTION_KEYS``      — the tuple (source of truth)
2. ``jarvis/core/config.py::OllamaModelOptions``            — the Pydantic fields
3. ``jarvis/brain/ollama_profiles.py::BAKEABLE_KEYS``       — a subset the profile bakes
4. ``jarvis/ui/web/frontend/src/lib/ollamaModelOptions.ts`` — the TS const array + interface
5. the route body (``OllamaModelOptionsBody`` in ``local_models_routes.py``)
   — checked when that module exists, so the chunk that adds the routes
   inherits the gate without touching this file.

A drift shows up here as one failing test instead of a knob the sheet offers
and the writer refuses, or a key the writer accepts and the plugin never reads.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

from jarvis.brain.ollama_profiles import BAKEABLE_KEYS
from jarvis.core.config import OLLAMA_MODEL_OPTION_KEYS, OllamaModelOptions

REPO_ROOT = Path(__file__).resolve().parents[3]
TS_FILE = REPO_ROOT / "jarvis/ui/web/frontend/src/lib/ollamaModelOptions.ts"


def _ts_const_array(name: str) -> list[str]:
    src = TS_FILE.read_text(encoding="utf-8")
    match = re.search(rf"export const {name}[^=]*= \[([^\]]*)\] as const;", src)
    assert match, f"{name} const array missing from {TS_FILE.name}"
    return re.findall(r'^\s*"([a-z_]+)",\s*$', match.group(1), flags=re.MULTILINE)


def _ts_interface_keys(name: str) -> list[str]:
    src = TS_FILE.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name} \{{([^}}]*)\}}", src)
    assert match, f"interface {name} missing from {TS_FILE.name}"
    return re.findall(r"^\s*([a-z_]+)\??:", match.group(1), flags=re.MULTILINE)


def test_the_tuple_has_no_duplicates() -> None:
    assert len(set(OLLAMA_MODEL_OPTION_KEYS)) == len(OLLAMA_MODEL_OPTION_KEYS)


def test_pydantic_fields_match_the_tuple_in_order() -> None:
    assert tuple(OllamaModelOptions.model_fields) == OLLAMA_MODEL_OPTION_KEYS


def test_bakeable_keys_are_a_subset_of_the_tuple() -> None:
    assert set(BAKEABLE_KEYS) <= set(OLLAMA_MODEL_OPTION_KEYS)
    # The per-request channel is exactly the complement.
    assert set(OLLAMA_MODEL_OPTION_KEYS) - set(BAKEABLE_KEYS) == {
        "num_predict",
        "temperature",
        "keep_alive",
        "think",
    }


def test_typescript_mirror_matches_the_tuple() -> None:
    assert _ts_const_array("OLLAMA_MODEL_OPTION_KEYS") == list(OLLAMA_MODEL_OPTION_KEYS)
    assert _ts_interface_keys("OllamaModelOptions") == list(OLLAMA_MODEL_OPTION_KEYS)


def test_typescript_bakeable_keys_match() -> None:
    src = TS_FILE.read_text(encoding="utf-8")
    match = re.search(r"export const OLLAMA_BAKEABLE_KEYS[^=]*= \[([^\]]*)\];", src)
    assert match, "OLLAMA_BAKEABLE_KEYS missing from the TS mirror"
    keys = re.findall(r'"([a-z_]+)"', match.group(1))
    assert keys == list(BAKEABLE_KEYS)


def test_route_body_matches_when_the_routes_exist() -> None:
    """Chunk 4 adds ``OllamaModelOptionsBody``; until then this is a no-op."""
    try:
        module = importlib.import_module("jarvis.ui.web.local_models_routes")
    except ImportError:
        return
    body = getattr(module, "OllamaModelOptionsBody", None)
    if body is None:
        return
    assert tuple(body.model_fields) == OLLAMA_MODEL_OPTION_KEYS
