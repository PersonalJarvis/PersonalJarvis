"""Tests for ``run_provider_test`` — the per-tier dispatch + outcome service.

Unit tests inject fakes for the network-touching seams (brain probe, tts/stt
builders, codex status) so they never hit a live provider; the real wiring is
exercised by the live probe behind the endpoint. What we verify here is the
DISPATCH + CLASSIFICATION glue: presence short-circuit, tier routing, and the
mapping from a probe result / raised error to an honest status.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.brain.healthcheck import HealthResult
from jarvis.brain.provider_test import ProviderTestResult, run_provider_test
from jarvis.ui.web.provider_spec import get_spec


def _cfg() -> SimpleNamespace:
    """Minimal cfg shape: brain.providers[id].model, tts.*, stt.*."""
    return SimpleNamespace(
        brain=SimpleNamespace(providers={"gemini": SimpleNamespace(model="gemini-3.5-flash")}),
        tts=SimpleNamespace(provider="gemini-flash-tts", model="x"),
        stt=SimpleNamespace(provider="groq-api", model="x", language="auto", bias_prompt=""),
    )


def _run(coro):
    return asyncio.run(coro)


def test_not_configured_short_circuits_without_calling() -> None:
    called = False

    async def probe(_p, _m):  # pragma: no cover - must NOT run
        nonlocal called
        called = True
        return HealthResult(provider="gemini", model="m", ok=True)

    res = _run(
        run_provider_test(
            get_spec("gemini"), _cfg(), present=False, brain_probe=probe,
        )
    )
    assert isinstance(res, ProviderTestResult)
    assert res.status == "not_configured"
    assert called is False


def test_brain_probe_ok_is_ok() -> None:
    async def probe(_p, _m):
        return HealthResult(provider="gemini", model="m", ok=True, duration_ms=1234.0)

    res = _run(run_provider_test(get_spec("gemini"), _cfg(), present=True, brain_probe=probe))
    assert res.status == "ok"
    assert res.latency_ms == pytest.approx(1234.0)


def test_brain_probe_bad_key_is_bad_key() -> None:
    async def probe(_p, _m):
        return HealthResult(
            provider="claude-api", model="m", ok=False,
            error="AuthenticationError: Error code: 401 - invalid x-api-key",
        )

    res = _run(run_provider_test(get_spec("claude-api"), _cfg(), present=True, brain_probe=probe))
    assert res.status == "bad_key"


def test_local_provider_none_auth_is_ok_without_network() -> None:
    # A local STT provider with auth_mode "none" needs no credential and no
    # network: a successful local build IS the "ok" signal. The former
    # faster-whisper spec was removed in v1.0.1, so synthesize the spec shape
    # here rather than resolve a live registry entry (which now returns None).
    spec = SimpleNamespace(id="local-stt", auth_mode="none", tier="stt")
    res = _run(
        run_provider_test(
            spec, _cfg(), present=True,
            make_stt=lambda _cfg, _prov: SimpleNamespace(name="fw"),
        )
    )
    assert res.status == "ok"


def test_codex_connected_is_ok() -> None:
    res = _run(
        run_provider_test(
            get_spec("codex"), _cfg(), present=True,
            codex_status=lambda: SimpleNamespace(installed=True, connected=True,
                                                 message="Connected via ChatGPT"),
        )
    )
    assert res.status == "ok"


def test_codex_not_connected_is_not_configured() -> None:
    res = _run(
        run_provider_test(
            get_spec("codex"), _cfg(), present=True,
            codex_status=lambda: SimpleNamespace(installed=True, connected=False,
                                                 message="Not connected"),
        )
    )
    assert res.status == "not_configured"


def test_antigravity_connected_is_ok_without_billed_call() -> None:
    # antigravity is OAuth-only: a connected Google login IS a working brain.
    # The real agy/gemini turn (~8s, bills the subscription) must NOT run on Test.
    called = False

    async def probe(_p, _m):  # pragma: no cover - must NOT run
        nonlocal called
        called = True
        return HealthResult(provider="antigravity", model="m", ok=True)

    res = _run(
        run_provider_test(
            get_spec("antigravity"), _cfg(), present=True, brain_probe=probe,
            antigravity_status=lambda: SimpleNamespace(
                installed=True, connected=True, message="Connected via Google subscription"
            ),
        )
    )
    assert res.status == "ok"
    assert called is False  # no slow/billed agy turn on a button click


def test_antigravity_not_connected_is_not_configured() -> None:
    res = _run(
        run_provider_test(
            get_spec("antigravity"), _cfg(), present=True,
            antigravity_status=lambda: SimpleNamespace(
                installed=True, connected=False, message="Not connected"
            ),
        )
    )
    assert res.status == "not_configured"


def test_tts_synthesis_credits_error_is_no_credits() -> None:
    class _Boom:
        async def synthesize(self, _text, voice=None):
            raise RuntimeError(
                "PermissionDeniedError: Error code: 403 - used all available credits "
                "or reached its monthly spending limit"
            )
            yield  # pragma: no cover - make it an async generator

    res = _run(
        run_provider_test(
            get_spec("grok-voice"), _cfg(), present=True,
            make_tts=lambda _cfg, _prov: _Boom(),
        )
    )
    assert res.status == "no_credits"


def test_tts_synthesis_bytes_is_ok() -> None:
    class _Voice:
        async def synthesize(self, _text, voice=None):
            yield SimpleNamespace(pcm=b"\x00\x01" * 100)

    res = _run(
        run_provider_test(
            get_spec("cartesia"), _cfg(), present=True,
            make_tts=lambda _cfg, _prov: _Voice(),
        )
    )
    assert res.status == "ok"


def test_realtime_probes_same_family_brain_not_stt() -> None:
    """A realtime card's test probes the text brain sharing its key slot.

    The realtime tier used to fall through to the STT branch, building an
    unrelated STT provider — the "Testing…" spinner then hung or lied. The fix
    routes it to a 1-token probe of the same-credential-family brain (resolved
    by shared secret slots, never a provider-name pin).
    """
    probed: list[tuple[str, str]] = []

    async def probe(p, m):
        probed.append((p, m))
        return HealthResult(provider=p, model=m, ok=True, duration_ms=42.0)

    res = _run(
        run_provider_test(
            get_spec("openai-realtime"), _cfg(), present=True, brain_probe=probe,
        )
    )
    assert res.status == "ok"
    # openai-realtime shares openai_api_key with the "openai" brain provider.
    assert probed and probed[0][0] == "openai"
    assert "openai" in res.detail


def test_realtime_bad_key_classified_from_family_probe() -> None:
    async def probe(_p, _m):
        return HealthResult(
            provider="gemini", model="m", ok=False,
            error="ClientError: Error code: 401 - API key not valid",
        )

    res = _run(
        run_provider_test(
            get_spec("gemini-live"), _cfg(), present=True, brain_probe=probe,
        )
    )
    assert res.status == "bad_key"
