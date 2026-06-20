"""vendors.py — provider->vendor map + credential extraction/placement + usage."""

from __future__ import annotations

import json

import pytest

from keyproxy import vendors


# --------------------------------------------------------------------------
# provider_id -> vendor wire contract (must match the client side exactly)
# --------------------------------------------------------------------------

def test_provider_to_vendor_map_is_exact() -> None:
    assert vendors.PROVIDER_VENDORS == {
        "claude-api": ("anthropic", "https://api.anthropic.com"),
        "openai": ("openai_compatible", "https://api.openai.com/v1"),
        "openrouter": ("openai_compatible", "https://openrouter.ai/api/v1"),
        "grok": ("openai_compatible", "https://api.x.ai/v1"),
        "gemini": ("gemini", "https://generativelanguage.googleapis.com"),
        "groq-api": ("openai_compatible", "https://api.groq.com/openai/v1"),
    }


def test_resolve_provider_known() -> None:
    vendor, base = vendors.resolve_provider("openrouter")
    assert vendor == "openai_compatible"
    assert base == "https://openrouter.ai/api/v1"


def test_resolve_provider_unknown_returns_none() -> None:
    assert vendors.resolve_provider("does-not-exist") is None


# --------------------------------------------------------------------------
# inbound credential extraction
# --------------------------------------------------------------------------

def test_extract_openai_compatible_bearer() -> None:
    headers = {"authorization": "Bearer kp_inbound_token"}
    token = vendors.extract_inbound_token(
        "openai_compatible", headers, query={}
    )
    assert token == "kp_inbound_token"


def test_extract_openai_compatible_missing() -> None:
    assert (
        vendors.extract_inbound_token("openai_compatible", {}, query={}) is None
    )


def test_extract_anthropic_x_api_key() -> None:
    headers = {"x-api-key": "kp_inbound_token"}
    token = vendors.extract_inbound_token("anthropic", headers, query={})
    assert token == "kp_inbound_token"


def test_extract_gemini_header() -> None:
    headers = {"x-goog-api-key": "kp_inbound_token"}
    token = vendors.extract_inbound_token("gemini", headers, query={})
    assert token == "kp_inbound_token"


def test_extract_gemini_query_key() -> None:
    token = vendors.extract_inbound_token(
        "gemini", {}, query={"key": "kp_inbound_token"}
    )
    assert token == "kp_inbound_token"


def test_extract_gemini_header_wins_over_query() -> None:
    token = vendors.extract_inbound_token(
        "gemini",
        {"x-goog-api-key": "from_header"},
        query={"key": "from_query"},
    )
    assert token == "from_header"


# --------------------------------------------------------------------------
# outbound credential placement (the real key goes in the right slot, the
# inbound token is gone)
# --------------------------------------------------------------------------

def test_place_openai_compatible_sets_bearer() -> None:
    headers, query = vendors.place_outbound_credential(
        "openai_compatible",
        headers={"content-type": "application/json"},
        query={},
        real_key="sk-REAL-KEY",
    )
    assert headers["authorization"] == "Bearer sk-REAL-KEY"
    assert headers["content-type"] == "application/json"
    assert "key" not in query


def test_place_anthropic_sets_x_api_key() -> None:
    headers, _query = vendors.place_outbound_credential(
        "anthropic", headers={}, query={}, real_key="sk-ant-REAL"
    )
    assert headers["x-api-key"] == "sk-ant-REAL"
    assert "authorization" not in headers


def test_place_gemini_sets_header_and_strips_query_key() -> None:
    headers, query = vendors.place_outbound_credential(
        "gemini",
        headers={},
        query={"key": "inbound", "alt": "sse"},
        real_key="REAL-GEMINI",
    )
    assert headers["x-goog-api-key"] == "REAL-GEMINI"
    # The inbound ?key= must be removed so the proxy token never reaches Google.
    assert "key" not in query
    assert query["alt"] == "sse"


# --------------------------------------------------------------------------
# usage parsing per vendor
# --------------------------------------------------------------------------

def test_parse_usage_openai_json() -> None:
    body = json.dumps(
        {
            "model": "gpt-4o-mini",
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 22,
                "total_tokens": 33,
            },
        }
    ).encode()
    u = vendors.parse_usage("openai_compatible", body)
    assert u is not None
    assert u.model == "gpt-4o-mini"
    assert u.prompt_tokens == 11
    assert u.completion_tokens == 22
    assert u.total_tokens == 33


def test_parse_usage_openai_sse_with_final_usage_chunk() -> None:
    body = (
        b'data: {"model":"gpt-4o-mini","choices":[{"delta":{"content":"hi"}}]}\n\n'
        b'data: {"model":"gpt-4o-mini","usage":{"prompt_tokens":5,'
        b'"completion_tokens":7,"total_tokens":12}}\n\n'
        b"data: [DONE]\n\n"
    )
    u = vendors.parse_usage("openai_compatible", body)
    assert u is not None
    assert u.prompt_tokens == 5
    assert u.completion_tokens == 7
    assert u.total_tokens == 12
    assert u.model == "gpt-4o-mini"


def test_parse_usage_anthropic_stream() -> None:
    body = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"model":"claude-3-5-sonnet",'
        b'"usage":{"input_tokens":40,"output_tokens":1}}}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":99}}\n\n'
    )
    u = vendors.parse_usage("anthropic", body)
    assert u is not None
    assert u.prompt_tokens == 40
    # output_tokens from the final message_delta wins (cumulative final count).
    assert u.completion_tokens == 99
    assert u.total_tokens == 139
    assert u.model == "claude-3-5-sonnet"


def test_parse_usage_anthropic_non_stream_json() -> None:
    body = json.dumps(
        {
            "model": "claude-3-5-haiku",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
    ).encode()
    u = vendors.parse_usage("anthropic", body)
    assert u is not None
    assert u.prompt_tokens == 10
    assert u.completion_tokens == 20
    assert u.total_tokens == 30


def test_parse_usage_gemini_usage_metadata() -> None:
    body = json.dumps(
        {
            "usageMetadata": {
                "promptTokenCount": 8,
                "candidatesTokenCount": 16,
                "totalTokenCount": 24,
            },
            "modelVersion": "gemini-2.0-flash",
        }
    ).encode()
    u = vendors.parse_usage("gemini", body)
    assert u is not None
    assert u.prompt_tokens == 8
    assert u.completion_tokens == 16
    assert u.total_tokens == 24
    assert u.model == "gemini-2.0-flash"


def test_parse_usage_miss_returns_none() -> None:
    assert vendors.parse_usage("openai_compatible", b"not json at all") is None
    assert vendors.parse_usage("anthropic", b"") is None
    assert vendors.parse_usage("gemini", b'{"no":"usage"}') is None


def test_parse_usage_never_raises_on_garbage() -> None:
    # Best-effort: a parse miss is a None, never an exception.
    for vendor in ("openai_compatible", "anthropic", "gemini"):
        assert vendors.parse_usage(vendor, b"\x00\xff\x00") is None
