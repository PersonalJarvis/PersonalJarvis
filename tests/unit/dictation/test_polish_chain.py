"""The polish provider chain and its circuit breaker.

Two properties are load-bearing here and neither is visible from the outside
until it fails on somebody else's machine:

* **The chain is derived from credentials, not from names** (AP-21/AP-22). It
  returns the families the user actually holds a key for, one entry per family,
  and it crosses families on failure rather than retrying a second model inside
  a rate-limited account. With no key anywhere it is EMPTY — the AP-23 gate,
  because the maintainer's key must never be the thing that makes a default
  safe.
* **The breaker stops a dead provider taxing every dictation.** Three
  consecutive failures and the pass short-circuits for two minutes instead of
  spending its whole latency budget discovering the same outage again.

Credentials are faked by replacing ``jarvis.core.config.get_secret`` — the real
lookup — so the slot NAMES in ``POLISH_FAMILIES`` are part of what is pinned. A
test that patched the private helper instead would keep passing after a typo in
a keyring slot.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.core import config as jarvis_config
from jarvis.dictation import polish, polish_client
from jarvis.dictation.polish import (
    POLISH_BREAKER_COOLDOWN_S,
    POLISH_BREAKER_THRESHOLD,
    polish_transcript,
)
from jarvis.dictation.polish_client import (
    POLISH_FAMILIES,
    POLISH_TRANSPORTS,
    GeminiPolishClient,
    OpenAIChatPolishClient,
    PolishFamily,
    PolishProviderError,
    family_by_id,
    resolve_model,
    resolve_polish_chain,
)

GROQ: PolishFamily = POLISH_FAMILIES[0]
OPENAI: PolishFamily = next(f for f in POLISH_FAMILIES if f.id == "openai")
GEMINI: PolishFamily = next(f for f in POLISH_FAMILIES if f.transport == "gemini")

RAW = "so we should probably move the meeting to the morning and tell the team"
POLISHED = "So we should probably move the meeting to the morning, and tell the team."


@dataclass
class _Cfg:
    polish: bool = True
    polish_provider: str = "auto"
    polish_model: str = ""
    polish_timeout_ms: int = 1200
    polish_max_input_chars: int = 4000
    polish_min_words: int = 4


def _with_keys(monkeypatch: pytest.MonkeyPatch, slots: dict[str, str]) -> None:
    """Pretend this host holds exactly *slots* and nothing else."""

    def _fake_get_secret(key: str, env_fallback: str | None = None) -> str | None:
        return slots.get(key)

    monkeypatch.setattr(jarvis_config, "get_secret", _fake_get_secret)


def _ids(chain: tuple[PolishFamily, ...]) -> list[str]:
    return [family.id for family in chain]


# --------------------------------------------------------------------------- #
# The table itself
# --------------------------------------------------------------------------- #


def test_the_family_table_is_a_usable_single_source_of_truth() -> None:
    assert POLISH_FAMILIES
    ids = [family.id for family in POLISH_FAMILIES]
    assert len(set(ids)) == len(ids), ids
    for family in POLISH_FAMILIES:
        assert family.transport in POLISH_TRANSPORTS, family.id
        assert family.label.strip(), family.id
        assert family.default_model.strip(), family.id
        assert family.base_url.startswith("http"), family.id
        assert family.default_timeout_ms > 0, family.id
        assert family_by_id(family.id) is family


def test_exactly_one_family_is_keyless_and_it_is_the_local_one() -> None:
    """A keyless family is a LOCAL engine. If a cloud family ever loses its
    credential slots it would silently become auto-selectable and dial out
    unauthenticated on every dictation."""
    keyless = [family for family in POLISH_FAMILIES if not family.needs_key]
    assert len(keyless) == 1, [f.id for f in keyless]
    assert "localhost" in keyless[0].base_url or "127.0.0.1" in keyless[0].base_url


# --------------------------------------------------------------------------- #
# Key-aware resolution
# --------------------------------------------------------------------------- #


def test_no_key_anywhere_yields_an_empty_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The AP-23 gate. Empty means the caller reports ``unavailable`` and hands
    back the raw transcript — exactly today's behaviour."""
    _with_keys(monkeypatch, {})
    assert resolve_polish_chain(_Cfg()) == ()


