"""Groq is a first-class Brain and Jarvis-Agent provider, distinct from STT."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.manager import (
    _SECRET_KEY_TO_BRAIN,
    PROVIDER_ALIASES,
    TIER_DEFAULTS_BY_PROVIDER,
    get_tier_default_model,
)
from jarvis.brain.model_catalog import _ENDPOINTS, CATALOG_PROVIDERS, catalog_spec
from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.core import config as cfg
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.missions.critic.runner import _API_CRITIC_PROVIDERS
from jarvis.missions.init import _API_AGENT_SLUGS, _select_subagent_worker_kind
from jarvis.missions.worker_runtime.provider_map import env_vars_for, to_worker_slug
from jarvis.missions.workers.api_agent_worker import (
    _BRAIN_BY_PROVIDER,
    _DEFAULT_MODEL,
    supports_api_agent_worker,
)
from jarvis.plugins.brain.groq import BASE_URL, DEFAULT_MODEL, GroqBrain
from jarvis.ui.web.provider_spec import get_spec


class _FakeOpenAI:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs


class _ToolStream:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any):  # noqa: ANN201
        self.kwargs = kwargs

        async def _chunks():  # noqa: ANN202
            function = SimpleNamespace(
                name="Write",
                arguments='{"file_path":"result.txt","content":"done"}',
            )
            tool_call = SimpleNamespace(index=0, id="call_1", function=function)
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None, tool_calls=[tool_call]),
                        finish_reason=None,
                    )
                ],
                usage=None,
            )
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None, tool_calls=[]),
                        finish_reason="tool_calls",
                    )
                ],
                usage=None,
            )

        return _chunks()


def test_groq_is_a_registered_brain_plugin() -> None:
    registry = BrainProviderRegistry()
    assert "groq" in registry.available()
    assert "groq" not in registry.failed()
    brain = registry.instantiate("groq")
    assert isinstance(brain, GroqBrain)


def test_groq_and_groq_stt_keep_distinct_provider_ids() -> None:
    brain = get_spec("groq")
    stt = get_spec("groq-api")
    assert brain is not None and stt is not None
    assert brain.tier == "brain"
    assert stt.tier == "stt"
    assert brain.secret_keys == stt.secret_keys == ("groq_api_key",)


def test_groq_credential_and_auto_activation_mapping() -> None:
    assert cfg.PROVIDER_SECRET_CANDIDATES["groq"] == (
        ("groq_api_key", "GROQ_API_KEY"),
    )
    assert _SECRET_KEY_TO_BRAIN["groq_api_key"] == "groq"


def test_groq_defaults_are_current_and_tool_capable() -> None:
    assert DEFAULT_MODEL == "openai/gpt-oss-120b"
    assert TIER_DEFAULTS_BY_PROVIDER["router"]["groq"] == DEFAULT_MODEL
    assert TIER_DEFAULTS_BY_PROVIDER["deep"]["groq"] == DEFAULT_MODEL
    assert get_tier_default_model("router", "groq") == DEFAULT_MODEL
    assert PROVIDER_ALIASES["groq"] == "groq"
    brain = GroqBrain()
    assert brain.context_window == 131_072
    assert brain.can_call_tools() is True
    assert brain.supports_vision is False


def test_groq_has_authenticated_live_model_catalog() -> None:
    spec = catalog_spec("groq")
    assert spec is not None and spec.tier == "brain" and spec.live is True
    assert spec.curated and spec.curated[0].id == DEFAULT_MODEL
    assert "groq" in CATALOG_PROVIDERS
    assert _ENDPOINTS["groq"] == (
        "https://api.groq.com/openai/v1/models",
        "bearer",
    )


def test_groq_uses_openai_compatible_client_without_vendor_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda provider, **kwargs: cfg.ResolvedEndpoint(
            base_url=kwargs["vendor_default_base_url"],
            credential="gsk-test",
            via_proxy=False,
        ),
    )
    GroqBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["api_key"] == "gsk-test"
    assert _FakeOpenAI.last_kwargs["base_url"] == BASE_URL


@pytest.mark.asyncio
async def test_groq_streams_a_local_tool_call_round_trip() -> None:
    completions = _ToolStream()
    brain = GroqBrain()
    brain._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    request = BrainRequest(
        messages=(BrainMessage(role="user", content="Create result.txt"),),
        tools=(
            {
                "name": "Write",
                "description": "Write a workspace file.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["file_path", "content"],
                },
            },
        ),
    )

    deltas = [delta async for delta in brain.complete(request)]
    tool_calls = [delta.tool_call for delta in deltas if delta.tool_call]
    assert tool_calls == [
        {
            "id": "call_1",
            "name": "Write",
            "input": {"file_path": "result.txt", "content": "done"},
        }
    ]
    assert completions.kwargs["model"] == DEFAULT_MODEL
    assert completions.kwargs["tools"][0]["function"]["name"] == "Write"


def test_groq_runs_in_process_for_worker_and_critic() -> None:
    assert "groq" in _API_AGENT_SLUGS
    assert _select_subagent_worker_kind("groq", "foreign-model") == "api_agent"
    assert supports_api_agent_worker("groq") is True
    assert _BRAIN_BY_PROVIDER["groq"] == (
        "jarvis.plugins.brain.groq",
        "GroqBrain",
    )
    assert _DEFAULT_MODEL["groq"] == DEFAULT_MODEL
    assert "groq" in _API_CRITIC_PROVIDERS
    assert to_worker_slug("groq") == "groq"
    assert env_vars_for("groq") == ("GROQ_API_KEY",)
