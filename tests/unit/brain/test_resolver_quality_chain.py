"""The writer chain that refuses to demote a quality task to a fast model.

``resolve_frontier_brain`` walks the whole fallback chain, and that chain
deliberately ends in small, fast, cheap models so a core path never dies. For
callers whose OUTPUT is the product — the Agentic IDE's prompt composer is the
first — that trade is wrong: a prompt written by a mini model on a depleted
primary is worse than an honestly plain deterministic one, and worse
*invisibly*. This resolver skips the latency-first stages and answers None
rather than quietly handing back something weaker than asked for.
"""
from __future__ import annotations

import pytest

from jarvis.brain import resolver


class _FakeRegistry:
    """Records what the resolver tried to instantiate."""

    def __init__(self, fail: bool = False) -> None:
        self.attempts: list[tuple[str, str]] = []
        self.fail = fail

    def instantiate(self, provider: str, **kwargs: object) -> object:
        self.attempts.append((provider, str(kwargs.get("model", ""))))
        if self.fail:
            raise RuntimeError("no key")
        return object()


def _config(router_provider: str = "gemini", router_model: str = "") -> object:
    """A stand-in carrying only what the chain filter reads."""

    class Router:
        provider = router_provider
        model = router_model

    class Brain:
        router = Router()

    class Config:
        brain = Brain()

    return Config()


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, "_cache", {})


def test_a_built_in_fast_tier_stage_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """claude-haiku is claude-api's ROUTER default — it must not write prompts."""
    monkeypatch.setattr(
        resolver,
        "_resolve_chain",
        lambda cfg: [
            ("claude-api", "claude-haiku-4-5-20251001"),
            ("gemini", "gemini-3.1-pro-preview"),
        ],
    )
    fake = _FakeRegistry()
    monkeypatch.setattr(resolver, "_get_registry", lambda: fake)

    brain = resolver.resolve_quality_brain(_config())

    assert brain is not None
    assert fake.attempts == [("gemini", "gemini-3.1-pro-preview")]


def test_the_users_own_configured_fast_model_is_skipped_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The install's configured router tier counts, not only the built-in
    defaults — otherwise a user who picked their own fast model is not
    protected."""
    monkeypatch.setattr(
        resolver,
        "_resolve_chain",
        lambda cfg: [("gemini", "gemini-3.5-flash"), ("openai", "gpt-5.5-pro")],
    )
    fake = _FakeRegistry()
    monkeypatch.setattr(resolver, "_get_registry", lambda: fake)

    resolver.resolve_quality_brain(_config("gemini", "gemini-3.5-flash"))

    assert ("gemini", "gemini-3.5-flash") not in fake.attempts
    assert ("openai", "gpt-5.5-pro") in fake.attempts


def test_a_providers_own_fast_default_is_skipped_even_mid_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gpt-5.5 is openai's ROUTER default while gpt-5.5-pro is its deep one, so
    the plain model is a latency-first stage even though it looks capable."""
    monkeypatch.setattr(
        resolver,
        "_resolve_chain",
        lambda cfg: [("openai", "gpt-5.5"), ("openai", "gpt-5.5-pro")],
    )
    fake = _FakeRegistry()
    monkeypatch.setattr(resolver, "_get_registry", lambda: fake)

    resolver.resolve_quality_brain(_config())

    assert fake.attempts == [("openai", "gpt-5.5-pro")]


def test_returns_none_when_only_fast_models_are_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Honestly plain beats silently poor: the caller degrades openly."""
    monkeypatch.setattr(
        resolver,
        "_resolve_chain",
        lambda cfg: [("claude-api", "claude-haiku-4-5-20251001")],
    )
    fake = _FakeRegistry()
    monkeypatch.setattr(resolver, "_get_registry", lambda: fake)

    assert resolver.resolve_quality_brain(_config()) is None
    assert fake.attempts == []


def test_returns_none_rather_than_raising_when_nothing_instantiates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver, "_resolve_chain", lambda cfg: [("gemini", "gemini-3.1-pro-preview")]
    )
    monkeypatch.setattr(resolver, "_get_registry", lambda: _FakeRegistry(fail=True))

    assert resolver.resolve_quality_brain(_config()) is None


def test_returns_none_rather_than_raising_on_a_broken_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(cfg: object) -> list[tuple[str, str]]:
        raise ValueError("config is nonsense")

    monkeypatch.setattr(resolver, "_resolve_chain", explode)

    assert resolver.resolve_quality_brain(_config()) is None


def test_a_provider_whose_fast_and_deep_defaults_coincide_is_not_filtered_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filtering must never leave a provider with nothing. grok's router and
    deep defaults are the same model, so it is that provider's only option."""
    from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER

    router_default = TIER_DEFAULTS_BY_PROVIDER["router"].get("grok")
    deep_default = TIER_DEFAULTS_BY_PROVIDER["deep"].get("grok")
    if router_default != deep_default:
        pytest.skip("grok's tier defaults have diverged; the case moved elsewhere")

    monkeypatch.setattr(
        resolver, "_resolve_chain", lambda cfg: [("grok", router_default)]
    )
    fake = _FakeRegistry()
    monkeypatch.setattr(resolver, "_get_registry", lambda: fake)

    assert resolver.resolve_quality_brain(_config()) is not None


def test_an_unknown_model_is_treated_as_qualified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """We filter what the tier tables KNOW is fast; we do not guess from names.
    Gating on a name would be the anti-pattern AP-21 forbids."""
    monkeypatch.setattr(
        resolver, "_resolve_chain", lambda cfg: [("someprovider", "some-mini-flash")]
    )
    fake = _FakeRegistry()
    monkeypatch.setattr(resolver, "_get_registry", lambda: fake)

    assert resolver.resolve_quality_brain(_config()) is not None
    assert fake.attempts == [("someprovider", "some-mini-flash")]
