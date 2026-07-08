"""``measure_mic_dbfs`` is the pure mic-level measurement reused by the CLI
diagnostics (``step_mic_level``) and the onboarding mic-level route
(``GET /api/settings/wake-word/mic-level``). It must never raise -- a
headless host / missing device / any capture error degrades to the honest
floor -120.0 dBFS instead of bubbling up.
"""
import pytest

from jarvis.speech import diagnose


@pytest.mark.asyncio
async def test_measure_mic_dbfs_no_device_returns_floor(monkeypatch):
    class _NoMic:
        async def __aenter__(self):
            raise OSError("no device")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(diagnose, "MicrophoneCapture", lambda: _NoMic())
    val = await diagnose.measure_mic_dbfs(duration_s=0.1)
    assert val == -120.0  # honest floor, never raises


@pytest.mark.asyncio
async def test_measure_mic_dbfs_never_raises_on_unexpected_error(monkeypatch):
    """Any capture-time exception (not just "no device") still degrades honestly."""

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self):
            raise RuntimeError("PortAudio exploded")

    monkeypatch.setattr(diagnose, "MicrophoneCapture", lambda: _Boom())
    val = await diagnose.measure_mic_dbfs(duration_s=0.1)
    assert val == -120.0


@pytest.mark.asyncio
async def test_measure_mic_dbfs_reports_loud_signal(monkeypatch):
    """A real (mocked) mic stream yields a max dBFS well above the floor."""
    import numpy as np

    class _Chunk:
        def __init__(self, pcm: bytes) -> None:
            self.pcm = pcm

    class _LoudMic:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def stream(self):
            # A single loud full-scale int16 chunk.
            arr = np.full(160, 32767, dtype=np.int16)
            yield _Chunk(arr.tobytes())

    monkeypatch.setattr(diagnose, "MicrophoneCapture", lambda: _LoudMic())
    val = await diagnose.measure_mic_dbfs(duration_s=1.0)
    assert val > -1.0  # near 0 dBFS for a full-scale signal


@pytest.mark.asyncio
async def test_measure_mic_dbfs_invokes_on_frame_callback(monkeypatch):
    """The optional on_frame hook (used by step_mic_level's CLI bar) fires per
    chunk with (dbfs, running_max, n_samples) and never affects the return value."""
    import numpy as np

    class _Chunk:
        def __init__(self, pcm: bytes) -> None:
            self.pcm = pcm

    class _OneShotMic:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def stream(self):
            arr = np.full(160, 32767, dtype=np.int16)
            yield _Chunk(arr.tobytes())

    monkeypatch.setattr(diagnose, "MicrophoneCapture", lambda: _OneShotMic())
    calls = []
    val = await diagnose.measure_mic_dbfs(
        duration_s=1.0, on_frame=lambda dbfs, running_max, n: calls.append((dbfs, running_max, n))
    )
    assert len(calls) == 1
    dbfs, running_max, n = calls[0]
    assert running_max == val
    assert n == 160
