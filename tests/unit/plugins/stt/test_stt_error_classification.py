"""A failing cloud STT must be CLASSIFIABLE, and a hanging one must be BOUNDED.

The retry ladder in ``jarvis.speech.pipeline`` decides "retry this / give up on
this" from the HTTP status behind a failed transcription, and how long to wait
from the server's ``Retry-After``. Before ``jarvis.plugins.stt.errors`` existed
it could only read those off an ``httpx.HTTPStatusError`` — which exactly ONE of
the four cloud plugins raised. For the other three the ladder was dead code: the
first 429 ended the turn, silently, for every downloader whose key is not a Groq
key, while those plugins' docstrings promised they "degrade honestly (AP-22)".
That is AP-23 — only the maintainer's provider actually worked.

These tests pin the contract from BOTH ends: each plugin raises the typed error
with the right status and delay, and the pipeline's own classifier agrees. The
second half is what makes this a guard rather than a restatement — a plugin that
raises a beautifully typed error the consumer cannot read is the bug we had.

The last section covers the failure that produces no error at all: Gemini STT
had NO timeout at any layer. Its ``timeout_s`` was assigned and read by nothing,
google-genai forces ``timeout=None`` onto its own HTTP client unless told
otherwise, and the call runs in an uncancellable thread — so a Gemini-only user
could hang the dictation lane forever with the microphone already closed.

No ``unittest.mock``: the HTTP plugins get an ``httpx.MockTransport``, Gemini
gets a hand-rolled fake client, exactly like the existing plugin tests.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from jarvis.plugins.stt.errors import (
    STTHTTPError,
    http_error_from_response,
    parse_retry_after,
    status_from_exception,
)
from jarvis.plugins.stt.gemini_api import GeminiSTT
from jarvis.plugins.stt.groq_api import GroqWhisperAPI
from jarvis.plugins.stt.openai_api import OpenAIWhisperAPI
from jarvis.plugins.stt.openrouter_stt import OpenRouterSTT
from jarvis.speech.pipeline import (
    _is_transient_stt_error,
    _stt_error_status,
    _stt_retry_delay,
)


def _fake_pcm(seconds: float = 0.2, sample_rate: int = 16_000) -> bytes:
    return b"\x00\x00" * int(sample_rate * seconds)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _responder(status: int, headers: dict[str, str] | None = None):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, json={"error": {"message": "slow down"}}, headers=headers or {}
        )

    return handler


# ---------------------------------------------------------------------------
# Fake google-genai error + client (the SDK is optional, so it is duck-typed)
# ---------------------------------------------------------------------------


class _FakeGenaiAPIError(Exception):
    """The shape google-genai's ``APIError`` presents: an int ``code``.

    Hand-rolled on purpose. The SDK is absent on a base install, so the plugin
    duck-types it and the test must too — importing the real class here would
    make this test pass for a reason the shipped code never relies on.
    """

    def __init__(self, code: int, message: str, headers: dict[str, str] | None = None):
        super().__init__(f"{code} {message}")
        self.code = code
        self.response = type("_Resp", (), {"headers": headers or {}})()


class _RaisingModels:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def generate_content(self, **_kwargs: Any):  # noqa: ANN204 — fake SDK surface
        raise self._exc


class _RaisingGenaiClient:
    def __init__(self, exc: BaseException) -> None:
        self.models = _RaisingModels(exc)


# ---------------------------------------------------------------------------
# The four plugins: a 429 with Retry-After is transient and carries the delay
# ---------------------------------------------------------------------------


def _raise_from_openai(status: int, headers: dict[str, str] | None = None) -> BaseException:
    provider = OpenAIWhisperAPI(
        api_key="k", http_client=_mock_client(_responder(status, headers))
    )
    with pytest.raises(Exception) as excinfo:  # noqa: PT011 — type is the assertion
        asyncio.run(provider.transcribe_pcm(_fake_pcm()))
    return excinfo.value


def _raise_from_openrouter(status: int, headers: dict[str, str] | None = None) -> BaseException:
    provider = OpenRouterSTT(
        api_key="k", http_client=_mock_client(_responder(status, headers))
    )
    with pytest.raises(Exception) as excinfo:  # noqa: PT011 — type is the assertion
        asyncio.run(provider.transcribe_pcm(_fake_pcm()))
    return excinfo.value


def _raise_from_groq(status: int, headers: dict[str, str] | None = None) -> BaseException:
    provider = GroqWhisperAPI(
        api_key="k", http_client=_mock_client(_responder(status, headers))
    )
    with pytest.raises(Exception) as excinfo:  # noqa: PT011 — type is the assertion
        asyncio.run(provider.transcribe_pcm(_fake_pcm()))
    return excinfo.value


def _raise_from_gemini(status: int, headers: dict[str, str] | None = None) -> BaseException:
    provider = GeminiSTT(
        client=_RaisingGenaiClient(_FakeGenaiAPIError(status, "RESOURCE_EXHAUSTED", headers))
    )
    with pytest.raises(Exception) as excinfo:  # noqa: PT011 — type is the assertion
        asyncio.run(provider.transcribe_pcm(_fake_pcm()))
    return excinfo.value


#: Every cloud STT plugin, by provider id. The behaviour these share is a
#: CAPABILITY — "the pipeline can read a status off my failure" — not a type
#: (AP-21). Groq reaches it through ``httpx.HTTPStatusError`` because that
#: plugin may not import ``jarvis.*`` at all, not even lazily (CLAUDE.md §5,
#: pinned by tests/contract/test_stt_protocol.py); the other three reach it
#: through the shared ``STTHTTPError``. Both satisfy the consumer, which is the
#: only thing that decides whether a user's turn survives a 429.
_RAISERS = {
    "openai-api": _raise_from_openai,
    "openrouter-stt": _raise_from_openrouter,
    "groq-api": _raise_from_groq,
    "gemini-api": _raise_from_gemini,
}

#: The subset that raises the shared typed error (see above for Groq's exemption).
_TYPED = ("openai-api", "openrouter-stt", "gemini-api")


@pytest.mark.parametrize("provider_id", sorted(_RAISERS))
def test_a_429_is_classifiable_and_carries_the_retry_after(provider_id: str) -> None:
    """The whole point of F1: all four, not just the one with httpx's error."""
    exc = _RAISERS[provider_id](429, {"Retry-After": "7"})

    assert _stt_error_status(exc) == 429
    assert _is_transient_stt_error(exc) is True
    assert _stt_retry_delay(exc, 0) == pytest.approx(2.0), (
        "the pipeline must honour the server's delay (capped at its own ceiling), "
        "not fall through to its 0.4 s first-attempt guess"
    )


