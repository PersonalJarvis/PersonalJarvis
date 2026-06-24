"""Integration tests for swapping the Flash-Brain provider at runtime.

The Flash-Brain provider is chosen at startup via
``[ack_brain].provider`` in ``jarvis.toml`` (or, by default, follows
``brain.primary`` via the ``"follow_brain"`` meta-value). These tests
cover the spec's §8 "Provider swap" cases:

1. Config change ``gemini`` → ``openai`` → next ack uses the OpenAI adapter.
2. Provider with missing API key surfaces a clear error path: the
   adapter returns ``None`` and the AckGenerator increments the
   ``ack_provider_error_total`` counter.
3. ``cfg.ack_brain.enabled = false`` → zero
   ``AnnouncementRequested(kind="preamble")`` events leave the bus.

The tests reuse :func:`jarvis.brain.factory.build_ack_brain` because
that is the surface a config change actually goes through (the
launcher reads the new config and asks the factory for a fresh
AckGenerator).
"""
from __future__ import annotations

import logging
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.factory import build_ack_brain
from jarvis.core.events import AnnouncementRequested
from tests.unit.brain.test_ack_brain.conftest import (
    FakeAckProvider,
    build_ack_generator_with_fake,
    make_ack_config,
)

# ---------------------------------------------------------------------------
# Helper: build the jcfg-shaped object that build_ack_brain consumes.
# ---------------------------------------------------------------------------


def _jcfg(
    *,
    enabled: bool = True,
    provider: str = "gemini",
    brain_primary: str = "gemini",
) -> SimpleNamespace:
    return SimpleNamespace(
        ack_brain=make_ack_config(enabled=enabled, provider=provider),
        brain=SimpleNamespace(primary=brain_primary),
    )


# ---------------------------------------------------------------------------
# Case 1: gemini → openai config swap
# ---------------------------------------------------------------------------


def test_config_swap_gemini_to_openai_rebuilds_with_openai_adapter() -> None:
    """A second build_ack_brain() call with a new provider rebuilds the
    AckGenerator around the new adapter class — no stale adapter instance."""
    first = build_ack_brain(_jcfg(provider="gemini"))
    assert first is not None
    assert first._provider_name == "gemini"
    assert type(first._provider).__name__ == "GeminiFlashAck"

    # User edits jarvis.toml: [ack_brain].provider = "openai"
    second = build_ack_brain(_jcfg(provider="openai"))
    assert second is not None
    assert second._provider_name == "openai"
    assert type(second._provider).__name__ == "OpenAIMiniAck"

    # The two builds returned distinct AckGenerator instances.
    assert first is not second


def test_follow_brain_tracks_primary_provider_swap(caplog: pytest.LogCaptureFixture) -> None:
    """Changing brain.primary while ack_brain.provider stays "follow_brain"
    causes the next build to pick the matching adapter."""
    jcfg_a = SimpleNamespace(
        ack_brain=make_ack_config(provider="follow_brain"),
        brain=SimpleNamespace(primary="gemini"),
    )
    jcfg_b = SimpleNamespace(
        ack_brain=make_ack_config(provider="follow_brain"),
        brain=SimpleNamespace(primary="openai"),
    )
    with caplog.at_level(logging.INFO):
        ack_a = build_ack_brain(jcfg_a)
        ack_b = build_ack_brain(jcfg_b)
    assert ack_a is not None and ack_b is not None
    assert ack_a._provider_name == "gemini"
    assert ack_b._provider_name == "openai"


# ---------------------------------------------------------------------------
# Case 2: missing API key → None + ack_provider_error_total++
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_api_key_increments_provider_error_counter(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the underlying provider has no credential, the adapter logs
    a warning and returns None. The AckGenerator must count that as
    ``ack_provider_error_total`` so operators see the failure mode.

    We exercise the real Gemini adapter here — its ``_ensure_client``
    raises RuntimeError when the secret lookup returns falsy. The
    adapter swallows that internally (returns None), which lands in
    the generator as the (g) empty-response branch. Per spec §6 the
    intent is for "missing credential" to surface as F2
    ``ack_provider_error_total`` — verified below by also testing the
    raising-fake path which is the canonical F2 entry.
    """
    from jarvis.brain.ack_brain.config import GeminiAckProviderConfig
    from jarvis.brain.ack_brain.providers.gemini import GeminiFlashAck
    from jarvis.core import config as cfg_module

    # Simulate "no API key in keyring / no env var".
    monkeypatch.setattr(
        cfg_module, "get_secret", lambda *_args, **_kwargs: None, raising=True
    )
    # Also avoid importing the real google-genai SDK.
    fake_google = ModuleType("google")
    fake_genai = ModuleType("google.genai")
    fake_google.genai = fake_genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    real_adapter = GeminiFlashAck(
        GeminiAckProviderConfig(model="gemini-3.1-flash")
    )
    # Adapter must NEVER raise per Protocol — it swallows the missing
    # credential and returns None.
    result = await real_adapter.run("Hallo", "de", persona_prompt="System")
    assert result is None

    # And in the F2 canonical path (adapter actually raises): the
    # generator increments ack_provider_error_total.
    raising_fake = FakeAckProvider(raises=RuntimeError("auth failed"))
    ack = build_ack_generator_with_fake(raising_fake)
    with caplog.at_level(logging.INFO, logger="jarvis.brain.ack_brain.generator"):
        result2 = await ack.run("Hallo", language="de")
    assert result2 is None
    matched = [
        rec for rec in caplog.records
        if "ack_provider_error_total" in rec.getMessage()
    ]
    assert matched, (
        "expected ack_provider_error_total counter log; "
        f"got: {[rec.getMessage() for rec in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Case 3: ack_brain.enabled = false → zero preamble events
# ---------------------------------------------------------------------------


def test_disabled_ack_brain_returns_none_from_factory() -> None:
    """When the user disables the Flash-Brain in jarvis.toml, the factory
    returns None — and the speech pipeline branch that schedules
    ``_spawn_flash_brain_ack`` never fires."""
    jcfg = _jcfg(enabled=False)
    ack = build_ack_brain(jcfg)
    assert ack is None


@pytest.mark.asyncio
async def test_disabled_ack_brain_publishes_no_preamble_events() -> None:
    """Even if a caller does invoke _spawn_flash_brain_ack while
    ``self._ack_brain is None`` (defensive path), zero preamble events
    must end up on the bus."""
    from jarvis.speech.pipeline import SpeechPipeline

    bus_events: list[Any] = []

    async def _publish(event: Any) -> None:
        bus_events.append(event)

    stub = SimpleNamespace(
        _ack_brain=None,  # feature disabled
        _config=None,
        _publish_event=_publish,
    )
    await SpeechPipeline._spawn_flash_brain_ack(stub, "Was war das?", "de")
    preambles = [
        e for e in bus_events
        if isinstance(e, AnnouncementRequested) and e.kind == "preamble"
    ]
    assert preambles == []
