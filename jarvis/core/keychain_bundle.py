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
a JSON object that maps key -> value. Every later get/set/delete is served
from an in-process cache with no further per-item OS prompt.

**Why one item is still not enough (the v2 layer).** Since macOS Sierra a
Keychain item also carries a *partition list*: even an item whose ACL says
"any application may read" silently admits only Apple-signed tools; every
unsigned binary (the venv python) still triggers the confirmation dialog —
and because "Always Allow" is bound to a code signature the unsigned
interpreter does not have, the grant never sticks. Each fresh python
process (main app, mission worker, CLI, pytest) then re-prompts once per
boot. The fix: route the vault item's I/O through ``/usr/bin/security`` —
an Apple-signed tool inside the ``apple-tool:`` partition — and create the
item with the "allow any application" ACL (``add-generic-password -A``).
Items written that way are readable and writable via the CLI with ZERO
dialogs, from any process, forever, regardless of interpreter signing.

Security tradeoff, stated honestly: a ``-A`` item is readable by any local
process running as the user (same trust level as this project's 0600
file-store fallback), while remaining Keychain-encrypted at rest and locked
with the keychain. The alternative was an unfixable per-process dialog
storm, because an unsigned interpreter can never hold a durable grant.

Secrets never appear on a command line: writes stream the payload to
``security -i`` via stdin, reads receive it on stdout. The payload is the
base64-encoded JSON bundle — base64 doubles as the format marker that the
item was already rewritten with the ``-A`` ACL (plain JSON means a
keyring-written legacy vault that still needs the one-time upgrade).

Windows and Linux never construct this class: ``jarvis/core/config.py`` only
wraps the platform backend when ``sys.platform == "darwin"``, so their
per-item keyring behavior is byte-identical to before this module existed.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import subprocess
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Account name for the single bundled Keychain item. Chosen to be extremely
# unlikely to collide with any real credential slot name (those are plain
# identifiers like "anthropic_api_key"); the double-underscore/dunder-ish
# shape also makes it obviously internal in the macOS Keychain Access UI.
VAULT_ACCOUNT = "__jarvis_vault__"

_SECURITY_BIN = "/usr/bin/security"
# ``security`` exits 44 (errSecItemNotFound) when the queried item is absent —
# probed empirically; the only non-zero exit treated as a clean "no value".
_SECURITY_NOT_FOUND_EXIT = 44
# Tokens interpolated into a ``security -i`` command line. Service and account
# names are fixed internal constants ("personal-jarvis", "__jarvis_vault__");
# anything outside this conservative charset falls back to the inner backend
# rather than risking the CLI's whitespace/quote parsing.
_SAFE_CLI_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")
# The written payload is standard base64 (never starts with ``-``, contains no
# whitespace/quotes), so it is safe on a ``security -i`` line after ``-w``.
_SAFE_CLI_VALUE = re.compile(r"^[A-Za-z0-9+/=]+$")


class SecurityCliVaultError(RuntimeError):
    """A ``security`` CLI vault operation failed (denied, timed out, crashed)."""


