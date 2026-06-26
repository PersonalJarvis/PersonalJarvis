"""STT provider plugins (faster-whisper, OpenAI, Groq, Deepgram).

This package exposes a single ``build_stt_from_config`` factory that turns an
``STTConfig`` into the configured ``STTProvider``-conformant instance via the
``jarvis.stt`` entry-point group. Local Whisper stays the fallback so the wake-
detector path keeps working when no cloud provider is registered.
"""
from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from loguru import logger

ENTRY_POINT_GROUP = "jarvis.stt"


def _load_provider_class(name: str) -> type | None:
    """Resolve an STT provider class by its entry-point ``name`` (e.g. ``groq-api``)."""
    eps = importlib_metadata.entry_points()
    selected = (
        eps.select(group=ENTRY_POINT_GROUP)
        if hasattr(eps, "select")
        else eps.get(ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]
    )
    for ep in selected:
        if ep.name == name:
            try:
                return ep.load()
            except (ImportError, ModuleNotFoundError) as exc:
                logger.warning(
                    "STT entry-point {!r} failed to load: {}", name, exc
                )
                return None
    return None


def build_stt_from_config(stt_cfg: Any) -> Any:
    """Return an STTProvider instance for ``stt_cfg.provider``.

    Falls back to a local FasterWhisperProvider if the configured provider has
    no entry-point or raises on construction.
    """
    provider_name = (getattr(stt_cfg, "provider", "") or "").strip()
    language = getattr(stt_cfg, "language", "auto")
    language = language if language and language != "auto" else None
    bias_prompt = (getattr(stt_cfg, "bias_prompt", "") or "").strip()

    cls = _load_provider_class(provider_name) if provider_name else None
    if cls is not None and provider_name != "faster-whisper":
        kwargs: dict[str, Any] = {}
        if language:
            kwargs["language"] = language
        if bias_prompt:
            kwargs["prompt"] = bias_prompt
        # Team-proxy mode (2026-06-20 spec §4): route the cloud STT through the
        # key proxy with the per-user token instead of the real vendor key. Only
        # groq-api (the cloud STT exposing `endpoint` + `api_key` constructor
        # args) is proxy-capable today; other providers fall through unchanged.
        # Direct mode injects nothing, so the provider keeps its own endpoint +
        # key resolution (behaviour unchanged).
        if provider_name == "groq-api":
            from jarvis.core import config as _cfg

            ep = _cfg.resolve_provider_endpoint("groq-api")
            if ep.via_proxy and ep.base_url:
                kwargs["endpoint"] = ep.base_url.rstrip("/") + "/audio/transcriptions"
                if ep.credential:
                    kwargs["api_key"] = ep.credential
        try:
            instance = cls(**kwargs) if kwargs else cls()
            logger.info(
                "STT provider resolved via entry-point: {} (class {}, bias_prompt={} chars)",
                provider_name,
                cls.__name__,
                len(bias_prompt),
            )
            return instance
        except TypeError as exc:
            # The provider class refused one of our kwargs — most likely because
            # it predates the bias_prompt addition. Retry without it so a stale
            # third-party plugin still loads. (faster-whisper local path is
            # handled below as the explicit fallback.)
            if "prompt" in kwargs:
                kwargs.pop("prompt", None)
                logger.warning(
                    "STT provider {!r} does not accept bias_prompt yet ({}); "
                    "retrying without it.",
                    provider_name,
                    exc,
                )
                try:
                    return cls(**kwargs) if kwargs else cls()
                except Exception as inner_exc:  # noqa: BLE001
                    logger.warning(
                        "STT provider {!r} init still failed ({}); falling back to faster-whisper",
                        provider_name,
                        inner_exc,
                    )
            else:
                logger.warning(
                    "STT provider {!r} init failed ({}); falling back to faster-whisper",
                    provider_name,
                    exc,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "STT provider {!r} init failed ({}); falling back to faster-whisper",
                provider_name,
                exc,
            )

    # Local fallback (also the explicit "faster-whisper" path).
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

    return FasterWhisperProvider(
        model=getattr(stt_cfg, "model", "distil-large-v3"),
        device=getattr(stt_cfg, "device", "cuda"),
        compute_type=getattr(stt_cfg, "compute_type", "int8_float16"),
        language=language,
    )


