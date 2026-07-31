"""resolve_tool_model_brain — do what the user picked, or say you could not.

The Tool Model tab is where a user answers "which model does work on my behalf".
Until callers could resolve THAT answer, the Agentic IDE's brief writer ran its
own order and landed wherever that order ended — so a user who had deliberately
pinned a strong Tool Model still found their task briefs written by whichever
coding CLI happened to be signed in, with no way to tell from the UI.

The guard that matters here is the absence of a chain: this resolver must never
walk on to a second candidate. A tier resolver falls through so a core path
never dies; falling through here would reintroduce exactly the silent
substitution the setting exists to end.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain import resolver
from jarvis.core.config import BrainProviderConfig, BrainTierConfig, JarvisConfig


class _FakeRegistry:
    """Records how each provider was instantiated."""

    def __init__(self, available: list[str], fail: tuple[str, ...] = ()) -> None:
        self._available = list(available)
        self._fail = set(fail)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def available(self) -> list[str]:
        return list(self._available)

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, dict(kwargs)))
        if name in self._fail:
            raise RuntimeError("not instantiable")
        return f"brain:{name}"


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    resolver._reset_for_tests()
    yield
    resolver._reset_for_tests()


def _cfg(provider: str | None, **provider_fields: Any) -> JarvisConfig:
    config = JarvisConfig()
    if provider is not None:
        config.brain.tool_model = BrainTierConfig(provider=provider)
        if provider_fields:
            config.brain.providers[provider] = BrainProviderConfig(**provider_fields)
    return config


def test_the_pinned_tool_model_is_what_gets_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _FakeRegistry(["gemini", "openai"])
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)

    brain = resolver.resolve_tool_model_brain(_cfg("gemini", tool_model="pinned-model"))

    assert brain == "brain:gemini"
    assert registry.calls == [("gemini", {"model": "pinned-model"})]


def test_the_per_provider_tool_model_beats_the_chat_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user who picks a provider means "that provider's TOOL model" — the
    field the Tool Model tab writes — not whatever its chat default happens
    to be."""
    registry = _FakeRegistry(["gemini"])
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)

    resolver.resolve_tool_model_brain(
        _cfg("gemini", model="chat-default", tool_model="tool-pick")
    )

    assert registry.calls == [("gemini", {"model": "tool-pick"})]


def test_the_plain_model_is_the_floor_when_no_override_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _FakeRegistry(["gemini"])
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)

    resolver.resolve_tool_model_brain(_cfg("gemini", model="chat-default"))

    assert registry.calls == [("gemini", {"model": "chat-default"})]


def test_an_unpinned_tool_model_resolves_to_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'auto' is the user not having answered the question. Guessing one here
    is what the whole setting exists to stop."""
    registry = _FakeRegistry(["gemini"])
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)

    assert resolver.resolve_tool_model_brain(_cfg("auto")) is None
    assert resolver.resolve_tool_model_brain(_cfg(None)) is None
    assert registry.calls == []


def test_an_uninstantiable_pin_never_walks_on_to_another_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load-bearing guard: no chain. A missing key is the ordinary case,
    and answering it with a DIFFERENT model is the silent substitution this
    resolver was added to end."""
    registry = _FakeRegistry(["gemini", "openai"], fail=("gemini",))
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)

    brain = resolver.resolve_tool_model_brain(_cfg("gemini"))

    assert brain is None
    assert [name for name, _kwargs in registry.calls] == ["gemini"]


def test_a_malformed_section_reads_as_unset_rather_than_raising() -> None:
    """A caller degrades to a plain prompt; none of them may lose the turn."""

    class _Exploding:
        @property
        def brain(self) -> Any:
            raise RuntimeError("config is broken")

    assert resolver._tool_model_selection(_Exploding()) == ("auto", None)


def test_the_legacy_computer_use_name_still_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tool_model` is the canonical name; installs written before the rename
    carry `computer_use` and must keep working (AP-16 read-time alias)."""
    registry = _FakeRegistry(["gemini"])
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    config = JarvisConfig()
    config.brain.computer_use = BrainTierConfig(provider="gemini")

    assert resolver.resolve_tool_model_brain(config) == "brain:gemini"
