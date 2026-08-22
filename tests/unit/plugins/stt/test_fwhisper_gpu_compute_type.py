"""On CUDA an int8 request is built as float16 — the int8 kernels measured 10x
slower on a Blackwell card (ctranslate2 4.8): large-v3 took 1-2 minutes for
three words and ran into the 15 s provider ceiling. CPU requests are untouched.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import jarvis.plugins.stt.fwhisper as fwhisper


def _runtime(monkeypatch: pytest.MonkeyPatch, supported: set[str]) -> None:
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(get_supported_compute_types=lambda _device: supported),
    )


def test_int8_on_cuda_becomes_float16_when_the_runtime_can(monkeypatch: pytest.MonkeyPatch) -> None:
    _runtime(monkeypatch, {"int8", "int8_float16", "float16"})
    assert fwhisper._gpu_compute_type("cuda", "int8_float16") == "float16"
    assert fwhisper._gpu_compute_type("cuda", "int8") == "float16"


def test_float16_requests_and_cpu_requests_are_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    _runtime(monkeypatch, {"int8", "int8_float16", "float16"})
    assert fwhisper._gpu_compute_type("cuda", "float16") == "float16"
    assert fwhisper._gpu_compute_type("cpu", "int8_float16") == "int8_float16"
    assert fwhisper._gpu_compute_type("cpu", "int8") == "int8"


def test_a_runtime_without_float16_keeps_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _runtime(monkeypatch, {"int8", "int8_float16"})
    assert fwhisper._gpu_compute_type("cuda", "int8_float16") == "int8_float16"


def test_a_runtime_that_cannot_answer_keeps_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            get_supported_compute_types=lambda _d: (_ for _ in ()).throw(RuntimeError("no CUDA"))
        ),
    )
    assert fwhisper._gpu_compute_type("cuda", "int8_float16") == "int8_float16"


def test_the_constructor_hands_the_remapped_type_to_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    _runtime(monkeypatch, {"int8", "int8_float16", "float16"})
    built: list[dict] = []

    class _Model:
        def __init__(self, name: str, **kwargs: object) -> None:
            built.append({"name": name, **kwargs})

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=_Model))
    monkeypatch.setattr(fwhisper, "ensure_cuda_libraries_findable", lambda: None)

    fwhisper._new_whisper_model("large-v3", "cuda", "int8_float16")
    fwhisper._new_whisper_model("large-v3", "cpu", "int8")

    assert built[0]["compute_type"] == "float16"
    assert built[1]["compute_type"] == "int8"
