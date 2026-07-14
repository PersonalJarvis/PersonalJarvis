"""Regression coverage for recovering from a transient keyring fallback.

An in-app save must not remain trapped in the local file backend after the OS
credential store becomes usable again. Otherwise an older platform credential
shadows the newly saved value on the next process start.
"""

from __future__ import annotations

import keyring
import keyring.backend
import pytest

import jarvis.core.config as cfg
from jarvis.marketplace.token_store import Tokens, TokenStore


class _MemoryBackend(keyring.backend.KeyringBackend):
    priority = 1.0

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class _UnavailableBackend(_MemoryBackend):
    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError("credential service unavailable")


class _ReadOnlyBackend(_MemoryBackend):
    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("WinVault write failed with error 1312")


class _NoDeleteBackend(_MemoryBackend):
    def delete_password(self, service: str, username: str) -> None:
        return None


class _LockableBackend(_MemoryBackend):
    """Platform store that becomes unreadable after a credential was saved."""

    locked = False

    def get_password(self, service: str, username: str) -> str | None:
        if self.locked:
            raise RuntimeError("platform credential store is locked")
        return super().get_password(service, username)

    def delete_password(self, service: str, username: str) -> None:
        if self.locked:
            raise RuntimeError("platform credential store is locked")
        super().delete_password(service, username)


@pytest.fixture
def _restore_keyring_backend():
    original = keyring.get_keyring()
    original_platform = cfg._PLATFORM_KEYRING_BACKEND
    try:
        yield
    finally:
        keyring.set_keyring(original)
        cfg._PLATFORM_KEYRING_BACKEND = original_platform


def test_explicit_save_restores_platform_backend_and_removes_stale_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    _restore_keyring_backend,
) -> None:
    file_backend = _MemoryBackend()
    platform_backend = _MemoryBackend()
    keyring.set_keyring(file_backend)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", True)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)

    store = cfg._FileCredStore()
    store.set(cfg.KEYRING_SERVICE, "openrouter_api_key", "new-file-value")

    def _restore_platform() -> None:
        keyring.set_keyring(platform_backend)

    monkeypatch.setattr(keyring.core, "init_backend", _restore_platform)

    revision_before = cfg.secret_revision("openrouter_api_key")
    assert cfg.set_secret("openrouter_api_key", "new-file-value") is True
    assert (
        platform_backend.get_password(cfg.KEYRING_SERVICE, "openrouter_api_key") == "new-file-value"
    )
    assert store.get(cfg.KEYRING_SERVICE, "openrouter_api_key") is None
    assert cfg._FILE_BACKEND_ACTIVE is False
    assert cfg.secret_revision("openrouter_api_key") == revision_before + 1


def test_platform_save_synchronizes_fallback_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    _restore_keyring_backend,
) -> None:
    platform_backend = _MemoryBackend()
    keyring.set_keyring(platform_backend)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", False)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)
    monkeypatch.delenv("TEST_GOOGLE_CLIENT_SECRET", raising=False)

    store = cfg._FileCredStore()
    store.set(cfg.KEYRING_SERVICE, "google_client_secret", "stale-file-value")

    def _failed_cleanup(self: cfg._FileCredStore, service: str, username: str) -> None:
        raise RuntimeError("credential fallback cleanup failed")

    monkeypatch.setattr(cfg._FileCredStore, "delete", _failed_cleanup)

    assert cfg.set_secret("google_client_secret", "new-platform-value") is True
    assert (
        platform_backend.get_password(cfg.KEYRING_SERVICE, "google_client_secret")
        == "new-platform-value"
    )
    assert store.get(cfg.KEYRING_SERVICE, "google_client_secret") == "new-platform-value"
    assert (
        cfg.get_secret("google_client_secret", "TEST_GOOGLE_CLIENT_SECRET") == "new-platform-value"
    )


