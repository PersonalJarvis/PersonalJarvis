"""Proxy configuration — real keys + base URLs from ENV only.

Real vendor keys and the admin key are read from the environment and are NEVER
written to disk or logs. The provider->vendor wire contract lives in
:mod:`keyproxy.vendors`; this module only attaches the real per-provider key
(and an optional base-URL override) to that contract.

Environment variables (per provider id, upper-cased with ``-`` -> ``_``):
    KEYPROXY_<PROVIDER_ID>_KEY      real vendor key (required to enable it)
    KEYPROXY_<PROVIDER_ID>_BASE     optional base-URL override
    KEYPROXY_ADMIN_KEY             bearer for the /admin endpoints
    KEYPROXY_ALLOW_INSECURE        "1"/"true" to allow token auth over plain HTTP (dev)
    KEYPROXY_DB_PATH               sqlite path (default ~/.keyproxy/keyproxy.sqlite)
"""

from __future__ import annotations

import os

from . import vendors

_TRUE = {"1", "true", "yes", "on"}


class ProxyConfig:
    """Holds the per-provider real (vendor, base, key) plus operational flags.

    ``__repr__`` and ``__str__`` deliberately omit every secret so the object
    can be logged without leaking keys.
    """

    def __init__(
        self,
        *,
        providers: dict[str, tuple[str, str, str]],
        admin_key: str | None,
        allow_insecure: bool,
    ) -> None:
        # provider_id -> (vendor, real_base, real_key)
        self._providers = providers
        self.admin_key = admin_key
        self.allow_insecure = allow_insecure

    # ------------------------------------------------------------------
    # ENV name helpers (also the documented wire convention)
    # ------------------------------------------------------------------

    @staticmethod
    def _env_stem(provider_id: str) -> str:
        return provider_id.upper().replace("-", "_")

    @classmethod
    def env_key_name(cls, provider_id: str) -> str:
        return f"KEYPROXY_{cls._env_stem(provider_id)}_KEY"

    @classmethod
    def env_base_name(cls, provider_id: str) -> str:
        return f"KEYPROXY_{cls._env_stem(provider_id)}_BASE"

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def lookup(self, provider_id: str) -> tuple[str, str, str] | None:
        """``provider_id -> (vendor, real_base, real_key)`` or ``None``.

        Returns ``None`` for an unknown provider id OR a known provider with no
        real key configured (fail closed — never invent a key).
        """
        return self._providers.get(provider_id)

    def configured_providers(self) -> list[str]:
        """Provider ids that have a real key configured."""
        return sorted(self._providers.keys())

    def __repr__(self) -> str:  # pragma: no cover - trivial, but secret-safe
        return (
            f"ProxyConfig(providers={self.configured_providers()}, "
            f"admin_key={'set' if self.admin_key else 'unset'}, "
            f"allow_insecure={self.allow_insecure})"
        )

    __str__ = __repr__


def load_config(env: dict[str, str] | None = None) -> ProxyConfig:
    """Build a :class:`ProxyConfig` from the environment."""
    src = os.environ if env is None else env

    providers: dict[str, tuple[str, str, str]] = {}
    for provider_id, (vendor, default_base) in vendors.PROVIDER_VENDORS.items():
        key = (src.get(ProxyConfig.env_key_name(provider_id)) or "").strip()
        if not key:
            continue  # not configured -> not available (fail closed)
        base = (src.get(ProxyConfig.env_base_name(provider_id)) or "").strip()
        providers[provider_id] = (vendor, base or default_base, key)

    admin_key = (src.get("KEYPROXY_ADMIN_KEY") or "").strip() or None
    allow_insecure = (src.get("KEYPROXY_ALLOW_INSECURE") or "").strip().lower() in _TRUE

    return ProxyConfig(
        providers=providers,
        admin_key=admin_key,
        allow_insecure=allow_insecure,
    )
