"""In-app secret save must survive a viable-but-LOCKED OS keyring (headless Linux).

The pure-headless case (no D-Bus at all -> keyring resolves to ``fail.Keyring``)
already degrades to the 0600 file store. But on a Linux host where a Secret
Service IS reachable yet its collection is LOCKED (a common SSH / auto-login /
partial-D-Bus container session), ``keyring.get_keyring()`` returns a viable
SecretService backend, NOT ``fail.Keyring`` — so the old fallback that only
engaged for ``fail.Keyring`` was skipped, ``keyring.set_password`` raised
``KeyringLocked``, and ``set_secret`` returned False. The in-app POST
``/secrets/{key}`` route turns that into HTTP 500, so pasting a key in the UI
failed and nothing persisted. A locked/unusable OS keyring must degrade to the
file store at RUNTIME, keeping the "recoverable in-app" guarantee (CLAUDE.md §3).
"""
from __future__ import annotations

import keyring
import keyring.backend
import pytest
from keyring.errors import KeyringLocked

from jarvis.core import config as c


class _LockedKeyring(keyring.backend.KeyringBackend):
    """A reachable OS keyring whose collection is locked: raises on every op.

    Deliberately NOT ``fail.Keyring`` — that is exactly the case the old
    ``isinstance(..., fail.Keyring)`` gate missed.
    """

    priority = 5  # type: ignore[assignment]

    def get_password(self, service: str, username: str) -> str | None:
        raise KeyringLocked("collection is locked")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise KeyringLocked("collection is locked")

    def delete_password(self, service: str, username: str) -> None:
        raise KeyringLocked("collection is locked")


@pytest.fixture
def locked_os_keyring(monkeypatch, tmp_path):
    """Install a locked (non-fail) OS keyring; file store writes under tmp."""
    original = keyring.get_keyring()
    keyring.set_keyring(_LockedKeyring())
    monkeypatch.setattr(c, "DATA_DIR", tmp_path)
    monkeypatch.setattr(c, "_KEYRING_BACKEND_READY", False)
    if hasattr(c, "_FILE_BACKEND_ACTIVE"):
        monkeypatch.setattr(c, "_FILE_BACKEND_ACTIVE", False)
    try:
        yield tmp_path
    finally:
        keyring.set_keyring(original)


def test_set_secret_degrades_to_file_store_on_locked_keyring(locked_os_keyring) -> None:
    # Old behaviour: returns False (KeyringLocked swallowed) -> HTTP 500 upstream.
    assert c.set_secret("probe_locked_key", "v-123") is True
    # And the value must persist + read back through the same file fallback.
    assert c.get_secret("probe_locked_key") == "v-123"
    # It must have landed in the 0600 file store, not silently vanished.
    assert (locked_os_keyring / "credentials.json").exists()


def test_get_secret_reads_file_store_on_locked_keyring(locked_os_keyring) -> None:
    # A prior run saved to the file store; a NEW process boots with the same locked
    # keyring (backend not yet swapped). get_secret must degrade to the file store
    # and still find the key instead of dead-ending on the locked OS keyring.
    store = c._FileCredStore()
    store.set(c.KEYRING_SERVICE, "prior_key", "prior-val")
    assert c.get_secret("prior_key") == "prior-val"


def test_delete_secret_degrades_to_file_store_on_locked_keyring(locked_os_keyring) -> None:
    store = c._FileCredStore()
    store.set(c.KEYRING_SERVICE, "del_prior", "y")
    # Old behaviour: keyring.delete_password raises -> returns False, value stays.
    assert c.delete_secret("del_prior") is True
    assert c.get_secret("del_prior") is None
