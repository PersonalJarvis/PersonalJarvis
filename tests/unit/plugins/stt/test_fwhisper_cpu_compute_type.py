"""Regression: a CPU device must never carry a CUDA-only compute type.

CTranslate2 does NOT silently downgrade an unsupported compute type on a CPU
device — it RAISES ``ValueError`` at model construction. The shipped cloud-first
default pairs ``device="cpu"`` with ``compute_type="int8_float16"`` (the value a
CUDA box needs), so without a construction-time coercion every fresh CPU/VPS
install hits that error on its first model build before ``_ensure_model``'s retry
recovers it. These tests pin the coercion so a GPU/``auto`` device keeps its exact
compute type while a CPU device is normalised up-front to the CPU-safe equivalent.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.stt.fwhisper import FasterWhisperProvider


@pytest.mark.parametrize(
    ("device", "compute_in", "compute_expected"),
    [
        ("cpu", "int8_float16", "int8"),   # the shipped cloud-first default combo
        ("cpu", "float16", "int8"),        # any CUDA-only type -> CPU-safe int8
        ("CPU", "int8_float16", "int8"),   # case-insensitive device match
        ("cpu", "int8", "int8"),           # already CPU-safe -> unchanged
    ],
)
def test_cpu_device_coerces_cuda_only_compute_type(device, compute_in, compute_expected):
    prov = FasterWhisperProvider(model="tiny", device=device, compute_type=compute_in)
    assert prov._compute_type == compute_expected


@pytest.mark.parametrize("device", ["cuda", "auto"])
def test_non_cpu_device_keeps_compute_type_verbatim(device):
    # The maintainer's CUDA path (and a device="auto" host that may resolve to a
    # GPU) must keep int8_float16 exactly — the coercion is CPU-only.
    prov = FasterWhisperProvider(model="tiny", device=device, compute_type="int8_float16")
    assert prov._device == device
    assert prov._compute_type == "int8_float16"
