"""Final dictation windows and their lossless text seam."""

import pytest

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


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("I said do not", "do-not delete these files.", "I said do not delete these files."),
        ("Use read only", "read-only mode today.", "Use read only mode today."),
        ("We know it s", "it's not ready.", "We know it s not ready."),
        ("Use version 2 5", "2.5 without upgrading.", "Use version 2 5 without upgrading."),
        ("The host is example com", "example.com stays local.",
         "The host is example com stays local."),
        ("Keep alpha beta", "alpha,beta,gamma intact.", "Keep alpha beta gamma intact."),
        ("Keep alpha beta", "alpha / beta: gamma intact.", "Keep alpha beta gamma intact."),
        ("Keep the value", "the value -5 unchanged.", "Keep the value -5 unchanged."),
        ("Keep the value", "the value:-5 unchanged.", "Keep the value -5 unchanged."),
        ("Use this flag", "this flag --verbose today.", "Use this flag --verbose today."),
        ("Use the ffi key", "\ufb03-key carefully.", "Use the ffi key carefully."),
        ("Use cafe\u0301 mode", "café-mode quietly.", "Use cafe\u0301 mode quietly."),
        ("Use café mode", "cafe\u0301-mode quietly.", "Use café mode quietly."),
        # i18n-allow: German speech with a hyphenated provider rendering
        ("Bitte den Schreib Schutz", "Schreib-Schutz nicht entfernen.",  # i18n-allow
         "Bitte den Schreib Schutz nicht entfernen."),  # i18n-allow
        # i18n-allow: punctuation and compatibility forms at a Japanese seam
        ("今日は東京、大阪", "東京、大阪へ行きます", "今日は東京、大阪へ行きます"),
        ("今日はガイド", "ｶﾞｲﾄﾞを見ます", "今日はガイドを見ます"),
    ],
)
def test_merge_only_removes_the_matched_original_span(
    left: str, right: str, expected: str,
) -> None:
    assert merge_transcripts([left, right]) == expected


def test_merge_keeps_an_indivisible_unicode_expansion() -> None:
    # The seam ends INSIDE a single source character (square corporation).
    # Keep that character whole, even if it means repeating the matched prefix.
    assert merge_transcripts(["株式", "㍿へ行く"]) == "株式 ㍿へ行く"  # i18n-allow


def test_merge_of_an_entire_repeated_window_drops_its_closing_punctuation() -> None:
    assert merge_transcripts(["Please stop now.", "“stop now.”"]) == "Please stop now."
