"""Unit tests for GeminiFlashTTS sibling-bridge + cooldown bookkeeping.

The bridge was added 2026-05-14 after live diagnosis showed that
``gemini-3.1-flash-tts-preview`` is hard-capped at 100 requests/day on
every Google AI Studio project — including Pay-as-you-go ones — while
``gemini-2.5-flash-preview-tts`` serves fine on the same key.
See docs/diagnostics/voice-overlap-2026-05-14.md context + commit
82d03e2b for the implementation.

Tests:
    test_parse_retry_delay_reads_googles_17270s
    test_parse_quota_cap_reads_100
    test_synthesize_uses_primary_when_quota_open
    test_synthesize_switches_to_sibling_on_429
    test_synthesize_skips_primary_when_cooldown_active
    test_synthesize_returns_silence_when_sibling_disabled
    test_synthesize_returns_silence_when_both_quota_exhausted
"""
from __future__ import annotations

import asyncio
import time

import pytest

from jarvis.plugins.tts.gemini_flash_tts import (
    GeminiFlashTTS,
    _parse_quota_cap,
    _parse_retry_delay,
    _QUOTA_COOLDOWN_S,
)


# Verbatim 429 message captured live on 2026-05-14 21:13 (gemini-3.1-flash-tts).
_GOOGLE_429_MESSAGE = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
    "'You exceeded your current quota, please check your plan and billing "
    "details. * Quota exceeded for metric: "
    "generativelanguage.googleapis.com/generate_requests_per_model_per_day, "
    "limit: 100, model: gemini-3.1-flash-tts', 'status': 'RESOURCE_EXHAUSTED', "
    "'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', "
    "'violations': [{'quotaValue': '100'}]}, "
    "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
    "'retryDelay': '17270s'}]}}"
)


# --- pure helper tests -------------------------------------------------------

def test_parse_retry_delay_reads_googles_17270s() -> None:
    """Real 429 message → 17270 seconds (= ~4h47m)."""
    assert _parse_retry_delay(_GOOGLE_429_MESSAGE) == 17270.0


def test_parse_retry_delay_falls_back_to_default_when_absent() -> None:
    """No retryDelay in message → defaults to _QUOTA_COOLDOWN_S (1 h)."""
    assert _parse_retry_delay("some other error") == _QUOTA_COOLDOWN_S


def test_parse_quota_cap_reads_100() -> None:
    """Verify quotaValue extraction for the log line."""
    assert _parse_quota_cap(_GOOGLE_429_MESSAGE) == "100"


def test_parse_quota_cap_returns_none_when_absent() -> None:
    assert _parse_quota_cap("no quota mentioned") is None


# --- bridge behaviour: built on a fake _synthesize_sync ---------------------

class _Fake429:
    """Raised in place of google.genai.errors.ClientError. The bridge only
    inspects ``str(exc)``, not the class, so a string-stub is enough.
    """
    def __init__(self, msg: str = _GOOGLE_429_MESSAGE) -> None:
        self.msg = msg

    def __str__(self) -> str:  # pragma: no cover - format helper
        return self.msg


def _new_tts(**overrides) -> GeminiFlashTTS:
    """Build a TTS instance bypassing the real client construction.

    The unit tests never call ``synthesize`` (which would touch the network);
    they exercise ``_synthesize_one`` directly. ``_client`` is set to a
    sentinel so ``_synthesize_sync``'s ``assert self._client is not None``
    would pass — but in practice we override ``_synthesize_sync`` itself.
    """
    tts = GeminiFlashTTS(**overrides)
    tts._client = object()  # truthy sentinel
    return tts


@pytest.mark.asyncio
async def test_synthesize_uses_primary_when_quota_open() -> None:
    """Quota timer not yet armed → first attempt goes to primary, success."""
    tts = _new_tts()
    calls: list[str] = []

    def fake_sync(text: str, voice: str, model: str | None = None, language_code: str | None = None) -> bytes:
        calls.append(model or tts._model_name)
        return b"PRIMARY_OK"

    tts._synthesize_sync = fake_sync  # type: ignore[assignment]
    out = await tts._synthesize_one("Hello.", "Charon")
    assert out == b"PRIMARY_OK"
    assert calls == ["gemini-3.1-flash-tts-preview"]
    assert tts._quota_blocked_until == 0.0


