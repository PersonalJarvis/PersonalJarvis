"""Recording preferences reach real MP4 encoding and respect each display's limits."""

from __future__ import annotations

import queue
import threading

import pytest
from pydantic import ValidationError

from jarvis.appshot.recording_options import RecordingOptions, effective_fps, output_size


@pytest.mark.parametrize("source,resolution,expected", [
    ((3840, 2160), "1080p", (1920, 1080)),
    ((3840, 2160), "1440p", (2560, 1440)),
    ((7680, 4320), "native", (7680, 4320)),
    ((1920, 1080), "2160p", (1920, 1080)),
    ((800, 600), "1080p", (800, 600)),
    ((3440, 1440), "1080p", (1920, 802)),
    ((2160, 3840), "1080p", (1080, 1920)),
    ((501, 299), "native", (502, 300)),
])
def test_resolution_fits_without_upscaling_or_stretching(source, resolution, expected):
    assert output_size(*source, resolution) == expected


@pytest.mark.parametrize("requested,hz,expected", [
    (120, 59.94, 60), (120, 119.88, 120), (120, 144, 120), (60, 75, 60),
    (30, 144, 30), (120, 0, 60), (120, None, 60), (120, float("nan"), 60),
    (60, 24, 24), (120, 100, 60),
])
def test_frame_rate_uses_selected_monitor(requested, hz, expected):
    assert effective_fps(requested, hz) == expected


def test_config_api_and_worker_share_defaults_and_validation(tmp_path):
    import json
    import tomllib
    from pathlib import Path

    from jarvis.core.config import AppshotConfig
    from jarvis.core.config_writer import set_appshot_settings
    from jarvis.ui.web.appshot_routes import SettingsPatch

    expected = {f"recording_{key}": value for key, value in RecordingOptions().model_dump().items()}
    block = AppshotConfig()
    assert {key: getattr(block, key) for key in expected} == expected
    assert SettingsPatch(**expected).model_dump(exclude_none=True) == expected
    path = tmp_path / "jarvis.toml"
    path.write_text("[appshot]\nsound = true\n", encoding="utf-8")
    changed = dict(recording_fps=120, recording_resolution="native", recording_bitrate_mbps=48,
                   recording_system_audio=True)
    set_appshot_settings(changed, path=path)
    stored = tomllib.loads(path.read_text(encoding="utf-8"))["appshot"]
    assert stored == {"sound": True, **changed}
    assert RecordingOptions.from_config(AppshotConfig(**stored)).fps == 120
    # Every locale exposes the same real preference labels.
    root = Path(__file__).resolve().parents[3] / "jarvis/ui/web/frontend/src/i18n/locales"
    keys = ["recording_resolution", "recording_fps", "recording_bitrate", "recording_system_audio"]
    for locale in ("en", "de", "es"):
        labels = json.loads((root / f"{locale}.json").read_text(encoding="utf-8"))["appshots"]
        assert all(labels[key] for key in keys)


@pytest.mark.parametrize("field,value", [
    ("fps", 0), ("fps", 240), ("resolution", "8k"), ("bitrate_mbps", 0),
    ("bitrate_mbps", 101), ("bitrate_mbps", 1.5), ("bitrate_mbps", True),
])
def test_invalid_quality_is_rejected_at_all_boundaries(field, value):
    from jarvis.core.config import AppshotConfig
    from jarvis.ui.web.appshot_routes import SettingsPatch

    for cls, values in ((RecordingOptions, {field: value}),
                        (AppshotConfig, {f"recording_{field}": value}),
                        (SettingsPatch, {f"recording_{field}": value})):
        with pytest.raises(ValidationError):
            cls(**values)


def _encoder(tmp_path, *, fps=60, size=(64, 48), resolution="native", audio=False):
    pytest.importorskip("av")
    qt = pytest.importorskip("PySide6.QtGui")
    from jarvis.appshot.video_encoder import VideoEncoder

    encoder = VideoEncoder(tmp_path / "capture.mp4", options=RecordingOptions(
        resolution=resolution, fps=fps, bitrate_mbps=7, system_audio=audio))
    # Deterministic producer: the test fills a short clip before the encoder runs.
    encoder.frames = queue.Queue()
    image = qt.QImage(*size, qt.QImage.Format.Format_RGBA8888)
    image.fill(qt.QColor("red"))
    for frame in range(fps):
        encoder.submit(image, frame / fps)
    encoder.stop(1.0)
    return encoder


