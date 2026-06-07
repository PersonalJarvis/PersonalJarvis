"""CodexDirectWorker env handling — honor both auth models.

The ChatGPT-subscription (OAuth) path wants ``OPENAI_API_KEY`` stripped so codex
falls back to ``~/.codex/auth.json``. The API-key path must KEEP the key so
``codex exec`` runs in API mode. ``CODEX_HOME`` is always dropped — the
per-mission dir breaks codex's global OAuth home ("Error finding codex home").
"""
from __future__ import annotations

from jarvis.missions.workers.codex_direct_worker import _build_codex_env


def test_oauth_available_strips_key_and_codex_home() -> None:
    env = {"OPENAI_API_KEY": "sk-x", "CODEX_HOME": "/mission/.codex", "FOO": "bar"}
    out = _build_codex_env(env, oauth_available=True)
    assert out == {"FOO": "bar"}


def test_api_key_path_keeps_key_but_drops_codex_home() -> None:
    env = {"OPENAI_API_KEY": "sk-x", "CODEX_HOME": "/mission/.codex", "FOO": "bar"}
    out = _build_codex_env(env, oauth_available=False)
    assert out == {"OPENAI_API_KEY": "sk-x", "FOO": "bar"}


def test_does_not_mutate_input() -> None:
    env = {"OPENAI_API_KEY": "sk-x", "CODEX_HOME": "/x"}
    _build_codex_env(env, oauth_available=True)
    assert env == {"OPENAI_API_KEY": "sk-x", "CODEX_HOME": "/x"}