@pytest.mark.asyncio
async def test_synthesize_switches_to_sibling_on_429() -> None:
    """Primary returns 429 → bridge calls sibling once, returns its audio,
    and records the primary cooldown matching Google's retryDelay."""
    tts = _new_tts()
    calls: list[str] = []

    def fake_sync(text: str, voice: str, model: str | None = None, language_code: str | None = None) -> bytes:
        calls.append(model)
        if model == "gemini-3.1-flash-tts-preview":
            raise RuntimeError(_GOOGLE_429_MESSAGE)
        return b"SIBLING_OK"

    tts._synthesize_sync = fake_sync  # type: ignore[assignment]
    out = await tts._synthesize_one("Hello.", "Charon")
    assert out == b"SIBLING_OK"
    # primary + sibling both attempted exactly once.
    assert calls == [
        "gemini-3.1-flash-tts-preview",
        "gemini-2.5-flash-preview-tts",
    ]
    # Cooldown timer parsed from the 429 (17270 s ± few seconds).
    # Upper bound is 17271.0 to tolerate float drift: time.monotonic() + 17270.0
    # can yield 17270.000000x when measured immediately after, exceeding 17270.0.
    remaining = tts._quota_blocked_until - time.monotonic()
    assert 17260.0 < remaining <= 17271.0


@pytest.mark.asyncio
async def test_synthesize_skips_primary_when_cooldown_active() -> None:
    """Subsequent sentences during cooldown go straight to sibling — no
    wasted round-trip to the dead primary endpoint."""
    tts = _new_tts()
    tts._quota_blocked_until = time.monotonic() + 1000.0  # primary blocked

    calls: list[str] = []

    def fake_sync(text: str, voice: str, model: str | None = None, language_code: str | None = None) -> bytes:
        calls.append(model)
        return b"SIBLING_OK"

    tts._synthesize_sync = fake_sync  # type: ignore[assignment]
    out = await tts._synthesize_one("Hello again.", "Charon")
    assert out == b"SIBLING_OK"
    # Primary skipped entirely; only sibling called.
    assert calls == ["gemini-2.5-flash-preview-tts"]


@pytest.mark.asyncio
async def test_synthesize_returns_silence_when_sibling_disabled() -> None:
    """Caller can opt out of the bridge by passing
    ``sibling_bridge_model=None``. On 429 the plugin returns b"" — original
    pre-2026-05-14 behaviour preserved for callers that prefer silence."""
    tts = _new_tts(sibling_bridge_model=None)
    calls: list[str] = []

    def fake_sync(text: str, voice: str, model: str | None = None, language_code: str | None = None) -> bytes:
        calls.append(model)
        raise RuntimeError(_GOOGLE_429_MESSAGE)

    tts._synthesize_sync = fake_sync  # type: ignore[assignment]
    out = await tts._synthesize_one("Hello.", "Charon")
    assert out == b""
    assert calls == ["gemini-3.1-flash-tts-preview"]


@pytest.mark.asyncio
async def test_synthesize_returns_silence_when_both_quota_exhausted() -> None:
    """If even the sibling 429s, plugin returns b"" rather than looping."""
    tts = _new_tts()

    def fake_sync(text: str, voice: str, model: str | None = None, language_code: str | None = None) -> bytes:
        raise RuntimeError(_GOOGLE_429_MESSAGE)

    tts._synthesize_sync = fake_sync  # type: ignore[assignment]
    out = await tts._synthesize_one("Hello.", "Charon")
    assert out == b""
    # Both timers armed.
    now = time.monotonic()
    assert tts._quota_blocked_until - now > 17000.0
    assert tts._sibling_blocked_until - now > 17000.0


@pytest.mark.asyncio
async def test_non_quota_error_does_not_arm_cooldown() -> None:
    """Network errors / SDK quirks should NOT poison the primary timer —
    only RESOURCE_EXHAUSTED / 429 does. Otherwise a transient 5xx would
    silently route every subsequent sentence through the sibling for an
    hour."""
    tts = _new_tts()

    def fake_sync(text: str, voice: str, model: str | None = None, language_code: str | None = None) -> bytes:
        raise RuntimeError("Connection reset by peer")

    tts._synthesize_sync = fake_sync  # type: ignore[assignment]
    out = await tts._synthesize_one("Hello.", "Charon")
    assert out == b""
    assert tts._quota_blocked_until == 0.0
    assert tts._sibling_blocked_until == 0.0
