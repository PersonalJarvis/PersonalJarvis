"""``recommend_whisper`` must be capability-VERIFIED and never English-only.

Two guarantees under test:

1. **Never English-only** (original regression): the wizard used to recommend
   distil-large-v3 / distil-small for NVIDIA GPUs. Every Distil-Whisper checkpoint
   is English-only (there is no multilingual distil) and turns German/Spanish into
   English words, so the recommender must hand out a multilingual model directly.

2. **GPU presence is not GPU usability** (AP-21/AP-25): ``recommend_whisper`` only
   hands back ``device="cuda"`` when a real GPU inference has been VERIFIED on this
   host (``gpu_inference_verified=True``). CUDA *presence* alone — the value a
   driver/runtime mismatch or the Blackwell hang leaves ``torch.cuda.is_available()``
   reporting — falls back to the CPU-first floor, so no ``cuda`` choice a host cannot
   actually run is ever persisted into ``jarvis.toml``. This also keeps the path
   vendor-neutral: an Apple-Silicon Mac (no NVIDIA, no CUDA) lands on CPU int8.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.hardware.detection import recommend_whisper
from jarvis.plugins.stt.fwhisper import _ENGLISH_ONLY_MODELS


def _gpu_report(vram_mb: int) -> SimpleNamespace:
    return SimpleNamespace(
        has_nvidia_gpu=True,
        torch_cuda_available=True,
        total_vram_mb=vram_mb,
        ram_total_mb=16384,
    )


def _cpu_report(*, ram_total_mb: int = 16384) -> SimpleNamespace:
    """A host with no NVIDIA GPU / no CUDA — a headless VPS or an Apple-Silicon Mac."""
    return SimpleNamespace(
        has_nvidia_gpu=False,
        torch_cuda_available=False,
        total_vram_mb=0,
        ram_total_mb=ram_total_mb,
    )


@pytest.mark.parametrize("vram", [2000, 4000, 6000, 8000, 16000, 24000])
def test_verified_gpu_recommendation_is_never_english_only(vram: int) -> None:
    rec = recommend_whisper(_gpu_report(vram), gpu_inference_verified=True)
    assert rec.model not in _ENGLISH_ONLY_MODELS, rec.model
    assert not rec.model.startswith("distil-"), (
        f"recommended {rec.model!r}: every distil-* model is English-only and "
        f"mangles German/Spanish."
    )


@pytest.mark.parametrize("vram", [4000, 8000, 16000, 24000])
def test_verified_capable_gpu_recommends_multilingual_turbo(vram: int) -> None:
    rec = recommend_whisper(_gpu_report(vram), gpu_inference_verified=True)
    assert rec.model == "large-v3-turbo"
    assert rec.device == "cuda"


def test_verified_low_vram_gpu_recommends_base_multilingual() -> None:
    rec = recommend_whisper(_gpu_report(2000), gpu_inference_verified=True)
    assert rec.model == "base"
    assert rec.device == "cuda"


@pytest.mark.parametrize("verified", [None, False])
def test_unverified_cuda_falls_back_to_cpu(verified) -> None:
    # CUDA is PRESENT (torch_cuda_available=True) but a real inference has not been
    # verified — the AP-25 "present but unusable" case. Must not persist "cuda".
    rec = recommend_whisper(_gpu_report(24000), gpu_inference_verified=verified)
    assert rec.device == "cpu"
    assert rec.compute_type == "int8"
    assert rec.model == "base"


def test_apple_silicon_recommends_cpu_int8() -> None:
    # No NVIDIA GPU, no CUDA (Apple Silicon / headless Linux). CTranslate2 has no
    # Metal backend, so CPU int8 is the correct, vendor-neutral choice.
    rec = recommend_whisper(_cpu_report(), gpu_inference_verified=None)
    assert rec.provider == "faster-whisper"
    assert rec.device == "cpu"
    assert rec.compute_type == "int8"


def test_default_is_cpu_first_when_verification_unknown() -> None:
    # The default (no flag passed) must be CPU-first even on a CUDA-present box —
    # a caller that does not KNOW the GPU works must never get a cuda recommendation.
    rec = recommend_whisper(_gpu_report(24000))
    assert rec.device == "cpu"
