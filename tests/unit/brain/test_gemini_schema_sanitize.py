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

import pytest

from jarvis.plugins.brain.gemini import _gemini_schema_keys, _sanitize_for_gemini


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


def test_an_unknown_extension_key_is_dropped_whatever_it_is_called():
    """The 2026-08-26 outage: the GitHub MCP server tags parameters with
    ``x-mcp-header``. It was on no block-list, so 81 validation errors killed
    the whole request and the front page's chat answered "I can't reach my
    provider". An allow-list does not need to have heard of it."""
    bad = {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "Repo owner", "x-mcp-header": "owner"},
            "repo": {"type": "string", "x-mcp-header": "repo"},
        },
        "required": ["owner", "repo"],
    }
    clean = _sanitize_for_gemini(bad)
    assert "x-mcp-header" not in _flatten(clean)
    # A stripped tool is not a fix: the parameters and their prose survive.
    assert set(clean["properties"]) == {"owner", "repo"}
    assert clean["properties"]["owner"]["description"] == "Repo owner"
    assert clean["required"] == ["owner", "repo"]
    # And an invented key nobody has seen yet goes the same way.
    invented = _sanitize_for_gemini({"type": "string", "x-vendor-someday": {"a": 1}})
    assert invented == {"type": "string"}


def test_a_property_named_like_a_schema_keyword_survives():
    """Under ``properties`` the keys are names a tool author chose. Judging
    those against the allow-list would delete the parameters themselves — the
    one way an allow-list can be worse than the block-list it replaces."""
    schema = {
        "type": "object",
        "properties": {
            "strict": {"type": "boolean"},
            "propertyNames": {"type": "string"},
            "x-mcp-header": {"type": "string"},
        },
    }
    clean = _sanitize_for_gemini(schema)
    assert set(clean["properties"]) == {"strict", "propertyNames", "x-mcp-header"}
    assert clean["properties"]["strict"]["type"] == "boolean"


def test_a_data_value_is_not_walked_as_a_schema():
    """``example``/``default`` carry the tool author's DATA. A dict in there
    that happens to look like a schema must come back byte-identical."""
    schema = {
        "type": "object",
        "properties": {
            "cfg": {"type": "object", "default": {"strict": True, "propertyNames": "x"}},
        },
    }
    clean = _sanitize_for_gemini(schema)
    assert clean["properties"]["cfg"]["default"] == {"strict": True, "propertyNames": "x"}


def test_the_allow_list_comes_from_the_installed_library():
    """The model that rejects the request is the only honest source for what
    it accepts — so the list is read off it, not spelled out beside it."""
    keys = _gemini_schema_keys()
    assert {"type", "properties", "items", "required", "description"} <= keys
    assert "x-mcp-header" not in keys and "strict" not in keys


def test_the_sanitized_schema_passes_googles_own_validator():
    """The end-to-end claim: what we send validates where it is validated."""
    types = pytest.importorskip("google.genai.types")
    dirty = {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "x-mcp-header": "owner"},
            "page": {"type": "integer", "exclusiveMinimum": 0},
            "anchor": {"type": "string", "propertyNames": {"type": "string"}},
            "opts": {"type": "object", "strict": True, "additionalProperties": False},
        },
        "required": ["owner"],
    }
    with pytest.raises(Exception):
        types.Schema.model_validate(dirty)
    clean = _sanitize_for_gemini(dirty)
    types.Schema.model_validate(clean)  # must not raise
    # The exclusive bound was converted, not merely dropped.
    assert clean["properties"]["page"]["minimum"] == 0
