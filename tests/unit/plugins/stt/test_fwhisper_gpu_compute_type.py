"""GPU construction preserves precision chosen for the user's hardware."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import jarvis.plugins.stt.fwhisper as fwhisper


@pytest.mark.parametrize("device", ["cuda", "auto", "cpu"])
@pytest.mark.parametrize("compute_type", ["int8", "int8_float16", "float16", "float32"])
def test_constructor_preserves_requested_precision(monkeypatch, device, compute_type) -> None:
    built: list[dict] = []

    class Model:
        def __init__(self, name: str, **kwargs: object) -> None:
            built.append({"name": name, **kwargs})

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(fwhisper, "ensure_cuda_libraries_findable", lambda: None)

    fwhisper._new_whisper_model("large-v3-turbo", device, compute_type)

    assert built[0]["device"] == device
    assert built[0]["compute_type"] == compute_type
