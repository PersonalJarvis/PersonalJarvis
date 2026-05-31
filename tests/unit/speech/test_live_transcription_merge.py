import pytest

from jarvis.core.protocols import Transcript
from jarvis.speech.pipeline import SpeechPipeline, _merge_partial_transcript


def test_merge_partial_transcript_appends_non_overlapping_tail() -> None:
    assert (
        _merge_partial_transcript("Hallo ich bin", "bin cool")
        == "Hallo ich bin cool"
    )


def test_merge_partial_transcript_keeps_existing_text_when_tail_repeats() -> None:
    assert (
        _merge_partial_transcript("Hallo ich bin cool", "ich bin cool")
        == "Hallo ich bin cool"
    )


def test_merge_partial_transcript_replaces_corrected_live_hypotheses() -> None:
    text = ""
    for partial in (
        "Was?",
        "Was ist morgens?",
        "Was ist morgen fuer ein Tag?",
        "Morgen fuer einen Tag.",
    ):
        text = _merge_partial_transcript(text, partial)

    assert text == "Was ist morgen fuer ein Tag?"


async def test_stt_probe_publishes_partial_transcription_update() -> None:
    class _STT:
        async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
            return Transcript(text="Hallo ich bin cool", language="de", confidence=0.9)

    class _Vad:
        def request_endpoint(self) -> None:
            raise AssertionError("real speech tail must not force endpoint")

    events = []

    async def _publish_event(event) -> None:
        events.append(event)

    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._stt = _STT()  # noqa: SLF001
    pipe._vad = _Vad()  # noqa: SLF001
    pipe._probe_min_text_len = 4  # noqa: SLF001
    pipe._probe_last_text = ""  # noqa: SLF001
    pipe._probe_live_text = ""  # noqa: SLF001
    pipe._probe_stable_count = 0  # noqa: SLF001
    pipe._probe_required_stable = 1  # noqa: SLF001
    pipe._probe_in_flight = True  # noqa: SLF001
    pipe._publish_event = _publish_event  # type: ignore[method-assign]

    await pipe._stt_probe_async(b"pcm")  # noqa: SLF001

    assert len(events) == 1
    assert events[0].text == "Hallo ich bin cool"
    assert events[0].is_final is False
    assert pipe._probe_in_flight is False  # noqa: SLF001


@pytest.mark.parametrize(
    "hallucination",
    [
        "Untertitelung des ZDF, 2020",
        "Untertitelung des ZDF fuer funk, 2017",
    ],
)
async def test_stt_probe_suppresses_subtitle_hallucination_partials(
    hallucination: str,
) -> None:
    class _STT:
        async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
            return Transcript(text=hallucination, language="de", confidence=0.9)

    class _Vad:
        def __init__(self) -> None:
            self.endpoint_requested = False

        def request_endpoint(self) -> None:
            self.endpoint_requested = True

    events = []

    async def _publish_event(event) -> None:
        events.append(event)

    vad = _Vad()
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._stt = _STT()  # noqa: SLF001
    pipe._vad = vad  # noqa: SLF001
    pipe._probe_min_text_len = 4  # noqa: SLF001
    pipe._probe_last_text = ""  # noqa: SLF001
    pipe._probe_live_text = ""  # noqa: SLF001
    pipe._probe_stable_count = 0  # noqa: SLF001
    pipe._probe_required_stable = 1  # noqa: SLF001
    pipe._probe_in_flight = True  # noqa: SLF001
    pipe._publish_event = _publish_event  # type: ignore[method-assign]

    await pipe._stt_probe_async(b"pcm")  # noqa: SLF001

    assert events == []
    assert vad.endpoint_requested is True
    assert pipe._probe_in_flight is False  # noqa: SLF001


async def test_stt_probe_suppresses_broadcast_boilerplate_partial() -> None:
    class _STT:
        async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
            return Transcript(
                text="Eine Sendung des NDR, 2020",
                language="de",
                confidence=0.88,
            )

    class _Vad:
        def __init__(self) -> None:
            self.endpoint_requested = False

        def request_endpoint(self) -> None:
            self.endpoint_requested = True

    events = []

    async def _publish_event(event) -> None:
        events.append(event)

    vad = _Vad()
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._stt = _STT()  # noqa: SLF001
    pipe._vad = vad  # noqa: SLF001
    pipe._probe_min_text_len = 4  # noqa: SLF001
    pipe._probe_last_text = ""  # noqa: SLF001
    pipe._probe_live_text = ""  # noqa: SLF001
    pipe._probe_stable_count = 0  # noqa: SLF001
    pipe._probe_required_stable = 1  # noqa: SLF001
    pipe._probe_in_flight = True  # noqa: SLF001
    pipe._publish_event = _publish_event  # type: ignore[method-assign]

    await pipe._stt_probe_async(b"pcm")  # noqa: SLF001

    assert events == []
    assert vad.endpoint_requested is True
    assert pipe._probe_in_flight is False  # noqa: SLF001
