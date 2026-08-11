"""resolve_subscription_brain — spec-driven, capability-gated, never name-gated.

The guard that matters here is the SKIP: a CLI brain that cannot forward the
caller's system contract answers conversationally, and three fluent sentences
read exactly like a valid brief. Choosing such a provider is worse than choosing
none, because the caller's own deterministic layer is at least honest about what
it is.
"""
from __future__ import annotations

from typing import Any

from jarvis.brain import resolver
from jarvis.core.config import JarvisConfig


class _FakeRegistry:
    """Records how each provider was instantiated."""

    def __init__(
        self,
        available: list[str],
        accepts_structured: tuple[str, ...] = (),
        fail: tuple[str, ...] = (),
        native_system: tuple[str, ...] = (),
    ) -> None:
        self._available = list(available)
        self._accepts = set(accepts_structured)
        self._fail = set(fail)
        self._native = set(native_system)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def available(self) -> list[str]:
        return list(self._available)

    def get_class(self, name: str) -> type:
        return type(
            "FakeBrain", (), {"native_system_prompt": name in self._native}
        )

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, dict(kwargs)))
        if name in self._fail:
            raise RuntimeError("not instantiable")
        if "structured_prompts" in kwargs and name not in self._accepts:
            raise TypeError("unexpected keyword argument 'structured_prompts'")
        return f"brain:{name}"


def _cfg() -> JarvisConfig:
    return JarvisConfig()


def test_only_subscription_providers_are_considered(monkeypatch) -> None:
    """API-key families never appear here — that is resolve_quality_brain's job."""
    registry = _FakeRegistry(
        available=["codex", "claude-cli", "antigravity", "gemini", "openai"],
        accepts_structured=("codex", "claude-cli", "antigravity"),
    )
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    monkeypatch.setattr(resolver, "_subscription_connected", lambda name: True)

    assert resolver.resolve_subscription_brain(_cfg()) is not None
    picked = [name for name, _ in registry.calls]
    assert "gemini" not in picked
    assert "openai" not in picked


def test_a_provider_that_cannot_take_structured_prompts_is_skipped(monkeypatch) -> None:
    """The load-bearing guard against invisible degradation."""
    registry = _FakeRegistry(
        available=["codex", "claude-cli"], accepts_structured=("claude-cli",)
    )
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    monkeypatch.setattr(resolver, "_subscription_connected", lambda name: True)

    assert resolver.resolve_subscription_brain(_cfg()) == "brain:claude-cli"


def test_a_native_system_channel_outranks_card_order(monkeypatch) -> None:
    """Contract fidelity beats card position.

    Live 2026-08-11: antigravity, first by card order, wrote every Agentic IDE
    pane title as a chat acknowledgement ("Understood! I see …") because its
    contract is only prepended text — while the signed-in Claude CLI, which
    takes a real system prompt, was never asked.
    """
    registry = _FakeRegistry(
        available=["antigravity", "claude-cli"],
        accepts_structured=("antigravity", "claude-cli"),
        native_system=("claude-cli",),
    )
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    monkeypatch.setattr(resolver, "_subscription_connected", lambda name: True)

    assert resolver.resolve_subscription_brain(_cfg()) == "brain:claude-cli"


def test_a_preferred_provider_that_is_not_signed_in_still_falls_through(
    monkeypatch,
) -> None:
    """The preference reorders candidates; the connection probe still gates."""
    registry = _FakeRegistry(
        available=["antigravity", "claude-cli"],
        accepts_structured=("antigravity", "claude-cli"),
        native_system=("claude-cli",),
    )
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    monkeypatch.setattr(
        resolver, "_subscription_connected", lambda name: name == "antigravity"
    )

    assert resolver.resolve_subscription_brain(_cfg()) == "brain:antigravity"


def test_disconnected_subscriptions_are_not_offered(monkeypatch) -> None:
    """A CLI that is installed but not logged in must not be chosen."""
    registry = _FakeRegistry(available=["codex"], accepts_structured=("codex",))
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    monkeypatch.setattr(resolver, "_subscription_connected", lambda name: False)

    assert resolver.resolve_subscription_brain(_cfg()) is None


def test_returns_none_instead_of_raising_when_nothing_is_reachable(monkeypatch) -> None:
    """None is the answer, not an error — the caller degrades openly."""
    registry = _FakeRegistry(
        available=["codex"], accepts_structured=("codex",), fail=("codex",)
    )
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    monkeypatch.setattr(resolver, "_subscription_connected", lambda name: True)

    assert resolver.resolve_subscription_brain(_cfg()) is None


def test_cli_timeout_is_forwarded(monkeypatch) -> None:
    """A caller waiting 90 s must not be killed by a provider's own voice cap."""
    registry = _FakeRegistry(available=["codex"], accepts_structured=("codex",))
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    monkeypatch.setattr(resolver, "_subscription_connected", lambda name: True)

    resolver.resolve_subscription_brain(_cfg(), cli_timeout_s=300.0)
    assert registry.calls[0][1]["cli_timeout_s"] == 300.0


def test_structured_prompts_is_always_requested(monkeypatch) -> None:
    """Every candidate is asked for the verbatim contract, never the chat wrapper."""
    registry = _FakeRegistry(available=["codex"], accepts_structured=("codex",))
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    monkeypatch.setattr(resolver, "_subscription_connected", lambda name: True)

    resolver.resolve_subscription_brain(_cfg())
    assert registry.calls[0][1]["structured_prompts"] is True


def test_the_result_is_not_cached_under_the_shared_key(monkeypatch) -> None:
    """The module cache is keyed (provider, model) and shared with the voice-tier
    resolvers. Caching a structured instance there would later hand a spoken turn
    a brain built to ignore its conversational wrapper."""
    registry = _FakeRegistry(available=["codex"], accepts_structured=("codex",))
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    monkeypatch.setattr(resolver, "_subscription_connected", lambda name: True)

    before = dict(resolver._cache)
    resolver.resolve_subscription_brain(_cfg())
    assert dict(resolver._cache) == before


def test_membership_is_spec_driven_not_a_name_list() -> None:
    """Adding a subscription card must extend the chain with no resolver edit."""
    from jarvis.ui.web import provider_spec

    ids = {
        spec.id
        for spec in provider_spec.PROVIDERS
        if spec.tier == "brain"
        and provider_spec.provider_billing(spec).startswith("subscription")
    }
    assert {"codex", "antigravity", "claude-cli"} <= ids


def test_every_subscription_card_can_answer_the_connection_probe() -> None:
    """A card whose class has no probe would be assumed connected forever.

    This is the drift guard: someone adds a fourth subscription brain, forgets
    the probe, and the resolver starts choosing a provider nobody is signed in
    to — which fails at call time, several seconds into a turn.
    """
    from jarvis.brain.provider_registry import BrainProviderRegistry
    from jarvis.ui.web import provider_spec

    registry = BrainProviderRegistry()
    available = set(registry.available())
    for spec in provider_spec.PROVIDERS:
        if spec.tier != "brain" or not provider_spec.provider_billing(
            spec
        ).startswith("subscription"):
            continue
        if spec.id not in available:
            continue
        brain_cls = registry.get_class(spec.id)
        assert callable(
            getattr(brain_cls, "subscription_connected", None)
        ), f"{spec.id} has no subscription_connected probe"
