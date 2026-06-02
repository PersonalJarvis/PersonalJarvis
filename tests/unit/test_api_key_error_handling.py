"""Tests fuer Bug API-1 (2026-04-29): API-Key/Account-Fehler-Behandlung.

Drei zusammenhaengende Defekte gefixt:

1. Gemini-Schema-Sanitizer — OpenAI-spezifische Felder (`strict`,
   `input_examples`) fliegen vor Gemini-API-Call raus, sonst wirft
   google-genai SDK 11 Pydantic-Validation-Errors.

2. _is_account_blocked_exc() — Anthropic credit-too-low / xAI no-team-access /
   OpenAI tier-not-available werden als terminales Account-Problem erkannt
   (nicht als rate_limit oder invalid_model).

3. _format_provider_chain_error — User-actionable Account-Block-Message
   statt "Provider unerreichbar"-Halluzination.
"""
from __future__ import annotations

from jarvis.brain.manager import (
    _classify_provider_error,
    _format_provider_chain_error,
    _is_account_blocked_exc,
    _is_invalid_model_exc,
    _is_missing_key_exc,
)
from jarvis.plugins.brain.gemini import (
    _GEMINI_FORBIDDEN_SCHEMA_KEYS,
    _gemini_tool_name_map,
    _sanitize_for_gemini,
    _sanitize_gemini_function_name,
    _tools_gemini_format,
)

# ---- 1. Gemini-Schema-Sanitizer ----------------------------------------

