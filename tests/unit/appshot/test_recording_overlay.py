"""Recording boundary placement, saved-video actions and the shared card handoff."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.appshot.recording_geometry import elapsed_label, selected_rect, toolbar_rect
from jarvis.cu.indicator import protocol


def test_selection_and_toolbar_follow_a_negative_origin_monitor():
    screen = (-1920, 100, 1920, 1080)
    selection = selected_rect(screen, (0.25, 0.2, 0.5, 0.4))
    assert selection == (-1440, 316, 960, 432)
    bar = toolbar_rect(selection, screen, (240, 44))
    assert bar == (-1080, 758, 240, 44)


def test_full_screen_controls_remain_inside_the_work_area():
    bar = toolbar_rect((0, 0, 3840, 2160), (0, 0, 3840, 2100), (260, 44))
    assert 0 <= bar[0] and bar[0] + bar[2] <= 3840
    assert 0 <= bar[1] and bar[1] + bar[3] <= 2100


def test_controls_use_space_above_a_selection_at_the_bottom_edge():
    assert toolbar_rect((100, 800, 500, 280), (0, 0, 1920, 1080), (240, 44))[1] == 746


@pytest.mark.parametrize("seconds,text", [(0, "00:00"), (61.9, "01:01"), (3601, "01:00:01")])
def test_recording_clock_is_elapsed_time(seconds, text):
    assert elapsed_label(seconds) == text


def test_video_poster_keeps_the_first_frame_when_later_frames_change():
    qt = pytest.importorskip("PySide6.QtGui")
    from jarvis.appshot.video_encoder import FirstFramePoster

    poster = FirstFramePoster()
    first = qt.QImage(1280, 720, qt.QImage.Format.Format_RGB32)
    first.fill(qt.QColor("red"))
    poster.observe(first)
    first.fill(qt.QColor("blue"))
    poster.observe(first)
    assert poster.image.width() == 560
    assert poster.image.pixelColor(100, 100).red() == 255
    assert poster.image.pixelColor(100, 100).blue() == 0


async def test_video_preview_uses_the_shared_sidecar_without_overwriting_a_screenshot(monkeypatch):
    from jarvis.cu.indicator.controller import CUIndicatorController

    controller = CUIndicatorController(SimpleNamespace())
    controller._card_image_b64 = "pending-screenshot"
    messages = []
    monkeypatch.setattr(controller, "_border_capability", lambda: (True, ""))
    monkeypatch.setattr(
        controller, "_spawn_sidecar", lambda: setattr(controller, "_proc", object())
    )
    monkeypatch.setattr(controller, "_schedule_idle_quit", lambda: None)
    monkeypatch.setattr("jarvis.cu.indicator.capture_guard.register_hook", lambda _hook: None)

    def send(command, timeout, **payload):
        messages.append((command, payload))
        return True

    monkeypatch.setattr(controller, "_send_and_wait", send)
    assert await controller.show_recording(
        recording_id="a" * 32,
        video_path="video.mp4",
        thumb_b64="jpeg",
        monitor=[0, 0, 1920, 1080],
        rect=[0.1, 0.2, 0.4, 0.5],
        screen_name="display",
        duration_s=12,
        rest_ms=6000,
        labels={},
    )
    assert messages[0][0] == protocol.CMD_RECORDING
    assert messages[0][1]["id"] == "recording:" + "a" * 32
    assert messages[0][1]["rect"] == [0.1, 0.2, 0.4, 0.5]
    assert controller._card_image_b64 == "pending-screenshot"


def test_saving_a_video_never_overwrites_an_existing_export(monkeypatch, tmp_path):
    from jarvis.appshot import recording, recording_cards

    recording_id = "a" * 32
    library = tmp_path / "library"
    library.mkdir()
    (library / f"{recording_id}.mp4").write_bytes(b"video-content")
    monkeypatch.setattr(recording, "recording_dir", lambda: library)
    monkeypatch.setattr(recording_cards.time, "strftime", lambda _fmt: "stamp")
    target = tmp_path / "Downloads"
    first = recording_cards.save_recording(recording_id, target)
    second = recording_cards.save_recording(recording_id, target)
    assert first != second
    assert first.read_bytes() == second.read_bytes() == b"video-content"
    assert recording_cards.save_recording("../outside", target) is None


def test_failed_video_export_removes_only_its_incomplete_copy(monkeypatch, tmp_path):
    from jarvis.appshot import recording, recording_cards

    (tmp_path / f"{'a' * 32}.mp4").write_bytes(b"video")
    monkeypatch.setattr(recording, "recording_dir", lambda: tmp_path)

    def failed_copy(source, target, **kwargs):
        target.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(recording_cards.shutil, "copyfileobj", failed_copy)
    exports = tmp_path / "exports"
    with pytest.raises(OSError, match="disk full"):
        recording_cards.save_recording("a" * 32, exports)
    assert not list(exports.iterdir())
    assert recording.recording_file("a" * 32).read_bytes() == b"video"


async def test_video_click_never_opens_the_screenshot_editor(monkeypatch):
    from jarvis.appshot import recording_cards
    from jarvis.cu.indicator.controller import CUIndicatorController

    opened = []
    monkeypatch.setattr(recording_cards, "open_recording", lambda key: opened.append(key))
    controller = CUIndicatorController(SimpleNamespace())
    await controller._recording_action("open", "recording:" + "b" * 32)
    assert opened == ["b" * 32]


async def test_recording_card_events_reach_the_video_action_handler(monkeypatch):
    from jarvis.cu.indicator.controller import CUIndicatorController

    controller = CUIndicatorController(SimpleNamespace())
    calls = []

    async def action(name, key):
        calls.append((name, key))

    monkeypatch.setattr(controller, "_recording_action", action)
    for event in (protocol.EVENT_RECORDING_OPEN, protocol.EVENT_RECORDING_SAVE):
        payload = protocol.decode_event(protocol.encode_event(event, id="recording:" + "a" * 32))
        controller._handle_sidecar_event(payload)
    await asyncio.sleep(0)
    assert calls == [("open", "recording:" + "a" * 32), ("save", "recording:" + "a" * 32)]


async def test_missing_preview_does_not_invalidate_a_saved_video(monkeypatch, tmp_path):
    from jarvis.appshot import recording, recording_cards

    monkeypatch.setattr(recording, "recording_dir", lambda: tmp_path)
    (tmp_path / f"{'c' * 32}.mp4").write_bytes(b"video")
    monkeypatch.setattr("jarvis.cu.indicator.controller.get_indicator_controller", lambda: None)
    assert not await recording_cards.show_recording_preview("c" * 32, {"preview": True})
    assert recording.recording_file("c" * 32).read_bytes() == b"video"
