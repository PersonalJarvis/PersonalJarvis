"""The lazy frontier candidate walk that makes AP-22 hold at CALL time.

``resolve_frontier_brain`` crosses families only when a stage cannot be
INSTANTIATED — but the ordinary depleted-key failure happens later, at call
time, after the client constructed happily. ``frontier_brain_candidates`` is
the call-time half: a caller iterates it and moves to the next provider FAMILY
when a request itself fails. These tests pin the promises that make that walk
usable: one candidate per family, lazy instantiation, and no exceptions out of
a broken chain.
"""
from __future__ import annotations

import pytest

from jarvis.brain import resolver


class _FakeRegistry:
    def __init__(self, fail_providers: set[str] | None = None) -> None:
        self.attempts: list[tuple[str, str]] = []
        self.fail_providers = fail_providers or set()

    def instantiate(self, provider: str, **kwargs: object) -> object:
        self.attempts.append((provider, str(kwargs.get("model", ""))))
        if provider in self.fail_providers:
            raise RuntimeError("no key")
        return object()


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, "_cache", {})


def test_each_provider_family_appears_at_most_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second model behind the same depleted key would fail the same way."""
    monkeypatch.setattr(
        resolver,
        "_resolve_chain",
        lambda cfg: [
            ("gemini", "gemini-3.1-pro-preview"),
            ("gemini", "gemini-3.5-flash"),
            ("claude-api", "claude-opus-5"),
        ],
    )
    fake = _FakeRegistry()
    monkeypatch.setattr(resolver, "_get_registry", lambda: fake)

    candidates = list(resolver.frontier_brain_candidates(object()))

    assert len(candidates) == 2
    assert fake.attempts == [
        ("gemini", "gemini-3.1-pro-preview"),
        ("claude-api", "claude-opus-5"),
    ]


def test_instantiation_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller that stops after the first candidate never pays for the rest."""
    monkeypatch.setattr(
        resolver,
        "_resolve_chain",
        lambda cfg: [("gemini", "gemini-3.1-pro-preview"), ("openai", "gpt-5.5-pro")],
    )
    fake = _FakeRegistry()
    monkeypatch.setattr(resolver, "_get_registry", lambda: fake)

    next(resolver.frontier_brain_candidates(object()))

    assert fake.attempts == [("gemini", "gemini-3.1-pro-preview")]


def test_a_stage_that_cannot_instantiate_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver,
        "_resolve_chain",
        lambda cfg: [("gemini", "gemini-3.1-pro-preview"), ("openai", "gpt-5.5-pro")],
    )
    fake = _FakeRegistry(fail_providers={"gemini"})
    monkeypatch.setattr(resolver, "_get_registry", lambda: fake)

    candidates = list(resolver.frontier_brain_candidates(object()))

    assert len(candidates) == 1
    assert fake.attempts[-1] == ("openai", "gpt-5.5-pro")


def test_a_broken_chain_yields_nothing_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(cfg: object) -> list[tuple[str, str]]:
        raise ValueError("config is nonsense")

    monkeypatch.setattr(resolver, "_resolve_chain", explode)

    assert list(resolver.frontier_brain_candidates(object())) == []