def _wake_cuda_cache_path() -> Path:
    """Location of the persisted CUDA-availability probe result.

    CUDA presence is a stable hardware fact, so the probe result is cached ACROSS
    process restarts (not just in-process). Honours the same data-dir env seam the
    rest of the app uses (``JARVIS__MEMORY__DATA_DIR``); defaults to ``./data``
    relative to the project-root CWD.
    """
    base = os.environ.get("JARVIS__MEMORY__DATA_DIR") or "data"
    return Path(base) / "wake_cuda_probe.json"


@lru_cache(maxsize=1)
def _wake_cuda_available() -> bool:
    """True iff a CUDA device is usable by the CTranslate2 / faster-whisper backend.

    Cached in-process (``lru_cache``) AND persisted to disk. The FIRST CUDA call
    in a process (``ctranslate2.get_cuda_device_count``) initializes the CUDA
    context, which on a Blackwell (sm_120) GPU JIT-compiles kernels and costs
    ~30-60 s (measured). ``build_wake_whisper`` runs this SYNCHRONOUSLY on the
    desktop boot path to choose the wake model, so the probe used to freeze voice
    boot ("VOICE STARTING…") for up to a minute on EVERY launch.

    Persisting the boolean across restarts removes the probe from the boot path on
    every boot after the first; the (unavoidable, one-time-per-process) CUDA
    context init then happens later, during the already-backgrounded wake-model
    load — never on the wake-ready path. Delete ``data/wake_cuda_probe.json`` to
    force a re-probe after a GPU/driver change. Any import/probe error is treated
    as "no CUDA" so a host without the GPU stack degrades to the cloud-first CPU
    default. AP-21: gate on the capability, never a hardware name.
    """
    cache_path = _wake_cuda_cache_path()

    # 1) Persisted result — skip the expensive probe on every boot after the first.
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and isinstance(cached.get("cuda"), bool):
            logger.info(
                "Wake-CUDA probe: cache HIT ({}) — probe skipped.",
                "available" if cached["cuda"] else "absent",
            )
            return cached["cuda"]
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 — a corrupt cache must never break boot
        logger.debug("Wake-CUDA probe cache unreadable ({}); re-probing.", exc)

    # 2) Cold path — pay the probe ONCE, log how long it took, then persist it.
    t0 = time.perf_counter()
    try:
        import ctranslate2

        available = ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - any failure means "treat as no GPU"
        available = False
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        "Wake-CUDA probe: cache MISS — probed in {:.0f} ms -> CUDA {}.",
        elapsed_ms,
        "available" if available else "absent",
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"cuda": available}), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — caching is best-effort
        logger.debug("Wake-CUDA probe cache write failed ({}).", exc)
    return available