class SecurityCliVault:
    """Vault-item I/O through the Apple-signed ``/usr/bin/security`` tool.

    Only the single bundled vault item goes through this store. Reads may
    legitimately BLOCK on one interactive dialog exactly once — when the
    vault item was originally written in-process by ``keyring`` (python ACL)
    and the CLI needs the user's one-time consent to migrate it — so the
    read timeout is generous. Writes and deletes only ever touch the
    CLI-created ``-A`` item (or a deletable legacy one) and never prompt.
    """

    _READ_TIMEOUT_S = 180.0  # may wait on the ONE interactive upgrade consent
    _WRITE_TIMEOUT_S = 20.0

    def __init__(self, binary: str = _SECURITY_BIN) -> None:
        self._binary = binary

    @staticmethod
    def _creationflags() -> int:
        # AP-1 discipline; the constant is 0 on POSIX, and this class only
        # ever runs on macOS anyway.
        from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

        return NO_WINDOW_CREATIONFLAGS

    @staticmethod
    def _check_tokens(*tokens: str) -> None:
        for token in tokens:
            if not _SAFE_CLI_TOKEN.match(token):
                raise SecurityCliVaultError(
                    f"unsafe token for security CLI interpolation: {token!r}"
                )

    def read(self, service: str, account: str) -> str | None:
        """Return the stored payload, ``None`` if absent, raise on denial."""
        self._check_tokens(service, account)
        try:
            proc = subprocess.run(  # noqa: S603 -- fixed Apple-signed binary
                [self._binary, "find-generic-password", "-s", service,
                 "-a", account, "-w"],
                capture_output=True,
                text=True,
                timeout=self._READ_TIMEOUT_S,
                creationflags=self._creationflags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SecurityCliVaultError(f"security CLI read failed: {exc}") from exc
        if proc.returncode == 0:
            return proc.stdout.rstrip("\n")
        if proc.returncode == _SECURITY_NOT_FOUND_EXIT:
            return None
        raise SecurityCliVaultError(
            f"security CLI read exited {proc.returncode}: {proc.stderr.strip()!r}"
        )

    def write(self, service: str, account: str, value: str) -> None:
        """Replace the vault item with a fresh any-application-ACL copy.

        Delete-then-add: updating an item in place would need the OLD item's
        ACL consent, while deleting never decrypts (always silent) and the
        fresh ``-A`` item admits every later CLI access without a dialog.
        The payload streams through stdin (``security -i``), never argv.
        """
        self._check_tokens(service, account)
        if not _SAFE_CLI_VALUE.match(value):
            raise SecurityCliVaultError(
                "vault payload is not clean base64; refusing CLI interpolation"
            )
        self.delete(service, account)
        command = (
            f"add-generic-password -U -A -s {service} -a {account} -w {value}\n"
        )
        try:
            proc = subprocess.run(  # noqa: S603 -- fixed Apple-signed binary
                [self._binary, "-i"],
                input=command,
                capture_output=True,
                text=True,
                timeout=self._WRITE_TIMEOUT_S,
                creationflags=self._creationflags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SecurityCliVaultError(f"security CLI write failed: {exc}") from exc
        if proc.returncode != 0:
            raise SecurityCliVaultError(
                f"security CLI write exited {proc.returncode}: {proc.stderr.strip()!r}"
            )
        # ``security -i`` can swallow a failing sub-command's exit status;
        # trust only a verified read-back.
        if self.read(service, account) != value:
            raise SecurityCliVaultError("security CLI write read-back mismatch")

    def delete(self, service: str, account: str) -> None:
        """Best-effort silent delete; absence is not an error."""
        self._check_tokens(service, account)
        try:
            subprocess.run(  # noqa: S603 -- fixed Apple-signed binary
                [self._binary, "delete-generic-password", "-s", service,
                 "-a", account],
                capture_output=True,
                text=True,
                timeout=self._WRITE_TIMEOUT_S,
                creationflags=self._creationflags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SecurityCliVaultError(f"security CLI delete failed: {exc}") from exc


def darwin_security_cli_vault() -> SecurityCliVault | None:
    """Return the CLI vault store when this host can use it, else ``None``.

    ``None`` (not darwin, or no ``security`` binary) keeps the wrapper on its
    pure in-process keyring behavior — exactly what every unit test that
    constructs ``DarwinBundleKeyringBackend(fake_inner)`` relies on.
    """
    if sys.platform != "darwin":
        return None
    import os

    if not os.path.exists(_SECURITY_BIN):
        return None
    return SecurityCliVault()


try:
    # ``keyring.set_keyring`` type-checks against this base class — a plain
    # object is rejected with ``TypeError`` (which the fail-open boot path
    # swallowed, silently leaving the raw per-item backend active: the v1
    # regression that kept the dialog storm alive). Subclass it whenever the
    # package is present; fall back to ``object`` so this module still
    # imports without ``keyring`` installed.
    from keyring.backend import KeyringBackend as _KeyringBackendBase
except ImportError:  # pragma: no cover -- keyring is present in every dev env
    _KeyringBackendBase = object  # type: ignore[assignment, misc]


class DarwinBundleKeyringBackend(_KeyringBackendBase):  # type: ignore[valid-type, misc]
    """Wrap an inner keyring backend, collapsing all secrets into one item.

    Implements the three methods ``jarvis/core/config.py`` calls through
    the process-global ``keyring`` module —
    ``get_password(service, key)``, ``set_password(service, key, value)``,
    ``delete_password(service, key)`` — so any object exposing that trio (a
    real platform backend, or a fake in tests) can be wrapped. MUST remain a
    ``keyring.backend.KeyringBackend`` subclass: ``keyring.set_keyring``
    rejects anything else, and the boot path treats that rejection as a
    swallowed no-op.
    """

    # Above every real platform backend (macOS Keychain announces 5), though
    # the wrapper is only ever installed explicitly via ``set_keyring``,
    # never through priority-based discovery (its ctor needs ``inner``, so
    # discovery instantiation fails harmlessly).
    priority = 6.0  # type: ignore[assignment]

    # Lets ``config._is_platform_keyring_backend`` recognize this wrapper as
    # a platform backend by delegating the check to the wrapped instance,
    # instead of inspecting class/module names against an unpinned library
    # (AP-28).
    _jarvis_platform_wrapper = True

    def __init__(self, inner: Any, cli: SecurityCliVault | None = None) -> None:
        if _KeyringBackendBase is not object:
            super().__init__()
        self._inner = inner
        # When present, vault-item reads/writes go through the Apple-signed
        # ``security`` CLI (partition-list-proof, zero dialogs once the item
        # carries the ``-A`` ACL). ``None`` — the default, and what every
        # fake-backed unit test gets — keeps pure in-process keyring I/O.
        self._cli = cli
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

    @staticmethod
    def _decode_vault_payload(raw: str) -> tuple[dict[str, Any] | None, bool]:
        """Parse a stored vault payload.

        Returns ``(bundle, was_base64)``; ``(None, False)`` when malformed.
        Plain JSON is the legacy in-process ``keyring`` format; base64-wrapped
        JSON marks an item already rewritten through the ``security`` CLI
        with the any-application ACL. The two are unambiguous: JSON of an
        object starts with ``{``, which is outside the base64 alphabet, and
        base64 text can never parse as a JSON object.
        """
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if parsed is not None:
            return (parsed, False) if isinstance(parsed, dict) else (None, False)
        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
            parsed = json.loads(decoded)
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return None, False
        return (parsed, True) if isinstance(parsed, dict) else (None, False)

    def _load_locked(self, service: str) -> dict[str, str]:
        """Return the parsed vault dict for *service*, loading it once.

        Caller must hold ``self._lock``. On a JSON-decode error, sets
        ``self._bundle_unusable`` and returns an empty dict without touching
        the stored item.
        """
        cached = self._cache.get(service)
        if cached is not None:
            return cached
        raw: str | None = None
        used_cli = False
        if self._cli is not None:
            try:
                raw = self._cli.read(service, VAULT_ACCOUNT)
                used_cli = True
            except Exception:  # noqa: BLE001 -- CLI oddity must not hide the vault
                logger.warning(
                    "security CLI vault read failed; falling back to the "
                    "in-process keyring read (which may show one Keychain "
                    "dialog).",
                    exc_info=True,
                )
        if not used_cli:
            raw = self._inner.get_password(service, VAULT_ACCOUNT)
        if raw is None:
            bundle: dict[str, str] = {}
            self._cache[service] = bundle
            return bundle
        parsed, was_base64 = self._decode_vault_payload(raw)
        if parsed is None:
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
        if used_cli and not was_base64:
            # A keyring-written legacy vault, just read through the CLI (the
            # one-time consent dialog, if any, already happened). Rewrite it
            # with the any-application ACL so no process ever prompts again.
            try:
                self._save_locked(service, bundle)
            except Exception:  # noqa: BLE001 -- upgrade is best-effort
                logger.warning(
                    "one-time Keychain vault ACL upgrade failed; the next "
                    "process may still see a Keychain dialog.",
                    exc_info=True,
                )
        return bundle

    def _save_locked(self, service: str, bundle: dict[str, str]) -> None:
        """Persist *bundle* as the single vault item. Caller holds the lock."""
        payload = json.dumps(bundle)
        if self._cli is not None:
            try:
                encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
                self._cli.write(service, VAULT_ACCOUNT, encoded)
                return
            except Exception:  # noqa: BLE001 -- CLI failure degrades, never loses
                logger.warning(
                    "security CLI vault write failed; falling back to the "
                    "in-process keyring write (per-app ACL, may prompt).",
                    exc_info=True,
                )
        self._inner.set_password(service, VAULT_ACCOUNT, payload)

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
