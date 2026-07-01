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

from jarvis.core.config import STTConfig
from jarvis.plugins.stt import build_wake_whisper
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider


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


def test_build_wake_whisper_custom_phrase_upgrades_to_gpu_turbo_with_bias() -> None:
    # Mission 2026-06-30 (live-log evidence, data/jarvis_desktop.log): the
    # base/cpu wake model WEDGES repeatedly ("5 consecutive transcribe failures ->
    # rebuilding the wedged wake model" — up to 40 s of total deafness) and
    # mis-transcribes even clear speech under app CPU/GIL contention, so a custom
    # wake ("Hey Nico") needs 2-3 tries. The utterance STT is cloud (Groq), so the
    # GPU sits idle. Fix: a custom phrase on a CUDA box now runs on the strong
    # turbo model — ~150 ms per window so it NEVER blows the transcribe timeout
    # (kills the wedge) and hears the proper noun accurately. The phrase bias is
    # KEPT (the earlier "turbo without bias mangles Hey Nico -> cuf ich" finding);
    # the earlier "bias hallucinates the phrase onto silence" concern does NOT
    # apply on the rolling wake path, which gates on rms/peak/no_speech and so
    # never feeds a silent window to the model. Reversible via wake_high_accuracy.
    p = build_wake_whisper(STTConfig(), wake_phrase="Hey Ruben", cuda_available=True)
    assert p._model_name == "large-v3-turbo"
    assert p._device == "cuda"
    assert p._initial_prompt == "Hey Ruben"  # bias KEPT — needed to hear the name


def test_build_wake_whisper_custom_phrase_gpu_upgrade_is_reversible() -> None:
    # wake_high_accuracy = False forces the validated base/cpu + bias config back
    # (the escape hatch if the strong model ever over-triggers on a given voice).
    cfg = STTConfig(wake_high_accuracy=False)
    p = build_wake_whisper(cfg, wake_phrase="Hey Ruben", cuda_available=True)
    assert p._model_name == "base"
    assert p._device == "cpu"
    assert p._initial_prompt == "Hey Ruben"


def test_build_wake_whisper_custom_phrase_fast_first_stays_base_for_quick_boot() -> None:
    # The boot path builds fast-first (base/cpu, ~3 s, no CUDA JIT) so the wake is
    # hear-ready immediately; the GPU turbo swaps in via the background hot-swap.
    p = build_wake_whisper(
        STTConfig(), wake_phrase="Hey Ruben", cuda_available=True, fast_first=True
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
    p = build_wake_whisper(STTConfig(), wake_phrase="Hey Ruben", cuda_available=False)
    assert p._model_name == "base"
    assert p._device == "cpu"
    assert p._initial_prompt == "Hey Ruben"


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
    # A custom wake word ("Hey Ruben") routes to the stt_match path, where the
    # small base/cpu model otherwise mis-hears the proper noun. Empirical
    # 2026-06-23 on the user's real wake WAVs: WITHOUT the bias the live model
    # heard "Hey Ruben" as "Space"/"Ego"/"Herum" -> 2-13% recall (effectively a
    # dead wake word); WITH the spoken phrase as initial_prompt -> 83% recall.
    # The earlier hallucination concern is held off by the strict ["hey","ruben"]
    # matcher (a stray "Ruben" in speech is not an adjacent "hey ruben") plus the
    # no_speech_prob/RMS gates: false-wake stayed ~0% on real speech. So the bias
    # is re-enabled on this path. It is scoped to the custom phrase only -- the
    # OWW/"Hey Jarvis" paths pass no phrase and stay unbiased (test below).
    p = build_wake_whisper(
        STTConfig(), language="de", wake_phrase="Hey Ruben", cuda_available=False
    )
    assert p._initial_prompt == "Hey Ruben"


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
