"""Unit-Tests fuer BrainHealthChecker (Phase 5, CL-4).

Statt der echten `BrainProviderRegistry` (entry_points-Discovery) wird hier
ein In-Memory-Stub mit derselben API genutzt: `instantiate(name, **kwargs)`.
Das ist der einzige Registry-Call, den der HealthChecker macht.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.healthcheck import (
    BrainConfigError,
    BrainHealthChecker,
    HealthResult,
)
from jarvis.core.config import BrainTierConfig
from jarvis.core.protocols import BrainDelta, BrainRequest


class _SlowBrain:
    """Scripted Brain, das `sleep_s` lang wartet, bevor es yields."""

    name = "slow-brain"
    context_window = 8192
    supports_tools = False
    supports_vision = False

    def __init__(self, *, model: str, sleep_s: float) -> None:
        self.model = model
        self._sleep_s = sleep_s

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        await asyncio.sleep(self._sleep_s)
        yield BrainDelta(content="ok", finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class _InstantBrain:
    """Liefert sofort einen Text-Delta — simuliert gesunden Provider."""

    name = "instant-brain"
    context_window = 8192
    supports_tools = False
    supports_vision = False

    def __init__(self, *, model: str) -> None:
        self.model = model

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="hi", finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class _RaisingBrain:
    """Wirft beim ersten Draining — simuliert missing key / bad model."""

    name = "raising-brain"
    context_window = 8192
    supports_tools = False
    supports_vision = False

    def __init__(self, *, model: str, raise_msg: str) -> None:
        self.model = model
        self._raise_msg = raise_msg

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        raise RuntimeError(self._raise_msg)
        yield  # pragma: no cover — Signatur-only


class _StubRegistry:
    """Minimal-Registry mit derselben `instantiate`-Signatur wie Production."""

    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        if name not in self._mapping:
            raise KeyError(f"provider '{name}' not registered")
        factory = self._mapping[name]
        return factory(**kwargs)


@pytest.mark.asyncio
async def test_probe_valid_model_succeeds() -> None:
    registry = _StubRegistry({"ok-provider": _InstantBrain})
    checker = BrainHealthChecker(registry)  # type: ignore[arg-type]

    result = await checker.probe("ok-provider", "claude-haiku-4-5-20251001")

    assert isinstance(result, HealthResult)
    assert result.ok is True
    assert result.error is None
    assert result.provider == "ok-provider"
    assert result.model == "claude-haiku-4-5-20251001"
    assert result.duration_ms >= 0.0


@pytest.mark.asyncio
async def test_probe_timeout_returns_ok_false() -> None:
    registry = _StubRegistry(
        {"slow-provider": lambda *, model: _SlowBrain(model=model, sleep_s=10.0)}
    )
    checker = BrainHealthChecker(registry)  # type: ignore[arg-type]

    result = await checker.probe("slow-provider", "gemini-3.1-pro", timeout_s=0.1)

    assert result.ok is False
    assert result.error is not None
    assert "timeout" in result.error.lower()


@pytest.mark.asyncio
async def test_probe_bad_model_fails_with_explicit_error() -> None:
    registry = _StubRegistry(
        {
            "bad-provider": lambda *, model: _RaisingBrain(
                model=model, raise_msg="model 'gpt-5.5' not found"
            )
        }
    )
    checker = BrainHealthChecker(registry)  # type: ignore[arg-type]

    result = await checker.probe("bad-provider", "gpt-5.5")

    assert result.ok is False
    assert result.error is not None
    assert "gpt-5.5" in result.error
    assert "RuntimeError" in result.error


@pytest.mark.asyncio
async def test_probe_missing_key_error_surfaces() -> None:
    def _raise_on_init(*, model: str) -> Any:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    registry = _StubRegistry({"claude-api": _raise_on_init})
    checker = BrainHealthChecker(registry)  # type: ignore[arg-type]

    result = await checker.probe("claude-api", "claude-haiku-4-5-20251001")

    assert result.ok is False
    assert result.error is not None
    assert "ANTHROPIC_API_KEY" in result.error


@pytest.mark.asyncio
async def test_probe_tier_returns_all_probes() -> None:
    registry = _StubRegistry(
        {
            "claude-api": _InstantBrain,
            "gemini": _InstantBrain,
            "openai": _InstantBrain,
        }
    )
    checker = BrainHealthChecker(registry)  # type: ignore[arg-type]

    tier = BrainTierConfig(
        provider="claude-api",
        model="claude-haiku-4-5-20251001",
        fallback_provider="gemini",
        fallback_model="gemini-2.5-flash",
        fallback_provider_2="openai",
        fallback_model_2="gpt-5.5",
    )

    results = await checker.probe_tier(tier)

    assert len(results) == 3
    assert [r.provider for r in results] == ["claude-api", "gemini", "openai"]
    assert all(r.ok for r in results)


@pytest.mark.asyncio
async def test_probe_tier_without_fallbacks_returns_single() -> None:
    registry = _StubRegistry({"claude-api": _InstantBrain})
    checker = BrainHealthChecker(registry)  # type: ignore[arg-type]

    tier = BrainTierConfig(
        provider="claude-api",
        model="claude-haiku-4-5-20251001",
    )

    results = await checker.probe_tier(tier)

    assert len(results) == 1
    assert results[0].provider == "claude-api"


def test_brain_config_error_is_exception() -> None:
    """Smoke-Test: BrainConfigError ist importierbar und eine Exception."""
    assert issubclass(BrainConfigError, Exception)
