"""``[appshot].keep_newest``: old screenshots and recordings are deleted automatically."""

from __future__ import annotations

import io
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from jarvis.appshot import library, recording, retention
from jarvis.appshot.store import Appshot
from jarvis.core.config import AppshotConfig, JarvisXConfig
from jarvis.core.config_writer import APPSHOT_SETTING_KEYS, JARVISX_SETTING_KEYS


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), (20, 120, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def _shot(shot_id: str, taken_at: float) -> Appshot:
    return Appshot(
        id=shot_id, image=_png(), mime="image/png", width=8, height=6, label="window",
        app_name="Editor", note="", ui_text="", trigger="hotkey", taken_at=taken_at,
    )


@pytest.fixture
def recordings(tmp_path, monkeypatch):
    folder = tmp_path / "recordings"
    folder.mkdir()
    monkeypatch.setattr(recording, "recording_dir", lambda: folder)
    return folder


def _recording(folder, index: int) -> str:
    recording_id = f"{index:032x}"
    path = folder / f"{recording_id}.mp4"
    path.write_bytes(b"mp4")
    os.utime(path, (1000 + index, 1000 + index))
    return recording_id


def test_the_setting_is_off_by_default_and_writable() -> None:
    assert AppshotConfig().keep_newest == 0
    assert JarvisXConfig().keep_newest == 0
    assert "keep_newest" in APPSHOT_SETTING_KEYS
    assert "keep_newest" in JARVISX_SETTING_KEYS


def test_keep_newest_reads_the_config_and_tolerates_junk() -> None:
    assert retention.keep_newest(SimpleNamespace(appshot=SimpleNamespace(keep_newest=10))) == 10
    assert retention.keep_newest(SimpleNamespace(appshot=SimpleNamespace(keep_newest="x"))) == 0
    assert retention.keep_newest(SimpleNamespace()) == 0


def test_only_the_newest_screenshots_stay(recordings) -> None:
    for index in range(5):
        library.save(_shot(f"s{index}", taken_at=float(index)))

    assert library.prune(2) == 3
    assert {item.id for item in library.list_items()} == {"s3", "s4"}


def test_only_the_newest_recordings_stay_and_foreign_files_are_left(recordings) -> None:
    ids = [_recording(recordings, index) for index in range(4)]
    (recordings / "notes.mp4").write_bytes(b"not a recording")
    (recordings / f"{'f' * 32}.partial").write_bytes(b"still being written")

    assert retention.prune_recordings(1) == 3
    assert sorted(path.name for path in recordings.iterdir()) == sorted(
        [f"{ids[-1]}.mp4", "notes.mp4", f"{'f' * 32}.partial"]
    )


def test_zero_keeps_everything(recordings) -> None:
    for index in range(3):
        library.save(_shot(f"s{index}", taken_at=float(index)))
        _recording(recordings, index)

    assert retention.apply(0) == 0
    assert len(library.list_items()) == 3
    assert len(list(recordings.glob("*.mp4"))) == 3


def test_apply_trims_both_kinds(recordings) -> None:
    for index in range(3):
        library.save(_shot(f"s{index}", taken_at=float(index)))
        _recording(recordings, index)

    assert retention.apply(2) == 2
    assert len(library.list_items()) == 2
    assert len(list(recordings.glob("*.mp4"))) == 2
