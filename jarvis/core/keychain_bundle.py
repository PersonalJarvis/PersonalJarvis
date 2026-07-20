"""macOS Keychain item-count collapse for Jarvis secrets (BUG-103).

On macOS every distinct ``keyring.set_password``/``get_password`` call
becomes its own Keychain item under the same service. An unsigned
interpreter (the dev venv ``python``) is not code-signed, so the OS
re-prompts "Allow / Always Allow / Deny" separately for EACH item — the
pre-boot key check alone touches roughly ten provider slots, producing a
storm of 5-10+ dialogs on every single boot.

``DarwinBundleKeyringBackend`` wraps the platform-detected keyring backend
(composition, not a registered ``keyring`` plugin) and collapses every
secret into ONE Keychain item — account name ``__jarvis_vault__`` — holding
a JSON object that maps key -> value. The user clicks "Always Allow" exactly
once for that single item; every later get/set/delete is served from an
in-process cache with no further per-item OS prompt.

Windows and Linux never construct this class: ``jarvis/core/config.py`` only
wraps the platform backend when ``sys.platform == "darwin"``, so their
per-item keyring behavior is byte-identical to before this module existed.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Account name for the single bundled Keychain item. Chosen to be extremely
# unlikely to collide with any real credential slot name (those are plain
# identifiers like "anthropic_api_key"); the double-underscore/dunder-ish
# shape also makes it obviously internal in the macOS Keychain Access UI.
VAULT_ACCOUNT = "__jarvis_vault__"


class DarwinBundleKeyringBackend:
    """Wrap an inner keyring backend, collapsing all secrets into one item.

    Only implements the three methods ``jarvis/core/config.py`` calls through
    the process-global ``keyring`` module —
    ``get_password(service, key)``, ``set_password(service, key, value)``,
    ``delete_password(service, key)`` — so any object exposing that trio (a
    real platform backend, or a fake in tests) can be wrapped.
    """

    # Lets ``config._is_platform_keyring_backend`` recognize this wrapper as
    # a platform backend by delegating the check to the wrapped instance,
    # instead of inspecting class/module names against an unpinned library
    # (AP-28).
    _jarvis_platform_wrapper = True

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        # Per-service cache of the parsed vault dict. Keyed by ``service``
        # because a single wrapper instance could in principle see more than
        # one service string, and each gets its own single vault item on the
        # inner backend. In practice jarvis/core/config.py always passes the
        # same ``KEYRING_SERVICE`` constant, so this holds exactly one entry.
        self._cache: dict[str, dict[str, str]] = {}
        # Set once an existing vault item fails to parse as a JSON object.
        # From then on every call for the rest of the process delegates
        # straight to the inner backend so the malformed item is read
        # (logged once), never silently overwritten or destroyed.
        self._bundle_unusable = False

    # -- internal helpers ---------------------------------------------------

    def _load_locked(self, service: str) -> dict[str, str]:
        """Return the parsed vault dict for *service*, loading it once.

        Caller must hold ``self._lock``. On a JSON-decode error, sets
        ``self._bundle_unusable`` and returns an empty dict without touching
        the stored item.
        """
        cached = self._cache.get(service)
        if cached is not None:
            return cached
        raw = self._inner.get_password(service, VAULT_ACCOUNT)
        if raw is None:
            bundle: dict[str, str] = {}
            self._cache[service] = bundle
            return bundle
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Jarvis Keychain vault item is not a JSON object")
        except Exception:  # noqa: BLE001 -- any parse failure disables the bundle
            logger.warning(
                "macOS Keychain vault item %r is malformed; falling back to "
                "per-item Keychain storage for the rest of this process. The "
                "malformed item is left untouched.",
                VAULT_ACCOUNT,
            )
            self._bundle_unusable = True
            return {}
        bundle = {str(k): str(v) for k, v in parsed.items()}
        self._cache[service] = bundle
        return bundle

    def _save_locked(self, service: str, bundle: dict[str, str]) -> None:
        """Persist *bundle* as the single vault item. Caller holds the lock."""
        self._inner.set_password(service, VAULT_ACCOUNT, json.dumps(bundle))

    @staticmethod
    def _raise_missing_password_error(service: str, key: str) -> None:
        """Raise the keyring-standard "no such password" error.

        Matches the contract callers in ``jarvis/core/config.py`` already
        handle (``keyring.errors.PasswordDeleteError``). Falls back to
        ``KeyError`` if the ``keyring`` package is unavailable, so this
        module never hard-depends on it at import time.
        """
        message = f"No such password for service {service!r} and key {key!r}"
        try:
            from keyring.errors import PasswordDeleteError
        except ImportError:
            raise KeyError(message) from None
        raise PasswordDeleteError(message)

    # -- public keyring-backend surface -------------------------------------

    def get_password(self, service: str, key: str) -> str | None:
        if key == VAULT_ACCOUNT:
            # The vault item itself is never bundled into itself.
            return self._inner.get_password(service, key)
        if self._bundle_unusable:
            return self._inner.get_password(service, key)

        with self._lock:
            bundle = self._load_locked(service)
            if self._bundle_unusable:
                # Discovered just now while loading; do not treat this read
                # as a bundle miss, delegate straight through instead.
                return self._inner.get_password(service, key)
            if key in bundle:
                return bundle[key]

        # Bundle miss: a legacy per-key item written before this wrapper
        # existed (or before this key was ever saved) may still be present.
        # Read it through the inner backend and, if found, migrate it into
        # the bundle so it never re-prompts again.
        legacy_val = self._inner.get_password(service, key)
        if legacy_val is None:
            return None
        try:
            self._migrate_legacy(service, key, legacy_val)
        except Exception:  # noqa: BLE001 -- migration is best-effort
            logger.warning(
                "Failed to migrate legacy Keychain item %r into the Jarvis "
                "vault; returning the value anyway and leaving the legacy "
                "item in place.",
                key,
            )
        return legacy_val

    def _migrate_legacy(self, service: str, key: str, value: str) -> None:
        with self._lock:
            bundle = self._load_locked(service)
            if self._bundle_unusable:
                # Nothing to migrate into; leave the legacy item alone.
                return
            bundle[key] = value
            self._save_locked(service, bundle)
        # Best-effort: the legacy item stops prompting forever once removed,
        # but a failure here must not undo the successful bundle write above
        # or lose the value we already returned to the caller.
        try:
            self._inner.delete_password(service, key)
        except Exception:  # noqa: BLE001, S110 -- legacy item may already be gone
            pass

    def set_password(self, service: str, key: str, value: str) -> None:
        if key == VAULT_ACCOUNT:
            self._inner.set_password(service, key, value)
            return
        if self._bundle_unusable:
            self._inner.set_password(service, key, value)
            return
        with self._lock:
            bundle = self._load_locked(service)
            if self._bundle_unusable:
                self._inner.set_password(service, key, value)
                return
            bundle[key] = value
            self._save_locked(service, bundle)

    def delete_password(self, service: str, key: str) -> None:
        if key == VAULT_ACCOUNT:
            self._inner.delete_password(service, key)
            return
        if self._bundle_unusable:
            self._inner.delete_password(service, key)
            return

        removed_from_bundle = False
        with self._lock:
            bundle = self._load_locked(service)
            if self._bundle_unusable:
                self._inner.delete_password(service, key)
                return
            if key in bundle:
                del bundle[key]
                self._save_locked(service, bundle)
                removed_from_bundle = True

        # Best-effort: also remove any legacy per-key item so it stops
        # prompting forever, but a missing legacy item (the common case) must
        # not turn a real bundle delete into a false "nothing to delete".
        removed_legacy = False
        try:
            self._inner.delete_password(service, key)
            removed_legacy = True
        except Exception:  # noqa: BLE001 -- absent legacy item is expected
            removed_legacy = False

        if not removed_from_bundle and not removed_legacy:
            self._raise_missing_password_error(service, key)
