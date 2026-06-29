"""Token storage for connected marketplace plugins.

Tokens persist as a JSON blob in Windows Credential Manager via the
existing `jarvis.core.config.{get,set,delete}_secret` helpers. The
keyring key follows the convention `plugin_{plugin_id}_tokens`, so a
single plugin maps to a single *logical* entry — the JSON fields are
never split across keys.

The blob *bytes*, however, may be split across several keyring entries by
`ChunkedBackend` (see below). The Windows Credential Manager caps one entry
at 2560 bytes (1280 UTF-16 chars) and fails with WinError 1783 ("CredWrite:
the stub received bad data") above it. A long Google/Gmail or Linear OAuth
token blob exceeds that, so the connect flow's final `TokenStore.save` raised
"keyring set failed" and the plugin never became connected. `ChunkedBackend`
transparently spreads an oversized blob over `<key>__0..N` entries and
reassembles it on read — do NOT "simplify" this back to a single set().

The `TokenStore` accepts a pluggable `TokenBackend` so unit tests can
use `InMemoryBackend` instead of touching the real Credential Manager.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class Tokens:
    """A plugin's auth state at a point in time. Immutable by design."""

    access: str
    refresh: str | None = None
    expires_at: datetime | None = None
    extra: dict[str, str] = field(default_factory=dict)
    # Set when the refresh scheduler hit an unrecoverable refresh (revoked /
    # un-healable). The entry is KEPT (never deleted) so the plugin stays
    # visible with a "Reconnect" affordance instead of silently disappearing.
    needs_reauth: bool = False

    def is_near_expiry(self, threshold_seconds: int = 600) -> bool:
        if self.expires_at is None:
            return False
        return (self.expires_at - datetime.now(UTC)) < timedelta(seconds=threshold_seconds)

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "access": self.access,
            "refresh": self.refresh,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "extra": dict(self.extra),
            "needs_reauth": self.needs_reauth,
        }
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> Tokens:
        data = json.loads(raw)
        expires_raw = data.get("expires_at")
        expires_at: datetime | None = None
        if expires_raw is not None:
            expires_at = datetime.fromisoformat(expires_raw)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
        return cls(
            access=data["access"],
            refresh=data.get("refresh"),
            expires_at=expires_at,
            extra=dict(data.get("extra") or {}),
            needs_reauth=bool(data.get("needs_reauth", False)),
        )


