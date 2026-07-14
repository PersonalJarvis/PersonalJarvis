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

        # Explicit disconnect is fail-closed. ``get_secret`` may degrade a
        # locked platform keyring to an empty file fallback, so a later read is
        # not proof that the OS credential was removed. ``delete_secret`` is
        # the authoritative cross-backend verification result.
        if not delete_secret(key):
            raise RuntimeError(f"keyring delete could not be verified for {key!r}")

    def delete_best_effort(self, key: str) -> None:
        """Delete housekeeping data without masking an already-successful save."""
        from jarvis.core.config import delete_secret

        delete_secret(key)


_CHUNK_SENTINEL = "\x00JCHUNKS\x00"  # primary-key header for a chunked value: sentinel + <count>
_CLEANUP_EXTENT_SUFFIX = "__extent"  # non-secret exclusive chunk cleanup extent
# Historical production values used 1000-character chunks. A one-mebibyte
# logical token budget is orders of magnitude above every catalog OAuth token
# shape while keeping the one-time legacy-disconnect sweep finite.
_HISTORIC_CHUNK_SIZE = 1000
_LEGACY_TOKEN_SCAN_BUDGET_CHARS = 1024 * 1024
_LEGACY_SPARSE_SCAN_SLOTS = (
    _LEGACY_TOKEN_SCAN_BUDGET_CHARS + _HISTORIC_CHUNK_SIZE - 1
) // _HISTORIC_CHUNK_SIZE


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
    A non-secret ``<key>__extent`` sidecar retains the maximum cleanup extent
    only when interrupted or best-effort deletion may have left old chunks.
    """

    # Stay well under the observed 1280-char Credential-Manager limit so a few
    # non-ASCII chars (extra UTF-16 bytes) can't push a single chunk over.
    DEFAULT_CHUNK_SIZE = _HISTORIC_CHUNK_SIZE

    def __init__(self, backend: TokenBackend, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self._backend = backend
        self._chunk_size = chunk_size

    def _delete_entry(self, key: str, *, strict: bool) -> None:
        if not strict:
            best_effort = getattr(self._backend, "delete_best_effort", None)
            if callable(best_effort):
                best_effort(key)
                return
        self._backend.delete(key)

    @staticmethod
    def _overflow_key(key: str, index: int) -> str:
        return f"{key}__{index}"

    @staticmethod
    def _cleanup_extent_key(key: str) -> str:
        return f"{key}{_CLEANUP_EXTENT_SUFFIX}"

    @staticmethod
    def _manifest_count(key: str, head: str) -> int:
        try:
            count = int(head[len(_CHUNK_SENTINEL) :])
        except ValueError as exc:
            raise RuntimeError(f"invalid chunk manifest for {key!r}") from exc
        if count < 1:
            raise RuntimeError(f"invalid chunk manifest for {key!r}")
        return count

    def _cleanup_extent(self, key: str) -> int:
        raw = self._backend.get(self._cleanup_extent_key(key))
        if raw is None:
            return 0
        try:
            extent = int(raw)
        except ValueError as exc:
            raise RuntimeError(f"invalid cleanup extent for {key!r}") from exc
        if extent < 1:
            raise RuntimeError(f"invalid cleanup extent for {key!r}")
        return extent

    def _persist_cleanup_extent(self, key: str, extent: int) -> None:
        current = self._cleanup_extent(key)
        if current >= extent:
            return
        extent_key = self._cleanup_extent_key(key)
        value = str(extent)
        self._backend.set(extent_key, value)
        if self._backend.get(extent_key) != value:
            raise RuntimeError(f"could not persist cleanup extent for {key!r}")

    def _clear_cleanup_extent_best_effort(self, key: str) -> None:
        try:
            self._delete_entry(self._cleanup_extent_key(key), strict=False)
        except Exception:  # noqa: BLE001 - save already succeeded
            return

    def _clear_cleanup_extent_verified(self, key: str) -> None:
        extent_key = self._cleanup_extent_key(key)
        self._delete_entry(extent_key, strict=True)
        if self._backend.get(extent_key) is not None:
            raise RuntimeError(f"could not delete cleanup extent for {key!r}")

    def _clear_overflow(self, key: str, *, start_index: int = 0) -> bool:
        """Best-effort delete contiguous ``<key>__i`` overflow pieces.

        This helper runs after a successful save, so it explicitly selects a
        backend's best-effort deletion path instead of strict disconnect
        semantics. A backend may therefore leave a chunk readable after the
        attempt. Stop at that first retained chunk instead of probing an
        unbounded sequence of generated keys, and report whether every
        discovered chunk was removed. Explicit deletion uses the persisted
        cleanup extent below so gaps cannot hide later chunks.
        """
        i = start_index
        while True:
            chunk_key = self._overflow_key(key, i)
            try:
                piece = self._backend.get(chunk_key)
            except Exception:  # noqa: BLE001 - keep save-time cleanup best-effort
                return False
            if piece is None:
                return True
            try:
                self._persist_cleanup_extent(key, i + 1)
            except Exception:  # noqa: BLE001 - keep save-time cleanup best-effort
                return False
            try:
                self._delete_entry(chunk_key, strict=False)
                retained = self._backend.get(chunk_key)
            except Exception:  # noqa: BLE001 - keep save-time cleanup best-effort
                return False
            if retained is not None:
                return False
            i += 1

    def _clear_indexed_overflow(
        self,
        key: str,
        count: int,
        *,
        start_index: int = 0,
        strict: bool = False,
    ) -> bool:
        """Attempt and verify every chunk named by a manifest or extent."""
        cleared = True
        for i in range(start_index, count):
            chunk_key = self._overflow_key(key, i)
            try:
                piece = self._backend.get(chunk_key)
            except Exception:  # noqa: BLE001 - finish the bounded cleanup pass
                cleared = False
                continue
            if piece is None:
                continue
            try:
                self._delete_entry(chunk_key, strict=strict)
            except Exception:  # noqa: BLE001 - finish the bounded cleanup pass
                cleared = False
                continue
            try:
                retained = self._backend.get(chunk_key)
            except Exception:  # noqa: BLE001 - finish the bounded cleanup pass
                cleared = False
                continue
            if retained is not None:
                cleared = False
        return cleared

    def _clear_sparse_legacy_overflow(self, key: str) -> bool:
        """Bounded sweep for pre-manifest orphan chunks during disconnect.

        Old cleanup could remove ``__0`` and lose the primary header before a
        later deletion silently failed. With neither header nor extent there is
        no exact upper bound left, so explicit legacy disconnect scans the full
        one-mebibyte compatibility budget instead of stopping at the first gap.
        Every discovered index is first recorded in the new durable extent.
        """
        cleared = True
        for i in range(_LEGACY_SPARSE_SCAN_SLOTS):
            chunk_key = self._overflow_key(key, i)
            try:
                piece = self._backend.get(chunk_key)
            except Exception:  # noqa: BLE001 - finish the bounded cleanup pass
                cleared = False
                continue
            if piece is None:
                continue
            try:
                self._persist_cleanup_extent(key, i + 1)
            except Exception:  # noqa: BLE001 - retain an untracked fragment
                cleared = False
                continue
            try:
                self._delete_entry(chunk_key, strict=True)
                retained = self._backend.get(chunk_key)
            except Exception:  # noqa: BLE001 - finish the bounded cleanup pass
                cleared = False
                continue
            if retained is not None:
                cleared = False
        return cleared

    def get(self, key: str) -> str | None:
        head = self._backend.get(key)
        if head is None or not head.startswith(_CHUNK_SENTINEL):
            return head  # missing, small, or legacy-plain value
        count = self._manifest_count(key, head)
        parts: list[str] = []
        for i in range(count):
            piece = self._backend.get(self._overflow_key(key, i))
            if piece is None:
                raise RuntimeError(f"missing chunk {i}/{count} for {key!r}")
            parts.append(piece)
        return "".join(parts)

    def set(self, key: str, value: str) -> None:
        """Write ``value``, chunking it when it exceeds the backend's per-entry cap.

        Write-then-swap: the new data (and, for a multi-chunk value, the new
        header) is written FIRST; only once that has fully succeeded do we drop
        chunks a previous, larger value left behind. The old header keeps
        pointing at the old data until the very last step, so a failure
        partway through the new write can roll back cleanly instead of
        leaving a header that points at deleted/missing chunks (the previous
        delete-then-write order did exactly that — a mid-write failure lost
        the last good token).
        """
        old_head = self._backend.get(key)
        old_active_count = (
            self._manifest_count(key, old_head)
            if old_head is not None and old_head.startswith(_CHUNK_SENTINEL)
            else 0
        )
        prior_cleanup_extent = self._cleanup_extent(key)
        prior_extent = max(old_active_count, prior_cleanup_extent)

        if len(value) <= self._chunk_size:
            if prior_extent:
                # Persist the old maximum before replacing its primary header.
                # A partial cleanup can then be completed after a restart.
                self._persist_cleanup_extent(key, prior_extent)
            # Fits in one entry: no chunks of its own. Write it first, then
            # drop any old overflow pieces a previous larger value left.
            self._backend.set(key, value)
            if prior_extent:
                cleanup_complete = self._clear_indexed_overflow(key, prior_extent)
            else:
                cleanup_complete = self._clear_overflow(key)
            if cleanup_complete:
                self._clear_cleanup_extent_best_effort(key)
            return

        chunks = [value[i : i + self._chunk_size] for i in range(0, len(value), self._chunk_size)]
        tracked_extent = max(prior_extent, len(chunks))
        # Record the maximum extent before overwriting any chunk. This also
        # makes rollback cleanup recoverable if a primitive delete silently
        # fails after a partial write.
        self._persist_cleanup_extent(key, tracked_extent)
        # Snapshot whatever currently sits at each index we're about to
        # overwrite (old chunk data, or None) so a partial failure can put it
        # back exactly as it was rather than leaving a gap under the still-
        # active OLD header.
        prior = [self._backend.get(self._overflow_key(key, i)) for i in range(len(chunks))]

        written = 0
        try:
            for i, piece in enumerate(chunks):
                self._backend.set(self._overflow_key(key, i), piece)
                written = i + 1
            self._backend.set(key, f"{_CHUNK_SENTINEL}{len(chunks)}")
        except Exception:
            for i in range(written):
                old_piece = prior[i]
                if old_piece is None:
                    self._delete_entry(self._overflow_key(key, i), strict=False)
                else:
                    self._backend.set(self._overflow_key(key, i), old_piece)
            raise

        # The new header is live now — safe to drop old chunks the new value
        # no longer needs (old_count > new_count leftovers).
        cleanup_complete = self._clear_indexed_overflow(
            key, tracked_extent, start_index=len(chunks)
        )
        if cleanup_complete:
            self._clear_cleanup_extent_best_effort(key)

    def delete(self, key: str) -> None:
        # Snapshot the manifest before touching any pieces. A previous partial
        # delete may already have left gaps, so an active chunked value must use
        # its exact persisted count rather than stop at the first missing index.
        head = self._backend.get(key)
        persisted_cleanup_extent = self._cleanup_extent(key)
        active_count = 0
        if head is not None and head.startswith(_CHUNK_SENTINEL):
            active_count = self._manifest_count(key, head)
        if persisted_cleanup_extent:
            cleanup_extent = max(active_count, persisted_cleanup_extent)
            overflow_cleared = self._clear_indexed_overflow(key, cleanup_extent, strict=True)
        else:
            # A pre-sidecar header records only its active prefix; an older
            # failed shrink may have left sparse chunks beyond that count.
            # Expensive only for explicit disconnect of this legacy shape.
            overflow_cleared = self._clear_sparse_legacy_overflow(key)
        if not overflow_cleared:
            raise RuntimeError(f"could not delete every overflow chunk for {key!r}")
        # The extent is the durable retry manifest for small values and old
        # chunks beyond a newer header. Remove it only after every named chunk
        # is verified absent, then remove the primary value/header.
        self._clear_cleanup_extent_verified(key)
        self._delete_entry(key, strict=True)


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
            raise RuntimeError(f"corrupted token blob for plugin {plugin_id!r}: {exc}") from exc

    def delete(self, plugin_id: str) -> None:
        key = _keyring_key(plugin_id)
        try:
            self._backend.delete(key)
            remaining = self._backend.get(key)
        except Exception as exc:
            raise RuntimeError(
                f"token deletion could not be verified for plugin {plugin_id!r}"
            ) from exc
        if remaining is not None:
            raise RuntimeError(f"token deletion failed for plugin {plugin_id!r}")