@pytest.mark.parametrize("provider_id", sorted(_RAISERS))
def test_a_401_is_not_transient_and_is_never_retried(provider_id: str) -> None:
    """A dead key is not a blip. Retrying it only hammers the provider."""
    exc = _RAISERS[provider_id](401)

    assert _stt_error_status(exc) == 401
    assert _is_transient_stt_error(exc) is False


@pytest.mark.parametrize("provider_id", sorted(_TYPED))
def test_the_typed_error_exposes_status_and_retry_after(provider_id: str) -> None:
    """The machine-readable facts, for a caller that wants them directly."""
    throttled = _RAISERS[provider_id](429, {"Retry-After": "7"})
    dead_key = _RAISERS[provider_id](401)

    assert isinstance(throttled, STTHTTPError)
    assert throttled.status == 429
    assert throttled.retry_after == 7.0
    assert isinstance(dead_key, STTHTTPError)
    assert dead_key.status == 401
    assert dead_key.retry_after is None


@pytest.mark.parametrize("provider_id", sorted(_TYPED))
def test_the_typed_error_is_still_a_runtime_error(provider_id: str) -> None:
    """Existing ``except RuntimeError`` degradation paths must be untouched.

    The STT factory and the pipeline both treat a RuntimeError as "degrade to
    the local floor / apologise honestly". If the new type escaped those
    handlers, F1 would have traded a dead retry ladder for a crash.
    """
    exc = _RAISERS[provider_id](500)
    assert isinstance(exc, RuntimeError)
    assert _is_transient_stt_error(exc) is True


