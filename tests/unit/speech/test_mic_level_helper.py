"""``measure_mic_dbfs`` is the pure mic-level measurement reused by the CLI
diagnostics (``step_mic_level``) and the onboarding mic-level route
(``GET /api/settings/wake-word/mic-level``). It must never raise -- a
headless host / missing device / any capture error degrades to the honest
floor -120.0 dBFS instead of bubbling up. The one exception is a microphone the
OS has not granted: that is NOT "no microphone", so ``MicrophoneAccessError``
propagates (carrying the permission layer's answer) and the CLI reports it.
"""
import numpy as np
import pytest

from jarvis.audio.capture import MicrophoneAccessError
from jarvis.platform.permission_service import PermissionOutcome
from jarvis.platform.permissions import PermissionId
from jarvis.speech import diagnose
from tests.fakes.fake_permission_service import make_result


@pytest.mark.asyncio
async def test_measure_mic_dbfs_no_device_returns_floor(monkeypatch):
    class _NoMic:
        async def __aenter__(self):
            raise OSError("no device")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(diagnose, "MicrophoneCapture", lambda **_kwargs: _NoMic())
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

    monkeypatch.setattr(diagnose, "MicrophoneCapture", lambda **_kwargs: _Boom())
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

    monkeypatch.setattr(diagnose, "MicrophoneCapture", lambda **_kwargs: _LoudMic())
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

    monkeypatch.setattr(diagnose, "MicrophoneCapture", lambda **_kwargs: _OneShotMic())
    calls = []
    val = await diagnose.measure_mic_dbfs(
        duration_s=1.0, on_frame=lambda dbfs, running_max, n: calls.append((dbfs, running_max, n))
    )
    assert len(calls) == 1
    dbfs, running_max, n = calls[0]
    assert running_max == val
    assert n == 160


class _DeniedMic:
    """A capture whose open is refused by the permission layer."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        result = make_result(PermissionId.MICROPHONE, PermissionOutcome.DENIED)
        raise MicrophoneAccessError(result.user_detail, result=result)

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_a_denied_microphone_is_not_reported_as_no_microphone(monkeypatch):
    monkeypatch.setattr(diagnose, "MicrophoneCapture", _DeniedMic)

    with pytest.raises(MicrophoneAccessError) as refused:
        await diagnose.measure_mic_dbfs(duration_s=0.1)

    assert refused.value.result.outcome is PermissionOutcome.DENIED  # not the -120.0 floor


@pytest.mark.asyncio
async def test_measure_hands_the_gesture_flag_to_the_capture(monkeypatch):
    seen: list[dict] = []

    class _Recorder(_DeniedMic):
        def __init__(self, **kwargs) -> None:
            seen.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(diagnose, "MicrophoneCapture", _Recorder)

    for interactive in (False, True):
        with pytest.raises(MicrophoneAccessError):
            await diagnose.measure_mic_dbfs(
                duration_s=0.1, interactive=interactive, permission_wait_s=3.0
            )

    assert [(k["permission_feature"], k["interactive"], k["permission_wait_s"]) for k in seen] == [
        ("voice", False, 3.0),
        ("voice", True, 3.0),
    ]


@pytest.mark.asyncio
async def test_the_default_measurement_never_asks_the_os(monkeypatch):
    """A route that already ensured the permission measures without a second ask."""
    seen: list[dict] = []

    class _Recorder(_DeniedMic):
        def __init__(self, **kwargs) -> None:
            seen.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(diagnose, "MicrophoneCapture", _Recorder)
    with pytest.raises(MicrophoneAccessError):
        await diagnose.measure_mic_dbfs(duration_s=0.1)

    assert seen[0]["interactive"] is False


@pytest.mark.asyncio
async def test_cli_mic_step_says_not_granted_and_returns_no_measurement(monkeypatch, capsys):
    monkeypatch.setattr(diagnose, "MicrophoneCapture", _DeniedMic)

    level = await diagnose.step_mic_level(duration_s=0.1)

    out = capsys.readouterr().out
    assert level is None  # a permission answer, not a silent room
    assert "Microphone access is not granted" in out
    assert "NO audio" not in out  # never the "check your mic selection" advice
    assert "turned off for Personal Jarvis" in out  # the layer's own sentence


@pytest.mark.asyncio
async def test_cli_mic_step_still_measures_a_granted_microphone(monkeypatch, capsys):
    class _Chunk:
        pcm = np.full(160, 16000, dtype=np.int16).tobytes()

    class _Mic:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def stream(self):
            yield _Chunk()

    monkeypatch.setattr(diagnose, "MicrophoneCapture", _Mic)

    level = await diagnose.step_mic_level(duration_s=1.0)

    assert level is not None and level > -20.0
    assert "Samples received: 160" in capsys.readouterr().out
