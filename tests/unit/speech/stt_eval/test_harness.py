"""Network-free tests for the STT comparison driver."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from jarvis.speech.stt_eval.corpus import STTEvalItem, load_corpus
from jarvis.speech.stt_eval.harness import Recognition, evaluate_contender


def _wav(path: Path, seconds: float = 1.0) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x00" * int(16_000 * seconds))


def test_manifest_resolves_relative_audio_and_annotations(tmp_path: Path) -> None:
    _wav(tmp_path / "mixed.wav")
    manifest = tmp_path / "corpus.jsonl"
    manifest.write_text(
        '{"id":"mixed","audio":"mixed.wav","reference":"hello mundo",'
        '"switch_anchors":["hello mundo"],"tags":["latin"]}\n',
        encoding="utf-8",
    )

    item = load_corpus(manifest)[0]

    assert item.audio_path == tmp_path / "mixed.wav"
    assert item.switch_anchors == ("hello mundo",)
    assert item.tags == ("latin",)


@pytest.mark.asyncio
async def test_harness_measures_quality_latency_cost_and_repeatability(
    tmp_path: Path,
) -> None:
    audio = tmp_path / "mixed.wav"
    _wav(audio, seconds=2.0)
    item = STTEvalItem(
        id="mixed",
        audio_path=audio,
        reference="hello mundo",
        switch_anchors=("hello mundo",),
    )
    answers = iter(("hello mundo", "hello mundo", "hello world"))

    async def recognize(_pcm: bytes) -> Recognition:
        return Recognition(text=next(answers), latency_ms=120.0, cost_usd=0.0002)

    report = await evaluate_contender(
        recognize,
        (item,),
        label="candidate",
        provider="example",
        model="multilingual",
        repeats=3,
        price_per_minute_usd=0.006,
    )

    assert report.wer == pytest.approx(1 / 6)
    assert report.switch_error_rate == pytest.approx(1 / 3)
    assert report.repeatability_error_rate == pytest.approx(1 / 4)
    assert report.median_latency_ms == 120.0
    assert report.measured_cost_usd == pytest.approx(0.0006)
    assert report.estimated_cost_usd == pytest.approx(0.0006)


@pytest.mark.asyncio
async def test_provider_error_counts_as_a_failed_transcript(tmp_path: Path) -> None:
    audio = tmp_path / "speech.wav"
    _wav(audio)
    item = STTEvalItem(id="failure", audio_path=audio, reference="spoken words")

    async def recognize(_pcm: bytes) -> Recognition:
        return Recognition(text="", latency_ms=50.0, error="rate_limited")

    report = await evaluate_contender(
        recognize,
        (item,),
        label="broken",
        provider="example",
        model="example",
    )

    assert report.wer == 1.0
    assert report.measured_cost_usd is None
    assert report.items[0].errors == ("rate_limited",)
