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
    from jarvis.memory.wiki.telemetry import telemetry

    before = telemetry.get("wiki_all_providers_failed")
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
    # The AP-22 honest signal must actually fire — a mis-bound telemetry
    # import once made this inc raise AttributeError into a swallowed
    # except, so the counter never moved and no test noticed.
    assert telemetry.get("wiki_all_providers_failed") == before + 1


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


async def test_empty_second_opinion_can_find_a_missed_durable_fact() -> None:
    reg = _ScriptedRegistry(
        {"openrouter": "[]", "gemini": '[{"fact":"The user owns a yacht."}]'}
    )

    result = await complete_with_fallback(
        registry=reg,
        chain=[("openrouter", None), ("gemini", None)],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
        validate=lambda agg: "empty-needs-second-opinion" if agg.text == "[]" else None,
        allow_last_rejection=lambda reason: reason == "empty-needs-second-opinion",
    )

    assert result is not None
    assert result[1] == "gemini"
    assert "yacht" in result[0].text
    assert reg.tried == ["openrouter", "gemini"]


async def test_final_valid_empty_is_accepted_after_bounded_second_opinion() -> None:
    reg = _ScriptedRegistry({"openrouter": "[]", "gemini": "[]"})

    result = await complete_with_fallback(
        registry=reg,
        chain=[("openrouter", None), ("gemini", None)],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
        validate=lambda agg: "empty-needs-second-opinion" if agg.text == "[]" else None,
        allow_last_rejection=lambda reason: reason == "empty-needs-second-opinion",
    )

    assert result is not None
    assert result[0].text == "[]"
    assert result[1] == "gemini"
    assert reg.tried == ["openrouter", "gemini"]


async def test_safe_empty_survives_later_unusable_provider() -> None:
    reg = _ScriptedRegistry({"openrouter": "[]", "gemini": "not-json"})

    def _validate(agg: Any) -> str | None:
        if agg.text == "[]":
            return "empty-needs-second-opinion"
        return "malformed JSON"

    result = await complete_with_fallback(
        registry=reg,
        chain=[("openrouter", None), ("gemini", None)],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
        validate=_validate,
        allow_last_rejection=lambda reason: reason == "empty-needs-second-opinion",
    )

    assert result is not None
    assert result[0].text == "[]"
    assert result[1] == "openrouter"
    assert reg.tried == ["openrouter", "gemini"]


async def test_two_valid_empty_opinions_stop_later_provider_attempts() -> None:
    reg = _ScriptedRegistry(
        {
            "openrouter": "[]",
            "antigravity": "not-json",
            "gemini": "[]",
            "nvidia": '[{"fact":"too late"}]',
        }
    )

    def _validate(agg: Any) -> str | None:
        if agg.text == "[]":
            return "empty-needs-second-opinion"
        if not agg.text.startswith("["):
            return "malformed JSON"
        return None

    result = await complete_with_fallback(
        registry=reg,
        chain=[
            ("openrouter", None),
            ("antigravity", None),
            ("gemini", None),
            ("nvidia", None),
        ],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
        validate=_validate,
        allow_last_rejection=lambda reason: reason == "empty-needs-second-opinion",
    )

    assert result is not None
    assert result[0].text == "[]"
    assert result[1] == "gemini"
    assert reg.tried == ["openrouter", "antigravity", "gemini"]


async def test_allowed_rejection_never_cools_the_provider_down() -> None:
    """A valid-but-empty answer is a CONTENT verdict, not provider damage —
    it must not demote the provider for the next 15 minutes."""
    from jarvis.memory.wiki import provider_chain as pc

    reg = _ScriptedRegistry(
        {"openrouter": "[]", "gemini": '[{"fact":"usable"}]'}
    )
    result = await complete_with_fallback(
        registry=reg,
        chain=[("openrouter", None), ("gemini", None)],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
        validate=lambda agg: "empty-needs-second-opinion" if agg.text == "[]" else None,
        allow_last_rejection=lambda reason: reason == "empty-needs-second-opinion",
    )
    assert result is not None and result[1] == "gemini"
    assert not pc._in_cooldown("openrouter")