def build_wake_whisper(
    stt_cfg: Any,
    *,
    language: str | None = None,
    wake_phrase: str | None = None,
    cuda_available: bool | None = None,
) -> Any:
    """Build the LOCAL wake-match / live-preview Whisper.

    Distinct from :func:`build_stt_from_config` (the post-wake *utterance* STT,
    often a cloud provider). This instance only powers wake-phrase transcript
    matching + the listening-bubble probe — both latency-tolerant — so it loads
    a small model on CPU by default (``stt_cfg.wake_model`` / ``wake_device`` /
    ``wake_compute_type``), NOT the heavy utterance model on the GPU.

    Why this matters for boot: on a Blackwell GPU (RTX 50xx) CTranslate2 JIT-
    compiles kernels at model load, costing ~71 s on CUDA vs ~0.45 s for ``base``
    on CPU (measured) — the dominant Phase-A warm-up cost. CPU is also the
    cloud-first floor. ``getattr`` fallbacks keep a pre-wake_*-field config (or a
    bare stub) building a safe small/cpu instance.

    ``wake_phrase`` (forensic 2026-06-22): when a user sets a CUSTOM wake word
    with no pretrained openWakeWord model ("Hey Alex"), the wake routes to this
    small CPU model. ``base`` transcribed the proper noun as a common word
    ("Alex" -> "job") so the wake never fired. Passing the spoken trigger here
    seeds Whisper's ``initial_prompt`` so it biases toward the actual name. This
    is deliberately scoped to the custom stt_match wake (the pipeline only
    forwards a phrase on that path) — the default "Hey Jarvis"/OWW paths pass
    nothing, so the hot-path prompt-hallucination caveat in
    ``FasterWhisperProvider.__init__`` does not apply to them.

    Bias is ON (forensic 2026-06-23). It was once disabled out of a hallucination
    concern, but that disabled the custom wake word entirely: empirically, on the
    user's real wake WAVs the unbiased base/cpu model heard "Hey Alex" as
    "Space"/"Ego"/"Herum" -> 2-13% recall; seeding ``wake_phrase`` as the
    ``initial_prompt`` lifts that to 83%. The earlier false-wake risk is held off
    by the strict ["hey","alex"] matcher (a stray "Alex" in ordinary speech is
    not an adjacent "hey alex") plus the ``no_speech_prob``/RMS gates, which kept
    the false-wake rate ~0% on 50 real talking-about-Alex clips. The bias is
    scoped to this path: only the stt_match custom-phrase route forwards a
    ``wake_phrase``; the default "Hey Jarvis"/OWW paths pass nothing and stay
    unbiased, so the hot-path prompt-hallucination caveat in
    ``FasterWhisperProvider.__init__`` does not apply to them. A blank phrase is
    treated as no bias.
    """
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

    model = getattr(stt_cfg, "wake_model", "base")
    device = getattr(stt_cfg, "wake_device", "cpu")
    compute = getattr(stt_cfg, "wake_compute_type", "int8")
    bias = wake_phrase.strip() if wake_phrase and wake_phrase.strip() else None

    # Capability-gated GPU upgrade (forensic 2026-06-24, validated on the user's
    # real wake WAVs). On the cloud-first CPU defaults (base/cpu) AND a CUDA
    # device, transcribe the wake on the GPU with a fast MULTILINGUAL turbo model
    # AND DROP THE BIAS. The strong model hears the proper noun WITHOUT the
    # ``initial_prompt`` hint, so the wake stays fast (~150 ms vs ~1.4 s on
    # base/cpu) while it does NOT hallucinate the primed phrase onto quiet
    # silence — the bias on the strong model was the false-wake source ("Vielen
    # Dank." silence artifact -> "Hey Alex"). Offline-validated: no-bias turbo is
    # 0 false-wakes across the user's silence / own-speech / other-wake clips,
    # while a clearly spoken wake still fires. The WEAK base/cpu model still NEEDS
    # the bias to hear the name, so the bias is kept ONLY there. Only the
    # stt_match custom-phrase path on a GPU box is affected; the bundled
    # "Hey Jarvis"/openWakeWord path and every CPU/VPS host are untouched. An
    # explicit wake_model/wake_device wins (only the base/cpu pair auto-upgrades).
    if cuda_available is None:
        cuda_available = _wake_cuda_available()
    if model == "base" and device == "cpu" and cuda_available:
        model, device, compute = "large-v3-turbo", "cuda", "int8_float16"
        bias = None  # strong model needs no bias; the bias is what hallucinates
        logger.info(
            "Wake-Whisper: CUDA present -> GPU turbo (large-v3-turbo/cuda), "
            "bias OFF (fast + no silence hallucination)."
        )

    return FasterWhisperProvider(
        model=model,
        device=device,
        compute_type=compute,
        language=language,
        initial_prompt=bias,
    )


__all__ = ["build_stt_from_config", "build_wake_whisper"]
