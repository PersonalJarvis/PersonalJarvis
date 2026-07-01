"""Crypto tests for the backend skeleton (Phase C, Commit 1)."""
from __future__ import annotations

import json

import pytest

from board_backend.crypto import (
    canonical_json,
    generate_keypair,
    sign,
    verify,
    verify_with_recanonicalize,
)


# ----------------------------------------------------------------------
# Canonical JSON
# ----------------------------------------------------------------------

def test_canonical_json_is_key_sorted() -> None:
    a = canonical_json({"z": 1, "a": 2})
    b = canonical_json({"a": 2, "z": 1})
    assert a == b


def test_canonical_json_no_whitespace() -> None:
    out = canonical_json({"a": 1, "b": [1, 2, 3]})
    assert b" " not in out


def test_canonical_json_handles_nested_structures() -> None:
    nested = {"x": {"b": 2, "a": 1}, "list": [{"k": "v"}, {"k": "w"}]}
    out = canonical_json(nested)
    parsed = json.loads(out)
    assert parsed == nested


def test_canonical_json_utf8_passthrough() -> None:
    out = canonical_json({"de": "Hallo Welt mit Umlaut: aeoeue ß"})  # i18n-allow
    parsed = json.loads(out.decode("utf-8"))
    assert parsed["de"] == "Hallo Welt mit Umlaut: aeoeue ß"  # i18n-allow


# ----------------------------------------------------------------------
# Sign / Verify
# ----------------------------------------------------------------------

def test_sign_and_verify_roundtrip() -> None:
    priv, pub = generate_keypair()
    assert len(priv) == 64
    assert len(pub) == 64

    payload = {"ts_ms": 1_700_000_000_000, "data": [1, 2, 3]}
    sig = sign(payload, privkey_hex=priv)
    assert len(sig) == 128

    assert verify_with_recanonicalize(
        pubkey_hex=pub, signature_hex=sig, parsed_payload=payload,
    ) is True


def test_verify_fails_on_tampered_payload() -> None:
    priv, pub = generate_keypair()
    payload = {"ts_ms": 1_700_000_000_000, "tasks_completed": 1}
    sig = sign(payload, privkey_hex=priv)

    payload["tasks_completed"] = 99999
    assert verify_with_recanonicalize(
        pubkey_hex=pub, signature_hex=sig, parsed_payload=payload,
    ) is False


def test_verify_fails_on_wrong_pubkey() -> None:
    priv, _ = generate_keypair()
    _, other_pub = generate_keypair()
    payload = {"x": 1}
    sig = sign(payload, privkey_hex=priv)
    assert verify_with_recanonicalize(
        pubkey_hex=other_pub, signature_hex=sig, parsed_payload=payload,
    ) is False


def test_verify_fails_on_garbage_signature() -> None:
    _, pub = generate_keypair()
    payload = {"x": 1}
    assert verify_with_recanonicalize(
        pubkey_hex=pub, signature_hex="00" * 64, parsed_payload=payload,
    ) is False


def test_verify_raw_body_path() -> None:
    """The server path: parse body, re-canonicalize, verify.

    Demonstrates that re-canonicalization is robust against whitespace
    manipulation by proxies.
    """
    priv, pub = generate_keypair()
    payload = {"a": 1, "b": "x"}
    sig = sign(payload, privkey_hex=priv)

    # Simulates a reverse proxy that inserts irrelevant whitespace.
    raw_with_extra_ws = b'{"a":   1,\n   "b": "x"}'
    parsed = json.loads(raw_with_extra_ws)

    assert verify_with_recanonicalize(
        pubkey_hex=pub, signature_hex=sig, parsed_payload=parsed,
    ) is True


def test_verify_low_level_strict_bytes() -> None:
    """The low-level ``verify`` API without re-canonicalize stays strict."""
    priv, pub = generate_keypair()
    body = canonical_json({"x": 1})
    sig = sign(body, privkey_hex=priv)
    assert verify(pubkey_hex=pub, signature_hex=sig, body=body) is True
    # The whitespace variant fails the low-level API, which is OK —
    # the HTTP path always calls verify_with_recanonicalize.
    assert verify(pubkey_hex=pub, signature_hex=sig, body=b'{"x": 1}') is False