def test_one_key_yields_exactly_that_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_keys(monkeypatch, {"groq_api_key": "gsk-test"})
    assert _ids(resolve_polish_chain(_Cfg())) == ["groq"]


def test_a_single_openrouter_key_is_enough_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One OpenRouter key reaches every upstream family, so a downloader whose
    only credential is that one still gets a working chain."""
    _with_keys(monkeypatch, {"openrouter_api_key": "or-test"})
    assert _ids(resolve_polish_chain(_Cfg())) == ["openrouter"]


def test_the_gemini_family_also_reads_the_ai_studio_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Gemini-only downloader saved their key from whichever card they opened
    first; requiring one specific slot would brick them for no reason."""
    _with_keys(monkeypatch, {"google_aistudio_api_key": "ai-test"})
    assert _ids(resolve_polish_chain(_Cfg())) == ["gemini"]


def test_several_keys_yield_the_canonical_order_one_entry_per_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_keys(
        monkeypatch,
        {"groq_api_key": "g", "openai_api_key": "o", "gemini_api_key": "m"},
    )
    chain = resolve_polish_chain(_Cfg())
    ids = _ids(chain)
    assert ids == ["groq", "gemini", "openai"]
    assert len(set(ids)) == len(ids)


def test_the_local_family_is_never_auto_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama needs no key, so a naive "families with a credential" rule would
    put it in EVERY chain — and a keyless install would then spend its whole
    latency budget on a connection refused to localhost."""
    _with_keys(monkeypatch, {})
    assert resolve_polish_chain(_Cfg()) == ()
    _with_keys(monkeypatch, {"groq_api_key": "g"})
    assert "ollama" not in _ids(resolve_polish_chain(_Cfg()))


# --------------------------------------------------------------------------- #
# The user pin
# --------------------------------------------------------------------------- #


def test_a_pin_moves_a_family_to_the_front_without_costing_the_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preference is a preference, not a single point of failure."""
    _with_keys(monkeypatch, {"groq_api_key": "g", "openai_api_key": "o"})
    chain = resolve_polish_chain(_Cfg(polish_provider="openai"))
    assert _ids(chain) == ["openai", "groq"]


def test_the_local_family_is_reachable_by_pinning_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_keys(monkeypatch, {"groq_api_key": "g"})
    chain = resolve_polish_chain(_Cfg(polish_provider="ollama"))
    assert _ids(chain) == ["ollama", "groq"]


def test_an_unknown_pin_falls_back_to_the_auto_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in a config file must not silently disable a feature the user
    explicitly asked for."""
    _with_keys(monkeypatch, {"groq_api_key": "g"})
    assert _ids(resolve_polish_chain(_Cfg(polish_provider="banana"))) == ["groq"]


def test_a_pin_to_a_family_with_no_key_degrades_to_what_the_user_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_keys(monkeypatch, {"groq_api_key": "g"})
    assert _ids(resolve_polish_chain(_Cfg(polish_provider="cerebras"))) == ["groq"]


def test_the_pinned_model_applies_to_the_primary_family_only() -> None:
    """A model id is family-specific. Carrying it across a fallback would turn a
    recoverable outage into a guaranteed 404."""
    cfg = _Cfg(polish_model="my-fast-model")
    assert resolve_model(GROQ, cfg, primary_id=GROQ.id) == "my-fast-model"
    assert resolve_model(GEMINI, cfg, primary_id=GROQ.id) == GEMINI.default_model
    assert resolve_model(GROQ, _Cfg(), primary_id=GROQ.id) == GROQ.default_model


# --------------------------------------------------------------------------- #
# The circuit breaker
# --------------------------------------------------------------------------- #


class _Clock:
    """A monotonic clock the test moves by hand instead of sleeping 120 s."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class _FakeClient:
    reply: str | None = None
    raises: BaseException | None = None
    delay_s: float = 0.0

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        temperature: float,
        timeout_s: float,
    ) -> str | None:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.raises is not None:
            raise self.raises
        return self.reply


@dataclass
class _FakeFactory:
    clients: list[_FakeClient | None]
    built: list[str] = field(default_factory=list)

    def __call__(self, family: PolishFamily, *, model: str) -> Any:
        index = len(self.built)
        self.built.append(family.id)
        return self.clients[index] if index < len(self.clients) else None


