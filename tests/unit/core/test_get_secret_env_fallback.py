"""H2 (open-source AP-22 / headless VPS): the documented keyring → ENV → .env
hierarchy must hold for EVERY credential slot, not only brain providers whose
callers happen to pass an explicit env var. A headless user sets GROQ_API_KEY in
the environment (no OS keyring on python:3.11-slim) and the STT/TTS/integration
slots must read it — so get_secret derives the env var from the slot name when
the caller passes none.
"""
from __future__ import annotations

import keyring

from jarvis.core.config import get_secret


def test_get_secret_derives_env_fallback_from_key(monkeypatch):
    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: None)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-from-env")
    assert get_secret("groq_api_key") == "gsk-from-env"


def test_explicit_env_fallback_still_wins(monkeypatch):
    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: None)
    monkeypatch.setenv("MY_EXPLICIT_VAR", "explicit")
    assert get_secret("some_slot", env_fallback="MY_EXPLICIT_VAR") == "explicit"


def test_keyring_value_wins_over_env(monkeypatch):
    monkeypatch.setattr(
        keyring, "get_password", lambda svc, k: "kr-value" if k == "x_api_key" else None
    )
    monkeypatch.setenv("X_API_KEY", "env-value")
    assert get_secret("x_api_key") == "kr-value"


def test_absent_everywhere_returns_none(monkeypatch):
    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: None)
    monkeypatch.delenv("TOTALLY_UNSET_SLOT", raising=False)
    assert get_secret("totally_unset_slot") is None


def test_file_fallback_is_read_when_working_keyring_returns_none(monkeypatch, tmp_path):
    """A prior fallback save remains visible after the OS keyring recovers."""
    from jarvis.core import config

    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config._FileCredStore().set(
        config.KEYRING_SERVICE, "openai_api_key", "file-saved-value"
    )

    assert get_secret("openai_api_key", "OPENAI_API_KEY") == "file-saved-value"


def test_environment_still_precedes_file_fallback(monkeypatch, tmp_path):
    from jarvis.core import config

    monkeypatch.setattr(keyring, "get_password", lambda *a, **k: None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-value")
    config._FileCredStore().set(
        config.KEYRING_SERVICE, "openai_api_key", "file-saved-value"
    )

    assert get_secret("openai_api_key", "OPENAI_API_KEY") == "environment-value"
