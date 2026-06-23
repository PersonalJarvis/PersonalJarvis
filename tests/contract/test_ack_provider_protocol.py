"""Contract tests for every adapter in
:data:`jarvis.brain.ack_brain.providers.REGISTRY`.

Each parametrised case runs against every registered adapter class, so
new entries in the REGISTRY automatically inherit:

- isinstance check against the runtime-checkable
  :class:`AbstractAckProvider` Protocol.
- Empty-utterance graceful handling — ``run("")`` must never raise.
- ``max_output_tokens`` config respected — verified by feeding a fake
  transport response and asserting the adapter forwards the configured
  ceiling into its request payload.

Adapters are intentionally constructable without credentials (lazy
auth — the credential lookup happens inside ``run()``). The tests
therefore stub out the HTTP/SDK transport per adapter so we never
touch a live endpoint.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import pytest

from jarvis.brain.ack_brain.config import (
    GeminiAckProviderConfig,
    OllamaAckProviderConfig,
    OpenAIAckProviderConfig,
)
from jarvis.brain.ack_brain.providers import (
    REGISTRY,
    AbstractAckProvider,
    GeminiFlashAck,
    OllamaFlashAck,
    OpenAIMiniAck,
)

# ---------------------------------------------------------------------------
# Config factory per registered provider name
# ---------------------------------------------------------------------------

_MAX_TOKENS_FIXTURE = 17  # arbitrary non-default value the tests assert on


def _config_for(provider_name: str) -> Any:
    if provider_name == "gemini":
        return GeminiAckProviderConfig(
            model="gemini-3.1-flash", max_output_tokens=_MAX_TOKENS_FIXTURE
        )
    if provider_name == "openai":
        return OpenAIAckProviderConfig(
            model="gpt-5-mini", max_output_tokens=_MAX_TOKENS_FIXTURE
        )
    if provider_name == "ollama":
        return OllamaAckProviderConfig(
            model="llama3.1:8b", max_output_tokens=_MAX_TOKENS_FIXTURE
        )
    raise AssertionError(
        f"Add a config factory for new REGISTRY entry: {provider_name!r}"
    )


# ---------------------------------------------------------------------------
# Fake SDK / HTTP transports per adapter
# ---------------------------------------------------------------------------


@dataclass
class _FakeOpenAIMessage:
    content: str = "Lass mich kurz nachschauen."


@dataclass
class _FakeOpenAIChoice:
    message: _FakeOpenAIMessage = field(default_factory=_FakeOpenAIMessage)


@dataclass
class _FakeOpenAIResponse:
    choices: list[_FakeOpenAIChoice] = field(
        default_factory=lambda: [_FakeOpenAIChoice()]
    )


@dataclass
class _FakeOpenAICompletions:
    calls: list[dict[str, Any]] = field(default_factory=list)
    response_text: str = "Lass mich kurz nachschauen."

    async def create(self, **kwargs: Any) -> _FakeOpenAIResponse:
        self.calls.append(kwargs)
        return _FakeOpenAIResponse(
            choices=[_FakeOpenAIChoice(message=_FakeOpenAIMessage(content=self.response_text))]
        )


@dataclass
class _FakeOpenAIChat:
    completions: _FakeOpenAICompletions = field(default_factory=_FakeOpenAICompletions)


@dataclass
class _FakeOpenAIClient:
    chat: _FakeOpenAIChat = field(default_factory=_FakeOpenAIChat)


@dataclass
class _FakeGeminiResponse:
    text: str = "Lass mich kurz nachschauen."


@dataclass
class _FakeGeminiAsyncModels:
    calls: list[dict[str, Any]] = field(default_factory=list)
    response_text: str = "Lass mich kurz nachschauen."

    async def generate_content(self, **kwargs: Any) -> _FakeGeminiResponse:
        self.calls.append(kwargs)
        return _FakeGeminiResponse(text=self.response_text)


@dataclass
class _FakeGeminiAio:
    models: _FakeGeminiAsyncModels = field(default_factory=_FakeGeminiAsyncModels)


@dataclass
class _FakeGeminiClient:
    aio: _FakeGeminiAio = field(default_factory=_FakeGeminiAio)


@dataclass
class _FakeHTTPResponse:
    status_code: int = 200
    payload: dict[str, Any] = field(
        default_factory=lambda: {
            "message": {"role": "assistant", "content": "Lass mich kurz nachschauen."}
        }
    )

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


@dataclass
class _FakeAsyncHTTPClient:
    post_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    response: _FakeHTTPResponse = field(default_factory=_FakeHTTPResponse)

    async def __aenter__(self) -> _FakeAsyncHTTPClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> _FakeHTTPResponse:
        self.post_calls.append((url, json))
        return self.response


# ---------------------------------------------------------------------------
# Helpers to install the fakes via monkeypatch
# ---------------------------------------------------------------------------


def _install_openai_fake(monkeypatch: pytest.MonkeyPatch) -> _FakeOpenAIClient:
    """Install a fake ``openai.AsyncOpenAI`` module-level constructor.

    The OpenAI adapter imports ``from openai import AsyncOpenAI`` lazily
    inside ``_ensure_client``.
    """
    fake_client = _FakeOpenAIClient()

    def _factory(*args: Any, **kwargs: Any) -> _FakeOpenAIClient:
        return fake_client

    fake_module = ModuleType("openai")
    fake_module.AsyncOpenAI = _factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return fake_client


def _install_gemini_fake(monkeypatch: pytest.MonkeyPatch) -> _FakeGeminiClient:
    fake_client = _FakeGeminiClient()

    fake_module = ModuleType("google")
    fake_genai = ModuleType("google.genai")
    fake_types = ModuleType("google.genai.types")

    def _genai_client(*args: Any, **kwargs: Any) -> _FakeGeminiClient:
        return fake_client

    class _GenerateContentConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake_genai.Client = _genai_client  # type: ignore[attr-defined]
    fake_genai.types = fake_types  # type: ignore[attr-defined]
    fake_types.GenerateContentConfig = _GenerateContentConfig  # type: ignore[attr-defined]
    fake_module.genai = fake_genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_module)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    return fake_client


def _install_httpx_fake(monkeypatch: pytest.MonkeyPatch) -> _FakeAsyncHTTPClient:
    fake_client = _FakeAsyncHTTPClient()

    class _AsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncHTTPClient:
            return fake_client

        async def __aexit__(self, *_: object) -> None:
            return None

    class _Timeout:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    fake_module = ModuleType("httpx")
    fake_module.AsyncClient = _AsyncClient  # type: ignore[attr-defined]
    fake_module.Timeout = _Timeout  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httpx", fake_module)
    return fake_client


def _install_secret_fake(
    monkeypatch: pytest.MonkeyPatch, value: str | None = "fake-key"
) -> None:
    from jarvis.core import config as cfg_module

    monkeypatch.setattr(
        cfg_module, "get_secret", lambda *_args, **_kwargs: value, raising=True
    )


# ---------------------------------------------------------------------------
# Per-adapter wiring table
# ---------------------------------------------------------------------------


@dataclass
class _AdapterFixture:
    """Bundles a wired adapter + the fake transport for max-tokens assertion."""

    adapter: AbstractAckProvider
    capture_max_tokens: Callable[[], int | None]


def _wire_adapter(
    provider_name: str, monkeypatch: pytest.MonkeyPatch
) -> _AdapterFixture:
    config = _config_for(provider_name)
    cls = REGISTRY[provider_name]
    if provider_name == "gemini":
        fake = _install_gemini_fake(monkeypatch)
        _install_secret_fake(monkeypatch)
        adapter = cls(config)
        return _AdapterFixture(
            adapter=adapter,
            capture_max_tokens=lambda: (
                fake.aio.models.calls[-1]["config"].kwargs.get("max_output_tokens")
                if fake.aio.models.calls
                else None
            ),
        )
    if provider_name == "openai":
        fake = _install_openai_fake(monkeypatch)
        _install_secret_fake(monkeypatch)
        adapter = cls(config)
        return _AdapterFixture(
            adapter=adapter,
            capture_max_tokens=lambda: (
                fake.chat.completions.calls[-1].get("max_tokens")
                if fake.chat.completions.calls
                else None
            ),
        )
    if provider_name == "ollama":
        fake = _install_httpx_fake(monkeypatch)
        adapter = cls(config)
        return _AdapterFixture(
            adapter=adapter,
            capture_max_tokens=lambda: (
                fake.post_calls[-1][1].get("options", {}).get("num_predict")
                if fake.post_calls
                else None
            ),
        )
    raise AssertionError(f"No wiring for provider {provider_name!r}")


# ---------------------------------------------------------------------------
# Parametrisation
# ---------------------------------------------------------------------------


@pytest.fixture(params=sorted(REGISTRY.keys()))
def provider_name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def adapter_fixture(
    provider_name: str, monkeypatch: pytest.MonkeyPatch
) -> _AdapterFixture:
    return _wire_adapter(provider_name, monkeypatch)


# ---------------------------------------------------------------------------
# Case 1: Protocol conformance
# ---------------------------------------------------------------------------


def test_every_adapter_satisfies_abstract_protocol(
    adapter_fixture: _AdapterFixture,
) -> None:
    """Every REGISTRY entry must pass isinstance against the runtime-checkable Protocol."""
    assert isinstance(adapter_fixture.adapter, AbstractAckProvider), (
        f"{type(adapter_fixture.adapter).__name__} does not conform to "
        "AbstractAckProvider Protocol"
    )


def test_registry_contains_all_known_adapter_classes() -> None:
    """Adapter classes are also directly importable — sanity guard against
    accidental removal from the package ``__all__``."""
    expected = {GeminiFlashAck, OpenAIMiniAck, OllamaFlashAck}
    assert set(REGISTRY.values()) == expected


# ---------------------------------------------------------------------------
# Case 2: Empty utterance never raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_utterance_returns_gracefully(
    adapter_fixture: _AdapterFixture,
) -> None:
    """Per Protocol docstring: ``run("")`` must never raise.

    Either ``None`` or a string is acceptable here — the adapter is
    free to pass the empty utterance through to the SDK. The contract
    is *no exception*.
    """
    result = await adapter_fixture.adapter.run(
        "", "de", persona_prompt="System prompt"
    )
    # No raise. Result may be None or a Fake response string.
    assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# Case 3: max_output_tokens config is respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_output_tokens_is_passed_through(
    adapter_fixture: _AdapterFixture,
) -> None:
    """The ``max_output_tokens`` config field must reach the underlying SDK."""
    await adapter_fixture.adapter.run(
        "Hallo Jarvis", "de", persona_prompt="System prompt"
    )
    captured = adapter_fixture.capture_max_tokens()
    assert captured == _MAX_TOKENS_FIXTURE, (
        f"Adapter did not pass max_output_tokens={_MAX_TOKENS_FIXTURE} to its "
        f"underlying SDK; observed {captured!r}"
    )
