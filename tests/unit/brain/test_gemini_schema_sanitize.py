"""Gemini function-schema sanitizer: strips JSON-schema keywords the google-genai
Schema model (extra="forbid") rejects.

Regression guard for the live 2026-07-23 outage: a connected MCP/plugin tool
shipped a parameter carrying ``propertyNames`` (functionDeclarations[105].
parameters.properties.anchor.propertyNames). Left in, ONE such schema failed the
WHOLE GenerateContent request with a Pydantic "extra_forbidden" validation error,
so the primary brain (Gemini) died and the turn fell through the entire provider
chain (claude 401 → openrouter 400 → openai 429 → anti-silence fallback) — a
Drive question got no real answer for reasons unrelated to Drive.
"""

import json

from jarvis.plugins.brain.gemini import _sanitize_for_gemini


def _flatten(schema: dict) -> str:
    return json.dumps(schema)


def test_property_names_is_stripped_but_type_kept():
    bad = {
        "type": "object",
        "properties": {
            "anchor": {"type": "string", "propertyNames": {"type": "string"}},
        },
    }
    clean = _sanitize_for_gemini(bad)
    assert "propertyNames" not in _flatten(clean)
    # The parameter itself survives — only the unsupported constraint is gone.
    assert clean["properties"]["anchor"]["type"] == "string"


def test_all_object_key_constraints_are_stripped():
    bad = {
        "type": "object",
        "properties": {
            "meta": {
                "type": "object",
                "patternProperties": {"^x": {"type": "string"}},
                "minProperties": 1,
                "maxProperties": 5,
                "unevaluatedProperties": False,
                "dependentRequired": {"a": ["b"]},
                "dependentSchemas": {"a": {"required": ["b"]}},
            },
        },
    }
    flat = _flatten(_sanitize_for_gemini(bad))
    for forbidden in (
        "patternProperties",
        "minProperties",
        "maxProperties",
        "unevaluatedProperties",
        "dependentRequired",
        "dependentSchemas",
    ):
        assert forbidden not in flat, forbidden


def test_sanitizer_recurses_into_nested_and_lists():
    bad = {
        "type": "object",
        "properties": {
            "outer": {
                "type": "array",
                "items": {"type": "object", "propertyNames": {"type": "string"}},
            }
        },
    }
    clean = _sanitize_for_gemini(bad)
    assert "propertyNames" not in _flatten(clean)
    # Structure preserved.
    assert clean["properties"]["outer"]["items"]["type"] == "object"