def test_explicit_save_keeps_file_backend_when_platform_store_is_still_unusable(
    monkeypatch: pytest.MonkeyPatch,
    _restore_keyring_backend,
) -> None:
    file_backend = _MemoryBackend()
    unavailable = _UnavailableBackend()
    keyring.set_keyring(file_backend)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", True)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)

    def _restore_unavailable_platform() -> None:
        keyring.set_keyring(unavailable)

    monkeypatch.setattr(keyring.core, "init_backend", _restore_unavailable_platform)

    assert cfg.set_secret("openrouter_api_key", "headless-value") is True
    assert file_backend.get_password(cfg.KEYRING_SERVICE, "openrouter_api_key") == "headless-value"
    assert keyring.get_keyring() is file_backend
    assert cfg._FILE_BACKEND_ACTIVE is True


def test_restart_prefers_newer_file_value_over_stale_platform_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    _restore_keyring_backend,
) -> None:
    platform_backend = _MemoryBackend()
    platform_backend.set_password(
        cfg.KEYRING_SERVICE, "google_client_secret", "stale-platform-value"
    )
    keyring.set_keyring(platform_backend)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", False)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)
    monkeypatch.delenv("TEST_GOOGLE_CLIENT_SECRET", raising=False)
    cfg._FileCredStore().set(cfg.KEYRING_SERVICE, "google_client_secret", "new-file-value")

    assert cfg.get_secret("google_client_secret", "TEST_GOOGLE_CLIENT_SECRET") == "new-file-value"


def test_file_swap_retains_readable_platform_slots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    _restore_keyring_backend,
) -> None:
    platform_backend = _MemoryBackend()
    platform_backend.set_password(cfg.KEYRING_SERVICE, "untouched_api_key", "platform-value")
    keyring.set_keyring(platform_backend)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", False)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)

    assert cfg._install_file_cred_backend("test platform failure") is True

    assert cfg._PLATFORM_KEYRING_BACKEND is platform_backend
    assert cfg.get_secret("untouched_api_key", "UNSET_TEST_KEY") == "platform-value"


def test_restore_probe_rejects_readable_but_unwritable_platform_backend(
    monkeypatch: pytest.MonkeyPatch,
    _restore_keyring_backend,
) -> None:
    file_backend = _MemoryBackend()
    read_only_backend = _ReadOnlyBackend()
    read_only_backend.values[(cfg.KEYRING_SERVICE, "existing")] = "still-readable"
    keyring.set_keyring(file_backend)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", True)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)

    def _restore_read_only_platform() -> None:
        keyring.set_keyring(read_only_backend)

    monkeypatch.setattr(keyring.core, "init_backend", _restore_read_only_platform)

    assert cfg.set_secret("google_client_secret", "new-file-value") is True
    assert keyring.get_keyring() is file_backend
    assert cfg._FILE_BACKEND_ACTIVE is True
    assert cfg._PLATFORM_KEYRING_BACKEND is read_only_backend
    assert (
        file_backend.get_password(cfg.KEYRING_SERVICE, "google_client_secret") == "new-file-value"
    )


def test_restore_probe_rejects_backend_that_cannot_delete(
    monkeypatch: pytest.MonkeyPatch,
    _restore_keyring_backend,
) -> None:
    file_backend = _MemoryBackend()
    no_delete_backend = _NoDeleteBackend()
    keyring.set_keyring(file_backend)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", True)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)

    def _restore_no_delete_platform() -> None:
        keyring.set_keyring(no_delete_backend)

    monkeypatch.setattr(keyring.core, "init_backend", _restore_no_delete_platform)

    assert cfg._try_restore_platform_keyring_backend() is False
    assert keyring.get_keyring() is file_backend
    assert cfg._FILE_BACKEND_ACTIVE is True


def test_delete_removes_file_and_retained_platform_copies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    _restore_keyring_backend,
) -> None:
    platform_backend = _MemoryBackend()
    platform_backend.set_password(
        cfg.KEYRING_SERVICE, "google_client_secret", "stale-platform-value"
    )
    keyring.set_keyring(platform_backend)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", False)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)
    monkeypatch.delenv("TEST_GOOGLE_CLIENT_SECRET", raising=False)
    assert cfg._install_file_cred_backend("test platform failure") is True
    keyring.set_password(cfg.KEYRING_SERVICE, "google_client_secret", "new-file-value")

    assert cfg.delete_secret("google_client_secret") is True
    assert platform_backend.get_password(cfg.KEYRING_SERVICE, "google_client_secret") is None
    assert cfg._FileCredStore().get(cfg.KEYRING_SERVICE, "google_client_secret") is None
    assert cfg.get_secret("google_client_secret", "TEST_GOOGLE_CLIENT_SECRET") is None


