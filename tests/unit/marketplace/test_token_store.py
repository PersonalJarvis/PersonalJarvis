"""Token store + Tokens model — persistence-critical behaviour.

The `needs_reauth` flag is the groundwork for the never-delete invariant: a
revoked token is KEPT and flagged, so a connected plugin never silently
disappears across an app close / PC restart.
"""

import pytest

from jarvis.marketplace.token_store import (
    ChunkedBackend,
    InMemoryBackend,
    Tokens,
    TokenStore,
)


class _SizeLimitedFakeBackend:
    """Mimics the Windows Credential Manager: ``set`` raises over ``limit``
    characters (the real CredWrite caps a blob at 2560 bytes / 1280 UTF-16
    chars and fails with WinError 1783)."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str) -> None:
        if len(value) > self._limit:
            raise RuntimeError(f"CredWrite blob too large ({len(value)} > {self._limit})")
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


def test_chunked_backend_round_trips_value_larger_than_primitive_limit():
    # A long Google OAuth token blob exceeds the Credential Manager's hard
    # per-entry limit. The chunking backend must split it across several
    # primitive entries and reassemble it on read.
    primitive = _SizeLimitedFakeBackend(limit=50)
    backend = ChunkedBackend(primitive, chunk_size=20)
    big = "z" * 200  # 4x the primitive's hard limit

    backend.set("plugin_gmail_tokens", big)

    assert backend.get("plugin_gmail_tokens") == big


def test_chunked_backend_reads_legacy_plain_value():
    # A short value written by the pre-chunking code (a bare string in the
    # primary key) must still load unchanged.
    primitive = _SizeLimitedFakeBackend(limit=50)
    primitive.store["plugin_discord_tokens"] = '{"access":"abc"}'
    backend = ChunkedBackend(primitive, chunk_size=20)

    assert backend.get("plugin_discord_tokens") == '{"access":"abc"}'


def test_chunked_backend_delete_removes_all_chunks():
    primitive = _SizeLimitedFakeBackend(limit=50)
    backend = ChunkedBackend(primitive, chunk_size=20)
    backend.set("plugin_gmail_tokens", "z" * 200)

    backend.delete("plugin_gmail_tokens")

    assert backend.get("plugin_gmail_tokens") is None
    assert primitive.store == {}  # no orphaned overflow chunks


def test_chunked_backend_resave_smaller_clears_stale_chunks():
    primitive = _SizeLimitedFakeBackend(limit=50)
    backend = ChunkedBackend(primitive, chunk_size=20)
    backend.set("plugin_gmail_tokens", "z" * 200)  # many chunks

    backend.set("plugin_gmail_tokens", "small")  # now fits in one

    assert backend.get("plugin_gmail_tokens") == "small"
    # Only the primary key remains — overflow chunks were cleaned up.
    assert list(primitive.store) == ["plugin_gmail_tokens"]


def test_chunked_backend_raises_when_a_single_chunk_still_too_big():
    # Defensive: if even one chunk can't be stored, fail loudly rather than
    # silently persisting a partial blob that can never be reassembled.
    primitive = _SizeLimitedFakeBackend(limit=10)
    backend = ChunkedBackend(primitive, chunk_size=50)  # chunk bigger than limit

    with pytest.raises(RuntimeError):
        backend.set("plugin_gmail_tokens", "z" * 200)


def test_store_round_trips_oversized_token_through_chunked_keyring():
    # End-to-end: a TokenStore over a size-limited backend persists and reloads
    # a token whose JSON exceeds the primitive's per-entry cap.
    primitive = _SizeLimitedFakeBackend(limit=50)
    store = TokenStore(ChunkedBackend(primitive, chunk_size=20))
    big_token = Tokens(access="y" * 300, refresh="r" * 200)

    store.save("gmail", big_token)
    loaded = store.load("gmail")

    assert loaded is not None
    assert loaded.access == "y" * 300
    assert loaded.refresh == "r" * 200


def test_needs_reauth_round_trips_through_json():
    t = Tokens(access="a", refresh="r", needs_reauth=True)
    restored = Tokens.from_json(t.to_json())
    assert restored.needs_reauth is True


def test_needs_reauth_defaults_false_for_legacy_blob():
    # A blob written before the field existed must load as needs_reauth=False.
    legacy = '{"access":"a","refresh":null,"expires_at":null,"extra":{}}'
    assert Tokens.from_json(legacy).needs_reauth is False


def test_store_persists_needs_reauth():
    store = TokenStore(InMemoryBackend())
    store.save("p", Tokens(access="a", needs_reauth=True))
    loaded = store.load("p")
    assert loaded is not None and loaded.needs_reauth is True
