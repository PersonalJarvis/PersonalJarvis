"""Token store: issue / verify / list / revoke, with hashing at rest."""

from __future__ import annotations

import hashlib

import pytest

from keyproxy.store import Store
from keyproxy.tokens import TokenStore


@pytest.fixture()
def tokens() -> TokenStore:
    return TokenStore(Store(":memory:"))


def test_issue_returns_plaintext_once_and_stores_only_hash(
    tokens: TokenStore,
) -> None:
    issued = tokens.issue("alice-laptop")

    assert issued.plaintext  # non-empty plaintext returned once
    assert issued.label == "alice-laptop"
    assert issued.id

    # The plaintext must NOT be stored anywhere — only the sha256 hex.
    rows = tokens.list()
    assert len(rows) == 1
    stored = rows[0]
    assert "token_sha256" in stored
    assert stored["token_sha256"] == hashlib.sha256(
        issued.plaintext.encode("utf-8")
    ).hexdigest()
    # The plaintext string itself is never present in any stored column.
    assert issued.plaintext not in str(dict(stored))


def test_verify_accepts_valid_returns_token_id(tokens: TokenStore) -> None:
    issued = tokens.issue("alice")
    token_id = tokens.verify(issued.plaintext)
    assert token_id == issued.id


def test_verify_rejects_unknown_token(tokens: TokenStore) -> None:
    tokens.issue("alice")
    assert tokens.verify("sk-not-a-real-token") is None


def test_verify_rejects_empty_token(tokens: TokenStore) -> None:
    assert tokens.verify("") is None
    assert tokens.verify(None) is None  # type: ignore[arg-type]


def test_revoked_token_fails_verify(tokens: TokenStore) -> None:
    issued = tokens.issue("alice")
    assert tokens.verify(issued.plaintext) == issued.id

    revoked = tokens.revoke(issued.id)
    assert revoked is True
    assert tokens.verify(issued.plaintext) is None


def test_revoke_unknown_id_returns_false(tokens: TokenStore) -> None:
    assert tokens.revoke("00000000-0000-0000-0000-000000000000") is False


def test_list_includes_revoked_with_timestamp(tokens: TokenStore) -> None:
    issued = tokens.issue("alice")
    tokens.revoke(issued.id)
    rows = tokens.list()
    assert len(rows) == 1
    assert rows[0]["revoked_at"] is not None


def test_each_token_is_unique(tokens: TokenStore) -> None:
    a = tokens.issue("a")
    b = tokens.issue("b")
    assert a.plaintext != b.plaintext
    assert a.id != b.id