def test_delete_rejects_active_backend_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    _restore_keyring_backend,
) -> None:
    platform_backend = _NoDeleteBackend()
    platform_backend.set_password(
        cfg.KEYRING_SERVICE, "google_client_secret", "stale-platform-value"
    )
    keyring.set_keyring(platform_backend)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", False)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)
    monkeypatch.delenv("TEST_GOOGLE_CLIENT_SECRET", raising=False)
    cfg._FileCredStore().set(cfg.KEYRING_SERVICE, "google_client_secret", "new-file-value")

    assert cfg.delete_secret("google_client_secret") is False
    assert (
        platform_backend.get_password(cfg.KEYRING_SERVICE, "google_client_secret")
        == "stale-platform-value"
    )
    assert cfg._FileCredStore().get(cfg.KEYRING_SERVICE, "google_client_secret") == "new-file-value"
    assert cfg.get_secret("google_client_secret", "TEST_GOOGLE_CLIENT_SECRET") == "new-file-value"


def test_delete_rejects_file_fallback_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    _restore_keyring_backend,
) -> None:
    platform_backend = _MemoryBackend()
    keyring.set_keyring(platform_backend)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", False)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)

    store = cfg._FileCredStore()
    store.set(cfg.KEYRING_SERVICE, "google_client_secret", "stale-file-value")
    monkeypatch.setattr(cfg._FileCredStore, "delete", lambda *_args: None)

    assert cfg.delete_secret("google_client_secret") is False
    assert store.get(cfg.KEYRING_SERVICE, "google_client_secret") == "stale-file-value"


def test_failed_retained_platform_delete_preserves_newer_file_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    _restore_keyring_backend,
) -> None:
    platform_backend = _NoDeleteBackend()
    platform_backend.set_password(
        cfg.KEYRING_SERVICE, "google_client_secret", "stale-platform-value"
    )
    keyring.set_keyring(platform_backend)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", False)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)
    monkeypatch.delenv("TEST_GOOGLE_CLIENT_SECRET", raising=False)
    assert cfg._install_file_cred_backend("test platform failure") is True
    keyring.set_password(cfg.KEYRING_SERVICE, "google_client_secret", "new-file-value")

    assert cfg.delete_secret("google_client_secret") is False
    assert cfg._FileCredStore().get(cfg.KEYRING_SERVICE, "google_client_secret") == "new-file-value"
    assert cfg.get_secret("google_client_secret", "TEST_GOOGLE_CLIENT_SECRET") == "new-file-value"


def test_marketplace_disconnect_fails_closed_when_platform_token_becomes_locked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    _restore_keyring_backend,
) -> None:
    platform_backend = _LockableBackend()
    credential_slot = "plugin_gmail_tokens"
    raw_token = Tokens(access="platform-access" * 20).to_json()
    chunks = [raw_token[i * len(raw_token) // 6 : (i + 1) * len(raw_token) // 6] for i in range(6)]
    platform_backend.set_password(cfg.KEYRING_SERVICE, credential_slot, "\x00JCHUNKS\x006")
    for index, chunk in enumerate(chunks):
        platform_backend.set_password(cfg.KEYRING_SERVICE, f"{credential_slot}__{index}", chunk)
    retained_entries = dict(platform_backend.values)
    assert len(retained_entries) == 7
    keyring.set_keyring(platform_backend)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", False)
    monkeypatch.setattr(cfg, "_PLATFORM_KEYRING_BACKEND", None)
    monkeypatch.delenv("PLUGIN_GMAIL_TOKENS", raising=False)
    platform_backend.locked = True

    with pytest.raises(RuntimeError, match="token deletion could not be verified"):
        TokenStore().delete("gmail")

    # The read path has degraded to an empty file fallback, but that absence is
    # not accepted as deletion proof while all seven OS entries are locked.
    assert cfg.get_secret(credential_slot, env_fallback=None) is None
    assert platform_backend.values == retained_entries
    assert cfg._FILE_BACKEND_ACTIVE is True
    assert cfg._PLATFORM_KEYRING_BACKEND is platform_backend
