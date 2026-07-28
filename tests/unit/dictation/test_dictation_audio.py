"""The audio sidecars kept for dictations that produced nothing usable.

Raw microphone audio is the most sensitive thing this application would ever
store, so the tests that matter most here are the ones about it going away
again: the retention caps, the purge, and the fact that an entry id can never
be used to write outside the audio directory.
"""

from __future__ import annotations

import os
import time
import wave
from pathlib import Path

import pytest

from jarvis.dictation.audio import (
    CHANNELS,
    MAX_AUDIO_BYTES,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    audio_exists,
    audio_path_for,
    default_audio_dir,
    delete_dictation_audio,
    load_dictation_audio,
    prune_audio,
    purge_dictation_audio,
    save_dictation_audio,
)


@pytest.fixture()
def audio_dir(tmp_path: Path) -> Path:
    return tmp_path / "dictation_audio"


def _pcm(seconds: float) -> bytes:
    return b"\x01\x02" * int(seconds * SAMPLE_RATE)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def test_saved_file_is_16k_mono_int16_wav(audio_dir: Path) -> None:
    path = save_dictation_audio("abc123", _pcm(0.5), directory=audio_dir)
    assert path is not None
    with wave.open(str(path), "rb") as handle:
        assert handle.getframerate() == SAMPLE_RATE
        assert handle.getnchannels() == CHANNELS
        assert handle.getsampwidth() == SAMPLE_WIDTH
        assert handle.getnframes() == int(0.5 * SAMPLE_RATE)


def test_round_trip_returns_the_same_pcm(audio_dir: Path) -> None:
    pcm = _pcm(0.25)
    path = save_dictation_audio("roundtrip", pcm, directory=audio_dir)
    assert path is not None
    assert load_dictation_audio(path) == pcm


def test_empty_pcm_writes_nothing(audio_dir: Path) -> None:
    assert save_dictation_audio("empty", b"", directory=audio_dir) is None
    assert audio_dir.exists() is False


def test_a_half_sample_tail_is_trimmed_instead_of_corrupting_the_frame(
    audio_dir: Path,
) -> None:
    path = save_dictation_audio("odd", b"\x01\x02\x03", directory=audio_dir)
    assert path is not None
    with wave.open(str(path), "rb") as handle:
        assert handle.getnframes() == 1


def test_audio_longer_than_the_ceiling_is_truncated_not_refused(
    audio_dir: Path,
) -> None:
    oversized = b"\x00\x01" * ((MAX_AUDIO_BYTES // 2) + 1000)
    path = save_dictation_audio("long", oversized, directory=audio_dir)
    assert path is not None
    assert len(load_dictation_audio(path)) == MAX_AUDIO_BYTES


def test_no_temporary_file_is_left_behind(audio_dir: Path) -> None:
    save_dictation_audio("clean", _pcm(0.1), directory=audio_dir)
    assert [p.name for p in audio_dir.iterdir()] == ["clean.wav"]


# --------------------------------------------------------------------------
# The id is untrusted input
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entry_id",
    ["../escape", "..\\escape", "a/b", "a\\b", "с:\\x"],
)
def test_a_hostile_entry_id_can_never_escape_the_audio_directory(
    entry_id: str, audio_dir: Path
) -> None:
    path = save_dictation_audio(entry_id, _pcm(0.1), directory=audio_dir)
    assert path is not None
    assert path.parent.resolve() == audio_dir.resolve()


@pytest.mark.parametrize("entry_id", ["", "   ", "..", "/", "///"])
def test_an_entry_id_with_nothing_usable_left_writes_nothing(
    entry_id: str, audio_dir: Path
) -> None:
    assert audio_path_for(entry_id, directory=audio_dir) is None
    assert save_dictation_audio(entry_id, _pcm(0.1), directory=audio_dir) is None


def test_the_path_is_deterministic(audio_dir: Path) -> None:
    first = audio_path_for("abc", directory=audio_dir)
    second = save_dictation_audio("abc", _pcm(0.1), directory=audio_dir)
    assert first == second


# --------------------------------------------------------------------------
# Reading a broken or absent file
# --------------------------------------------------------------------------


def test_a_missing_file_reads_as_empty_bytes(audio_dir: Path) -> None:
    assert load_dictation_audio(audio_dir / "nope.wav") == b""
    assert load_dictation_audio(None) == b""
    assert load_dictation_audio("") == b""


def test_a_file_that_is_not_a_wav_reads_as_empty_bytes(tmp_path: Path) -> None:
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"this is not a wav file at all")
    assert load_dictation_audio(broken) == b""