def test_groq_keeps_raising_the_httpx_error_it_always_raised() -> None:
    """Groq's exemption is intentional — pin it so nobody "unifies" it.

    That plugin is under a total ``jarvis.*``-import ban (it re-implements even
    the keyring read for that reason), so it cannot reach the shared error type.
    It never needed it: its failure was already the one the pipeline could read,
    which is exactly why the retry ladder worked here and nowhere else.
    """
    exc = _raise_from_groq(429, {"Retry-After": "7"})

    assert isinstance(exc, httpx.HTTPStatusError)
    assert _stt_error_status(exc) == 429
    assert _stt_retry_delay(exc, 0) == pytest.approx(2.0)


@pytest.mark.parametrize(
    "provider_id,needle",
    [
        ("openai-api", "OpenAI STT failed: OpenAI rate limit / quota exceeded"),
        ("openrouter-stt", "OpenRouter STT failed: OpenRouter rate limit / quota exceeded"),
    ],
)
def test_the_english_message_is_unchanged(provider_id: str, needle: str) -> None:
    """The human-readable reason is what a user sees in a log; only the TYPE moved."""
    exc = _RAISERS[provider_id](429)
    assert needle in str(exc)
    assert "slow down" in str(exc), "the provider's own detail must survive"


def test_gemini_keeps_its_own_message_and_a_status_less_failure_stays_plain() -> None:
    """Gemini has no HTTP wire here — a transport error must NOT invent a status."""
    provider = GeminiSTT(client=_RaisingGenaiClient(TimeoutError("read timed out")))
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(provider.transcribe_pcm(_fake_pcm()))

    assert not isinstance(excinfo.value, STTHTTPError), (
        "a timeout has no HTTP status; claiming one would be a lie"
    )
    assert "Gemini STT request failed" in str(excinfo.value)
    assert _stt_error_status(excinfo.value) is None


# ---------------------------------------------------------------------------
# Retry-After parsing — both RFC 9110 forms
# ---------------------------------------------------------------------------


def test_retry_after_accepts_the_delta_seconds_form() -> None:
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after(" 2.5 ") == 2.5


def test_retry_after_accepts_the_http_date_form() -> None:
    """A gateway in front of the provider answers with a date, not a delta."""
    now = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    header = format_datetime(now + timedelta(seconds=45), usegmt=True)

    delay = parse_retry_after(header, now=now.timestamp())

    assert delay == pytest.approx(45.0, abs=1.0)


def test_retry_after_never_returns_a_negative_or_raises() -> None:
    """A past date, a clock skew, or garbage must degrade — never blow up."""
    now = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    past = format_datetime(now - timedelta(seconds=60), usegmt=True)

    assert parse_retry_after(past, now=now.timestamp()) == 0.0
    assert parse_retry_after("in a little while") is None
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None


def test_retry_after_header_lookup_is_case_insensitive() -> None:
    """Providers send ``Retry-After``; the classifier looks for ``retry-after``."""
    exc = _raise_from_openai(429, {"RETRY-AFTER": "3"})
    assert exc.retry_after == 3.0
    assert exc.response.headers.get("retry-after") == "3"


# ---------------------------------------------------------------------------
# The primitives themselves
# ---------------------------------------------------------------------------


def test_status_from_exception_reads_all_three_shapes() -> None:
    typed = STTHTTPError("x", status=503)
    sdk_shaped = _FakeGenaiAPIError(429, "quota")
    httpx_shaped = httpx.HTTPStatusError(
        "boom",
        request=httpx.Request("POST", "https://example.invalid"),
        response=httpx.Response(502),
    )

    assert status_from_exception(typed) == 503
    assert status_from_exception(sdk_shaped) == 429
    assert status_from_exception(httpx_shaped) == 502
    assert status_from_exception(ValueError("nothing http here")) is None
    assert status_from_exception(None) is None


def test_status_from_exception_ignores_a_non_status_integer() -> None:
    """``.code`` is not always an HTTP status — a subprocess exit code is not one."""

    class _ProcessError(Exception):
        code = 1

    assert status_from_exception(_ProcessError()) is None


def test_http_error_from_response_falls_back_to_the_raw_body() -> None:
    """An overloaded gateway answers HTML; the only clue must still reach the log."""
    response = httpx.Response(503, text="<html>upstream is down</html>")

    exc = http_error_from_response(response, vendor="OpenRouter")

    assert exc.status == 503
    assert "OpenRouter STT HTTP 503" in str(exc)
    assert "upstream is down" in str(exc)


