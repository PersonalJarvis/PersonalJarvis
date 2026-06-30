"""C1 (open-source AP-22 / headless VPS): on python:3.11-slim there is no OS
Secret Service, so the platform keyring resolves to fail.Keyring and every in-app
key save / channel-connect / plugin-connect would raise → the whole API-Keys
section is unusable. Jarvis must fall back to a local 0600 file store so a fresh
VPS downloader can paste a key in the UI and have it persist.
"""
from __future__ import annotations

import sys

import jarvis.core.config as cfg


def test_file_cred_store_roundtrip(tmp_path):
    store = cfg._FileCredStore(path=tmp_path / "creds.json")
    assert store.get("personal-jarvis", "groq_api_key") is None
    store.set("personal-jarvis", "groq_api_key", "gsk-secret")
    assert store.get("personal-jarvis", "groq_api_key") == "gsk-secret"
    store.delete("personal-jarvis", "groq_api_key")
    assert store.get("personal-jarvis", "groq_api_key") is None


def test_file_cred_store_is_0600_on_posix(tmp_path):
    p = tmp_path / "creds.json"
    cfg._FileCredStore(path=p).set("svc", "k", "v")
    if sys.platform != "win32":
        assert (p.stat().st_mode & 0o777) == 0o600


def test_ensure_keyring_backend_registers_file_store_when_os_keyring_dead(monkeypatch, tmp_path):
    import keyring
    from keyring.backends import fail

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    captured: dict = {}
    monkeypatch.setattr(keyring, "set_keyring", lambda kr: captured.__setitem__("kr", kr))
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", False, raising=False)

    cfg._ensure_keyring_backend()

    kr = captured.get("kr")
    assert kr is not None, "a file backend must be registered when the OS keyring is dead"
    # The registered backend must actually store + retrieve.
    kr.set_password("personal-jarvis", "telegram_bot_token", "123:abc")
    assert kr.get_password("personal-jarvis", "telegram_bot_token") == "123:abc"


def test_ensure_keyring_backend_leaves_working_os_keyring_untouched(monkeypatch):
    import keyring

    class _RealishBackend:
        def get_password(self, s, u): return None
        def set_password(self, s, u, p): return None
        def delete_password(self, s, u): return None

    monkeypatch.setattr(keyring, "get_keyring", lambda: _RealishBackend())
    called: dict = {}
    monkeypatch.setattr(keyring, "set_keyring", lambda kr: called.__setitem__("kr", kr))
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", False, raising=False)

    cfg._ensure_keyring_backend()

    assert "kr" not in called, "a working OS keyring must NOT be replaced by the file fallback"
