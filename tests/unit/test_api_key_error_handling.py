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
    _sanitize_for_gemini,
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
