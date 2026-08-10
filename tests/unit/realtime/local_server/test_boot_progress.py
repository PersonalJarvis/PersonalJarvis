"""Boot-stage parsing, boot statistics and crash-tail forensics.

The log fixtures are verbatim lines from a real managed-server boot
(2026-08-10 20:16 — the model switch whose 65-second window produced the
static "about a minute" toast this module exists to replace).
"""

from __future__ import annotations

import time
from pathlib import Path

from jarvis.realtime.local_server import boot_progress


def _epoch(stamp: str) -> float:
    return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))


_BOOT_LINES = [
    "DeepFilterNet not available for audio enhancement: No module named 'df'",
    "2026-08-10 20:17:07,702 - speech_to_speech.VAD.smart_turn - INFO - Loaded Smart Turn v3.2",
    (
        "2026-08-10 20:17:08,010 - speech_to_speech.STT.parakeet_tdt_handler - INFO - "
        "Loading Parakeet TDT model: nvidia/parakeet-tdt-0.6b-v3 on cpu"
    ),
    (
        "2026-08-10 20:17:22,921 - speech_to_speech.STT.parakeet_tdt_handler - INFO - "
        "Warming up ParakeetTDTSTTHandler"
    ),
    (
        "2026-08-10 20:17:23,568 - speech_to_speech.LLM.chat_completions_language_model - "
        "INFO - Warming up ChatCompletionsApiModelHandler"
    ),
    (
        "2026-08-10 20:17:25,797 - speech_to_speech.TTS.qwen3_tts_handler - INFO - "
        "Loading Qwen3-TTS model: Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
    ),
    # Verbatim localized OS noise from a real boot on a German Windows host —
    # the parser must ignore it, so the fixture keeps it. (i18n-allow)
    'Der Befehl "sox" ist entweder falsch geschrieben oder',  # i18n-allow
    (
        "2026-08-10 20:17:36,489 - speech_to_speech.TTS.qwen3_tts_handler - INFO - "
        "Warming up Qwen3TTSHandler"
    ),
    (
        "2026-08-10 20:17:47,589 - speech_to_speech.api.openai_realtime.server - INFO - "
        "OpenAI Realtime API starting on ws://127.0.0.1:8765/v1/realtime (pool size 1)"
    ),
]


class TestParseBootStage:
    def test_reports_the_newest_marker_of_the_current_boot(self) -> None:
        spawned_at = _epoch("2026-08-10 20:16:42")
        for upto, expected in [
            (3, ("listening-model", "loading the listening model")),
            (4, ("listening-model", "warming up the listening model")),
            (5, ("brain", "warming up the language model")),
            (6, ("voice-model", "loading the speaking voice")),
            (8, ("voice-warmup", "warming up the speaking voice")),
            (9, ("opening", "opening the call endpoint")),
        ]:
            assert (
                boot_progress.parse_boot_stage(_BOOT_LINES[:upto], spawned_at=spawned_at)
                == expected
            )

    def test_earlier_generations_in_the_shared_log_are_invisible(self) -> None:
        # The append-only log still carries yesterday's full boot; a parse for
        # a child spawned AFTER those lines must not claim their progress.
        spawned_at = _epoch("2026-08-11 09:00:00")
        assert boot_progress.parse_boot_stage(_BOOT_LINES, spawned_at=spawned_at) is None

    def test_model_names_never_matter_only_the_module_prefix(self) -> None:
        # A different tier ships Whisper + another TTS; the module prefixes
        # are the stable contract.
        lines = [
            (
                "2026-08-10 20:17:08,010 - speech_to_speech.STT.whisper_handler - INFO - "
                "Loading Whisper large-v3 on cuda"
            )
        ]
        spawned_at = _epoch("2026-08-10 20:16:42")
        assert boot_progress.parse_boot_stage(lines, spawned_at=spawned_at) == (
            "listening-model",
            "loading the listening model",
        )

    def test_call_traffic_lines_are_not_boot_markers(self) -> None:
        lines = [
            (
                "2026-08-10 20:18:24,558 - [pipeline 0] speech_to_speech.STT."
                "parakeet_tdt_handler - INFO - Parakeet final STT start turn=turn_1"
            ),
            'INFO:     127.0.0.1:50085 - "GET /v1/pool HTTP/1.1" 200 OK',
        ]
        spawned_at = _epoch("2026-08-10 20:16:42")
        assert boot_progress.parse_boot_stage(lines, spawned_at=spawned_at) is None


