"""Boot-time heal for stale inherited provider env vars.

A long-running ancestor process (e.g. Windows Explorer, started at login) can
freeze a STALE value of a ``JARVIS__*__PROVIDER`` user-scope env var in its
process environment. Every Jarvis instance launched from it inherits that stale
value. Because ``_apply_env_overrides`` lets ``JARVIS__*`` win over jarvis.toml,
the stale value silently overrides the correct, drift-guard-maintained choice —
e.g. a TTS switch to ``cartesia`` reverts to ``gemini-flash-tts`` on every boot.

``refresh_persisted_env_from_user_registry`` is called once at app boot, BEFORE
``load_config``, and overwrites ``os.environ`` for the persistent provider keys
with the authoritative HKCU\\Environment value (which the drift-guard keeps in
sync with jarvis.toml + config-soll.json). A stale inherited value can then
never win. The registry reader is injectable so these tests need no real winreg.
"""
from __future__ import annotations

import os

import pytest

from jarvis.core import config


def test_refresh_overwrites_stale_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process env that disagrees with the registry is healed to the registry value."""
    monkeypatch.setenv("JARVIS__TTS__PROVIDER", "gemini-flash-tts")  # stale inherited
    reg = {"JARVIS__TTS__PROVIDER": "cartesia"}  # authoritative

    changed = config.refresh_persisted_env_from_user_registry(read=reg.get)

    assert os.environ["JARVIS__TTS__PROVIDER"] == "cartesia"
    assert changed == {"JARVIS__TTS__PROVIDER": "cartesia"}


def test_refresh_skips_when_already_in_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS__TTS__PROVIDER", "cartesia")
    reg = {"JARVIS__TTS__PROVIDER": "cartesia"}

    changed = config.refresh_persisted_env_from_user_registry(read=reg.get)

    assert os.environ["JARVIS__TTS__PROVIDER"] == "cartesia"
    assert changed == {}  # no needless rewrite


def test_refresh_ignores_unpinned_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key absent from the registry (reader returns None) is left untouched."""
    monkeypatch.setenv("JARVIS__STT__PROVIDER", "groq-api")

    changed = config.refresh_persisted_env_from_user_registry(read=lambda name: None)

    assert os.environ["JARVIS__STT__PROVIDER"] == "groq-api"  # untouched
    assert changed == {}


def test_refresh_sets_env_when_process_var_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the process has no value at all, the registry value is applied."""
    monkeypatch.delenv("JARVIS__BRAIN__PRIMARY", raising=False)
    reg = {"JARVIS__BRAIN__PRIMARY": "gemini"}

    changed = config.refresh_persisted_env_from_user_registry(read=reg.get)

    assert os.environ["JARVIS__BRAIN__PRIMARY"] == "gemini"
    assert changed == {"JARVIS__BRAIN__PRIMARY": "gemini"}


def test_refresh_covers_all_persistent_provider_keys() -> None:
    """The healed key set must include every user-switchable provider tier so a
    future tier (e.g. a new provider class) is not forgotten."""
    keys = set(config._PERSISTED_PROVIDER_ENV_KEYS)
    assert {
        "JARVIS__BRAIN__PRIMARY",
        "JARVIS__BRAIN__SUB_JARVIS__PROVIDER",
        "JARVIS__TTS__PROVIDER",
        "JARVIS__STT__PROVIDER",
    } <= keys


def test_refresh_default_reader_is_noop_off_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no injected reader on a non-win32 platform the default reader yields
    None for every key, so nothing is changed (cloud-first / Linux VPS safe)."""
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setenv("JARVIS__TTS__PROVIDER", "cartesia")

    changed = config.refresh_persisted_env_from_user_registry()

    assert changed == {}
    assert os.environ["JARVIS__TTS__PROVIDER"] == "cartesia"
