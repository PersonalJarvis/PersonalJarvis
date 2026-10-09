"""The video editor: a played recording opens in the app's own editor window,
and its trim and speed are written as a new recording beside the original."""

from __future__ import annotations

from fractions import Fraction

import pytest

SOURCE = "a" * 32


@pytest.fixture
def library(monkeypatch, tmp_path):
    from jarvis.appshot import recording

    monkeypatch.setattr(recording, "recording_dir", lambda: tmp_path)
    monkeypatch.setattr("jarvis.appshot.video_edit.recording_dir", lambda: tmp_path)
    monkeypatch.setattr("jarvis.appshot.video_edit._clips", {})
    return tmp_path


def _write_video(path, seconds: float = 3.0, fps: int = 30) -> None:
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    with av.open(str(path), "w", format="mp4") as container:
        codec = "libx264" if "libx264" in av.codecs_available else "mpeg4"
        stream = container.add_stream(codec, rate=fps)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for index in range(round(seconds * fps)):
            pixels = np.full((48, 64, 3), index % 255, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, fps)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


async def test_playing_a_recording_opens_the_video_editor_window(monkeypatch):
    from jarvis.appshot import editor_window, recording_cards

    queries, played = [], []
    editor_window.register_window_opener(lambda query: queries.append(query) or {"ok": True})
    monkeypatch.setattr(recording_cards, "open_recording", lambda key: played.append(key))
    try:
        assert await recording_cards.run_recording_card_action("open", "b" * 32) == ""
    finally:
        editor_window.register_window_opener(None)
    assert queries == ["recording=" + "b" * 32]
    assert played == []


async def test_without_a_window_the_system_player_still_plays_it(monkeypatch):
    from jarvis.appshot import editor_window, recording_cards

    editor_window.register_window_opener(None)
    played = []
    monkeypatch.setattr(recording_cards, "open_recording", lambda key: played.append(key) or True)
    assert await recording_cards.run_recording_card_action("open", "b" * 32) == ""
    assert played == ["b" * 32]


async def test_only_recording_ids_reach_the_window_url():
    from jarvis.appshot import editor_window

    queries = []
    editor_window.register_window_opener(lambda query: queries.append(query) or {"ok": True})
    try:
        assert not await editor_window.open_recording_window("../../etc/passwd")
        assert not await editor_window.open_recording_window("abc")
    finally:
        editor_window.register_window_opener(None)
    assert queries == []


def test_a_trimmed_faster_clip_is_a_new_recording(library):
    pytest.importorskip("av")
    from jarvis.appshot.recording import recording_file
    from jarvis.appshot.video_edit import export_clip, probe_duration

    _write_video(library / f"{SOURCE}.mp4")
    original = (library / f"{SOURCE}.mp4").read_bytes()
    clip = export_clip(SOURCE, 0.5, 2.5, 2.0)
    assert clip != SOURCE and recording_file(clip) is not None
    assert probe_duration(clip) == pytest.approx(1.0, abs=0.1)
    # The original is never touched, and the same edit is not encoded twice.
    assert (library / f"{SOURCE}.mp4").read_bytes() == original
    assert export_clip(SOURCE, 0.5, 2.5, 2.0) == clip
    assert not list(library.glob("*.partial"))


def test_slow_motion_stretches_the_clip(library):
    pytest.importorskip("av")
    from jarvis.appshot.video_edit import export_clip, probe_duration

    _write_video(library / f"{SOURCE}.mp4")
    assert probe_duration(export_clip(SOURCE, 1.0, 2.0, 0.5)) == pytest.approx(2.0, abs=0.1)


def test_an_unchanged_edit_keeps_the_original(library):
    pytest.importorskip("av")
    from jarvis.appshot.video_edit import export_clip, probe_duration

    _write_video(library / f"{SOURCE}.mp4")
    assert export_clip(SOURCE, 0.0, probe_duration(SOURCE), 1.0) == SOURCE
    assert len(list(library.glob("*.mp4"))) == 1


def test_bad_edits_are_refused_with_a_clear_reason(library):
    pytest.importorskip("av")
    from jarvis.appshot.video_edit import ClipError, export_clip

    _write_video(library / f"{SOURCE}.mp4")
    with pytest.raises(ClipError, match="speed"):
        export_clip(SOURCE, 0.0, 1.0, 3.0)
    with pytest.raises(ClipError, match="too short"):
        export_clip(SOURCE, 1.0, 1.1, 1.0)
    with pytest.raises(FileNotFoundError):
        export_clip("c" * 32, 0.0, 1.0, 1.0)