def test_the_error_holds_no_reference_to_the_response_object() -> None:
    """The headers are copied, so a stored exception cannot pin a live response."""
    response = httpx.Response(429, json={}, headers={"Retry-After": "1"})

    exc = http_error_from_response(response, vendor="OpenAI")

    assert exc.response is not response
    assert exc.response.status_code == 429
    assert exc.response.headers.get("retry-after") == "1"


# ---------------------------------------------------------------------------
# The Gemini timeout — the failure that raises nothing at all
# ---------------------------------------------------------------------------


def test_gemini_timeout_is_expressed_in_milliseconds() -> None:
    """``HttpOptions.timeout`` is milliseconds, not seconds.

    Off by 1000x in the safe direction it is a 30-second wait that looks fine
    until a stall; in the other direction it times out every healthy request.
    Neither would be caught by a test that only asserted "a timeout is set".
    """
    assert GeminiSTT(timeout_s=2.5)._http_options() == {"timeout": 2500}
    assert GeminiSTT(timeout_s=30.0)._http_options() == {"timeout": 30_000}


def test_gemini_timeout_has_a_floor_so_a_bad_value_cannot_brick_it() -> None:
    """0 or a negative value must bound the call, not reject every request."""
    assert GeminiSTT(timeout_s=0)._http_options() == {"timeout": 1000}
    assert GeminiSTT(timeout_s=-5)._http_options() == {"timeout": 1000}


def test_gemini_client_is_built_with_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring itself: without this the value is computed and thrown away."""
    genai = pytest.importorskip("google.genai")
    captured: dict[str, Any] = {}

    class _RecordingClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(genai, "Client", _RecordingClient)

    GeminiSTT(api_key="k", timeout_s=4.0)._ensure_client()

    assert captured["api_key"] == "k"
    assert captured["http_options"] == {"timeout": 4000}


def test_the_factory_forwards_a_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A timeout in the config has to reach the provider constructor.

    Nothing else can bound a Gemini call: the pipeline's ``wait_for`` stops
    WAITING for the worker thread, it cannot stop the request inside it.
    """
    import jarvis.plugins.stt as stt_pkg
    from jarvis.core import config as cfg

    built: dict[str, Any] = {}

    class _Recording:
        def __init__(self, **kwargs: Any) -> None:
            built.update(kwargs)

    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda _name: _Recording)
    monkeypatch.setattr(cfg, "get_secret_any", lambda _candidates: "real-key")
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: cfg.ResolvedEndpoint(
            base_url=None, credential=None, via_proxy=False
        ),
    )

    class _CfgWithTimeout:
        provider = "gemini-api"
        language = "auto"
        bias_prompt = ""
        timeout_s = 8.0

    stt_pkg.build_stt_from_config(_CfgWithTimeout())
    assert built["timeout_s"] == 8.0

    built.clear()

    class _CfgWithout:
        provider = "gemini-api"
        language = "auto"
        bias_prompt = ""

    stt_pkg.build_stt_from_config(_CfgWithout())
    assert "timeout_s" not in built, (
        "with nothing configured the provider must keep its own documented "
        "default rather than being handed a value the user never chose"
    )


def test_a_provider_that_refuses_the_optional_kwargs_still_builds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A third-party plugin predating ``timeout_s`` must not fall to local whisper.

    The retry used to drop only ``prompt``; a plugin that refuses ``timeout_s``
    would have failed the retry too and dead-ended on an engine the base install
    does not ship.
    """
    import jarvis.plugins.stt as stt_pkg
    from jarvis.core import config as cfg

    accepted: dict[str, Any] = {}

    class _OldPlugin:
        def __init__(self, *, language: str | None = None) -> None:
            accepted["language"] = language

    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda _name: _OldPlugin)
    monkeypatch.setattr(cfg, "get_secret_any", lambda _candidates: "real-key")
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: cfg.ResolvedEndpoint(
            base_url=None, credential=None, via_proxy=False
        ),
    )

    class _Cfg:
        provider = "gemini-api"
        language = "de"
        bias_prompt = "Jarvis, Ruben"
        timeout_s = 8.0

    provider = stt_pkg.build_stt_from_config(_Cfg())

    assert isinstance(provider, _OldPlugin)
    assert accepted["language"] == "de"
