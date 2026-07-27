"""The gate that skipped the very files it was written for.

Forensic 2026-07-27. A folder source pointed at the directory the app itself
lives in produced a 236 131-item corpus. 218 419 of those items — 92 % of
everything the knowledge base held — were wake-word debug clips the app had
recorded of itself into ``data/wake_debug``, and 220 520 items sat queued for
a media-enrichment model call each.

``media_enrich.PROGRAM_DIR_NAMES`` already existed, and its own docstring
named those clips as the measured motivation. It was only ever consulted on
the image path. The recordings that motivated the rule were exempt from it.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.ultrawiki.connectors.local_folder import _is_own_data_dir, _own_data_dirs
from jarvis.ultrawiki.media_enrich import (
    MIN_RECORDING_BYTES,
    skip_reason_for_image,
    skip_reason_for_recording,
)

BIG = 4 * 1024 * 1024


class TestRecordingProvenance:
    def test_the_wake_debug_clips_are_skipped(self) -> None:
        """The exact path 218 419 items came from."""
        reason = skip_reason_for_recording(
            "Personal Jarvis/data/wake_debug/wake_000001_rms0.003_Deine_Hohen_.wav",
            size_bytes=BIG,
        )
        assert reason
        assert "data/" in reason

    def test_a_real_voice_note_survives(self) -> None:
        """The rule must cost nothing to anything a person actually recorded."""
        assert (
            skip_reason_for_recording(
                "Voice Memos/2026-07-12 call with the accountant.m4a", size_bytes=BIG
            )
            == ""
        )

    def test_a_short_but_real_recording_survives(self) -> None:
        """A brief voice note is still a voice note; only fragments go."""
        assert skip_reason_for_recording("Notes/idea.m4a", size_bytes=64 * 1024) == ""

    def test_a_fragment_is_skipped(self) -> None:
        assert skip_reason_for_recording("Notes/blip.wav", size_bytes=200)

    def test_a_folder_the_user_named_data_is_judged_by_path_not_name(self) -> None:
        """`data` is far too common a folder name to blocklist outright...

        ...on the ENRICHMENT side it is a deliberate exception, because a model
        call is expensive and the folder is machine output either way. The
        distinction that protects the user's own files lives in the walker
        (see :class:`TestOwnDataDirIsSkipped`), which matches a resolved path.
        """
        assert skip_reason_for_recording("data/whatever.wav", size_bytes=BIG)

    def test_the_filename_alone_never_decides(self) -> None:
        """A file merely CALLED data.wav is not machine output (AP-27's logic)."""
        assert skip_reason_for_recording("Recordings/data.wav", size_bytes=BIG) == ""

    def test_windows_separators_are_understood(self) -> None:
        assert skip_reason_for_recording(
            r"Personal Jarvis\data\wake_debug\wake_01.wav", size_bytes=BIG
        )

    def test_the_image_gate_still_behaves(self) -> None:
        """The refactor that added the audio gate must not move the image one."""
        assert skip_reason_for_image("data/wake_debug/x.png", size_bytes=BIG)
        assert skip_reason_for_image("Photos/holiday.jpg", size_bytes=BIG) == ""
        assert skip_reason_for_image("Photos/icon.png", size_bytes=512)

    def test_the_floor_is_permissive_enough_for_a_second_of_speech(self) -> None:
        """16-bit mono at 16 kHz is 32 kB a second — the floor must sit under it."""
        assert MIN_RECORDING_BYTES < 32 * 1024


class TestOwnDataDirIsSkipped:
    """The walker half: never ingest the directory this install writes to."""

    def test_the_apps_own_data_dir_is_recognised(self) -> None:
        own = _own_data_dirs()
        assert own, "the app must be able to resolve its own data dir"
        assert _is_own_data_dir(Path(own[0]), own)

    def test_an_unrelated_folder_named_data_is_not(self, tmp_path: Path) -> None:
        """The reason this matches a PATH and not the name `data`.

        People keep research data, exports and project files in a folder
        called exactly that. A name rule would swallow them silently, which
        is a worse failure than the one it fixes.
        """
        elsewhere = tmp_path / "Research" / "data"
        elsewhere.mkdir(parents=True)
        assert _is_own_data_dir(elsewhere, _own_data_dirs()) is False

    def test_a_missing_path_never_raises(self, tmp_path: Path) -> None:
        assert _is_own_data_dir(tmp_path / "nope" / "gone", _own_data_dirs()) is False

    def test_no_resolvable_data_dir_skips_nothing(self, tmp_path: Path) -> None:
        assert _is_own_data_dir(tmp_path, ()) is False