class TestGeminiSchemaSanitize:
    def test_strips_strict_at_root(self) -> None:
        schema = {"type": "object", "strict": True, "properties": {}}
        out = _sanitize_for_gemini(schema)
        assert "strict" not in out
        assert out["type"] == "object"

    def test_strips_input_examples(self) -> None:
        schema = {
            "type": "object",
            "input_examples": [{"path": "foo"}],
            "properties": {"path": {"type": "string"}},
        }
        out = _sanitize_for_gemini(schema)
        assert "input_examples" not in out
        assert out["properties"]["path"]["type"] == "string"

    def test_strips_recursively(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "strict": True,
                    "additionalProperties": False,
                    "properties": {"x": {"type": "string"}},
                },
            },
        }
        out = _sanitize_for_gemini(schema)
        nested = out["properties"]["nested"]
        assert "strict" not in nested
        assert "additionalProperties" not in nested
        assert nested["properties"]["x"]["type"] == "string"

    def test_strips_pydantic_snake_case_additional_properties(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "additional_properties": False,
                    "properties": {"x": {"type": "string"}},
                },
            },
        }
        out = _sanitize_for_gemini(schema)
        assert "additional_properties" not in out["properties"]["nested"]

    def test_strips_in_array_items(self) -> None:
        schema = {
            "type": "array",
            "items": {"type": "object", "strict": True, "properties": {}},
        }
        out = _sanitize_for_gemini(schema)
        assert "strict" not in out["items"]

    def test_tools_gemini_format_does_not_raise_with_self_mod_schema(self) -> None:
        """Self-Mod-Tools (Phase 7.3) haben strict + input_examples — vor dem
        Fix crashte gemini-Plugin mit `extra_forbidden`. Jetzt: clean."""
        tools = (
            {
                "name": "set_config_value",
                "description": "Patcht jarvis.toml.",
                "input_schema": {
                    "type": "object",
                    "strict": True,
                    "additionalProperties": False,
                    "input_examples": [{"path": "tts.provider"}],
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        )
        out = _tools_gemini_format(tools)
        assert out is not None
        decl = out[0]["functionDeclarations"][0]
        params = decl["parameters"]
        assert "strict" not in params
        assert "input_examples" not in params
        assert "additionalProperties" not in params
        # Nutzbare Felder bleiben erhalten
        assert params["properties"]["path"]["type"] == "string"
        assert params["required"] == ["path"]

    def test_forbidden_keys_constant_is_complete(self) -> None:
        # Sanity-Check: alle bekannten OpenAI-Only-Felder sind drin.
        assert "strict" in _GEMINI_FORBIDDEN_SCHEMA_KEYS
        assert "input_examples" in _GEMINI_FORBIDDEN_SCHEMA_KEYS
        assert "additionalProperties" in _GEMINI_FORBIDDEN_SCHEMA_KEYS
        assert "additional_properties" in _GEMINI_FORBIDDEN_SCHEMA_KEYS


# ---- 1b. exclusiveMinimum/Maximum conversion (Bug 2026-06-01) ---------
#
# Pydantic Field(gt=N)/Field(lt=N) emit JSON-schema exclusiveMinimum/
# exclusiveMaximum. The google-genai Schema model uses extra="forbid" and
# accepts only minimum/maximum — the exclusive variants trip
# "extra_forbidden". The sanitizer must convert gt -> minimum / lt -> maximum
# (pragmatic semantics-preserving fix) and drop the exclusive keywords.


class TestGeminiExclusiveBoundsConversion:
    def test_exclusive_minimum_converted_to_minimum(self) -> None:
        schema = {"type": "integer", "exclusiveMinimum": 0}
        out = _sanitize_for_gemini(schema)
        assert "exclusiveMinimum" not in out
        assert out["minimum"] == 0

    def test_exclusive_maximum_converted_to_maximum(self) -> None:
        schema = {"type": "number", "exclusiveMaximum": 10}
        out = _sanitize_for_gemini(schema)
        assert "exclusiveMaximum" not in out
        assert out["maximum"] == 10

    def test_exclusive_bounds_converted_when_nested(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "size": {"type": "integer", "exclusiveMinimum": 0, "title": "Size"},
                "items": {
                    "type": "array",
                    "items": {"type": "number", "exclusiveMaximum": 100},
                },
            },
        }
        out = _sanitize_for_gemini(schema)
        size = out["properties"]["size"]
        assert "exclusiveMinimum" not in size
        assert size["minimum"] == 0
        arr_items = out["properties"]["items"]["items"]
        assert "exclusiveMaximum" not in arr_items
        assert arr_items["maximum"] == 100

    def test_existing_minimum_is_not_clobbered_by_conversion(self) -> None:
        # If an explicit `minimum` already exists alongside an exclusive bound,
        # the existing inclusive bound must win (do not weaken it).
        schema = {"type": "integer", "minimum": 5, "exclusiveMinimum": 0}
        out = _sanitize_for_gemini(schema)
        assert "exclusiveMinimum" not in out
        assert out["minimum"] == 5

    def test_boolean_exclusive_minimum_draft4_is_dropped(self) -> None:
        # JSON-schema draft-04 used exclusiveMinimum: bool (a flag on minimum).
        # We can't convert a bool to a number, so it must simply be dropped.
        schema = {"type": "integer", "minimum": 0, "exclusiveMinimum": True}
        out = _sanitize_for_gemini(schema)
        assert "exclusiveMinimum" not in out
        assert out["minimum"] == 0


# ---- 1c. real-SDK acceptance regression (Bug 2026-06-01) --------------


class TestGeminiSdkAcceptsSanitizedSchema:
    def test_real_genai_config_accepts_converted_schema(self) -> None:
        """Regression: a tool schema with exclusiveMinimum (from Field(gt=0))
        must produce a Gemini-valid GenerateContentConfig (no extra_forbidden).
        This is the exact shape that crashed the live provider 2026-06-01.
        """
        import pytest

        try:
            from google.genai import types as genai_types
        except Exception:  # pragma: no cover - SDK not installed in this env
            pytest.skip("google-genai SDK not installed")

        tools = (
            {
                "name": "make_thumbnail",
                "description": "demo tool with a constrained int field",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "size": {"type": "integer", "exclusiveMinimum": 0,
                                 "title": "Size"},
                        "ratio": {"type": "number", "exclusiveMaximum": 10},
                    },
                    "required": ["size"],
                },
            },
        )
        payload = _tools_gemini_format(tools)
        assert payload is not None
        # The SDK validation raises pydantic.ValidationError on bad fields.
        genai_types.GenerateContentConfig(tools=payload)


