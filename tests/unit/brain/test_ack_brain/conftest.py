"""Shared fixtures and Fakes for the ack_brain test suite.

A FakeAckProvider implements the :class:`AbstractAckProvider` protocol
without any network or SDK dependency. Tests can script its responses
per-utterance (or globally) and inspect call recordings afterwards.

Per project policy (CLAUDE.md "Testing-Konventionen"), tests use Fakes
rather than ``unittest.mock`` for protocol-level dependencies. The
Fakes live here so they can be shared across the integration suite
without duplication.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.brain.ack_brain.config import (
    AckBrainConfig,
    GeminiAckProviderConfig,
    GrokAckProviderConfig,
    OllamaAckProviderConfig,
    OpenAIAckProviderConfig,
    _ProvidersBundle,
)

# ---------------------------------------------------------------------------
# FakeAckProvider — generic scripted provider for unit + integration tests
# ---------------------------------------------------------------------------


@dataclass
class FakeAckCall:
    """Record of a single ``FakeAckProvider.run()`` invocation."""

    utterance: str
    language: str
    persona_prompt: str


@dataclass
class FakeAckProvider:
    """Scripted AbstractAckProvider implementation.

    Construct with either:
      - ``response="..."`` for a constant reply,
      - ``responses={"hallo": "Hi!", ...}`` for an utterance map,
      - ``raises=Exception("...")`` to test the silent-on-failure path,
      - ``delay_s=0.5`` to simulate latency for timeout tests.

    Call history is recorded in :attr:`calls` so the test can assert
    which utterances reached the adapter.
    """

    response: str | None = None
    responses: dict[str, str | None] = field(default_factory=dict)
    raises: BaseException | None = None
    delay_s: float = 0.0
    return_empty: bool = False
    calls: list[FakeAckCall] = field(default_factory=list)
    # Some tests want to verify the adapter respects max_output_tokens —
    # the Fake will truncate its reply to this many tokens if set.
    max_output_tokens: int | None = None

    async def run(
        self,
        utterance: str,
        language: str,
        *,
        persona_prompt: str,
    ) -> str | None:
        self.calls.append(
            FakeAckCall(
                utterance=utterance,
                language=language,
                persona_prompt=persona_prompt,
            )
        )
        if self.delay_s > 0:
            import asyncio

            await asyncio.sleep(self.delay_s)
        if self.raises is not None:
            # Adapters are documented as never raising. The Fake supports
            # raising as a way to verify the AckGenerator's defence-in-depth
            # branch (F2 path) — and to verify the contract test that says
            # adapters themselves should swallow.
            raise self.raises
        if self.return_empty:
            return ""
        if utterance in self.responses:
            text = self.responses[utterance]
        else:
            text = self.response
        if text is None:
            return None
        if self.max_output_tokens is not None:
            words = text.split()
            text = " ".join(words[: self.max_output_tokens])
        return text


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def make_ack_config(
    *,
    enabled: bool = True,
    provider: str = "gemini",
    timeout_ms: int = 1500,
    suppress_if_brain_faster_than_ms: int = 0,
    circuit_breaker_threshold: int = 3,
    circuit_breaker_cooldown_s: int = 60,
    max_output_tokens: int = 40,
) -> AckBrainConfig:
    """Build a Pydantic AckBrainConfig with sensible defaults for tests."""
    providers = _ProvidersBundle(
        gemini=GeminiAckProviderConfig(
            model="gemini-3.1-flash", max_output_tokens=max_output_tokens
        ),
        grok=GrokAckProviderConfig(
            model="grok-4-fast-non-reasoning", max_output_tokens=max_output_tokens
        ),
        openai=OpenAIAckProviderConfig(
            model="gpt-5-mini", max_output_tokens=max_output_tokens
        ),
        ollama=OllamaAckProviderConfig(
            model="llama3.1:8b", max_output_tokens=max_output_tokens
        ),
    )
    return AckBrainConfig(
        enabled=enabled,
        provider=provider,
        timeout_ms=timeout_ms,
        suppress_if_brain_faster_than_ms=suppress_if_brain_faster_than_ms,
        circuit_breaker_threshold=circuit_breaker_threshold,
        circuit_breaker_cooldown_s=circuit_breaker_cooldown_s,
        providers=providers,
    )


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_provider() -> FakeAckProvider:
    """Default FakeAckProvider that returns a short German ack."""
    return FakeAckProvider(response="Lass mich kurz nachschauen.")


@pytest.fixture
def ack_config() -> AckBrainConfig:
    """Default AckBrainConfig — Flash-Brain enabled, gemini provider, no suppress-window."""
    return make_ack_config()


@pytest.fixture
def make_ack_config_fn() -> Callable[..., AckBrainConfig]:
    """Expose make_ack_config as a fixture for tests that need variants."""
    return make_ack_config


# ---------------------------------------------------------------------------
# Fake HTTP transport for provider unit tests
# ---------------------------------------------------------------------------


@dataclass
class FakeHTTPResponse:
    """Mimics an httpx.Response shape just enough for the Ollama adapter."""

    status_code: int = 200
    payload: dict[str, Any] = field(default_factory=dict)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


@dataclass
class FakeAsyncClient:
    """Minimal async-context-manager that mimics httpx.AsyncClient.post.

    Used by the Ollama provider unit test to confirm the adapter passes
    ``max_output_tokens`` through to the request body and surfaces the
    response text.
    """

    response: FakeHTTPResponse = field(default_factory=FakeHTTPResponse)
    post_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> FakeHTTPResponse:
        self.post_calls.append((url, json))
        return self.response


# ---------------------------------------------------------------------------
# Test-utility: ack ConfigBuilder-with-runtime-fake-provider
# ---------------------------------------------------------------------------


def build_ack_generator_with_fake(
    fake: FakeAckProvider,
    *,
    config: AckBrainConfig | None = None,
    breaker_clock: Callable[[], float] | None = None,
) -> Any:
    """Wire up an AckGenerator that uses ``fake`` as its provider.

    Convenience builder that mirrors the real
    :func:`jarvis.brain.factory.build_ack_brain` enough for integration
    tests that want to bypass entry-point discovery.
    """
    from jarvis.brain.ack_brain import AckGenerator, CircuitBreaker

    cfg = config or make_ack_config()
    breaker_kwargs: dict[str, Any] = {
        "threshold": cfg.circuit_breaker_threshold,
        "cooldown_s": cfg.circuit_breaker_cooldown_s,
    }
    if breaker_clock is not None:
        breaker_kwargs["now"] = breaker_clock
    breaker = CircuitBreaker(**breaker_kwargs)
    return AckGenerator(provider=fake, config=cfg, breaker=breaker)


__all__ = [
    "FakeAckCall",
    "FakeAckProvider",
    "FakeAsyncClient",
    "FakeHTTPResponse",
    "ack_config",
    "build_ack_generator_with_fake",
    "fake_provider",
    "make_ack_config",
    "make_ack_config_fn",
]