def _wire(
    monkeypatch: pytest.MonkeyPatch, clients: list[_FakeClient | None]
) -> _FakeFactory:
    factory = _FakeFactory(clients=clients)
    monkeypatch.setattr(polish, "resolve_polish_chain", lambda cfg: (GROQ,))
    monkeypatch.setattr(polish, "build_polish_client", factory)
    return factory


@pytest.fixture(autouse=True)
def _fresh_breaker() -> None:
    polish.reset_polish_state()


def test_the_breaker_settings_are_the_designed_ones() -> None:
    """Not config keys on purpose: this is a safety floor, and a user who could
    set the threshold to 99 would be configuring their own dictation to hang."""
    assert POLISH_BREAKER_THRESHOLD == 3
    assert POLISH_BREAKER_COOLDOWN_S == 120


async def test_three_consecutive_failures_stop_the_pass_dialling_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polish.reset_polish_state(now=_Clock())
    error = _FakeClient(raises=PolishProviderError("down", status=503))
    factory = _wire(monkeypatch, [error, error, error, _FakeClient(reply=POLISHED)])

    for _ in range(POLISH_BREAKER_THRESHOLD):
        outcome = await polish_transcript(RAW, language="en", cfg=_Cfg())
        assert outcome.status == "provider_error"
        assert outcome.text == RAW
    assert len(factory.built) == POLISH_BREAKER_THRESHOLD

    outcome = await polish_transcript(RAW, language="en", cfg=_Cfg())
    assert outcome.status == "unavailable"
    assert outcome.reason == "circuit_open"
    assert outcome.text == RAW
    # The whole point: no client was built, so nothing was dialled.
    assert len(factory.built) == POLISH_BREAKER_THRESHOLD


async def test_the_breaker_lets_the_next_call_through_after_the_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    polish.reset_polish_state(now=clock)
    error = _FakeClient(raises=PolishProviderError("down", status=503))
    factory = _wire(monkeypatch, [error, error, error, _FakeClient(reply=POLISHED)])

    for _ in range(POLISH_BREAKER_THRESHOLD):
        await polish_transcript(RAW, language="en", cfg=_Cfg())
    assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == (
        "unavailable"
    )

    clock.now = POLISH_BREAKER_COOLDOWN_S + 1
    outcome = await polish_transcript(RAW, language="en", cfg=_Cfg())

    assert outcome.status == "applied"
    assert outcome.text == POLISHED
    assert len(factory.built) == POLISH_BREAKER_THRESHOLD + 1


async def test_a_timeout_counts_towards_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that hangs is as dead as one that 500s, and costs more."""
    polish.reset_polish_state(now=_Clock())
    slow = _FakeClient(reply=POLISHED, delay_s=5.0)
    factory = _wire(monkeypatch, [slow, slow, slow, slow])

    for _ in range(POLISH_BREAKER_THRESHOLD):
        outcome = await polish_transcript(
            RAW, language="en", cfg=_Cfg(), timeout_s=0.02
        )
        assert outcome.status == "timeout"

    outcome = await polish_transcript(RAW, language="en", cfg=_Cfg(), timeout_s=0.02)
    assert outcome.status == "unavailable"
    assert outcome.reason == "circuit_open"
    assert len(factory.built) == POLISH_BREAKER_THRESHOLD


async def test_a_success_resets_the_failure_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Three CONSECUTIVE failures" — an intermittent provider must not
    accumulate its way to an open breaker over an afternoon."""
    polish.reset_polish_state(now=_Clock())
    error = _FakeClient(raises=PolishProviderError("blip", status=500))
    good = _FakeClient(reply=POLISHED)
    _wire(monkeypatch, [error, error, good, error, error, good])

    assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == (
        "provider_error"
    )
    assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == (
        "provider_error"
    )
    assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == "applied"
    assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == (
        "provider_error"
    )
    assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == (
        "provider_error"
    )
    # Still closed after 2 + 2 non-consecutive failures.
    assert (await polish_transcript(RAW, language="en", cfg=_Cfg())).status == "applied"


async def test_the_pass_never_touches_a_provider_when_it_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _wire(monkeypatch, [_FakeClient(reply=POLISHED)])

    outcome = await polish_transcript(RAW, language="en", cfg=_Cfg(polish=False))

    assert outcome.status == "off"
    assert outcome.text == RAW
    assert factory.built == []


