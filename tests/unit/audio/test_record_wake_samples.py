"""The recorder still writes exactly what the wake provider consumes.

The meter was added around the capture, and the capture itself changed from a
blocking `sd.rec` to a callback stream. That is the risky part: the training
side reads these files unchanged, so a WAV that came out at the wrong rate,
width or channel count would break the wake word in a way no test of the meter
would notice.

The audio device is replaced by a hand-written fake — no `unittest.mock`, per
the repo rule — so this runs on a machine with no microphone.
"""
from __future__ import annotations

import importlib.util
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "record_wake_samples.py"


def _load_recorder(monkeypatch, phrase: str = "Hey Test"):
    """Import the script as a module, with argv set the way a user would."""
    monkeypatch.setattr(sys, "argv", ["record_wake_samples.py", phrase, "2"])
    spec = importlib.util.spec_from_file_location("_wake_recorder", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeStream:
    """Stands in for `sd.InputStream`, delivering one prepared block.

    Real capture hands blocks to the callback from its own thread; here the
    block is delivered on `__enter__`, which is enough because the recorder
    only ever reads what has accumulated.
    """

    def __init__(self, block: np.ndarray, callback, status: str = ""):
        self._block = block
        self._callback = callback
        self._status = status

    def __enter__(self):
        self._callback(self._block, len(self._block), None, self._status)
        return self

    def __exit__(self, *exc):
        return False


class FakeSoundDevice:
    def __init__(self, block: np.ndarray, status: str = ""):
        self._block = block
        self._status = status
        self.requested: dict = {}

    def InputStream(self, **kwargs):  # noqa: N802 — mirrors the real API name
        self.requested = kwargs
        return FakeStream(self._block, kwargs["callback"], self._status)


@pytest.fixture
def recorder(monkeypatch):
    return _load_recorder(monkeypatch)


def _install_fake(monkeypatch, recorder, block: np.ndarray, status: str = ""):
    fake = FakeSoundDevice(block, status)
    monkeypatch.setattr(recorder, "_import_sounddevice", lambda: fake)
    # The loop is wall-clock bound; collapse it so the test does not wait 2 s.
    monkeypatch.setattr(recorder, "DUR", 0.0)
    return fake


def test_importing_the_script_does_not_start_recording(monkeypatch):
    # It used to call main() at module scope, so merely importing it opened the
    # microphone. Loading it in this test at all is the assertion.
    recorder = _load_recorder(monkeypatch)
    assert callable(recorder.main)


def test_the_wav_is_16khz_mono_int16(monkeypatch, recorder, tmp_path):
    block = (np.sin(np.arange(3200) / 20) * 8000).astype(np.int16)
    _install_fake(monkeypatch, recorder, block)
    path = tmp_path / "sample.wav"

    recorder.record_one(str(path), label="sample 1")

    with wave.open(str(path), "rb") as wf:
        assert wf.getframerate() == 16_000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        written = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    # Byte-identical to what the device delivered: the meter observes, it does
    # not filter, normalize or gate.
    assert np.array_equal(written, block)


def test_the_capture_is_requested_at_the_providers_format(monkeypatch, recorder, tmp_path):
    fake = _install_fake(monkeypatch, recorder, np.zeros(1600, dtype=np.int16))
    recorder.record_one(str(tmp_path / "s.wav"), label="sample 1")
    assert fake.requested["samplerate"] == 16_000
    assert fake.requested["channels"] == 1
    assert fake.requested["dtype"] == "int16"


def test_a_clipped_take_comes_back_flagged(monkeypatch, recorder, tmp_path):
    full = np.full(1600, 32767, dtype=np.int16)
    _install_fake(monkeypatch, recorder, full)
    quality = recorder.record_one(str(tmp_path / "s.wav"), label="sample 1")
    assert quality.clipped
    assert not quality.usable


def test_a_dropped_frame_warning_is_reported_not_swallowed(
    monkeypatch, recorder, tmp_path, capsys
):
    _install_fake(monkeypatch, recorder, np.zeros(1600, dtype=np.int16), status="overflow")
    recorder.record_one(str(tmp_path / "s.wav"), label="sample 1")
    # A gapped recording that says nothing is how an unexplained bad sample
    # gets into the training set.
    assert "overflow" in capsys.readouterr().out


def test_a_recording_with_no_audio_at_all_still_writes_a_file(
    monkeypatch, recorder, tmp_path
):
    _install_fake(monkeypatch, recorder, np.zeros(0, dtype=np.int16))
    path = tmp_path / "s.wav"
    quality = recorder.record_one(str(path), label="sample 1")
    assert path.exists()
    assert not quality.usable


def test_the_slug_is_filesystem_safe(monkeypatch):
    recorder = _load_recorder(monkeypatch, phrase="Hey, Jarvis!")
    assert recorder.SLUG == "hey_jarvis"


def test_a_re_record_target_is_parsed_one_based(recorder):
    assert recorder._parse_target("2", 5) == [1]
    assert recorder._parse_target("0", 5) == []
    assert recorder._parse_target("6", 5) == []
    assert recorder._parse_target("not a number", 5) == []
