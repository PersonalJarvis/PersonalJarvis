"""Network-free tests for the STT comparison driver."""

from __future__ import annotations

import json
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
    assert report.quality.failed_attempts == 3
    assert report.quality.speech_errors.deletions == 6


def test_manifest_accepts_an_explicit_empty_silence_reference(tmp_path: Path) -> None:
    manifest = tmp_path / "corpus.jsonl"
    manifest.write_text(
        '{"id":"quiet","audio":"quiet.wav","reference":"","tags":["silence"]}\n',
        encoding="utf-8",
    )
    assert load_corpus(manifest)[0].reference == ""


@pytest.mark.parametrize("reference", [None, 7, False, []])
def test_manifest_does_not_turn_invalid_references_into_silence(
    tmp_path: Path, reference: object,
) -> None:
    manifest = tmp_path / "corpus.jsonl"
    manifest.write_text(
        json.dumps({"id": "bad", "audio": "bad.wav", "reference": reference}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="string reference"):
        load_corpus(manifest)


def test_manifest_requires_the_reference_field_even_for_silence(tmp_path: Path) -> None:
    manifest = tmp_path / "corpus.jsonl"
    manifest.write_text('{"id":"bad","audio":"bad.wav"}', encoding="utf-8")
    with pytest.raises(ValueError, match="string reference"):
        load_corpus(manifest)


@pytest.mark.asyncio
async def test_word_weighting_silence_categories_and_tail_latency(tmp_path: Path) -> None:
    audio = tmp_path / "sample.wav"
    _wav(audio)
    items = (
        STTEvalItem("short", audio, "one two", tags=("speech", "short", "short")),
        STTEvalItem("long", audio, "one two three four five six seven eight", tags=("speech",)),
        STTEvalItem("quiet", audio, "", tags=("silence",)),
    )
    responses = iter(
        (
            Recognition("one wrong", 10.0),
            Recognition("one two three four five six seven eight", 20.0),
            Recognition("invented words", 100.0),
        )
    )

    async def recognize(_pcm: bytes) -> Recognition:
        return next(responses)

    report = await evaluate_contender(
        recognize, items, label="test", provider="fake", model="fake", repeats=1,
    )

    assert report.wer == 0.5  # Legacy per-item mean, including the silence failure.
    assert report.quality.word_weighted_wer == 0.1
    assert report.quality.speech_errors.reference_words == 10
    assert report.quality.speech_errors.substitutions == 1
    assert report.quality.speech_errors.insertions == 0
    assert report.quality.silence_hallucination_rate == 1.0
    assert report.quality.p95_latency_ms == 100.0
    assert report.by_tag["speech"].word_weighted_wer == 0.1
    assert report.by_tag["speech"].p95_latency_ms == 20.0
    assert report.by_tag["short"].attempts == 1
    assert report.by_tag["silence"].word_weighted_wer is None


@pytest.mark.asyncio
async def test_silence_errors_never_count_as_successful_suppression(tmp_path: Path) -> None:
    audio = tmp_path / "quiet.wav"
    _wav(audio)
    responses = iter(
        (Recognition("", 10, error="timeout"), Recognition("", 20), Recognition("...", 30))
    )

    async def recognize(_pcm: bytes) -> Recognition:
        return next(responses)

    report = await evaluate_contender(
        recognize, (STTEvalItem("quiet", audio, ""),),
        label="test", provider="fake", model="fake", repeats=3,
    )

    assert report.quality.attempts == 3
    assert report.quality.failed_attempts == 1
    assert report.quality.silence_evaluated == 2
    assert report.quality.silence_hallucinations == 1
    assert report.quality.silence_hallucination_rate == 0.5
    assert report.quality.word_weighted_wer is None


@pytest.mark.asyncio
async def test_all_failed_silence_has_no_accuracy_measurement(tmp_path: Path) -> None:
    audio = tmp_path / "quiet.wav"
    _wav(audio)

    async def recognize(_pcm: bytes) -> Recognition:
        return Recognition("", 10, error="timeout")

    report = await evaluate_contender(
        recognize, (STTEvalItem("quiet", audio, ""),),
        label="test", provider="fake", model="fake", repeats=1,
    )
    assert report.quality.failed_attempts == 1
    assert report.quality.silence_hallucination_rate is None


@pytest.mark.asyncio
async def test_empty_corpus_fails_before_any_recognizer_call() -> None:
    async def recognize(_pcm: bytes) -> Recognition:
        pytest.fail("An empty corpus must not reach the provider")

    with pytest.raises(ValueError, match="corpus is empty"):
        await evaluate_contender(recognize, (), label="test", provider="fake", model="fake")


def test_cli_writes_versioned_metrics_without_private_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from jarvis.speech.stt_eval import __main__ as cli

    audio = tmp_path / "private.wav"
    _wav(audio)
    private_text = "private dictation content"

    async def recognize(_pcm: bytes) -> Recognition:
        return Recognition(private_text, 42.0)

    async def run(_args: object):
        return (
            await evaluate_contender(
                recognize, (STTEvalItem("sample", audio, private_text, tags=("quiet",)),),
                label="test", provider="fake", model="fake", repeats=1,
            ),
        )

    monkeypatch.setattr(cli, "_run", run)
    output = tmp_path / "report.json"
    assert cli.main([
        "--corpus", "unused.jsonl", "--contender", "test|fake|fake|0", "--out", str(output),
    ]) == 0
    serialized = output.read_text(encoding="utf-8")
    report = json.loads(serialized)
    assert report["schema_version"] == 3
    assert report["contenders"][0]["quality"]["word_weighted_wer"] == 0.0
    assert report["contenders"][0]["by_tag"]["quiet"]["p95_latency_ms"] == 42.0
    assert private_text not in serialized
    assert str(audio) not in serialized
    assert private_text not in capsys.readouterr().out
