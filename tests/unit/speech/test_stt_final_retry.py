"""Regression guards for the final-utterance STT transient-error retry.

Root cause (2026-05-25): the post-wake *final* utterance transcription went
straight to ``transcribe_pcm`` with no retry. The in-utterance stability probe
fires a cloud-STT call every ~650 ms and shares one rate budget with this final
call, so under speech the provider returns ``429 Too Many Requests`` — and the
final call inherited it. ``_handle_utterance`` caught the error, logged it, and
silently returned to LISTENING: the user's turn never reached the brain
("Jarvis listens forever and never answers", intermittent because 429 is
bursty). That is an AD-OE6 violation ("zero silent drops").

The probe stops the instant the VAD endpoint fires, so the rate window frees
within ~1 s. ``_transcribe_final`` therefore retries *transient* failures
(429 / 5xx / timeout) with capped backoff and only returns ``None`` when every
attempt fails — the caller then speaks an apology instead of going mute. A
*non-transient* error (e.g. 401 bad key) is not retried.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.protocols import Transcript
from jarvis.speech import pipeline as pipeline_mod
from jarvis.speech.pipeline import SpeechPipeline


class _FakeResponse:
    def __init__(self, status_code: int, retry_after: str | None = "0") -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        if retry_after is not None:
            self.headers["retry-after"] = retry_after


class _FakeHTTPStatusError(Exception):
    """Mimics ``httpx.HTTPStatusError`` shape (``.response.status_code``)."""

    def __init__(self, status_code: int, retry_after: str | None = "0") -> None:
        super().__init__(f"HTTP {status_code}")
        self.response = _FakeResponse(status_code, retry_after)


class _ScriptedSTT:
    """STT whose ``transcribe_pcm`` follows a scripted list of behaviours.

    Each entry is either an exception instance (raised) or a ``Transcript``
    (returned). The last entry repeats once the script is exhausted.
    """

    def __init__(self, script: list[object]) -> None:
        self._script = script
        self.calls = 0

    async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
        idx = min(self.calls, len(self._script) - 1)
        self.calls += 1
        outcome = self._script[idx]
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, Transcript)
        return outcome


class _HangingThenSTT:
    """First call blocks until ``gate`` is set (→ timeout); later calls return."""

    def __init__(self, result: Transcript) -> None:
        self._result = result
        self.gate = asyncio.Event()
        self.calls = 0

    async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
        self.calls += 1
        if self.calls == 1:
            await self.gate.wait()
        return self._result


def _make_pipe(stt: object, *, timeout_s: float = 5.0) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._utterance_stt = stt
    pipe._stt_final_timeout_s = timeout_s
    return pipe


_OK = Transcript(text="hallo jarvis", language="de", confidence=0.9, is_partial=False)


@pytest.mark.asyncio
async def test_final_retries_transient_429_then_succeeds() -> None:
    """A bursty 429 must be retried, not silently dropped, then the turn proceeds."""
    stt = _ScriptedSTT(
        [_FakeHTTPStatusError(429), _FakeHTTPStatusError(429), _OK]
    )
    pipe = _make_pipe(stt)

    transcript = await pipe._transcribe_final(b"\x00\x00" * 256)

    assert transcript is _OK
    assert stt.calls == 3, "should retry the two 429s before the success"


@pytest.mark.asyncio
async def test_final_returns_none_when_429_never_clears() -> None:
    """If every attempt 429s the helper gives up with None (caller will speak)."""
    stt = _ScriptedSTT([_FakeHTTPStatusError(429)])
    pipe = _make_pipe(stt)

    transcript = await pipe._transcribe_final(b"\x00\x00" * 256)

    assert transcript is None
    assert stt.calls == 3, "1 initial attempt + 2 retries"


@pytest.mark.asyncio
async def test_final_does_not_retry_non_transient_error() -> None:
    """A 401 (bad key) is not a rate-limit — fail fast, do not hammer the API."""
    stt = _ScriptedSTT([_FakeHTTPStatusError(401)])
    pipe = _make_pipe(stt)

    transcript = await pipe._transcribe_final(b"\x00\x00" * 256)

    assert transcript is None
    assert stt.calls == 1, "non-transient errors must not be retried"


@pytest.mark.asyncio
async def test_final_retries_timeout_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung first attempt times out and is retried rather than dropped."""
    monkeypatch.setattr(pipeline_mod, "_STT_RETRY_BASE_S", 0.0, raising=False)
    stt = _HangingThenSTT(_OK)
    pipe = _make_pipe(stt, timeout_s=0.05)

    transcript = await pipe._transcribe_final(b"\x00\x00" * 256)

    assert transcript is _OK
    assert stt.calls == 2, "first call timed out, second succeeded"