@pytest.mark.parametrize("fps", [30, 60, 120])
def test_real_mp4_has_requested_frame_rate_and_duration(tmp_path, fps):
    av = pytest.importorskip("av")
    encoder = _encoder(tmp_path, fps=fps)
    encoder._run()
    assert encoder.error == ""
    with av.open(str(encoder.path)) as source:
        video = source.streams.video[0]
        assert video.average_rate == fps
        assert float(video.duration * video.time_base) == pytest.approx(1.0, abs=1 / fps)
        assert len(list(source.decode(video))) == fps
        assert not source.streams.audio


def test_real_mp4_downscales_the_pixels(tmp_path):
    av = pytest.importorskip("av")
    encoder = _encoder(tmp_path, fps=30, size=(2560, 1440), resolution="1080p")
    encoder._run()
    assert encoder.error == ""
    with av.open(str(encoder.path)) as source:
        frame = next(source.decode(video=0))
        assert (frame.width, frame.height) == (1920, 1080)


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
@pytest.mark.parametrize("offset", [0, 0.2])
def test_real_mp4_audio_survives_trim_and_speed_changes(tmp_path, monkeypatch, speed, offset):
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    from jarvis.appshot import recording_audio

    class Audio:
        def __init__(self, epoch):
            self.ready = threading.Event()
            self.stopping = threading.Event()
            self.error = ""
            self.chunks = queue.Queue()
            count = round((1 - offset) * 48000)
            wave = (np.sin(np.arange(count) * 440 * 2 * np.pi / 48000) * 0.1).astype("float32")
            self.chunks.put((np.stack([wave, wave]), round(offset * 48000)))

        def start(self):
            self.ready.set()

        def stop(self):
            self.stopping.set()

    monkeypatch.setattr(recording_audio, "SystemAudioCapture", Audio)
    encoder = _encoder(tmp_path, audio=True)
    encoder._run()
    assert encoder.error == ""
    with av.open(str(encoder.path)) as source:
        audio = source.streams.audio[0]
        assert audio.codec_context.sample_rate == 48000
        assert audio.codec_context.channels == 2
        assert float(audio.duration * audio.time_base) == pytest.approx(1 - offset, abs=0.05)
        data = np.concatenate([frame.to_ndarray() for frame in source.decode(audio)], axis=1)
        assert np.max(np.abs(data)) > 0.05

    from jarvis.appshot import recording, video_edit

    monkeypatch.setattr(recording, "recording_dir", lambda: tmp_path)
    monkeypatch.setattr(video_edit, "recording_dir", lambda: tmp_path)
    monkeypatch.setattr(video_edit, "_clips", {})
    original = tmp_path / ("a" * 32 + ".mp4")
    encoder.path.rename(original)
    clip = video_edit.export_clip("a" * 32, 0.1, 0.9, speed)
    with av.open(str(tmp_path / f"{clip}.mp4")) as source:
        sound = source.streams.audio[0]
        picture = source.streams.video[0]
        audio_duration = float(sound.duration * sound.time_base)
        video_duration = float(picture.duration * picture.time_base)
        assert video_duration == pytest.approx(0.8 / speed, abs=0.04)
        assert audio_duration == pytest.approx(video_duration, abs=0.08)
        assert picture.base_rate == min(60, round(60 * speed))
        assert any(np.max(np.abs(frame.to_ndarray())) > 0.05 for frame in source.decode(sound))


def test_audio_unavailable_never_falls_back_to_microphone(monkeypatch):
    from jarvis.appshot import recording_audio

    monkeypatch.setattr(recording_audio.sys, "platform", "darwin")
    assert not recording_audio.capability()["available"]


def test_duplicate_timestamps_do_not_stretch_video(tmp_path):
    encoder = _encoder(tmp_path)
    image = encoder.frames.queue[0][0]
    encoder.frames.put((image, 59 / 60))
    encoder._run()
    assert encoder.error == ""
    assert encoder.dropped_frames == 0  # A redundant early tick is not a lost source frame.


def test_capture_gaps_are_reported_without_speeding_up_playback(tmp_path):
    av = pytest.importorskip("av")
    encoder = _encoder(tmp_path)
    image = encoder.frames.queue[0][0]
    encoder.frames = queue.Queue()
    encoder.frames.put((image, 0.0))
    encoder.frames.put((image, 0.5))
    encoder._run()
    assert encoder.error == ""
    assert encoder.dropped_frames == 57
    with av.open(str(encoder.path)) as source:
        video = source.streams.video[0]
        assert float(video.duration * video.time_base) == pytest.approx(1, abs=0.03)


