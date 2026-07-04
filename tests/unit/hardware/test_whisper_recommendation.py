"""recommend_whisper must never recommend an English-only Whisper model.

Regression guard for the "German is mangled on a GPU box" first-run bug: the
wizard used to recommend distil-large-v3 / distil-small for NVIDIA GPUs. Every
Distil-Whisper checkpoint is English-only (there is no multilingual distil) and
turns German/Spanish into English words. The runtime already force-upgrades such
models to large-v3-turbo, so recommending them only persisted a confusing,
self-overridden value into jarvis.toml. The recommender must stay aligned with
that runtime guard and hand out a multilingual model directly.
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
    )


@pytest.mark.parametrize("vram", [2000, 4000, 6000, 8000, 16000, 24000])
def test_gpu_recommendation_is_never_english_only(vram: int) -> None:
    rec = recommend_whisper(_gpu_report(vram))
    assert rec.model not in _ENGLISH_ONLY_MODELS, rec.model
    assert not rec.model.startswith("distil-"), (
        f"recommended {rec.model!r}: every distil-* model is English-only and "
        f"mangles German/Spanish."
    )


@pytest.mark.parametrize("vram", [4000, 8000, 16000, 24000])
def test_capable_gpu_recommends_multilingual_turbo(vram: int) -> None:
    rec = recommend_whisper(_gpu_report(vram))
    assert rec.model == "large-v3-turbo"
    assert rec.device == "cuda"


def test_low_vram_gpu_recommends_base_multilingual() -> None:
    rec = recommend_whisper(_gpu_report(2000))
    assert rec.model == "base"
