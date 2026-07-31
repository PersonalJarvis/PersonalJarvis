"""Final dictation windows and their lossless text seam."""

from jarvis.dictation.merge import merge_transcripts
from jarvis.dictation.segment import quality_windows

BYTES_PER_SECOND = 16_000 * 2


def test_short_recording_is_one_complete_window() -> None:
    pcm = b"x" * (7 * BYTES_PER_SECOND)
    assert quality_windows(
        pcm,
        window_bytes=25 * BYTES_PER_SECOND,
        overlap_bytes=int(1.5 * BYTES_PER_SECOND),
    ) == [(0, len(pcm))]


def test_long_recording_is_fully_covered_with_overlap() -> None:
    pcm = b"\x00\x01" * (61 * 16_000)
    windows = quality_windows(
        pcm,
        window_bytes=25 * BYTES_PER_SECOND,
        overlap_bytes=int(1.5 * BYTES_PER_SECOND),
    )

    assert windows[0][0] == 0
    assert windows[-1][1] == len(pcm)
    assert all(start < end for start, end in windows)
    pairs = zip(windows, windows[1:], strict=False)
    assert all(next_start < end for (_, end), (next_start, _) in pairs)
    assert all(
        next_start <= end and next_start > start
        for (start, end), (next_start, _) in zip(
            windows, windows[1:], strict=False
        )
    )


def test_pathological_overlap_still_has_linear_progress() -> None:
    pcm = b"x" * (20 * BYTES_PER_SECOND)
    windows = quality_windows(
        pcm,
        window_bytes=5 * BYTES_PER_SECOND,
        overlap_bytes=60 * BYTES_PER_SECOND,
    )

    assert len(windows) <= 8
    assert all(
        next_start - start >= (5 * BYTES_PER_SECOND) // 2
        for (start, _), (next_start, _) in zip(
            windows, windows[1:], strict=False
        )
    )


def test_merge_removes_the_largest_normalized_word_overlap() -> None:
    assert merge_transcripts(
        [
            "We deploy the final release tomorrow.",
            "the FINAL release tomorrow, then monitor it.",
        ]
    ) == "We deploy the final release tomorrow. then monitor it."


def test_merge_preserves_unmatched_code_switching_text() -> None:
    result = merge_transcripts(
        [
            "We deploy the release heute Abend.",  # i18n-allow: mixed-language fixture
            "heute Abend. Luego revisamos métricas.",  # i18n-allow: mixed-language fixture
        ]
    )
    assert result == (
        "We deploy the release heute Abend. Luego revisamos métricas."
    )  # i18n-allow: mixed-language fixture


def test_merge_does_not_insert_spaces_into_cjk_text() -> None:
    assert merge_transcripts(
        [
            "今日は東京へ行きます",  # i18n-allow: Japanese transcription fixture
            "東京へ行きます明日は大阪です",  # i18n-allow: Japanese transcription fixture
        ]
    ) == "今日は東京へ行きます明日は大阪です"  # i18n-allow: Japanese fixture
