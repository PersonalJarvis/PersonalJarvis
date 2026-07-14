"""C1 (open-source AP-22 / headless VPS): on python:3.11-slim there is no OS
Secret Service, so the platform keyring resolves to fail.Keyring and every in-app
key save / channel-connect / plugin-connect would raise → the whole API-Keys
section is unusable. Jarvis must fall back to a local 0600 file store so a fresh
VPS downloader can paste a key in the UI and have it persist.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

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


def test_file_cred_store_concurrent_writes_no_lost_update(tmp_path):
    """Concurrent set() calls on the SAME store file must not race and drop
    one of the two updates (bare read-modify-write with no lock did exactly
    that)."""
    store = cfg._FileCredStore(path=tmp_path / "creds.json")
    n = 20
    barrier = threading.Barrier(n)

    def _writer(i: int) -> None:
        barrier.wait()
        store.set("svc", f"key{i}", f"val{i}")

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(n):
        assert store.get("svc", f"key{i}") == f"val{i}"


def test_file_cred_store_save_failure_raises_clear_error(monkeypatch, tmp_path):
    store = cfg._FileCredStore(path=tmp_path / "creds.json")

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(Path, "write_text", _boom)

    with pytest.raises(RuntimeError, match="failed to write credential store"):
        store.set("svc", "k", "v")


def test_resolve_writable_data_dir_honors_env_override(monkeypatch, tmp_path):
    override_dir = tmp_path / "custom_data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(override_dir))
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)

    assert cfg._resolve_writable_data_dir() == override_dir


def test_resolve_writable_data_dir_default_unchanged_when_writable(monkeypatch, tmp_path):
    writable_dir = tmp_path / "data"
    monkeypatch.setattr(cfg, "DATA_DIR", writable_dir)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)

    assert cfg._resolve_writable_data_dir() == writable_dir
    assert writable_dir.exists()


def test_resolve_writable_data_dir_falls_back_when_project_dir_unwritable(monkeypatch, tmp_path):
    unwritable_dir = tmp_path / "readonly_data"
    fallback_dir = tmp_path / "user_data_dir"
    monkeypatch.setattr(cfg, "DATA_DIR", unwritable_dir)
    monkeypatch.setattr(cfg, "_data_dir_cache", None, raising=False)
    monkeypatch.setattr("jarvis.core.paths.user_data_dir", lambda: fallback_dir)

    original_write_text = Path.write_text

    def _fail_on_probe(self: Path, *args: object, **kwargs: object) -> int:
        if self.name.startswith(".write_probe_"):
            raise OSError("read-only filesystem (simulated)")
        return original_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", _fail_on_probe)

    assert cfg._resolve_writable_data_dir() == fallback_dir


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


def test_delete_secret_succeeds_on_file_backend_only_host(monkeypatch, tmp_path):
    """No OS keyring at all (pure headless VPS): _ensure_keyring_backend installs
    the file store as the active backend, so a delete through it must succeed —
    the honest-failure behavior for a genuinely broken/locked OS keyring must
    not regress this already-working headless path."""
    import keyring
    from keyring.backends import fail

    original = keyring.get_keyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", False, raising=False)
    monkeypatch.setattr(cfg, "_FILE_BACKEND_ACTIVE", False, raising=False)
    try:
        assert cfg.set_secret("headless_del_key", "v1") is True
        assert cfg.delete_secret("headless_del_key") is True
        assert cfg.get_secret("headless_del_key") is None
    finally:
        keyring.set_keyring(original)
