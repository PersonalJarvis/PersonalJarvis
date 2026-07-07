"""The local wake-match Whisper must be small + CPU, independent of the
utterance STT model.

Measured on the maintainer's Blackwell GPU (RTX 5070 Ti): loading the utterance
model on CUDA cost ~71 s (CTranslate2 kernel JIT for the new arch), while the
SAME model on CPU loads in 3.45 s and a `base` model on CPU in 0.45 s. The local
Whisper only powers wake-phrase transcript matching + the live-preview probe
(both latency-tolerant; utterance STT is separate — cloud or `stt.model`), so it
loads a small model on CPU by default. This collapses Phase-A warm-up from ~71 s
to a few seconds.
"""
from __future__ import annotations

import pytest

import jarvis.plugins.stt as stt_pkg
from jarvis.core.config import STTConfig
from jarvis.plugins.stt import build_wake_whisper
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider


@pytest.fixture(autouse=True)
def _gpu_probe_verified(monkeypatch):
    """Default the GPU inference probe to VERIFIED without touching a real
    subprocess or the on-disk cache (both belong to integration, not here).
    Tests for the probe-fail path override with ``lambda: False``."""
    monkeypatch.setattr(stt_pkg, "_wake_gpu_inference_verified", lambda: True)


def test_stt_config_wake_defaults_small_and_cpu() -> None:
    cfg = STTConfig()
    assert cfg.wake_model == "base"
    assert cfg.wake_device == "cpu"
    assert cfg.wake_compute_type == "int8"


def test_build_wake_whisper_uses_wake_fields_not_utterance_model() -> None:
    cfg = STTConfig(
        model="large-v3-turbo",
        device="cuda",
        compute_type="int8_float16",
        wake_model="base",
        wake_device="cpu",
        wake_compute_type="int8",
    )
    # cuda_available=False isolates this from the GPU auto-upgrade.
    p = build_wake_whisper(cfg, cuda_available=False)

    assert isinstance(p, FasterWhisperProvider)
    # The wake instance must NOT inherit the heavy utterance model / cuda.
    assert p._model_name == "base"
    assert p._device == "cpu"
    assert p._compute_type == "int8"


def test_build_wake_whisper_custom_phrase_default_gets_turbo_when_probe_verifies() -> None:
    # DEFAULT (wake_high_accuracy=True) + CUDA + a VERIFIED real inference probe
    # -> a custom phrase upgrades to large-v3-turbo/cuda WITH bias. History: the
    # default was False after the AP-25 Blackwell hang; re-measured 2026-07-05
    # (ctranslate2 4.7.1, same GPU: 40/40 inferences, 0 hangs) the hang was
    # constellation-specific, so the blind flag became the automated probe.
    p = build_wake_whisper(STTConfig(), wake_phrase="Hey Alex", cuda_available=True)
    assert p._model_name == "large-v3-turbo"
    assert p._device == "cuda"
    assert p._initial_prompt == "Hey Alex"  # bias KEPT — needed to hear the name


def test_build_wake_whisper_custom_phrase_stays_base_cpu_when_probe_fails(
    monkeypatch,
) -> None:
    # An UNVERIFIED GPU (probe hang/failure — the AP-25 class) must keep the
    # validated base/cpu + phrase-bias config even though CUDA is present.
    monkeypatch.setattr(stt_pkg, "_wake_gpu_inference_verified", lambda: False)
    p = build_wake_whisper(STTConfig(), wake_phrase="Hey Alex", cuda_available=True)
    assert p._model_name == "base"
    assert p._device == "cpu"
    assert p._initial_prompt == "Hey Alex"


def test_build_wake_whisper_high_accuracy_false_is_a_hard_opt_out() -> None:
    # wake_high_accuracy=False must force base/cpu even when the probe verifies
    # the GPU — the user's explicit kill switch always wins.
    cfg = STTConfig(wake_high_accuracy=False)
    p = build_wake_whisper(cfg, wake_phrase="Hey Alex", cuda_available=True)
    assert p._model_name == "base"
    assert p._device == "cpu"
    assert p._initial_prompt == "Hey Alex"


def test_build_wake_whisper_fast_first_never_runs_the_gpu_probe(
    monkeypatch,
) -> None:
    # The probe BLOCKS (subprocess, up to minutes on a cache miss). fast_first
    # builds run on the boot / hear-ready path (AP-26) and on live settings
    # switches, so they must never reach it.
    def _boom() -> bool:
        raise AssertionError("fast_first build must not run the GPU probe")

    monkeypatch.setattr(stt_pkg, "_wake_gpu_inference_verified", _boom)
    p = build_wake_whisper(
        STTConfig(), wake_phrase="Hey Alex", cuda_available=True, fast_first=True
    )
    assert p._model_name == "base"
    assert p._device == "cpu"


def test_build_wake_whisper_custom_phrase_fast_first_stays_base_for_quick_boot() -> None:
    # The boot path builds fast-first (base/cpu, ~3 s, no CUDA JIT) so the wake is
    # hear-ready immediately; the GPU turbo swaps in via the background hot-swap.
    p = build_wake_whisper(
        STTConfig(), wake_phrase="Hey Alex", cuda_available=True, fast_first=True
    )
    assert p._model_name == "base"
    assert p._device == "cpu"


def test_build_wake_whisper_default_phrase_gets_turbo_no_bias_on_cuda() -> None:
    # The default "Hey Jarvis" / OWW path carries NO custom bias, so on a CUDA
    # box it still gets the fast turbo upgrade (no bias means nothing to
    # hallucinate the wake onto silence).
    p = build_wake_whisper(STTConfig(), wake_phrase=None, cuda_available=True)
    assert p._model_name == "large-v3-turbo"
    assert p._device == "cuda"
    assert p._initial_prompt is None  # bias OFF on turbo (no custom phrase)


