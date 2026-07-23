"""The CU v2 vision dispatch reaches the blind-flag last resort ONLY when the
normal pass dispatched nothing.

Live forensic 2026-07-23 (Computer-Use "open Gemini"): every real vision
provider failed for real this turn — ``gemini`` 400 INVALID_ARGUMENT,
``openrouter`` 400 "reasoning is mandatory", ``nvidia`` no-multimodal,
``anthropic`` 401. The stale-dead-flag last resort then reached ``codex``,
whose API key was 429'd so it answered from the image-dropping ChatGPT CLI:
~68 s of unparseable prose, ending in the misleading exit-2 phrase "I couldn't
get a valid screen-control response, so I stopped." while the screen sat
frozen.

The last resort exists for ONE case — a stale ``_dead_providers`` flag filtered
the only vision brain OUT of the chain, so the normal pass dispatched NOTHING
(``attempted == 0``). When real vision providers WERE dispatched and each
failed (``attempted > 0``), the chain is genuinely exhausted; resurrecting a
provider only drags in a blind path. This module pins both directions:

* ``attempted > 0`` (real failures) → NO last resort, honest
  :class:`CUNoVisionProviderError` (engine maps it to exit 3, "check keys/
  credit") — and the rescue brain is never dispatched.
* ``attempted == 0`` (chain filtered empty) → last resort still runs and
  reaches the wrongly-flagged vision brain (the 2026-06-21 fix stays green).

Fakes only, no network — mirrors ``test_brain_call_truncation_retry.py``.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.core.protocols import BrainDelta, ImageBlock
from jarvis.cu.brain_call import CUNoVisionProviderError, call_vision_brain

_COMPLETE = '{"action": "done"}'
_IMG = ImageBlock(mime="image/jpeg", data_b64="QQ==", source_hash="x")


class _OkBrain:
    """A vision-capable brain that answers cleanly; counts its dispatches."""

    supports_tools = False
    supports_vision = True

    def __init__(self, text: str = _COMPLETE) -> None:
        self.text = text
        self.calls = 0

    async def complete(self, req: Any):  # type: ignore[no-untyped-def]
        self.calls += 1
        yield BrainDelta(content=self.text)
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: Any) -> float:
        return 0.0


class _FailingVisionBrain:
    """A vision-capable brain that raises a real (non-dead) provider error."""

    supports_tools = False
    supports_vision = True

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req: Any):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise RuntimeError("400 Bad Request: request contains an invalid argument")
        yield  # pragma: no cover — makes this an async generator

    def estimate_cost(self, req: Any) -> float:
        return 0.0


class _BlindBrain:
    """The active provider that cannot see the screen (supports_vision=False)."""

    supports_tools = True
    supports_vision = False

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req: Any):  # type: ignore[no-untyped-def]
        self.calls += 1
        yield BrainDelta(content='{"action": "click", "x": 1, "y": 1}')
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: Any) -> float:
        return 0.0


class _Registry:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def available(self) -> list[str]:
        return list(self._names)


class _Manager:
    """BrainManager-shaped fake exposing the CU dispatch + last-resort surface."""

    def __init__(
        self,
        *,
        chain: list[tuple[str, str | None]],
        brains: dict[str, Any],
        registry: list[str],
    ) -> None:
        self.active_provider = chain[0][0] if chain else ""
        self._chain = chain
        self._brains = brains
        self._registry = _Registry(registry)
        self._dead_providers: set[str] = set()

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        assert level == "fast"
        return list(self._chain)

    def _get_brain(self, name: str, model: str | None = None) -> Any:
        return self._brains.get(name)

    def _cu_model(self, name: str) -> str | None:
        return None

    def _cu_provider(self) -> str:
        return ""


def _build_prompt(provider: str, brain: Any) -> tuple[str, str]:
    return "system", "user"


async def test_real_vision_failures_skip_the_blind_last_resort() -> None:
    """Every real vision provider failed this turn (attempted > 0) → the last
    resort must NOT run, so CU raises the honest ``CUNoVisionProviderError``
    (exit 3) instead of dragging in a resurrected brain."""
    failing = _FailingVisionBrain()
    rescue = _OkBrain()
    manager = _Manager(
        chain=[("vision-a", None)],
        brains={"vision-a": failing, "rescue-b": rescue},
        registry=["vision-a", "rescue-b"],
    )

    with pytest.raises(CUNoVisionProviderError):
        await call_vision_brain(
            manager, build_prompt=_build_prompt, images=[_IMG],
            max_tokens=320, early_stop_json=True,
        )

    assert failing.calls == 1, "the real vision provider must be dispatched once"
    assert rescue.calls == 0, (
        "the last resort must NOT resurrect a provider after a real failure — "
        "that is how the blind codex CLI got dispatched for 68 s"
    )


async def test_stale_dead_flag_still_rescued_when_chain_dispatched_nothing() -> None:
    """The chain reached NO vision brain (the only candidate is blind, so
    attempted == 0). The last resort MUST still run and reach the wrongly
    filtered vision provider — the 2026-06-21 resilience stays intact."""
    blind = _BlindBrain()
    rescue = _OkBrain()
    manager = _Manager(
        chain=[("blind-active", None)],
        brains={"blind-active": blind, "rescue-b": rescue},
        registry=["blind-active", "rescue-b"],
    )

    reply = await call_vision_brain(
        manager, build_prompt=_build_prompt, images=[_IMG],
        max_tokens=320, early_stop_json=True,
    )

    assert reply.text == _COMPLETE
    assert reply.provider == "rescue-b"
    assert blind.calls == 0, "the blind active provider must never plan a screenshot"
    assert rescue.calls == 1, "the last resort must reach the vision-capable brain"
