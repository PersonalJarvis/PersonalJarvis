"""Credential-scope guards for Realtime Voice and Jarvis-Agents."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core import config as cfg


def _secret_reader(values: dict[str, str]):
    def read(key: str, env_fallback: str | None = None) -> str | None:
        return values.get(key)

    return read


@pytest.mark.parametrize(
    ("provider", "agent_slot", "realtime_provider", "realtime_slot"),
    [
        ("openai", "jarvis_agent_openai_api_key", "openai-realtime", "realtime_openai_api_key"),
        ("gemini", "jarvis_agent_gemini_api_key", "gemini-live", "realtime_gemini_api_key"),
    ],
)
def test_scoped_keys_keep_precedence_but_brain_reads_realtime_last(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    agent_slot: str,
    realtime_provider: str,
    realtime_slot: str,
) -> None:
    """Dedicated slots win on their surface; Brain cross-reads only last-resort.

    2026-07-21 Mac forensic: an install whose ONLY credential was saved from
    the Realtime card had a fully working Gemini-Live session while every
    delegated Brain/Tool-Model turn failed with the spoken provider-down
    phrase. The Brain family therefore reads the Realtime-scoped key as a
    trailing fallback — never ahead of a generic family key.
    """
    monkeypatch.setattr(
        cfg,
        "get_secret",
        _secret_reader({agent_slot: "agent-key", realtime_slot: "realtime-key"}),
    )

    assert cfg.get_jarvis_agent_secret(provider) == "agent-key"
    assert cfg.get_provider_secret(realtime_provider) == "realtime-key"
    assert cfg.get_provider_secret(provider) == "realtime-key"


@pytest.mark.parametrize(
    ("provider", "generic_slot", "realtime_slot"),
    [
        ("openai", "openai_api_key", "realtime_openai_api_key"),
        ("gemini", "gemini_api_key", "realtime_gemini_api_key"),
    ],
)
def test_generic_brain_key_wins_over_realtime_scoped_key(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    generic_slot: str,
    realtime_slot: str,
) -> None:
    monkeypatch.setattr(
        cfg,
        "get_secret",
        _secret_reader(
            {generic_slot: "generic-key", realtime_slot: "realtime-key"}
        ),
    )

    assert cfg.get_provider_secret(provider) == "generic-key"


@pytest.mark.parametrize(
    ("provider", "realtime_slot"),
    [
        ("openai", "realtime_openai_api_key"),
        ("gemini", "realtime_gemini_api_key"),
    ],
)
def test_realtime_only_install_still_gets_a_brain_key(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    realtime_slot: str,
) -> None:
    monkeypatch.setattr(
        cfg,
        "get_secret",
        _secret_reader({realtime_slot: "realtime-key"}),
    )

    assert cfg.get_provider_secret(provider) == "realtime-key"
    assert cfg.get_jarvis_agent_secret(provider) == "realtime-key"


def test_agent_and_realtime_keep_generic_keys_as_upgrade_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cfg,
        "get_secret",
        _secret_reader({"openai_api_key": "legacy-key"}),
    )

    assert cfg.get_jarvis_agent_secret("openai") == "legacy-key"
    assert cfg.get_provider_secret("openai-realtime") == "legacy-key"


def test_dedicated_agent_key_wins_over_generic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cfg,
        "get_secret",
        _secret_reader(
            {
                "jarvis_agent_openai_api_key": "agent-key",
                "openai_api_key": "generic-key",
            }
        ),
    )

    assert cfg.get_jarvis_agent_secret("openai") == "agent-key"
    assert cfg.get_provider_secret("openai") == "generic-key"


@pytest.mark.asyncio
async def test_provider_overrides_are_task_local_and_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cfg,
        "get_secret",
        _secret_reader({"openai_api_key": "generic-key"}),
    )
    both_inside = asyncio.Event()
    release = asyncio.Event()
    entered = 0

    async def read_scoped(value: str) -> str | None:
        nonlocal entered
        with cfg.override_provider_secrets({"openai": value}):
            entered += 1
            if entered == 2:
                both_inside.set()
            await both_inside.wait()
            observed = cfg.get_provider_secret("openai")
            if value == "worker-a":
                release.set()
            else:
                await release.wait()
            return observed

    assert await asyncio.gather(read_scoped("worker-a"), read_scoped("worker-b")) == [
        "worker-a",
        "worker-b",
    ]
    assert cfg.get_provider_secret("openai") == "generic-key"


def test_provider_override_restores_after_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cfg,
        "get_secret",
        _secret_reader({"openai_api_key": "generic-key"}),
    )

    with pytest.raises(RuntimeError):
        with cfg.override_provider_secrets({"openai": "worker-key"}):
            assert cfg.get_provider_secret("openai") == "worker-key"
            raise RuntimeError("stop")

    assert cfg.get_provider_secret("openai") == "generic-key"