def test_audio_exists_is_tolerant(audio_dir: Path) -> None:
    assert audio_exists(None) is False
    assert audio_exists("") is False
    assert audio_exists(audio_dir / "nope.wav") is False
    path = save_dictation_audio("here", _pcm(0.1), directory=audio_dir)
    assert audio_exists(path) is True


# --------------------------------------------------------------------------
# Deleting — the part that matters
# --------------------------------------------------------------------------


def test_delete_is_idempotent(audio_dir: Path) -> None:
    path = save_dictation_audio("gone", _pcm(0.1), directory=audio_dir)
    assert path is not None
    assert delete_dictation_audio(path) is True
    assert delete_dictation_audio(path) is False
    assert delete_dictation_audio(None) is False


def test_purge_removes_every_sidecar(audio_dir: Path) -> None:
    for name in ("a", "b", "c"):
        save_dictation_audio(name, _pcm(0.1), directory=audio_dir)
    assert purge_dictation_audio(directory=audio_dir) == 3
    assert list(audio_dir.glob("*.wav")) == []


def test_purge_on_a_missing_directory_is_a_quiet_zero(tmp_path: Path) -> None:
    assert purge_dictation_audio(directory=tmp_path / "never-created") == 0


def test_prune_keeps_only_the_newest_files(audio_dir: Path) -> None:
    for index in range(5):
        path = save_dictation_audio(f"f{index}", _pcm(0.05), directory=audio_dir)
        assert path is not None
        os.utime(path, (time.time() - (5 - index) * 60, time.time() - (5 - index) * 60))
    assert prune_audio(max_files=2, retention_days=0, directory=audio_dir) == 3
    assert sorted(p.stem for p in audio_dir.glob("*.wav")) == ["f3", "f4"]


def test_prune_ages_files_out_regardless_of_the_count(audio_dir: Path) -> None:
    fresh = save_dictation_audio("fresh", _pcm(0.05), directory=audio_dir)
    stale = save_dictation_audio("stale", _pcm(0.05), directory=audio_dir)
    assert fresh is not None and stale is not None
    old = time.time() - (10 * 86_400)
    os.utime(stale, (old, old))
    assert prune_audio(max_files=100, retention_days=7, directory=audio_dir) == 1
    assert fresh.exists() is True
    assert stale.exists() is False


def test_prune_counts_each_file_once_when_both_caps_hit(audio_dir: Path) -> None:
    """A file past BOTH caps must not be counted as two deletions."""
    path = save_dictation_audio("both", _pcm(0.05), directory=audio_dir)
    assert path is not None
    old = time.time() - (10 * 86_400)
    os.utime(path, (old, old))
    assert prune_audio(max_files=0, retention_days=7, directory=audio_dir) == 1


def test_prune_with_both_caps_disabled_deletes_nothing(audio_dir: Path) -> None:
    save_dictation_audio("keep", _pcm(0.05), directory=audio_dir)
    assert prune_audio(max_files=0, retention_days=0, directory=audio_dir) == 0
    assert len(list(audio_dir.glob("*.wav"))) == 1


def test_prune_on_a_missing_directory_is_a_quiet_zero(tmp_path: Path) -> None:
    assert prune_audio(directory=tmp_path / "never-created") == 0


# --------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------


def test_default_directory_lives_under_the_user_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local only, never a synced folder, and resolved with pathlib."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    directory = default_audio_dir()
    assert directory.name == "dictation_audio"
    assert directory.parent.name == "data"