# ---- 1d. function-name sanitize (Bug 2026-06-01, live forensic) --------
#
# After the exclusiveMinimum fix (afc623fb) Gemini stopped failing schema
# validation and reached its NEXT gate: function-name validation. Connected
# MCP/marketplace plugin tools carry names Gemini rejects ("Invalid function
# name. Must start with a letter or an underscore. Must be alphameric
# (a-z, A-Z, 0-9), underscores (_), dots (.), colons (:), or dashes (-),
# with a maximum length of 128."). The live log (data/jarvis_desktop.log,
# 2026-06-01 23:34:57) shows function_declarations[18..54] rejected → the
# whole turn fell through to claude-api (401) + grok (403) and the chain-
# error diagnostic was spoken aloud. The sanitizer must coerce names to the
# Gemini rule AND stay round-trippable (the model calls back the sanitized
# name; the executor only knows the original).


class TestGeminiFunctionNameSanitize:
    def test_valid_name_is_unchanged(self) -> None:
        taken: set[str] = set()
        assert _sanitize_gemini_function_name("wiki-recall", taken) == "wiki-recall"

    def test_leading_digit_gets_underscore_prefix(self) -> None:
        taken: set[str] = set()
        out = _sanitize_gemini_function_name("3d_render", taken)
        assert out[0] == "_"
        assert out == "_3d_render"

    def test_forbidden_chars_replaced(self) -> None:
        taken: set[str] = set()
        # spaces and slashes are not in [A-Za-z0-9_.:-]
        out = _sanitize_gemini_function_name("Google Calendar/list events", taken)
        assert " " not in out and "/" not in out
        assert all(c.isalnum() or c in "_.:-" for c in out)
        assert out[0].isalpha() or out[0] == "_"

    def test_empty_name_becomes_valid_placeholder(self) -> None:
        taken: set[str] = set()
        out = _sanitize_gemini_function_name("", taken)
        assert out  # non-empty
        assert out[0].isalpha() or out[0] == "_"

    def test_overlong_name_is_truncated_within_limit(self) -> None:
        taken: set[str] = set()
        out = _sanitize_gemini_function_name("a" * 400, taken)
        assert len(out) <= 128

    def test_exactly_128_chars_is_preserved(self) -> None:
        taken: set[str] = set()
        name = "a" * 128
        out = _sanitize_gemini_function_name(name, taken)
        assert out == name  # boundary: not truncated

    def test_collision_on_overlong_base_stays_valid_and_distinct(self) -> None:
        import re
        rx = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
        taken: set[str] = set()
        first = _sanitize_gemini_function_name("a" * 400, taken)
        taken.add(first)
        second = _sanitize_gemini_function_name("a" * 401, taken)
        # both clamp to the same 128-char base → collision suffix kicks in
        assert first != second
        assert rx.match(first) and rx.match(second)
        assert len(second) <= 128

    def test_collision_yields_distinct_names(self) -> None:
        taken: set[str] = set()
        first = _sanitize_gemini_function_name("weird name", taken)
        taken.add(first)
        second = _sanitize_gemini_function_name("weird/name", taken)
        assert first != second

    def test_name_map_is_bijective_and_deterministic(self) -> None:
        tools = (
            {"name": "wiki-recall", "description": "", "input_schema": {}},
            {"name": "Google Calendar/list", "description": "", "input_schema": {}},
            {"name": "Google Calendar list", "description": "", "input_schema": {}},
            {"name": "9lives", "description": "", "input_schema": {}},
        )
        m1 = _gemini_tool_name_map(tools)
        m2 = _gemini_tool_name_map(tools)
        assert m1 == m2  # deterministic
        # every original maps to a unique sanitized name (collision-free)
        assert len(set(m1.values())) == len(m1)
        # valid names survive verbatim
        assert m1["wiki-recall"] == "wiki-recall"
        # every sanitized name is Gemini-valid
        import re
        rx = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
        assert all(rx.match(v) for v in m1.values())

    def test_tools_format_emits_only_valid_names(self) -> None:
        import re
        rx = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
        tools = (
            {"name": "Spotify: play track", "description": "d", "input_schema": {}},
            {"name": "1password/read", "description": "d", "input_schema": {}},
        )
        out = _tools_gemini_format(tools)
        assert out is not None
        for decl in out[0]["functionDeclarations"]:
            assert rx.match(decl["name"]), decl["name"]

    def test_real_genai_config_accepts_sanitized_names(self) -> None:
        """The exact failure shape from the live log: invalid tool names must
        produce a Gemini-valid GenerateContentConfig (no INVALID_ARGUMENT)."""
        import pytest

        try:
            from google.genai import types as genai_types
        except Exception:  # pragma: no cover - SDK not installed
            pytest.skip("google-genai SDK not installed")

        tools = tuple(
            {"name": f"plugin {i}/do something!", "description": "d",
             "input_schema": {"type": "object", "properties": {}}}
            for i in range(40)
        )
        payload = _tools_gemini_format(tools)
        assert payload is not None
        genai_types.GenerateContentConfig(tools=payload)