async def test_aclose_is_safe_on_a_host_that_never_polished_anything() -> None:
    """Teardown runs on every shutdown, including the headless ones where the
    HTTP client was never built."""
    await polish.aclose()
    await polish.aclose()


# --------------------------------------------------------------------------- #
# The two transports — driven through a fake transport, never over the wire
# --------------------------------------------------------------------------- #


class _StubPool:
    """Stands in for the shared keep-alive pool with a caller-supplied client."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self) -> Any:
        return self._client

    async def aclose(self) -> None:
        await self._client.aclose()


def _mock_http(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    import httpx

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(polish_client, "_HTTP", _StubPool(client))


async def test_the_openai_chat_transport_returns_the_message_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": POLISHED}}]}
        )

    _mock_http(monkeypatch, handler)
    client = OpenAIChatPolishClient(GROQ, model="m", api_key="test-key")

    text = await client.complete(
        "system", "user", max_output_tokens=1200, temperature=0.0, timeout_s=1.0
    )

    assert text == POLISHED
    assert seen[0]["model"] == "m"
    assert [m["role"] for m in seen[0]["messages"]] == ["system", "user"]
    assert seen[0]["max_tokens"] == 1200


async def test_openai_compatible_transport_requests_low_reasoning_cross_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rewrite intent is a transport option, never a family/model allowlist."""
    import httpx

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": POLISHED}}]}
        )

    _mock_http(monkeypatch, handler)
    client = OpenAIChatPolishClient(OPENAI, model="arbitrary-future-model", api_key="k")

    text = await client.complete(
        "system", "user", max_output_tokens=256, temperature=0.0, timeout_s=1.0
    )

    assert text == POLISHED
    assert seen[0]["max_tokens"] == 256
    assert seen[0]["reasoning_effort"] == "low"


async def test_low_reasoning_intent_is_stripped_after_explicit_400_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incompatible endpoint gets one honest retry without the option."""
    import httpx

    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(
                400, text="Unsupported parameter: 'reasoning_effort'."
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": POLISHED}}]}
        )

    _mock_http(monkeypatch, handler)
    client = OpenAIChatPolishClient(GROQ, model="any-model", api_key="k")

    text = await client.complete(
        "system", "user", max_output_tokens=256, temperature=0.0, timeout_s=1.0
    )

    assert text == POLISHED
    assert len(bodies) == 2
    assert bodies[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in bodies[1]


async def test_a_rate_limited_transport_reports_its_status_and_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller crosses families on this; the status is what makes a log line
    say "depleted account" rather than "something went wrong"."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited", headers={"retry-after": "7"})

    _mock_http(monkeypatch, handler)
    client = OpenAIChatPolishClient(GROQ, model="m", api_key="k")

    with pytest.raises(PolishProviderError) as excinfo:
        await client.complete(
            "system", "user", max_output_tokens=64, temperature=0.0, timeout_s=1.0
        )

    assert excinfo.value.status == 429
    assert excinfo.value.retry_after == 7.0


async def test_a_schema_rejection_is_retried_once_with_the_renamed_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Newer OpenAI-schema endpoints renamed ``max_tokens``. The retry is driven
    by what the SERVER said, not by a model-id allowlist that would rot with
    every release (AP-21)."""
    import httpx

    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        bodies.append(body)
        if "max_tokens" in body:
            return httpx.Response(
                400,
                text="Unsupported parameter: use 'max_completion_tokens' instead.",
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": POLISHED}}]}
        )

    _mock_http(monkeypatch, handler)
    client = OpenAIChatPolishClient(GROQ, model="m", api_key="k")

    text = await client.complete(
        "system", "user", max_output_tokens=512, temperature=0.0, timeout_s=1.0
    )

    assert text == POLISHED
    assert len(bodies) == 2
    assert bodies[1]["max_completion_tokens"] == 512
    assert "max_tokens" not in bodies[1]


