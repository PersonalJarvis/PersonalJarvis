"""The wake-model prefetch only runs where something adopts the weights.

The prefetch loads a faster-whisper model into a hand-over cache whose only
reader is ``FasterWhisperProvider._build_model``. A host whose wake engine
never builds one — vosk_kws, custom_onnx, openWakeWord — therefore paid a full
cold-disk model load plus a priming decode for weights nobody would ever take,
in front of the models the user is actually waiting for (BUG-214: ~50 s of the
2026-09-03 cold boot).

These tests pin the predicate, not the thread: the decision runs inside the
prefetch thread, so asserting on it directly is what keeps the boot path free
of the check.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.plugins.stt import wake_whisper_is_consumed


def _cfg(
    *,
    phrase: str = "Hey Nova",
    engine: str = "auto",
    language: str = "de",
    heavy: bool = False,
) -> SimpleNamespace:
    """A duck-typed app config — every read in the predicate is a getattr chain."""
    return SimpleNamespace(
        trigger=SimpleNamespace(
            heavy_local_whisper=heavy,
            wake_word=SimpleNamespace(
                phrase=phrase,
                engine=engine,
                custom_model_path="",
                sensitivity=0.5,
                fuzzy_match_ratio=0.8,
                language=language,
            ),
        ),
        stt=SimpleNamespace(language="auto"),
        ui=SimpleNamespace(language="de"),
    )


@pytest.fixture()
def vosk_installed(tmp_path, monkeypatch) -> None:
    """A German Vosk model on disk, which makes resolve_wake_plan pick vosk_kws."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    (tmp_path / "wake_models" / "vosk" / "de" / "vosk-model-small-de-0.15" / "am").mkdir(
        parents=True
    )


@pytest.fixture()
def no_vosk(tmp_path, monkeypatch) -> None:
    """No Vosk model anywhere, so an arbitrary phrase falls to stt_match."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))


def test_a_vosk_host_skips_the_prefetch(vosk_installed) -> None:
    """The whole point: nothing on a vosk_kws host would adopt the weights."""
    consumed, reason = wake_whisper_is_consumed(_cfg())

    assert consumed is False
    assert "vosk_kws" in reason  # the reason names the engine, for the log


def test_a_transcript_matching_host_still_prefetches(no_vosk) -> None:
    """stt_match transcribes locally — for it the prefetch is a real win and
    removing it would be the regression this change must not cause."""
    consumed, reason = wake_whisper_is_consumed(_cfg())

    assert consumed is True
    assert reason


def test_heavy_local_whisper_prefetches_whatever_the_engine_is(vosk_installed) -> None:
    """The flag forces a local Whisper, so the weights get a consumer even on a
    host whose wake plan resolves to vosk."""
    consumed, reason = wake_whisper_is_consumed(_cfg(heavy=True))

    assert consumed is True
    assert "heavy_local_whisper" in reason


@pytest.mark.parametrize("broken", [None, object(), SimpleNamespace()])
def test_an_unreadable_config_prefetches_rather_than_guessing(broken: Any) -> None:
    """Failing closed here would silently slow down the hosts that need the
    prefetch. An unreadable plan reproduces today's unconditional behaviour."""
    consumed, reason = wake_whisper_is_consumed(broken)

    assert consumed is True
    assert reason