# ---- 2. _is_account_blocked_exc ---------------------------------------

class TestAccountBlocked:
    def test_anthropic_credit_too_low(self) -> None:
        msg = ("Error code: 400 - {'type': 'error', 'error': {"
               "'message': 'Your credit balance is too low to access "
               "the Anthropic API. Please go to Plans & Billing.'}}")
        assert _is_account_blocked_exc(msg)
        assert _classify_provider_error(msg, default="call_fail") == "account_blocked"

    def test_xai_team_does_not_have_access(self) -> None:
        msg = ("Error code: 404 - {'code': 'Some requested entity was not "
               "found', 'error': 'The model grok-4.1-fast does not exist or "
               "your team does not have access to it.'}")
        assert _is_account_blocked_exc(msg)
        # Wird account_blocked, nicht invalid_model — wichtig fuer User-Message.
        assert _classify_provider_error(msg, default="call_fail") == "account_blocked"

    def test_openai_tier_not_available(self) -> None:
        msg = "The model `o1-pro` is not available on your tier."
        assert _is_account_blocked_exc(msg)

    def test_invalid_model_does_not_match_account(self) -> None:
        # Echte invalid_model-Fehler ohne Account-Hint bleiben "invalid_model".
        msg = "model_not_found: gpt-foo"
        assert not _is_account_blocked_exc(msg)
        assert _is_invalid_model_exc(msg)

    def test_missing_key_does_not_match_account(self) -> None:
        msg = "Kein OpenAI-API-Key gefunden (openai_api_key / OPENAI_API_KEY)."
        assert _is_missing_key_exc(msg)
        assert not _is_account_blocked_exc(msg)


# ---- 3. _format_provider_chain_error ----------------------------------

class TestChainErrorFormat:
    def test_account_blocked_message_user_actionable(self) -> None:
        errors = [
            ("claude-api", "claude-haiku-4-5", "account_blocked",
             "credit balance too low"),
        ]
        msg = _format_provider_chain_error(errors)
        assert "Account-Problem" in msg
        assert "claude-api" in msg
        assert "billing" in msg.lower() or "credit" in msg.lower()

    def test_account_blocked_with_billing_url_hint(self) -> None:
        errors = [
            ("claude-api", "haiku", "account_blocked", "credit too low"),
            ("grok", "grok-4.1-fast", "account_blocked", "team no access"),
        ]
        msg = _format_provider_chain_error(errors)
        assert "claude-api" in msg
        assert "grok" in msg
        # Konkrete URL-Hinweise damit User klicken kann
        assert "anthropic" in msg.lower()
        assert "x.ai" in msg.lower() or "console" in msg.lower()

    def test_mixed_missing_key_and_account_blocked(self) -> None:
        errors = [
            ("openai", "gpt-5.5", "missing_key", "no key"),
            ("claude-api", "haiku", "account_blocked", "credit"),
        ]
        msg = _format_provider_chain_error(errors)
        # Beide Bereiche werden adressiert
        assert "Brain-Key" in msg or "missing" in msg.lower()
        assert "Account-Problem" in msg
