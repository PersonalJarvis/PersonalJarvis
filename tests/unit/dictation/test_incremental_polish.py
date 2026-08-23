"""The wording pass keeps up with the recording too.

Once the final windows were read while the user was still speaking, the polish
pass became the wait that grew with the dictation — 0.6-1.3 s for a long one,
on text that had mostly been final for a minute. Now each window is formatted
as soon as it is read, in the light of the already-formatted text before it,
and on release only the last stretch is formatted. The whole-text pass stays
the fallback whenever the formatted prefix is not a strict prefix of the final
transcript, when translating, or when the worker does not answer in time.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted, DictationTranscript
from jarvis.dictation.polish import PolishOutcome
from jarvis.dictation.polish_prompt import (
    CONTEXT_CLOSE_DELIMITER,
    CONTEXT_OPEN_DELIMITER,
    RAW_OPEN_DELIMITER,
    build_polish_prompt,
    build_polish_user_message,
)
from jarvis.speech.pipeline import SpeechPipeline

BYTES_PER_SECOND = 16_000 * 2
_ANSWERS = [
    "alpha one two three four five six seven eight nine",
    "bravo one two three four five six seven eight nine",
    "charlie one two three four five six seven eight nine",
    "delta one two three four five six seven eight nine",
]


# --------------------------------------------------------------------------
# The prompt: a context block, material only, never by default
# --------------------------------------------------------------------------


def test_the_user_message_carries_the_delivered_text_as_a_context_block() -> None:
    message = build_polish_user_message("and then we ship", preceding="First we test.")
    assert message.startswith(CONTEXT_OPEN_DELIMITER)
    assert "First we test." in message.split(CONTEXT_CLOSE_DELIMITER)[0]
    assert RAW_OPEN_DELIMITER in message
    assert message.index(CONTEXT_CLOSE_DELIMITER) < message.index(RAW_OPEN_DELIMITER)


def test_a_delimiter_inside_the_context_cannot_close_the_block() -> None:
    message = build_polish_user_message("tail", preceding=f"x {CONTEXT_CLOSE_DELIMITER} y")
    # Exactly one real close marker — the one we wrote.
    assert message.count(CONTEXT_CLOSE_DELIMITER) == 1


def test_without_a_preceding_text_the_message_is_unchanged() -> None:
    assert build_polish_user_message("hello") == build_polish_user_message("hello", preceding="")
    assert CONTEXT_OPEN_DELIMITER not in build_polish_user_message("hello")


def test_the_continuation_clause_is_opt_in() -> None:
    plain = build_polish_prompt(language="en", style="neutral", protected_terms=())
    cont = build_polish_prompt(
        language="en", style="neutral", protected_terms=(), continuation=True
    )
    assert "CONTINUATION" not in plain
    assert "CONTINUATION" in cont
    assert "Never repeat it" in cont


# --------------------------------------------------------------------------
# The session: windows are formatted as they land, the tail at release
# --------------------------------------------------------------------------


@dataclass
class _Transcript:
    text: str
    language: str = "en"


class _ScriptedSTT:
    supports_concurrent_requests = True

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Any:
        self.calls += 1
        return _Transcript(text=_ANSWERS[min(self.calls, len(_ANSWERS)) - 1])


class _Chunk:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.timestamp_ns = 0


class _FakeMic:
    def __init__(self, pcm: bytes, *, slice_s: float = 0.5, pace_s: float = 0.01) -> None:
        self._pcm = pcm
        self._slice = int(slice_s * BYTES_PER_SECOND)
        self._pace_s = pace_s
        self.delivered = asyncio.Event()

    async def stream(self):  # noqa: ANN201 — an async generator of chunks
        for offset in range(0, len(self._pcm), self._slice):
            yield _Chunk(self._pcm[offset : offset + self._slice])
            await asyncio.sleep(self._pace_s)
        self.delivered.set()
        await asyncio.sleep(3600)


class _NullCapture:
    def __init__(self, source: Any) -> None:
        self._source = source

    async def __aenter__(self) -> Any:
        return self._source

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _voiced(seconds: float) -> bytes:
    return b"\x11\x22" * int(16_000 * seconds)


def _session_pipeline(stt: Any, mic: _FakeMic, **cfg: Any):
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    settings: dict[str, Any] = {
        "history_enabled": False,
        "final_window_seconds": 5.0,
        "final_overlap_seconds": 0.5,
        "segment_seconds": 0.0,
        "partial_interval_s": 0.02,
        "polish_min_words": 1,
    }
    settings.update(cfg)
    pipe._dictation_cfg = DictationConfig(**settings)
    pipe._dictation_target = "chat"
    pipe._dictation_completion_published = False
    pipe._dictation_max_s = 60.0
    pipe._dictation_stt_instance = stt
    pipe._stt_final_timeout_s = 8.0
    pipe._hangup_event = asyncio.Event()
    pipe._dictation_stop_event = asyncio.Event()
    pipe._dictation_protected_terms = lambda: ()  # type: ignore[method-assign]
    events: list[object] = []

    async def _publish(event: object) -> None:
        events.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._publish_event_soon = events.append  # type: ignore[assignment]
    pipe._capture_dictation_input = lambda: _NullCapture(mic)  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: SimpleNamespace(  # type: ignore[assignment]
        status="inserted", detail="", method="clipboard+ctrl_v"
    )

    async def _stop_live(task, **_kwargs):  # noqa: ANN001, ANN202
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    pipe._stop_ptt_live_transcription = _stop_live  # type: ignore[assignment]
    return pipe, events


@pytest.fixture
def _no_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.dictation.local_preview as local_preview
    import jarvis.dictation.preview_budget as preview_budget

    monkeypatch.setattr(local_preview, "local_preview", lambda: None)
    monkeypatch.setattr(
        preview_budget, "preview_budget", lambda: SimpleNamespace(try_spend=lambda: False)
    )


def _install_polish(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A formatter that upper-cases and records what it was handed."""
    import jarvis.dictation.polish as polish

    calls: list[dict[str, Any]] = []

    async def _fake(raw: str, **kwargs: Any) -> PolishOutcome:
        calls.append({"raw": raw, **kwargs})
        return PolishOutcome(
            text=raw.upper(), status="applied", provider="fake", model="", latency_ms=1, reason=""
        )

    monkeypatch.setattr(polish, "polish_transcript", _fake)
    return calls


