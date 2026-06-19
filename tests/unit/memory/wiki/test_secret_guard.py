"""Unit tests for ``jarvis.memory.wiki.secret_guard`` (AP-2).

The guard is a pure, regex-only function: a body that contains a
credential shape (API key, bearer token, password, JWT, PEM, long
opaque hex/base64) is reported; ordinary prose passes untouched.
"""
from __future__ import annotations

import pytest

from jarvis.memory.wiki.secret_guard import contains_secret, find_secrets


@pytest.mark.parametrize(
    "body",
    [
        "The deploy key is sk-proj-AbCdEf0123456789AbCdEf0123456789",
        "openai key: sk-AbCdEf0123456789AbCdEf0123",
        "Authorization: Bearer aB3dEfGhIjKlMnOpQrStUvWx",
        "api_key = ABCD1234EFGH5678IJKL",
        "password: hunter2-supersecret",
        "client_secret=QmFzZTY0LXNlY3JldC12YWx1ZQ",
        "token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NSJ9.sig",
        "google AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7",
        "key xai-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345",
        "-----BEGIN RSA PRIVATE KEY-----",
        # 64-char hex = SHA-256-length opaque secret (the long-hex rule).
        "checksum 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    ],
)
def test_secret_bodies_are_detected(body: str) -> None:
    assert contains_secret(body) is True
    assert find_secrets(body)  # non-empty list of pattern names


@pytest.mark.parametrize(
    "body",
    [
        "Ruben prefers a multi-provider brain and bilingual replies.",
        "The project shipped v0.2.0 on 2026-06-09 to the public repo.",
        "Note: the password is written on a sticky note in the drawer.",
        "He uses GPT and Gemini; the API design favours streaming.",
        "",
        "Short hex: deadbeef and a code C0FFEE reference.",
        # A full git SHA-1 (40 hex chars) is legitimate biographical prose
        # ("the fix landed at commit ...") and must never be refused.
        "commit a3f2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0 landed today.",
    ],
)
def test_normal_bodies_pass(body: str) -> None:
    assert contains_secret(body) is False
    assert find_secrets(body) == []


def test_find_secrets_returns_pattern_names_not_values() -> None:
    hits = find_secrets("api_key = ABCD1234EFGH5678IJKL")
    assert "labelled_secret" in hits
    # The matched credential value is never returned.
    assert all("ABCD1234" not in name for name in hits)
