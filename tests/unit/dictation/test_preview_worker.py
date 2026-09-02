"""The out-of-process dictation preview: protocol, proxy, and fallbacks.

The worker exists so the CUDA DLL load (cublas, ~600 MB, held Windows loader
lock, 15-30 s whole-app freezes on a cold boot) never happens in the desktop
process. These tests run the protocol in-memory — no subprocess, no model.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pytest

import jarvis.dictation.local_preview as local_preview_mod
from jarvis.dictation.local_preview import LocalPreviewTranscriber, _WorkerModel
from jarvis.dictation.preview_worker import read_message, serve, write_message


def _messages_from(buffer: bytes) -> list[dict[str, Any]]:
    stream = io.BytesIO(buffer)
    out: list[dict[str, Any]] = []
    while True:
        message = read_message(stream)
        if message is None:
            return out
        out.append(message)


class _FakeEngine:
    """Stands in for LocalPreviewTranscriber inside the worker."""

    def __init__(self, model_name: str, *, ready: bool = True) -> None:
        self._model_name = model_name
        self.ready = ready
        self._engine_device = "cpu"
        self._engine_compute = "int8"
        self.requests: list[tuple[bytes, str | None]] = []

    def _load_model(self) -> None:
        return None

    def _transcribe_sync(self, pcm: bytes, language: str | None) -> tuple[str, str, float]:
        self.requests.append((pcm, language))
        if language == "boom":
            raise RuntimeError("bad clip")
        return f"heard {len(pcm)} bytes", "de", 0.9


def _request_bytes(pcm: bytes, language: str | None) -> bytes:
    header = io.BytesIO()
    write_message(header, {"n": len(pcm), "language": language})
    return header.getvalue() + pcm


def test_serve_answers_ready_then_transcribes(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _FakeEngine("base")
    monkeypatch.setattr(local_preview_mod, "LocalPreviewTranscriber", lambda name: engine)
    stdin = io.BytesIO(_request_bytes(b"\x00\x01" * 100, "de") + _request_bytes(b"", None))
    stdout = io.BytesIO()

    assert serve(stdin, stdout, "base") == 0

    ready, first, second = _messages_from(stdout.getvalue())
    assert ready == {"ready": True, "device": "cpu", "compute": "int8"}
    assert first == {
        "text": "heard 200 bytes",
        "language": "de",
        "probability": 0.9,
        "segments": [],
    }
    assert second["text"] == "heard 0 bytes"
    assert engine.requests[0][1] == "de"


def test_serve_reports_a_failed_build_and_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_preview_mod,
        "LocalPreviewTranscriber",
        lambda name: _FakeEngine(name, ready=False),
    )
    stdout = io.BytesIO()

    assert serve(io.BytesIO(b""), stdout, "base") == 1

    (message,) = _messages_from(stdout.getvalue())
    assert message["ready"] is False


def test_serve_turns_a_bad_clip_into_an_error_message_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine("base")
    monkeypatch.setattr(local_preview_mod, "LocalPreviewTranscriber", lambda name: engine)
    stdin = io.BytesIO(_request_bytes(b"\x00\x00", "boom") + _request_bytes(b"\x00\x00", "de"))
    stdout = io.BytesIO()

    assert serve(stdin, stdout, "base") == 0

    _ready, error, ok = _messages_from(stdout.getvalue())
    assert "bad clip" in error["error"]
    assert ok["language"] == "de"


class _FakePipeProc:
    """A Popen double whose pipes loop back through the worker protocol."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.stdin = io.BytesIO()
        out = io.BytesIO()
        for response in responses:
            write_message(out, response)
        out.seek(0)
        self.stdout = out
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def test_worker_model_speaks_the_protocol_and_reports_the_language() -> None:
    proc = _FakePipeProc([{"text": "hallo", "language": "de", "probability": 0.8}])
    model = _WorkerModel(proc, "cuda", "float16")  # type: ignore[arg-type]

    samples = np.zeros(160, dtype=np.float32)
    segments, info = model.transcribe(samples, language=None)

    assert [seg.text for seg in segments] == ["hallo"]
    assert (info.language, info.language_probability) == ("de", 0.8)
    header = read_message(io.BytesIO(proc.stdin.getvalue()))
    assert header == {"n": 320, "language": None}


def test_worker_model_raises_on_error_and_on_a_dead_worker() -> None:
    erring = _WorkerModel(_FakePipeProc([{"error": "boom"}]), "cpu", "int8")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="boom"):
        erring.transcribe(np.zeros(10, dtype=np.float32))

    dead = _WorkerModel(_FakePipeProc([]), "cpu", "int8")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="exited"):
        dead.transcribe(np.zeros(10, dtype=np.float32))


def test_dropping_a_worker_engine_kills_the_process() -> None:
    """The upgrade over in-process: recovery is a kill, not a shrug (AP-24)."""
    proc = _FakePipeProc([])
    engine = LocalPreviewTranscriber()
    engine._model = _WorkerModel(proc, "cuda", "float16")  # type: ignore[arg-type]  # noqa: SLF001

    for _ in range(3):
        engine._note_failure("timed out")  # noqa: SLF001

    assert proc.killed is True
    assert engine._model is None  # noqa: SLF001


def test_worker_preferring_load_falls_back_to_the_cpu_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No worker on this host: the in-process build must pin CPU — never the
    CUDA ladder whose DLL load holds the loader lock."""
    from jarvis.plugins.stt import fwhisper

    built: list[tuple[str, str]] = []

    class _Model:
        def transcribe(self, *_a: Any, **_k: Any) -> tuple[list[Any], Any]:
            return [], None

    def _build(name: str, device: str, compute: str, *a: Any, **k: Any) -> Any:
        built.append((device, compute))
        return _Model()

    monkeypatch.setattr(fwhisper, "_new_whisper_model", _build)
    monkeypatch.setattr(local_preview_mod, "_spawn_worker_model", lambda name: None)
    engine = LocalPreviewTranscriber(prefer_worker=True)

    engine._load_model()  # noqa: SLF001

    assert built == [("cpu", "int8")]
    assert engine.ready


def test_the_factory_defaults_to_the_worker_hosting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_preview_mod, "faster_whisper_available", lambda: True)
    monkeypatch.delenv("JARVIS_DICTATION_PREVIEW_IN_PROCESS", raising=False)
    local_preview_mod.reset_local_preview_for_tests()
    try:
        engine = local_preview_mod.local_preview()
        assert engine is not None
        assert engine._prefer_worker is True  # noqa: SLF001
    finally:
        local_preview_mod.reset_local_preview_for_tests()