async def test_chain_success_clears_the_sticky_health_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live 2026-07-18: one exhausted chain painted the Wiki tab red forever
    ("Obsidian not connected" perception) although later runs succeeded —
    nothing ever cleared last_chain_failure. Any usable outcome must clear it."""
    from jarvis.memory.wiki import health as health_module
    from jarvis.memory.wiki.health import WikiHealth

    isolated_health = WikiHealth()
    monkeypatch.setattr(health_module, "health", isolated_health)

    reg = _FakeRegistry(fail_providers={"openrouter", "gemini"})
    chain = [("openrouter", None), ("gemini", None)]
    failed = await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    assert failed is None
    assert isolated_health.snapshot()["last_chain_failure"] is not None

    reg._fail = set()  # providers recover
    ok = await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    assert ok is not None
    assert isolated_health.snapshot()["last_chain_failure"] is None


# --- failure cooldown: dead rungs stop taxing every call ---------------------


async def test_recently_failed_provider_is_demoted_behind_healthy_ones() -> None:
    reg = _FakeRegistry(fail_providers={"openrouter"})
    chain = [("openrouter", None), ("gemini", None)]

    first = await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    assert first is not None and first[1] == "gemini"
    assert reg.tried == ["openrouter", "gemini"]  # the failure that arms the cooldown

    second = await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    assert second is not None and second[1] == "gemini"
    # Within the cooldown the dead provider is no longer tried FIRST — the
    # healthy one answers before the doomed round-trip is even attempted.
    assert reg.tried == ["openrouter", "gemini", "gemini"]


async def test_cooldown_never_removes_the_last_resort() -> None:
    """AP-22 honesty: when every healthy provider fails, cooled ones still run."""
    reg = _FakeRegistry(fail_providers={"openrouter"})
    chain = [("openrouter", None), ("gemini", None)]

    await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )  # arms the cooldown for openrouter

    reg._fail = {"gemini"}  # now the previously-healthy provider dies...
    reg.tried.clear()
    result = await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    # ...and the cooled provider, tried last, saves the call.
    assert result is not None and result[1] == "openrouter"
    assert reg.tried == ["gemini", "openrouter"]


async def test_transport_success_clears_the_cooldown() -> None:
    from jarvis.memory.wiki import provider_chain as pc

    reg = _FakeRegistry(fail_providers={"openrouter"})
    chain = [("openrouter", None), ("gemini", None)]
    await complete_with_fallback(
        registry=reg,
        chain=chain,
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    assert pc._in_cooldown("openrouter")

    reg._fail = set()  # provider recovered
    reg.tried.clear()
    result = await complete_with_fallback(
        registry=reg,
        chain=[("gemini", None), ("openrouter", None)],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    assert result is not None and result[1] == "gemini"
    # gemini answered first, so openrouter stays cooled until it is needed…
    reg.tried.clear()
    result2 = await complete_with_fallback(
        registry=reg,
        chain=[("openrouter", None)],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )
    # …a single-provider chain still tries it (demotion, never removal) and
    # the success clears the memory.
    assert result2 is not None and result2[1] == "openrouter"
    assert not pc._in_cooldown("openrouter")


async def test_cooldown_expires_by_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.memory.wiki import provider_chain as pc

    clock = {"now": 1000.0}
    monkeypatch.setattr(pc.time, "monotonic", lambda: clock["now"])
    pc._note_provider_failure("openrouter", "429")
    assert pc._in_cooldown("openrouter")
    clock["now"] += pc._PROVIDER_COOLDOWN_S + 1
    assert not pc._in_cooldown("openrouter")


@pytest.mark.asyncio
async def test_provider_failures_never_expose_raw_exception_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from jarvis.memory.wiki import health as health_module
    from jarvis.memory.wiki.health import WikiHealth

    secret = "sk-proj-" + "Z" * 32

    class _SecretRegistry:
        def instantiate(self, _name: str, **_kwargs: Any) -> Any:
            raise RuntimeError(f"request failed ?key={secret} at C:/Users/private")

    isolated_health = WikiHealth()
    monkeypatch.setattr(health_module, "health", isolated_health)
    result = await complete_with_fallback(
        registry=_SecretRegistry(),
        chain=[("openrouter", None)],
        request=object(),
        timeout_s=5.0,
        label="test",
        aggregate=_aggregate,
    )

    assert result is None
    detail = isolated_health.snapshot()["last_chain_failure"]["detail"]
    assert "RuntimeError" in detail
    assert secret not in detail
    assert "C:/Users/private" not in detail
    assert secret not in caplog.text