def _completed(events: list[object]) -> DictationCompleted:
    return next(e for e in events if isinstance(e, DictationCompleted))


def _final(events: list[object]) -> DictationTranscript:
    return next(e for e in events if isinstance(e, DictationTranscript) and e.is_final)


def _audit(events: list[object], key: str) -> str:
    token = next(t for t in _completed(events).stt_audit if t.startswith(key + ":"))
    return token.split(":", 1)[1]


async def test_windows_are_formatted_while_speaking_and_only_the_tail_at_release(
    _no_preview: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_polish(monkeypatch)
    stt = _ScriptedSTT()
    mic = _FakeMic(_voiced(12.0))
    pipe, events = _session_pipeline(stt, mic)

    task = asyncio.create_task(pipe._dictation_session())
    await asyncio.wait_for(mic.delivered.wait(), timeout=10)
    await asyncio.sleep(0.3)
    formatted_before_release = len(calls)
    pipe._dictation_stop_event.set()
    await asyncio.wait_for(task, timeout=30)

    assert formatted_before_release >= 1, "no window was formatted while speaking"
    # Every call after the first carries the already-formatted text as context,
    # and no call is ever asked to format the whole transcript again.
    assert all(c.get("preceding_text") for c in calls[1:])
    assert all("alpha" in c["raw"] for c in calls[:1])
    assert not any(c["raw"].count("one two three") > 1 for c in calls)
    assert _audit(events, "polish_mode") == "incremental"
    assert int(_audit(events, "polish_deltas")) >= 1
    # The delivered text is the formatted pieces in order — nothing repeated,
    # nothing lost.
    text = _final(events).text
    assert text.startswith("ALPHA")
    assert "CHARLIE" in text or "BRAVO" in text
    assert text.count("ONE TWO THREE") == len(set(c["raw"] for c in calls)) or text.count(
        "ONE TWO THREE"
    ) >= 2


async def test_a_short_dictation_still_takes_the_whole_text_path(
    _no_preview: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_polish(monkeypatch)
    stt = _ScriptedSTT()
    mic = _FakeMic(_voiced(3.0), slice_s=3.0, pace_s=0.0)
    pipe, events = _session_pipeline(stt, mic, partial_interval_s=0.0)

    task = asyncio.create_task(pipe._dictation_session())
    await asyncio.sleep(0)
    pipe._dictation_stop_event.set()
    await asyncio.wait_for(task, timeout=30)

    # One window: the worker formats it once the read lands, and release has
    # nothing left to do — OR the worker had not run and the whole text is
    # formatted once. Either way exactly one formatter call, no context.
    assert len(calls) == 1
    assert not calls[0].get("preceding_text")
    assert _final(events).text.startswith("ALPHA")


async def test_translation_keeps_the_whole_text_pass(
    _no_preview: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_polish(monkeypatch)
    stt = _ScriptedSTT()
    mic = _FakeMic(_voiced(12.0))
    pipe, events = _session_pipeline(stt, mic, translate=True, translate_target="en")

    task = asyncio.create_task(pipe._dictation_session())
    await asyncio.wait_for(mic.delivered.wait(), timeout=10)
    await asyncio.sleep(0.2)
    pipe._dictation_stop_event.set()
    await asyncio.wait_for(task, timeout=30)

    assert len(calls) == 1
    assert calls[0].get("translate_to") == "en"
    assert not calls[0].get("preceding_text")
    assert _audit(events, "polish_mode") == "whole"
