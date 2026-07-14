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


@pytest.fixture
def _restore_keyring_backend():
    original = keyring.get_keyring()
    try:
        yield
    finally:
        keyring.set_keyring(original)


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

    store = cfg._FileCredStore()
    store.set(cfg.KEYRING_SERVICE, "openrouter_api_key", "new-file-value")

    def _restore_platform() -> None:
        keyring.set_keyring(platform_backend)

    monkeypatch.setattr(keyring.core, "init_backend", _restore_platform)

    revision_before = cfg.secret_revision("openrouter_api_key")
    assert cfg.set_secret("openrouter_api_key", "new-file-value") is True
    assert (
        platform_backend.get_password(cfg.KEYRING_SERVICE, "openrouter_api_key")
        == "new-file-value"
    )
    assert store.get(cfg.KEYRING_SERVICE, "openrouter_api_key") is None
    assert cfg._FILE_BACKEND_ACTIVE is False
    assert cfg.secret_revision("openrouter_api_key") == revision_before + 1


def test_explicit_save_keeps_file_backend_when_platform_store_is_still_unusable(
    monkeypatch: pytest.MonkeyPatch,
    _restore_keyring_backend,
) -> None:
    file_backend = _MemoryBackend()
    unavailable = _UnavailableBackend()
    keyring.set_keyring(file_backend)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", True)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", True)

    def _restore_unavailable_platform() -> None:
        keyring.set_keyring(unavailable)

    monkeypatch.setattr(keyring.core, "init_backend", _restore_unavailable_platform)

    assert cfg.set_secret("openrouter_api_key", "headless-value") is True
    assert (
        file_backend.get_password(cfg.KEYRING_SERVICE, "openrouter_api_key")
        == "headless-value"
    )
    assert keyring.get_keyring() is file_backend
    assert cfg._FILE_BACKEND_ACTIVE is True
