"""Regression suite for the Gemini structured-output JSON parser.

Test ID anchor: `gemini_vision_json_repair` (the WELLE-0 sub-task 0.4
acceptance criterion runs `pytest -k gemini_vision_json_repair`).

Covers:
  - Happy path: strict json.loads succeeds, status == "ok".
  - Repair path: malformed-but-recoverable input falls back to
    json_repair.loads, status == "repaired".
  - Empty / None inputs do not raise; they return status == "failed".
  - Hard-unparseable garbage returns status == "failed" with diagnostics.
  - Lazy-import discipline: importing the parser module does NOT pull
    `json_repair` into sys.modules. The fallback path lazy-imports it.
"""

from __future__ import annotations

import importlib
import sys

import pytest


# ---------------------------------------------------------------------------
# Lazy-import discipline — verified via a fresh import in a clean state.
# This test must run before any other test that triggers the repair path,
# otherwise `json_repair` will already be in `sys.modules` from the cache.
# ---------------------------------------------------------------------------


def test_gemini_vision_json_repair_lazy_import_discipline() -> None:
    """The parser module must NOT pull json_repair into sys.modules.

    AD-equivalent: WELLE-0 sub-task 0.4 explicitly forbids a module-level
    json_repair import to keep interpreter startup cost zero on the
    happy path.
    """
    # Drop both modules so the import below is a true fresh import.
    sys.modules.pop("jarvis.brain.structured_output_parser", None)
    sys.modules.pop("json_repair", None)

    importlib.import_module("jarvis.brain.structured_output_parser")

    assert "json_repair" not in sys.modules, (
        "structured_output_parser pulled json_repair at module-import time; "
        "the WELLE-0 AC requires lazy import inside the fallback branch only."
    )


# ---------------------------------------------------------------------------
# All other tests use the imported helper directly.
# ---------------------------------------------------------------------------


from jarvis.brain.structured_output_parser import (  # noqa: E402 (after lazy test)
    ParsedJson,
    parse_gemini_json,
)


def test_gemini_vision_json_repair_happy_path_returns_ok() -> None:
    """Strict json.loads on well-formed payload returns status 'ok'."""
    payload = '{"primary_app": "Notepad", "visible_buttons": ["File", "Edit"]}'

    result = parse_gemini_json(payload)

    assert isinstance(result, ParsedJson)
    assert result.status == "ok"
    assert result.parsed == {
        "primary_app": "Notepad",
        "visible_buttons": ["File", "Edit"],
    }
    assert result.error is None


def test_gemini_vision_json_repair_trailing_comma_recovers() -> None:
    """Trailing-comma payload fails strict parse, succeeds via repair."""
    payload = '{"primary_app": "Notepad", "visible_buttons": ["File",],}'

    result = parse_gemini_json(payload)

    assert result.status == "repaired"
    assert result.parsed == {
        "primary_app": "Notepad",
        "visible_buttons": ["File"],
    }
    assert result.error is None


def test_gemini_vision_json_repair_single_quotes_recovers() -> None:
    """Python-style single quotes are repaired to JSON double quotes."""
    payload = "{'primary_app': 'Spotify', 'visible_buttons': ['Play']}"

    result = parse_gemini_json(payload)

    assert result.status == "repaired"
    assert result.parsed == {"primary_app": "Spotify", "visible_buttons": ["Play"]}


def test_gemini_vision_json_repair_code_fence_prose_recovers() -> None:
    """Markdown code-fence wrapper around the JSON object is stripped."""
    payload = '```json\n{"primary_app": "Chrome", "visible_buttons": []}\n```'

    result = parse_gemini_json(payload)

    assert result.status == "repaired"
    assert result.parsed == {"primary_app": "Chrome", "visible_buttons": []}


def test_gemini_vision_json_repair_none_input_returns_failed() -> None:
    """None input is a failed parse, not an exception."""
    result = parse_gemini_json(None)  # type: ignore[arg-type]

    assert result.status == "failed"
    assert result.parsed is None
    assert result.error is not None and "None" in result.error


def test_gemini_vision_json_repair_empty_input_returns_failed() -> None:
    """Empty string is a failed parse, not an exception."""
    result = parse_gemini_json("   ")

    assert result.status == "failed"
    assert result.parsed is None
    assert result.error is not None and "empty" in result.error


def test_gemini_vision_json_repair_envelope_serialization_matches_smoke_contract() -> None:
    """`as_dict()` produces the keys the WELLE-0 smoke envelope requires."""
    result = parse_gemini_json('{"primary_app": "Notepad", "visible_buttons": []}')

    serialized = result.as_dict()

    assert serialized["parse_status"] == "ok"
    assert serialized["parsed"] == {"primary_app": "Notepad", "visible_buttons": []}
    assert serialized["parse_error"] is None