class TestReadLogTail:
    def test_reads_only_the_requested_tail(self, tmp_path: Path) -> None:
        log_file = tmp_path / "server.log"
        log_file.write_text("first\n" + "x" * 100 + "\nlast line\n", encoding="utf-8")
        lines = boot_progress.read_log_tail(log_file, max_bytes=12)
        assert lines[-1] == "last line"
        assert "first" not in lines

    def test_missing_file_is_an_empty_tail_not_an_error(self, tmp_path: Path) -> None:
        assert boot_progress.read_log_tail(tmp_path / "absent.log") == []


class TestCrashTail:
    def test_polling_noise_and_blank_lines_are_dropped(self) -> None:
        lines = [
            "2026-08-10 18:37:25,279 - ... - INFO - Pipeline 0 released",
            'INFO:     127.0.0.1:57496 - "GET /v1/pool HTTP/1.1" 200 OK',
            "",
            'INFO:     127.0.0.1:57504 - "GET /v1/pool HTTP/1.1" 200 OK',
            "Windows fatal exception: access violation",
        ]
        tail = boot_progress.crash_tail(lines)
        assert tail == [
            "2026-08-10 18:37:25,279 - ... - INFO - Pipeline 0 released",
            "Windows fatal exception: access violation",
        ]

    def test_output_is_bounded_in_lines_and_line_length(self) -> None:
        lines = [f"line {index} " + "y" * 1000 for index in range(200)]
        tail = boot_progress.crash_tail(lines)
        assert len(tail) == boot_progress.CRASH_TAIL_MAX_LINES
        assert all(len(line) <= boot_progress.CRASH_TAIL_MAX_LINE_CHARS for line in tail)


class TestBootStats:
    def test_ready_is_recorded_once_per_generation_and_resets_the_streak(
        self, tmp_path: Path
    ) -> None:
        stats_file = tmp_path / "boot.json"
        generation_a, generation_b = "gen-a", "gen-b"
        assert boot_progress.record_timeout(stats_file, token=generation_a)
        assert boot_progress.load_stats(stats_file)["failed_streak"] == 1

        assert boot_progress.record_ready(stats_file, token=generation_b, duration_s=65.2)
        # The same generation observed again (status poll race) is a no-op.
        assert not boot_progress.record_ready(stats_file, token=generation_b, duration_s=120.0)
        stats = boot_progress.load_stats(stats_file)
        assert stats["durations_s"] == [65.2]
        assert stats["failed_streak"] == 0

    def test_timeout_streak_counts_distinct_generations_only(self, tmp_path: Path) -> None:
        stats_file = tmp_path / "boot.json"
        generation_a, generation_b = "gen-a", "gen-b"
        assert boot_progress.record_timeout(stats_file, token=generation_a)
        assert not boot_progress.record_timeout(stats_file, token=generation_a)
        assert boot_progress.record_timeout(stats_file, token=generation_b)
        assert boot_progress.load_stats(stats_file)["failed_streak"] == 2

    def test_expected_boot_is_the_median_of_recent_boots(self, tmp_path: Path) -> None:
        stats_file = tmp_path / "boot.json"
        assert boot_progress.expected_boot_s(boot_progress.load_stats(stats_file)) is None
        for index, duration in enumerate([60.0, 90.0, 70.0]):
            boot_progress.record_ready(stats_file, token=f"gen-{index}", duration_s=duration)
        assert boot_progress.expected_boot_s(boot_progress.load_stats(stats_file)) == 70.0

    def test_history_is_bounded_to_the_recent_boots(self, tmp_path: Path) -> None:
        stats_file = tmp_path / "boot.json"
        for index in range(boot_progress.MAX_RECORDED_BOOTS + 3):
            boot_progress.record_ready(stats_file, token=f"gen-{index}", duration_s=60.0 + index)
        stats = boot_progress.load_stats(stats_file)
        assert len(stats["durations_s"]) == boot_progress.MAX_RECORDED_BOOTS  # type: ignore[arg-type]

    def test_corrupt_stats_degrade_to_empty_defaults(self, tmp_path: Path) -> None:
        stats_file = tmp_path / "boot.json"
        stats_file.write_text('{"durations_s": ["nan", -3, true], "failed_streak": -1}')
        stats = boot_progress.load_stats(stats_file)
        assert stats["durations_s"] == []
        assert stats["failed_streak"] == 0
