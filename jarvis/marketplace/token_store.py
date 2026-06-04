"""Token storage for connected marketplace plugins.

Tokens persist as a JSON blob in Windows Credential Manager via the
existing `jarvis.core.config.{get,set,delete}_secret` helpers. The
keyring key follows the convention `plugin_{plugin_id}_tokens`, so a
single plugin maps to a single keyring entry — never split fields
across multiple keys.

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
        self._backend: TokenBackend = backend if backend is not None else KeyringBackend()

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