def test_hardware_encoder_failure_closes_candidate_before_software_fallback(tmp_path):
    from types import SimpleNamespace

    from jarvis.appshot.recording_codec import open_video

    attempts = []

    class Container:
        closed = False

        def add_stream(self, codec, **kwargs):
            self.codec = codec
            def open_codec():
                if codec == "h264_nvenc":
                    raise RuntimeError("Driver unavailable")
            return SimpleNamespace(codec_context=SimpleNamespace(open=open_codec))

        def close(self):
            self.closed = True

    def open_container(*args, **kwargs):
        container = Container()
        attempts.append(container)
        return container

    av = SimpleNamespace(codecs_available={"h264_nvenc", "libx264"}, open=open_container)
    container, stream = open_video(av, tmp_path / "test.partial", (1920, 1080), 60, 37000000)
    assert len(attempts) == 2 and attempts[0].closed
    assert container.codec == "libx264" and not container.closed
    assert stream.bit_rate == 37000000 and stream.pix_fmt == "yuv420p"


def test_subframe_clock_jitter_keeps_frames_and_editor_rate(tmp_path, monkeypatch):
    av = pytest.importorskip("av")
    from jarvis.appshot import recording, video_edit

    encoder = _encoder(tmp_path)
    image = encoder.frames.queue[0][0]
    encoder.frames = queue.Queue()
    # Two real consecutive frames can arrive inside one nominal frame slot.
    # Coarse 1/60 timestamps used to discard them and reduce apparent FPS.
    for index in range(60):
        at = index / 60 + (0.005 if index % 2 else -0.005)
        encoder.frames.put((image, max(0, at)))
    encoder._run()
    assert not encoder.error
    with av.open(str(encoder.path)) as source:
        assert len(list(source.decode(video=0))) >= 60
    encoder.path.rename(tmp_path / ("b" * 32 + ".mp4"))
    monkeypatch.setattr(recording, "recording_dir", lambda: tmp_path)
    monkeypatch.setattr(video_edit, "recording_dir", lambda: tmp_path)
    monkeypatch.setattr(video_edit, "_clips", {})
    result = video_edit.export_clip("b" * 32, 0.1, 0.9, 1)
    with av.open(str(tmp_path / f"{result}.mp4")) as source:
        assert source.streams.video[0].codec_context.framerate == 60


async def test_saved_display_details_do_not_collide_with_preview_geometry():
    import json
    from types import SimpleNamespace

    from jarvis.appshot.recording import RecordingService

    class Events:
        async def __aiter__(self):
            for phase in ("recording", "saved"):
                yield json.dumps({
                    "phase": phase, "width": 1920, "height": 1080, "fps": 60,
                    "bitrate_mbps": 12, "system_audio": True, "display_name": "Display 2",
                    "monitor": [-1920, 0, 1920, 1080], "refresh_hz": 60,
                }).encode("utf-8")

    async def wait():
        return 0

    service = RecordingService()
    await service._read(SimpleNamespace(stdout=Events(), stdin=None, wait=wait, returncode=0))
    state = service.status()
    assert state["phase"] == "saved"
    assert state["display_name"] == "Display 2" and state["fps"] == 60
    assert state["system_audio"] and "monitor" not in state


@pytest.mark.parametrize("codec", ["libx264", "mpeg4"])
def test_software_only_install_can_encode_without_a_gpu(tmp_path, monkeypatch, codec):
    av = pytest.importorskip("av")
    if codec not in av.codecs_available:
        pytest.skip(f"{codec} is not part of this PyAV build")
    monkeypatch.setattr(av, "codecs_available", {codec})
    encoder = _encoder(tmp_path, fps=120)
    encoder._run()
    assert not encoder.error
    with av.open(str(encoder.path)) as source:
        video = source.streams.video[0]
        assert video.average_rate == 120
        assert len(list(source.decode(video))) == 120


def test_requested_audio_failure_never_reports_a_silent_success(tmp_path, monkeypatch):
    from jarvis.appshot import recording_audio

    class UnavailableAudio:
        def __init__(self, epoch):
            self.error = "The output device is unavailable."
            self.ready = threading.Event()
            self.stopping = threading.Event()

        def start(self):
            self.ready.set()

        def stop(self):
            self.stopping.set()

    monkeypatch.setattr(recording_audio, "SystemAudioCapture", UnavailableAudio)
    encoder = _encoder(tmp_path, audio=True)
    encoder._run()
    assert encoder.done.is_set()
    assert "output device" in encoder.error
    assert not encoder.path.exists() and not encoder.partial.exists()


def test_delayed_first_frame_does_not_create_a_black_lead_in(tmp_path):
    av = pytest.importorskip("av")
    encoder = _encoder(tmp_path)
    image = encoder.frames.queue[0][0]
    encoder.frames = queue.Queue()
    encoder.frames.put((image, 0.2))
    encoder.frames.put((image, 0.3))
    encoder._run()
    assert not encoder.error
    with av.open(str(encoder.path)) as source:
        stream = source.streams.video[0]
        assert stream.start_time == 0
        assert next(source.decode(stream)).pts == 0
