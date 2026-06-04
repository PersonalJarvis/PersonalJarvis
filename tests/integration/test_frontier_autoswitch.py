"""Integration-Test fuer den Frontier-Autoswitch-Boot-Hook.

Mit gemocktem FrontierResolver verifizieren wir:
1. Bei neuerem Modell: ProviderConfig wird mutiert + Bus-Event emittiert +
   Pending-Eintrag liegt in der Modal-Queue.
2. Bei gleichem Modell: keine Mutation, kein Event, keine Pending.
3. Bei Resolver-Crash: keine Mutation, kein Event, kein Pending —
   silent Fallback auf TOML-Default.
4. Sub-Jarvis-Override (`[brain.sub_jarvis] model = "..."`) bleibt
   unangetastet.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.frontier_autoswitch import (
    ack_pending_switches,
    apply_frontier_resolution,
    get_pending_switches,
)
from jarvis.core.bus import EventBus
from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    BrainTierConfig,
    JarvisConfig,
)
from jarvis.core.events import FrontierModelSwitched


class _StubResolver:
    """Resolver-Stub: returns vorgegebene Werte pro (provider, tier)."""

    def __init__(self, mapping: dict[tuple[str, str], str | None]) -> None:
        self._map = mapping
        self.calls: list[tuple[str, str]] = []

    async def resolve_latest(self, provider: str, tier: str) -> str | None:
        self.calls.append((provider, tier))
        return self._map.get((provider, tier))


class _CrashingResolver:
    """Resolver-Stub der bei jedem Call crasht."""

    async def resolve_latest(self, provider: str, tier: str) -> str | None:
        raise RuntimeError("simulated provider down")


def _make_config_with_old_models() -> JarvisConfig:
    """Baut eine JarvisConfig mit alten Hauptjarvis-Modellen + Sub-Jarvis-Pin."""
    cfg = JarvisConfig.model_validate({
        "brain": {
            "primary": "claude-api",
            "providers": {
                "gemini": {
                    "model": "gemini-2.5-flash",
                    "deep_model": "gemini-2.5-pro",
                },
                "grok": {"model": "grok-3"},
                "claude-api": {
                    "model": "claude-sonnet-4-6",
                    "deep_model": "claude-opus-4-7",
                },
            },
            # Sub-Jarvis-Pin: explizites Override
            "sub_jarvis": {
                "provider": "gemini",
                "model": "gemini-2.5-pro",
            },
        },
    })
    return cfg


@pytest.fixture(autouse=True)
def _clear_pending_between_tests() -> Any:
    """Module-State (`_pending_switches`) zwischen Tests resetten."""
    ack_pending_switches()
    yield
    ack_pending_switches()


@pytest.fixture(autouse=True)
def _mock_toml_persist(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, str | None]]]:
    """Verhindert, dass Tests die echte jarvis.toml beschreiben.

    Returns die Liste der Calls, damit Tests verifizieren koennen dass
    persist tatsaechlich aufgerufen wurde.
    """
    calls: list[tuple[str, dict[str, str | None]]] = []

    def _stub(provider: str, *, model: str | None = None,
              deep_model: str | None = None, **_: object) -> None:
        calls.append((provider, {"model": model, "deep_model": deep_model}))

    monkeypatch.setattr(
        "jarvis.brain.frontier_autoswitch.set_brain_provider_model", _stub,
    )
    return calls


@pytest.mark.asyncio
async def test_apply_switches_when_resolver_returns_newer_models(
    _mock_toml_persist: list[tuple[str, dict[str, str | None]]],
) -> None:
    cfg = _make_config_with_old_models()
    resolver = _StubResolver({
        ("gemini", "fast"): "gemini-3-flash",
        ("gemini", "deep"): "gemini-3.1-pro-preview",
        ("grok", "fast"): "grok-4.1-fast",
        ("claude-api", "fast"): "claude-sonnet-4-6",  # unverändert
        ("claude-api", "deep"): "claude-opus-4-7",    # unverändert
    })
    bus = EventBus()
    received: list[FrontierModelSwitched] = []
    bus.subscribe(FrontierModelSwitched, lambda e: received.append(e))

    switches = await apply_frontier_resolution(cfg, resolver, bus)

    # Verifiziere dass TOML-Persist auch gerufen wurde (3 Switches → 3 Calls).
    assert len(_mock_toml_persist) == 3
    persist_providers = {p for p, _ in _mock_toml_persist}
    assert persist_providers == {"gemini", "grok"}

    # 3 Switches: gemini/fast, gemini/deep, grok/fast.
    assert len(switches) == 3
    providers_switched = {(s.provider, s.tier) for s in switches}
    assert providers_switched == {
        ("gemini", "fast"), ("gemini", "deep"), ("grok", "fast"),
    }

    # Config tatsaechlich mutiert
    assert cfg.brain.providers["gemini"].model == "gemini-3-flash"
    assert cfg.brain.providers["gemini"].deep_model == "gemini-3.1-pro-preview"
    assert cfg.brain.providers["grok"].model == "grok-4.1-fast"

    # Sub-Jarvis-Override unangetastet
    assert cfg.brain.sub_jarvis is not None
    assert cfg.brain.sub_jarvis.model == "gemini-2.5-pro"

    # Bus-Events publisht (event-loop-tick fuer subscribe-async)
    import asyncio
    await asyncio.sleep(0.01)
    assert len(received) == 3
    assert any(
        e.provider == "gemini" and e.tier == "fast"
        and e.old_model == "gemini-2.5-flash"
        and e.new_model == "gemini-3-flash"
        for e in received
    )

    # Pending-Modal-Queue gefuellt
    pending = get_pending_switches()
    assert len(pending) == 3


@pytest.mark.asyncio
async def test_no_switch_when_models_unchanged() -> None:
    cfg = _make_config_with_old_models()
    resolver = _StubResolver({
        ("gemini", "fast"): "gemini-2.5-flash",  # gleich
        ("gemini", "deep"): "gemini-2.5-pro",    # gleich
        ("grok", "fast"): "grok-3",              # gleich
        ("claude-api", "fast"): "claude-sonnet-4-6",
        ("claude-api", "deep"): "claude-opus-4-7",
    })
    bus = EventBus()
    received: list[FrontierModelSwitched] = []
    bus.subscribe(FrontierModelSwitched, lambda e: received.append(e))

    switches = await apply_frontier_resolution(cfg, resolver, bus)
    assert switches == []
    assert get_pending_switches() == []
    import asyncio
    await asyncio.sleep(0.01)
    assert received == []


@pytest.mark.asyncio
async def test_resolver_crash_does_not_mutate_config() -> None:
    cfg = _make_config_with_old_models()
    original_grok = cfg.brain.providers["grok"].model
    resolver = _CrashingResolver()
    bus = EventBus()

    # Sollte nicht raisen — Auto-Switch ist defensive.
    switches = await apply_frontier_resolution(cfg, resolver, bus)
    assert switches == []

    # Config unveraendert
    assert cfg.brain.providers["grok"].model == original_grok


@pytest.mark.asyncio
async def test_resolver_returns_none_no_switch() -> None:
    """Wenn Resolver fuer einen Provider None liefert (z.B. kein API-Key),
    soll fuer diesen Provider nichts passieren — andere Provider weiterhin OK.
    """
    cfg = _make_config_with_old_models()
    resolver = _StubResolver({
        ("gemini", "fast"): None,                  # kein Key
        ("gemini", "deep"): None,
        ("grok", "fast"): "grok-4.1-fast",         # OK
        ("claude-api", "fast"): "claude-sonnet-4-6",
        ("claude-api", "deep"): "claude-opus-4-7",
    })
    bus = EventBus()

    switches = await apply_frontier_resolution(cfg, resolver, bus)
    # Nur grok/fast hat geswitcht
    assert len(switches) == 1
    assert switches[0].provider == "grok"
    # Gemini unangetastet
    assert cfg.brain.providers["gemini"].model == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_ack_clears_pending() -> None:
    cfg = _make_config_with_old_models()
    resolver = _StubResolver({
        ("gemini", "fast"): "gemini-3-flash",
        ("gemini", "deep"): None,
        ("grok", "fast"): None,
        ("claude-api", "fast"): None,
        ("claude-api", "deep"): None,
    })
    bus = EventBus()
    await apply_frontier_resolution(cfg, resolver, bus)
    assert len(get_pending_switches()) == 1
    cleared = ack_pending_switches()
    assert cleared == 1
    assert get_pending_switches() == []
