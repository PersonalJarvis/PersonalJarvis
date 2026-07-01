"""Integration test for the Frontier-Autoswitch boot hook.

With a mocked FrontierResolver we verify:
1. On a newer model: ProviderConfig is mutated + a bus event is emitted +
   a pending entry lands in the modal queue.
2. On the same model: no mutation, no event, no pending.
3. On a resolver crash: no mutation, no event, no pending —
   silent fallback to the TOML default.
4. The Sub-Jarvis override (`[brain.sub_jarvis] model = "..."`) stays
   untouched.
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
from jarvis.core.config import JarvisConfig
from jarvis.core.events import FrontierModelSwitched


class _StubResolver:
    """Resolver stub: returns pre-set values per (provider, tier)."""

    def __init__(self, mapping: dict[tuple[str, str], str | None]) -> None:
        self._map = mapping
        self.calls: list[tuple[str, str]] = []

    async def resolve_latest(self, provider: str, tier: str) -> str | None:
        self.calls.append((provider, tier))
        return self._map.get((provider, tier))


class _CrashingResolver:
    """Resolver stub that crashes on every call."""

    async def resolve_latest(self, provider: str, tier: str) -> str | None:
        raise RuntimeError("simulated provider down")


def _make_config_with_old_models() -> JarvisConfig:
    """Builds a JarvisConfig with old Hauptjarvis models + a Sub-Jarvis pin."""
    cfg = JarvisConfig.model_validate({
        "brain": {
            "primary": "claude-api",
            # The auto-switch is opt-in (default False); these tests exercise the
            # enabled apply path, so turn it on explicitly.
            "frontier_auto_apply": True,
            "providers": {
                "gemini": {
                    "model": "gemini-2.5-flash",
                    "deep_model": "gemini-2.5-pro",
                },
                "openai": {"model": "gpt-5"},
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
    """Prevents tests from writing the real jarvis.toml.

    Returns the list of calls so tests can verify that
    persist was actually invoked.
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
        ("openai", "fast"): "gpt-5.5",
        ("claude-api", "fast"): "claude-sonnet-4-6",  # unchanged
        ("claude-api", "deep"): "claude-opus-4-7",    # unchanged
    })
    bus = EventBus()
    received: list[FrontierModelSwitched] = []
    bus.subscribe(FrontierModelSwitched, lambda e: received.append(e))

    switches = await apply_frontier_resolution(cfg, resolver, bus)

    # Verify that TOML persist was also called (3 switches → 3 calls).
    assert len(_mock_toml_persist) == 3
    persist_providers = {p for p, _ in _mock_toml_persist}
    assert persist_providers == {"gemini", "openai"}

    # 3 Switches: gemini/fast, gemini/deep, openai/fast.
    assert len(switches) == 3
    providers_switched = {(s.provider, s.tier) for s in switches}
    assert providers_switched == {
        ("gemini", "fast"), ("gemini", "deep"), ("openai", "fast"),
    }

    # Config tatsaechlich mutiert
    assert cfg.brain.providers["gemini"].model == "gemini-3-flash"
    assert cfg.brain.providers["gemini"].deep_model == "gemini-3.1-pro-preview"
    assert cfg.brain.providers["openai"].model == "gpt-5.5"

    # Sub-Jarvis-Override unangetastet
    assert cfg.brain.worker is not None
    assert cfg.brain.worker.model == "gemini-2.5-pro"

    # Bus events published (event-loop tick for subscribe-async)
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
async def test_disabled_by_default_is_noop(
    _mock_toml_persist: list[tuple[str, dict[str, str | None]]],
) -> None:
    """With ``brain.frontier_auto_apply`` unset (default False) the boot hook is
    a complete no-op: no resolver call, no TOML/soll persist, no mutation, no  # i18n-allow — "soll" = config-soll.json, not German prose
    event, no pending — even though newer models are available. User mandate
    2026-06-20: providers/models must NOT switch by themselves.
    """
    cfg = _make_config_with_old_models()
    # Flip the flag back off — the default the user actually runs with.
    cfg.brain.frontier_auto_apply = False
    resolver = _StubResolver({
        ("gemini", "fast"): "gemini-3-flash",
        ("gemini", "deep"): "gemini-3.1-pro-preview",
        ("openai", "fast"): "gpt-5.5",
    })
    bus = EventBus()
    received: list[FrontierModelSwitched] = []
    bus.subscribe(FrontierModelSwitched, lambda e: received.append(e))

    switches = await apply_frontier_resolution(cfg, resolver, bus)

    assert switches == []
    assert resolver.calls == []           # resolver never queried
    assert _mock_toml_persist == []       # nothing persisted to TOML/soll  # i18n-allow — "soll" = config-soll.json
    assert cfg.brain.providers["gemini"].model == "gemini-2.5-flash"  # unmutated
    assert get_pending_switches() == []
    import asyncio
    await asyncio.sleep(0.01)
    assert received == []


@pytest.mark.asyncio
async def test_no_switch_when_models_unchanged() -> None:
    cfg = _make_config_with_old_models()
    resolver = _StubResolver({
        ("gemini", "fast"): "gemini-2.5-flash",  # gleich
        ("gemini", "deep"): "gemini-2.5-pro",    # gleich
        ("openai", "fast"): "gpt-5",             # gleich
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
    original_openai = cfg.brain.providers["openai"].model
    resolver = _CrashingResolver()
    bus = EventBus()

    # Should not raise — auto-switch is defensive.
    switches = await apply_frontier_resolution(cfg, resolver, bus)
    assert switches == []

    # Config unveraendert
    assert cfg.brain.providers["openai"].model == original_openai


@pytest.mark.asyncio
async def test_resolver_returns_none_no_switch() -> None:
    """When the resolver returns None for a provider (e.g. no API key),
    nothing should happen for that provider — other providers stay OK.
    """
    cfg = _make_config_with_old_models()
    resolver = _StubResolver({
        ("gemini", "fast"): None,                  # no key
        ("gemini", "deep"): None,
        ("openai", "fast"): "gpt-5.5",             # OK
        ("claude-api", "fast"): "claude-sonnet-4-6",
        ("claude-api", "deep"): "claude-opus-4-7",
    })
    bus = EventBus()

    switches = await apply_frontier_resolution(cfg, resolver, bus)
    # Nur openai/fast hat geswitcht
    assert len(switches) == 1
    assert switches[0].provider == "openai"
    # Gemini unangetastet
    assert cfg.brain.providers["gemini"].model == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_ack_clears_pending() -> None:
    cfg = _make_config_with_old_models()
    resolver = _StubResolver({
        ("gemini", "fast"): "gemini-3-flash",
        ("gemini", "deep"): None,
        ("openai", "fast"): None,
        ("claude-api", "fast"): None,
        ("claude-api", "deep"): None,
    })
    bus = EventBus()
    await apply_frontier_resolution(cfg, resolver, bus)
    assert len(get_pending_switches()) == 1
    cleared = ack_pending_switches()
    assert cleared == 1
    assert get_pending_switches() == []
