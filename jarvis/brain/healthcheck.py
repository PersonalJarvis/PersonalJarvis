"""Brain health checker for the bootstrap sequence (Phase 5, CL-4).

Probes a brain provider with a minimal "hi" call and measures the time until
the first BrainDelta. Uses the Brain protocol (`async complete`) so that every
entry-point-registered provider (Claude, Gemini, OpenAI, …) can be validated
identically.

No auto-downgrade: the Q1 decision in the plan (§9) requires that aggressive
models such as `gemini-3.1-pro` / `gpt-5.5` are only used when they actually
exist. The caller (usually `factory._phase2_full_brain`) decides what to do
with a negative probe result — typically a bootstrap abort with an explicit
message. This class only returns the measurement data.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jarvis.core.protocols import BrainMessage, BrainRequest

if TYPE_CHECKING:
    from jarvis.brain.provider_registry import BrainProviderRegistry
    from jarvis.core.config import BrainTierConfig


class BrainConfigError(Exception):
    """Error in the brain-tier configuration (missing key, bad model, …)."""


@dataclass(frozen=True, slots=True)
class HealthResult:
    """Result of a single probe against a provider and model."""
    provider: str
    model: str
    ok: bool
    error: str | None = None
    duration_ms: float = 0.0


_PROBE_REQUEST = BrainRequest(
    messages=(BrainMessage(role="user", content="hi"),),
    max_tokens=8,
    temperature=0.0,
    stream=True,
)


class BrainHealthChecker:
    """Validates brain providers via a minimal live call."""

    def __init__(self, registry: BrainProviderRegistry) -> None:
        self._registry = registry

    async def probe(
        self,
        provider: str,
        model: str,
        *,
        timeout_s: float = 5.0,
    ) -> HealthResult:
        """Calls the provider with a 1-token prompt and measures latency."""
        start = time.perf_counter()
        try:
            brain = self._registry.instantiate(provider, model=model)
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - start) * 1000.0
            return HealthResult(
                provider=provider,
                model=model,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        try:
            await asyncio.wait_for(
                self._drain_first_delta(brain),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - start) * 1000.0
            return HealthResult(
                provider=provider,
                model=model,
                ok=False,
                error=f"timeout after {timeout_s:.1f}s",
                duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - start) * 1000.0
            return HealthResult(
                provider=provider,
                model=model,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        duration_ms = (time.perf_counter() - start) * 1000.0
        return HealthResult(
            provider=provider,
            model=model,
            ok=True,
            error=None,
            duration_ms=duration_ms,
        )

    async def probe_tier(self, tier_config: BrainTierConfig) -> list[HealthResult]:
        """Probes the primary provider and all configured fallbacks sequentially."""
        pairs: list[tuple[str, str]] = [(tier_config.provider, tier_config.model)]
        if tier_config.fallback_provider and tier_config.fallback_model:
            pairs.append((tier_config.fallback_provider, tier_config.fallback_model))
        if tier_config.fallback_provider_2 and tier_config.fallback_model_2:
            pairs.append((tier_config.fallback_provider_2, tier_config.fallback_model_2))

        results: list[HealthResult] = []
        for provider, model in pairs:
            results.append(await self.probe(provider, model))
        return results

    @staticmethod
    async def _drain_first_delta(brain: object) -> None:
        """Fetches the first BrainDelta from the stream and discards the rest."""
        stream = brain.complete(_PROBE_REQUEST)  # type: ignore[attr-defined]
        async for _delta in stream:
            break
