"""Wiki provider fallback chain — the fix for the silent single-provider brick.

Live 2026-06-30: openrouter 403 (key over total limit), gemini 429 (credit
depleted) and claude-api 401 (auth) all hit at various moments. The wiki was
pinned to ONE provider with no fallback, so whenever that one erred it silently
journaled/wrote nothing — while the main brain limped on via its chain. These
tests pin the new resilience: cross to a working FAMILY, give up honestly only
when ALL fail.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.memory.wiki.provider_chain import (
    build_wiki_provider_chain,
    complete_with_fallback,
    credential_ready_wiki_providers,
)

_ALL = {"openrouter", "gemini", "claude-api", "openai"}


# --- chain shape (pure) ------------------------------------------------------


def test_chain_leads_with_primary_then_crosses_families():
    chain = build_wiki_provider_chain(primary="openrouter", model_override="", available=_ALL)
    providers = [p for p, _ in chain]
    assert providers[0] == "openrouter"  # configured/primary first
    assert "claude-api" in providers  # then a different family
    assert "gemini" in providers
    assert providers.count("openrouter") == 1  # primary not duplicated


def test_chain_keeps_only_available_providers():
    chain = build_wiki_provider_chain(primary="gemini", model_override="", available={"gemini"})
    assert [p for p, _ in chain] == ["gemini"]  # nothing to cross to


def test_model_override_applies_only_to_primary():
    chain = build_wiki_provider_chain(
        primary="gemini", model_override="gemini-custom-x", available={"gemini", "claude-api"}
    )
    by = dict(chain)
    assert by["gemini"] == "gemini-custom-x"  # explicit model honored for primary
    assert by["claude-api"] != "gemini-custom-x"  # fallback gets its OWN cheap model


@pytest.mark.parametrize(
    "provider",
    ["claude-api", "gemini", "nvidia", "openai", "openrouter", "future-brain"],
)
def test_every_single_registered_provider_can_power_the_wiki(provider: str) -> None:
    chain = build_wiki_provider_chain(
        primary="missing-primary",
        model_override="",
        available={provider},
        credential_ready={provider},
    )
    assert [name for name, _model in chain] == [provider]


def test_keyless_primary_is_skipped_for_the_users_available_key() -> None:
    chain = build_wiki_provider_chain(
        primary="openrouter",
        model_override="openrouter-only-model",
        available={"openrouter", "nvidia"},
        credential_ready={"nvidia"},
    )
    assert [provider for provider, _model in chain] == ["nvidia"]
    assert chain[0][1]  # fallback receives its own cheap provider-family model


def test_credential_probe_uses_core_portable_storage_and_keeps_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.core import config as config_module

    monkeypatch.setattr(
        config_module,
        "resolve_provider_endpoint",
        lambda provider, config: SimpleNamespace(
            credential="configured" if provider == "nvidia" else None
        ),
    )
    ready = credential_ready_wiki_providers(
        available={"openai", "nvidia", "future-oauth"},
        config=object(),
    )
    assert ready == {"nvidia", "future-oauth"}


# --- the fallback loop -------------------------------------------------------


class _FakeBrain:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    def complete(self, request: Any):
        async def _gen():
            if self._fail:
                raise RuntimeError("provider down")
            yield "chunk"

        return _gen()


class _FakeRegistry:
    def __init__(self, fail_providers: set[str]) -> None:
        self._fail = set(fail_providers)
        self.tried: list[str] = []

    def available(self) -> set[str]:
        return set(_ALL)

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.tried.append(name)
        return _FakeBrain(fail=name in self._fail)


async def _aggregate(stream: Any) -> Any:
    chunks = []
    async for c in stream:
        chunks.append(c)
    return type("Agg", (), {"text": "".join(chunks), "finish_reason": "stop"})()


async def test_falls_over_to_first_working_provider():
    reg = _FakeRegistry(fail_providers={"openrouter", "gemini"})
    chain = build_wiki_provider_chain(
        primary="openrouter", model_override="", available=reg.available()
    )
    result = await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    assert result is not None
    agg, provider = result
    assert provider == "claude-api"  # crossed past the two dead ones
    assert reg.tried == ["openrouter", "claude-api"]
    assert agg.text == "chunk"


async def test_returns_none_only_when_every_provider_fails():
    reg = _FakeRegistry(fail_providers=set(_ALL))
    chain = build_wiki_provider_chain(
        primary="openrouter", model_override="", available=reg.available()
    )
    result = await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    assert result is None  # honest give-up, not a crash
    assert set(reg.tried) == _ALL  # it really tried all families


async def test_first_provider_success_does_not_try_others():
    reg = _FakeRegistry(fail_providers=set())
    chain = build_wiki_provider_chain(
        primary="gemini", model_override="", available=reg.available()
    )
    result = await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    assert result is not None
    assert result[1] == "gemini"
    assert reg.tried == ["gemini"]  # no needless fallback calls


class _TextBrain:
    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, request: Any):  # noqa: ARG002
        async def _gen():
            yield self._text

        return _gen()


class _ScriptedRegistry:
    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.tried: list[str] = []

    def instantiate(self, name: str, **kwargs: Any) -> Any:  # noqa: ARG002
        self.tried.append(name)
        return _TextBrain(self._responses[name])


async def test_semantically_invalid_success_crosses_to_next_provider() -> None:
    reg = _ScriptedRegistry(
        {"openrouter": "not-json", "gemini": '[{"fact":"usable"}]'}
    )
    result = await complete_with_fallback(
        registry=reg,
        chain=[("openrouter", None), ("gemini", None)],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
        validate=lambda agg: None if agg.text.startswith("[") else "malformed JSON",
    )

    assert result is not None
    assert result[1] == "gemini"
    assert reg.tried == ["openrouter", "gemini"]


async def test_returns_none_when_every_provider_output_is_invalid() -> None:
    reg = _ScriptedRegistry({"openrouter": "bad", "gemini": "also bad"})
    result = await complete_with_fallback(
        registry=reg,
        chain=[("openrouter", None), ("gemini", None)],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
        validate=lambda _agg: "malformed JSON",
    )

    assert result is None
    assert reg.tried == ["openrouter", "gemini"]
