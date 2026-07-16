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
def test_scoped_keys_do_not_cross_between_agent_realtime_and_brain(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    agent_slot: str,
    realtime_provider: str,
    realtime_slot: str,
) -> None:
    monkeypatch.setattr(
        cfg,
        "get_secret",
        _secret_reader({agent_slot: "agent-key", realtime_slot: "realtime-key"}),
    )

    assert cfg.get_jarvis_agent_secret(provider) == "agent-key"
    assert cfg.get_provider_secret(realtime_provider) == "realtime-key"
    assert cfg.get_provider_secret(provider) is None


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