class TokenBackend(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeyringBackend:
    """Default backend: Windows Credential Manager via `keyring`.

    Delegates to the existing `jarvis.core.config` helpers so the keyring
    service name (`personal-jarvis`) and ENV-fallback semantics stay in
    one place. We deliberately do NOT pass `env_fallback` for plugin
    tokens — they must be configured through the Marketplace UI, not
    through environment variables (that would bypass the audit + lifecycle).
    """

    def get(self, key: str) -> str | None:
        from jarvis.core.config import get_secret

        return get_secret(key, env_fallback=None)

    def set(self, key: str, value: str) -> None:
        from jarvis.core.config import set_secret

        if not set_secret(key, value):
            raise RuntimeError(f"keyring set failed for {key!r}")

    def delete(self, key: str) -> None:
        from jarvis.core.config import delete_secret

        delete_secret(key)


_CHUNK_SENTINEL = "\x00JCHUNKS\x00"  # primary-key header for a chunked value: sentinel + <count>


class ChunkedBackend:
    """Wrap a size-limited `TokenBackend` so a value larger than the backend's
    per-entry cap is split across several entries and reassembled on read.

    The Windows Credential Manager rejects a blob over ~1280 chars (2560 bytes
    UTF-16) with WinError 1783, which made `keyring.set_password` raise for long
    Google/Linear OAuth tokens — the connect flow then failed at the final save
    and the plugin never connected.

    Layout: the primary key holds either a bare value (small or pre-chunking
    legacy) or a sentinel header ``\\x00JCHUNKS\\x00<n>``; the n pieces live in
    ``<key>__0 .. <key>__{n-1}``. A token blob is JSON and always starts with
    ``{``, so a legacy plain value is never mistaken for a header — reads stay
    backward-compatible with already-stored short tokens (Discord/Telegram).
    """

    # Stay well under the observed 1280-char Credential-Manager limit so a few
    # non-ASCII chars (extra UTF-16 bytes) can't push a single chunk over.
    DEFAULT_CHUNK_SIZE = 1000

    def __init__(self, backend: TokenBackend, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self._backend = backend
        self._chunk_size = chunk_size

    @staticmethod
    def _overflow_key(key: str, index: int) -> str:
        return f"{key}__{index}"

    def _clear_overflow(self, key: str) -> None:
        """Delete any ``<key>__i`` pieces left by a previous larger value."""
        i = 0
        while self._backend.get(self._overflow_key(key, i)) is not None:
            self._backend.delete(self._overflow_key(key, i))
            i += 1

    def get(self, key: str) -> str | None:
        head = self._backend.get(key)
        if head is None or not head.startswith(_CHUNK_SENTINEL):
            return head  # missing, small, or legacy-plain value
        count = int(head[len(_CHUNK_SENTINEL):])
        parts: list[str] = []
        for i in range(count):
            piece = self._backend.get(self._overflow_key(key, i))
            if piece is None:
                raise RuntimeError(f"missing chunk {i}/{count} for {key!r}")
            parts.append(piece)
        return "".join(parts)

    def set(self, key: str, value: str) -> None:
        # Drop any chunks from a previous larger value first so we never leave
        # orphaned overflow entries behind.
        self._clear_overflow(key)
        if len(value) <= self._chunk_size:
            self._backend.set(key, value)
            return
        chunks = [
            value[i : i + self._chunk_size]
            for i in range(0, len(value), self._chunk_size)
        ]
        # Write the pieces first; only stamp the header once they all succeed,
        # so a mid-write failure can't leave a header pointing at missing data.
        try:
            for i, piece in enumerate(chunks):
                self._backend.set(self._overflow_key(key, i), piece)
        except Exception:
            self._clear_overflow(key)
            raise
        self._backend.set(key, f"{_CHUNK_SENTINEL}{len(chunks)}")

    def delete(self, key: str) -> None:
        self._clear_overflow(key)
        self._backend.delete(key)


class InMemoryBackend:
    """Test backend. Holds tokens in a process-local dict."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


def _keyring_key(plugin_id: str) -> str:
    if not plugin_id or "/" in plugin_id or " " in plugin_id:
        raise ValueError(f"invalid plugin_id: {plugin_id!r}")
    return f"plugin_{plugin_id}_tokens"


class TokenStore:
    """Per-plugin token persistence.

    One keyring entry per plugin_id, holding the JSON-serialized `Tokens`.
    """

    def __init__(self, backend: TokenBackend | None = None) -> None:
        # The real Credential-Manager backend caps an entry at ~1280 chars, so
        # wrap it in ChunkedBackend; long OAuth tokens would otherwise fail to
        # save. An explicitly-injected backend (tests) is used as-is.
        self._backend: TokenBackend = (
            backend if backend is not None else ChunkedBackend(KeyringBackend())
        )

    def save(self, plugin_id: str, tokens: Tokens) -> None:
        self._backend.set(_keyring_key(plugin_id), tokens.to_json())

    def load(self, plugin_id: str) -> Tokens | None:
        raw = self._backend.get(_keyring_key(plugin_id))
        if raw is None:
            return None
        try:
            return Tokens.from_json(raw)
        except (ValueError, KeyError) as exc:
            raise RuntimeError(
                f"corrupted token blob for plugin {plugin_id!r}: {exc}"
            ) from exc

    def delete(self, plugin_id: str) -> None:
        self._backend.delete(_keyring_key(plugin_id))
