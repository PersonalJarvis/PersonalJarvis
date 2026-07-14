"""xAI Grok is a first-class Brain and Jarvis-Agent provider."""
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
from jarvis.plugins.brain.grok import BASE_URL, DEFAULT_MODEL, GrokBrain
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


def test_grok_is_registered_while_groq_is_stt_only() -> None:
    registry = BrainProviderRegistry()
    assert "grok" in registry.available()
    assert "groq" not in registry.available()
    assert isinstance(registry.instantiate("grok"), GrokBrain)

    grok = get_spec("grok")
    groq_stt = get_spec("groq-api")
    assert grok is not None and grok.tier == "brain"
    assert grok.label == "xAI Grok"
    assert grok.secret_keys == ("grok_api_key",)
    assert groq_stt is not None and groq_stt.tier == "stt"
    assert groq_stt.secret_keys == ("groq_api_key",)
    assert get_spec("groq") is None


def test_grok_credential_and_auto_activation_mapping() -> None:
    assert cfg.PROVIDER_SECRET_CANDIDATES["grok"] == (
        ("grok_api_key", "GROK_API_KEY"),
        ("xai_api_key", "XAI_API_KEY"),
    )
    assert _SECRET_KEY_TO_BRAIN["grok_api_key"] == "grok"
    assert _SECRET_KEY_TO_BRAIN["xai_api_key"] == "grok"


def test_grok_defaults_are_universal_and_tool_capable() -> None:
    assert DEFAULT_MODEL == "grok-4.3"
    assert TIER_DEFAULTS_BY_PROVIDER["router"]["grok"] == DEFAULT_MODEL
    assert TIER_DEFAULTS_BY_PROVIDER["deep"]["grok"] == DEFAULT_MODEL
    assert get_tier_default_model("router", "grok") == DEFAULT_MODEL
    assert PROVIDER_ALIASES["grok"] == "grok"
    brain = GrokBrain()
    assert brain.context_window == 1_000_000
    assert brain.can_call_tools() is True
    assert brain.supports_vision is False


def test_grok_has_authenticated_live_model_catalog() -> None:
    spec = catalog_spec("grok")
    assert spec is not None and spec.tier == "brain" and spec.live is True
    assert spec.curated and spec.curated[0].id == DEFAULT_MODEL
    assert "grok" in CATALOG_PROVIDERS
    assert "groq" not in CATALOG_PROVIDERS
    assert _ENDPOINTS["grok"] == ("https://api.x.ai/v1/models", "bearer")


def test_grok_uses_xai_openai_compatible_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda provider, **kwargs: cfg.ResolvedEndpoint(
            base_url=kwargs["vendor_default_base_url"],
            credential="xai-test",
            via_proxy=False,
        ),
    )
    GrokBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["api_key"] == "xai-test"
    assert _FakeOpenAI.last_kwargs["base_url"] == BASE_URL


@pytest.mark.asyncio
async def test_grok_streams_a_local_tool_call_round_trip() -> None:
    completions = _ToolStream()
    brain = GrokBrain()
    brain._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
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


def test_grok_runs_in_process_for_worker_and_critic() -> None:
    assert "grok" in _API_AGENT_SLUGS
    assert _select_subagent_worker_kind("grok", "foreign-model") == "api_agent"
    assert supports_api_agent_worker("grok") is True
    assert _BRAIN_BY_PROVIDER["grok"] == (
        "jarvis.plugins.brain.grok",
        "GrokBrain",
    )
    assert _DEFAULT_MODEL["grok"] == DEFAULT_MODEL
    assert "grok" in _API_CRITIC_PROVIDERS
    assert to_worker_slug("grok") == "xai"
    assert env_vars_for("grok") == ("XAI_API_KEY", "GROK_API_KEY")
