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


class _SilentDeleteBackend:
    """Backend that reports no error but retains every deleted credential."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.overflow_get_calls = 0

    def get(self, key: str) -> str | None:
        if key.rpartition("__")[2].isdigit():
            self.overflow_get_calls += 1
            return "retained-chunk"
        return self.store.get(key)

    def set(self, key: str, value: str) -> None:
        self.store[key] = value

    def delete(self, key: str) -> None:
        return


class _PartialOverflowDeleteBackend(_SizeLimitedFakeBackend):
    """Silently retains chunk one until the test allows its deletion."""

    allow_chunk_one_delete = False

    def delete(self, key: str) -> None:
        if key.endswith("__1") and not self.allow_chunk_one_delete:
            return
        super().delete(key)


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


def test_chunked_backend_cleanup_stops_after_silent_delete_failure():
    primitive = _SilentDeleteBackend()
    backend = ChunkedBackend(primitive, chunk_size=20)

    backend.set("plugin_gmail_tokens", "small")

    assert backend.get("plugin_gmail_tokens") == "small"
    assert primitive.overflow_get_calls == 2


class _FailNthSetBackend:
    """Mimics a keyring backend whose ``set`` raises on a chosen call number
    (1-indexed across the object's whole lifetime) — used to simulate a
    write failing partway through a chunked ``ChunkedBackend.set``."""

    def __init__(self) -> None:
        self.fail_on_call: int | None = None
        self.calls = 0
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str) -> None:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("simulated backend write failure")
        self.store[key] = value

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


def test_chunked_backend_set_failure_leaves_old_value_intact():
    # Delete-then-write used to drop the OLD chunks before writing the new
    # ones, so a failure partway through left a header pointing at deleted
    # data. Write-then-swap must instead leave the last-good value readable.
    primitive = _FailNthSetBackend()
    backend = ChunkedBackend(primitive, chunk_size=20)
    old_value = "a" * 55  # 3 chunks: 20 / 20 / 15
    backend.set("plugin_gmail_tokens", old_value)
    assert backend.get("plugin_gmail_tokens") == old_value

    # Fail on the 2nd chunk write of the NEXT set() call (chunk index 1 of 3).
    primitive.fail_on_call = primitive.calls + 2
    with pytest.raises(RuntimeError):
        backend.set("plugin_gmail_tokens", "b" * 55)

    assert backend.get("plugin_gmail_tokens") == old_value


def test_chunked_backend_set_success_leaves_no_orphan_chunks_when_shrinking():
    # New value needs FEWER chunks than the old one -- the old header's
    # higher-index leftovers must be cleaned up, not just the count that
    # overlaps with the new value.
    primitive = _SizeLimitedFakeBackend(limit=1000)
    backend = ChunkedBackend(primitive, chunk_size=20)
    backend.set("plugin_gmail_tokens", "a" * 100)  # 5 chunks
    backend.set("plugin_gmail_tokens", "b" * 30)  # 2 chunks

    assert backend.get("plugin_gmail_tokens") == "b" * 30
    assert set(primitive.store) == {
        "plugin_gmail_tokens",
        "plugin_gmail_tokens__0",
        "plugin_gmail_tokens__1",
    }


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


def test_store_delete_raises_when_backend_silently_retains_token():
    backend = _SilentDeleteBackend()
    store = TokenStore(backend)
    store.save("gmail", Tokens(access="access"))

    with pytest.raises(RuntimeError, match="token deletion failed for plugin 'gmail'"):
        store.delete("gmail")

    assert store.load("gmail") is not None


def test_store_delete_keeps_manifest_until_every_indexed_chunk_is_removed():
    backend = _PartialOverflowDeleteBackend(limit=1000)
    store = TokenStore(ChunkedBackend(backend, chunk_size=20))
    store.save("gmail", Tokens(access="access" * 20))
    primary_key = "plugin_gmail_tokens"
    manifest = backend.store[primary_key]

    with pytest.raises(
        RuntimeError,
        match="token deletion could not be verified for plugin 'gmail'",
    ):
        store.delete("gmail")

    assert backend.store[primary_key] == manifest
    assert f"{primary_key}__0" not in backend.store
    assert backend.store[f"{primary_key}__1"]

    # The retry must use the retained manifest, skip the already-missing zero
    # index, and still detect chunk one instead of falsely reporting success.
    with pytest.raises(RuntimeError):
        store.delete("gmail")
    assert backend.store[primary_key] == manifest
    assert backend.store[f"{primary_key}__1"]

    backend.allow_chunk_one_delete = True
    store.delete("gmail")

    assert backend.store == {}


def test_shrink_persists_cleanup_extent_across_restart_until_delete_succeeds():
    backend = _PartialOverflowDeleteBackend(limit=1000)
    key = "plugin_gmail_tokens"
    chunked = ChunkedBackend(backend, chunk_size=20)
    chunked.set(key, "secret" * 20)

    # Shrinking replaces the active chunk header with a plain value. Chunk zero
    # is removed, chunk one silently remains, and later chunks are still tried.
    chunked.set(key, "small")

    assert chunked.get(key) == "small"
    assert f"{key}__0" not in backend.store
    assert backend.store[f"{key}__1"]
    extent = backend.store[f"{key}__extent"]
    assert int(extent) == 6

    # Rebuild both wrappers to model an application restart. Explicit deletion
    # must use the persisted extent rather than stop at the missing zero index.
    restarted_backend = ChunkedBackend(backend, chunk_size=20)
    assert restarted_backend.get(key) == "small"
    restarted = TokenStore(restarted_backend)
    with pytest.raises(RuntimeError):
        restarted.delete("gmail")

    assert backend.store[key] == "small"
    assert backend.store[f"{key}__extent"] == extent
    assert backend.store[f"{key}__1"]

    backend.allow_chunk_one_delete = True
    restarted.delete("gmail")

    assert backend.store == {}


def test_legacy_sparse_delete_finds_later_chunks_with_no_primary():
    backend = _SizeLimitedFakeBackend(limit=1000)
    key = "plugin_gmail_tokens"
    backend.store[f"{key}__1"] = "legacy-fragment-one"
    backend.store[f"{key}__4"] = "legacy-fragment-four"
    store = TokenStore(ChunkedBackend(backend))

    store.delete("gmail")

    assert backend.store == {}


def test_legacy_sparse_delete_finds_later_chunks_behind_plain_primary():
    backend = _SizeLimitedFakeBackend(limit=1000)
    key = "plugin_gmail_tokens"
    backend.store[key] = Tokens(access="small").to_json()
    backend.store[f"{key}__2"] = "legacy-fragment-two"
    store = TokenStore(ChunkedBackend(backend))

    store.delete("gmail")

    assert backend.store == {}


def test_legacy_sparse_delete_retains_primary_and_extent_after_noop():
    backend = _PartialOverflowDeleteBackend(limit=1000)
    key = "plugin_gmail_tokens"
    primary = Tokens(access="small").to_json()
    backend.store[key] = primary
    backend.store[f"{key}__1"] = "legacy-fragment-one"
    store = TokenStore(ChunkedBackend(backend))

    with pytest.raises(RuntimeError):
        store.delete("gmail")

    assert backend.store[key] == primary
    assert backend.store[f"{key}__1"] == "legacy-fragment-one"
    assert backend.store[f"{key}__extent"] == "2"

    backend.allow_chunk_one_delete = True
    store.delete("gmail")

    assert backend.store == {}