def test_build_wake_whisper_cpu_keeps_bias() -> None:
    # The weak base/cpu model (no GPU / VPS) still NEEDS the bias to hear the
    # proper noun, so it is kept there.
    p = build_wake_whisper(STTConfig(), wake_phrase="Hey Alex", cuda_available=False)
    assert p._model_name == "base"
    assert p._device == "cpu"
    assert p._initial_prompt == "Hey Alex"


def test_build_wake_whisper_uses_greedy_beam_for_speed() -> None:
    # The wake transcribes short phrases on a latency-sensitive always-on loop.
    # Greedy decoding (beam_size=1) is ~3-5x faster than the beam-5 default on
    # base/cpu, so a window transcribes in a fraction of the time — far less
    # likely to blow the wedge timeout under app CPU load, and snappier. The
    # phrase bias + sound-folding matcher keep recall high without beam search.
    p = build_wake_whisper(STTConfig(), cuda_available=False)
    assert p._beam_size == 1


def test_build_wake_whisper_passes_language() -> None:
    p = build_wake_whisper(STTConfig(), language="de", cuda_available=False)
    assert p._language == "de"


def test_build_wake_whisper_tolerates_missing_wake_fields() -> None:
    # A config object predating the wake_* fields falls back to small/cpu/int8.
    class _Bare:
        pass

    p = build_wake_whisper(_Bare(), cuda_available=False)
    assert p._model_name == "base"
    assert p._device == "cpu"
    assert p._compute_type == "int8"


def test_build_wake_whisper_biases_prompt_with_custom_phrase() -> None:
    # A custom wake word ("Hey Alex") routes to the stt_match path, where the
    # small base/cpu model otherwise mis-hears the proper noun. Empirical
    # 2026-06-23 on the user's real wake WAVs: WITHOUT the bias the live model
    # heard "Hey Alex" as "Space"/"Ego"/"Herum" -> 2-13% recall (effectively a
    # dead wake word); WITH the spoken phrase as initial_prompt -> 83% recall.
    # The earlier hallucination concern is held off by the strict ["hey","alex"]
    # matcher (a stray "Alex" in speech is not an adjacent "hey alex") plus the
    # no_speech_prob/RMS gates: false-wake stayed ~0% on real speech. So the bias
    # is re-enabled on this path. It is scoped to the custom phrase only -- the
    # OWW/"Hey Jarvis" paths pass no phrase and stay unbiased (test below).
    p = build_wake_whisper(
        STTConfig(), language="de", wake_phrase="Hey Alex", cuda_available=False
    )
    assert p._initial_prompt == "Hey Alex"


def test_build_wake_whisper_default_has_no_prompt_bias() -> None:
    p = build_wake_whisper(STTConfig(), language="de", cuda_available=False)
    assert p._initial_prompt is None

    p_blank = build_wake_whisper(
        STTConfig(), language="de", wake_phrase="   ", cuda_available=False
    )
    assert p_blank._initial_prompt is None


# --- Persisted CUDA-availability probe (boot-speed fix) ---------------------
#
# The first CUDA call (``ctranslate2.get_cuda_device_count``) JIT-compiles
# kernels for ~30-60 s on a Blackwell GPU and used to run synchronously on the
# desktop boot path, freezing "VOICE STARTING…". The probe result is a stable
# hardware fact, so it is cached to disk and skipped on every boot after the
# first.
import json  # noqa: E402

from jarvis.plugins.stt import _wake_cuda_available, _wake_cuda_cache_path  # noqa: E402


def test_wake_cuda_cache_path_honours_data_dir_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    assert _wake_cuda_cache_path() == tmp_path / "wake_cuda_probe.json"


def test_wake_cuda_available_returns_persisted_value_without_probing(
    tmp_path, monkeypatch
) -> None:
    """A cache HIT must return the stored value and never touch ctranslate2."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    _wake_cuda_available.cache_clear()
    (tmp_path / "wake_cuda_probe.json").write_text(
        json.dumps({"cuda": True}), encoding="utf-8"
    )
    # A real probe in CI (no GPU) would return False; a True result therefore
    # proves the cached value was used, not a fresh probe.
    assert _wake_cuda_available() is True
    _wake_cuda_available.cache_clear()


def test_wake_cuda_available_writes_cache_on_miss(tmp_path, monkeypatch) -> None:
    """A cold probe (no cache) must persist its result for the next boot."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    _wake_cuda_available.cache_clear()
    cache_file = tmp_path / "wake_cuda_probe.json"
    assert not cache_file.exists()

    value = _wake_cuda_available()  # CI has no GPU → False, but the path runs.

    assert cache_file.exists()
    assert json.loads(cache_file.read_text(encoding="utf-8"))["cuda"] == value
    _wake_cuda_available.cache_clear()


def test_wake_cuda_available_survives_corrupt_cache(tmp_path, monkeypatch) -> None:
    """A corrupt cache file must not break boot — it falls back to a fresh probe."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    _wake_cuda_available.cache_clear()
    cache_file = tmp_path / "wake_cuda_probe.json"
    cache_file.write_text("{not json", encoding="utf-8")

    # Must not raise; re-probes and overwrites the corrupt file with a valid one.
    value = _wake_cuda_available()
    assert isinstance(value, bool)
    assert json.loads(cache_file.read_text(encoding="utf-8"))["cuda"] == value
    _wake_cuda_available.cache_clear()