async def test_a_schema_rejection_is_only_ever_retried_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that keeps saying no must not become an unbounded retry loop
    inside a 1200 ms budget."""
    import httpx

    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, text="Unsupported parameter: max_completion_tokens")

    _mock_http(monkeypatch, handler)
    client = OpenAIChatPolishClient(GROQ, model="m", api_key="k")

    with pytest.raises(PolishProviderError):
        await client.complete(
            "system", "user", max_output_tokens=512, temperature=0.0, timeout_s=1.0
        )
    assert len(attempts) == 2


async def test_a_transport_level_failure_becomes_a_polish_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable host has no status — the caller must still be able to
    cross to the next family instead of seeing a raw httpx exception."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _mock_http(monkeypatch, handler)
    client = OpenAIChatPolishClient(GROQ, model="m", api_key="k")

    with pytest.raises(PolishProviderError) as excinfo:
        await client.complete(
            "system", "user", max_output_tokens=64, temperature=0.0, timeout_s=1.0
        )
    assert excinfo.value.status is None


async def test_the_gemini_transport_returns_the_response_text() -> None:
    client = GeminiPolishClient(GEMINI, model="m", api_key="k")
    client._client = _FakeGenaiClient(response=_FakeGenaiResponse(text=POLISHED))

    text = await client.complete(
        "system", "user", max_output_tokens=1200, temperature=0.0, timeout_s=1.0
    )

    assert text == POLISHED


async def test_a_gemini_sdk_failure_becomes_a_polish_provider_error() -> None:
    """The SDK raises its own hierarchy; the chain only understands one type."""
    client = GeminiPolishClient(GEMINI, model="m", api_key="k")
    client._client = _FakeGenaiClient(error=RuntimeError("quota exhausted"))

    with pytest.raises(PolishProviderError):
        await client.complete(
            "system", "user", max_output_tokens=64, temperature=0.0, timeout_s=1.0
        )


def test_the_gemini_request_deadline_never_goes_under_the_server_minimum(
    monkeypatch,
) -> None:
    """The polish budget must not become the REQUEST deadline.

    generate-content rejects anything under 10 s outright — ``400
    INVALID_ARGUMENT: Manually set deadline 2s is too short`` — before the model
    runs. This family's budget is 1.5 s, so sending it as the deadline made every
    call fail: measured on the live log, 56 failures and 0 successes, i.e. a user
    who pinned Gemini never once got it and every dictation crossed silently to
    another family (AP-31). The caller's ``wait_for`` still bounds the wait.
    """
    from jarvis.dictation import polish_client as pc

    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules, "google", types.SimpleNamespace(genai=types.SimpleNamespace(Client=_Client))
    )
    monkeypatch.setitem(
        sys.modules, "google.genai", types.SimpleNamespace(Client=_Client)
    )

    client = GeminiPolishClient(GEMINI, model="m", api_key="k")
    client._ensure_client(1.5)

    timeout_ms = captured["http_options"]["timeout"]  # type: ignore[index]
    assert timeout_ms >= pc._GEMINI_MIN_DEADLINE_S * 1000, (
        f"a {timeout_ms} ms deadline is rejected by the server before the "
        "model runs, so the pinned provider can never answer"
    )


def test_a_generous_caller_budget_is_still_honoured(monkeypatch) -> None:
    """The floor raises a too-short deadline; it does not cap a long one."""
    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules, "google", types.SimpleNamespace(genai=types.SimpleNamespace(Client=_Client))
    )
    monkeypatch.setitem(
        sys.modules, "google.genai", types.SimpleNamespace(Client=_Client)
    )

    client = GeminiPolishClient(GEMINI, model="m", api_key="k")
    client._ensure_client(30.0)

    assert captured["http_options"]["timeout"] == 30_000  # type: ignore[index]


@dataclass
class _FakeGenaiResponse:
    text: str


class _FakeGenaiModels:
    def __init__(
        self, *, response: _FakeGenaiResponse | None, error: BaseException | None
    ) -> None:
        self._response = response
        self._error = error

    async def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        if self._error is not None:
            raise self._error
        return self._response


class _FakeGenaiAio:
    def __init__(self, models: _FakeGenaiModels) -> None:
        self.models = models


class _FakeGenaiClient:
    """The three attribute hops the adapter walks: ``client.aio.models``."""

    def __init__(
        self,
        *,
        response: _FakeGenaiResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.aio = _FakeGenaiAio(_FakeGenaiModels(response=response, error=error))
