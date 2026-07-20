"""Compatibility properties of the provider-agnostic brain health probe."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.healthcheck import BrainHealthChecker
from jarvis.core.protocols import BrainDelta, BrainRequest


class _CapturingBrain:
    requests: list[BrainRequest] = []

    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(request)
        yield BrainDelta(content="ok")


class _Registry:
    def instantiate(self, _provider: str, **_kwargs: Any) -> _CapturingBrain:
        return _CapturingBrain()


@pytest.mark.asyncio
async def test_probe_does_not_force_zero_temperature() -> None:
    """Connectivity checks must remain valid for default-only tool models."""
    _CapturingBrain.requests.clear()
    checker = BrainHealthChecker(_Registry())  # type: ignore[arg-type]

    result = await checker.probe("provider", "tool-model")

    assert result.ok is True
    assert len(_CapturingBrain.requests) == 1
    assert _CapturingBrain.requests[0].temperature == 1.0
