"""Honest on-disk verification of the voice models — the truth behind "done".

``prefetch.py`` DOWNLOADS the models the voice path needs; this module VERIFIES
what actually landed on disk afterwards, so the installer (and a standalone
check) can print a per-model truth instead of a flat "all models are on disk".
It answers the only question that matters to a fresh downloader: *is the thing
really here, and if not, why?*

Design contract (CLAUDE.md section 3 — a flaky probe must never brick an
install):

- **Read-only + best-effort.** Every probe is wrapped so a failure reads as
  "absent", never as a raised exception. The report can always be produced.
- **No network.** The faster-whisper cache probe uses ``local_files_only`` so it
  never triggers a download while merely *checking*.
- **Profile-aware optionality.** ``required`` separates the models the DEFAULT
  voice path needs from optional local models. When the advertised ``[full]``
  desktop installer asks for a full-profile report, every supported Vosk
  language and the local Whisper wake model become required, so its completion
  message cannot hide a partial model download. The cloud/headless report keeps
  those artifacts optional.

Seams (module-level functions) keep the heavy imports lazy and let tests inject
fakes without a real config, network, or model cache — same style as
``prefetch.py``.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any


@dataclass(frozen=True)
class ModelStatus:
    """One line of the report: a model, whether it is usable, and why."""

    label: str  # human-readable name shown to the user
    present: bool  # usable on disk right now
    detail: str  # honest one-line explanation (why present / why not)
    required: bool  # True: the default voice path needs it; False: optional/by-design


# --------------------------------------------------------------------------
# Seams — patched in tests; keep the heavy imports lazy for a light module load.
# --------------------------------------------------------------------------
def _wake_backbone_present() -> bool:
    """The always-on neural wake backbones ship in-repo (jarvis/assets/wakeword)."""
    import jarvis.assets

    return jarvis.assets.bundled_wakeword_models() is not None


def _vad_present() -> bool:
    """The Silero end-of-speech model ships in-repo (jarvis/assets/vad)."""
    import jarvis.assets

    return jarvis.assets.bundled_silero_vad_model() is not None


def _load_config() -> Any:
    from jarvis.core.config import load_config

    return load_config()


def _wake_language(cfg: Any) -> str | None:
    from jarvis.speech.wake_model_fetch import resolve_wake_language

    return resolve_wake_language(cfg)


def _supported_wake_languages() -> tuple[str, ...]:
    from jarvis.speech.wake_model_fetch import VOSK_MODELS

    return tuple(VOSK_MODELS)


def _vosk_present(language: str | None, data_dir: str | None) -> bool:
    from jarvis.speech.wake_model_fetch import vosk_model_present

    return vosk_model_present(language, data_dir=data_dir)


def _faster_whisper_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def _neural_wake_runtime_available() -> bool:
    """True when the neural wake path (openWakeWord on onnxruntime) can run here."""
    return (
        importlib.util.find_spec("onnxruntime") is not None
        and importlib.util.find_spec("openwakeword") is not None
    )


def _silero_runtime_available() -> bool:
    """True when onnxruntime is importable, i.e. the Silero VAD model can run here."""
    return importlib.util.find_spec("onnxruntime") is not None


def _webrtc_vad_available() -> bool:
    """True when the webrtcvad fallback tier is importable."""
    return importlib.util.find_spec("webrtcvad") is not None


def _whisper_cached(name: str) -> bool:
    """True when faster-whisper model ``name`` is already in the local HF cache.

    ``local_files_only`` keeps this a pure cache lookup — it never reaches the
    network. Any failure (not cached, lib missing, odd cache layout) reads as
    "not present".
    """
    from faster_whisper.utils import download_model  # type: ignore[import-not-found,import-untyped]

    download_model(name, local_files_only=True)
    return True


def _whisper_models_needed(cfg: Any) -> list[str]:
    """The faster-whisper model names the CURRENT config would load at runtime.

    Mirrors ``prefetch._whisper_models_needed``: the wake-match model always,
    plus the utterance model only when the local provider is selected.
    """
    models = [cfg.stt.wake_model]
    if cfg.stt.provider == "faster-whisper" and cfg.stt.model not in models:
        models.append(cfg.stt.model)
    return models


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def _safe(fn: Callable[[], Any], default: Any) -> Any:
    """Run a probe; any failure degrades to ``default`` — the report never raises."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - a probe that cannot answer means "absent"
        return default


def voice_model_report(
    cfg: Any | None = None,
    *,
    data_dir: str | None = None,
    full_profile: bool = False,
) -> list[ModelStatus]:
    """Verify, read-only, which voice models are actually ready on disk.

    ``cfg`` defaults to the live config (loaded via the same path the runtime
    uses); pass one explicitly to keep a caller/test hermetic.
    """
    if cfg is None:
        cfg = _safe(_load_config, None)

    items: list[ModelStatus] = []

    # 1. Neural wake-word backbone — bundled in-repo, core path.
    # Decision matrix: a required item FAILS only when the platform COULD run
    # the neural path but the asset file is missing (partial download); a
    # platform that cannot run it degrades to the vosk_kws tier — an honest
    # PASS, never an install failure.
    wake_ok = bool(_safe(_wake_backbone_present, False))
    wake_runtime = bool(_safe(_neural_wake_runtime_available, False))
    if wake_runtime:
        wake_present = wake_ok
        wake_detail = (
            "bundled with the app"
            if wake_ok
            else "MISSING from the package - partial download? re-run the installer"
        )
    else:
        wake_present = True
        wake_detail = "vosk_kws keyword spotting (neural wake runtime unavailable on this platform)"
    items.append(ModelStatus("wake word (neural)", wake_present, wake_detail, required=True))

    # 2. End-of-speech detection (Silero VAD) — bundled in-repo, core path.
    # Same matrix: without onnxruntime the runtime falls back to WebRTC VAD or
    # the portable energy endpointer (jarvis/audio/vad.py tiers) — a PASS.
    vad_ok = bool(_safe(_vad_present, False))
    silero_runtime = bool(_safe(_silero_runtime_available, False))
    if silero_runtime:
        vad_present = vad_ok
        vad_detail = (
            "bundled with the app"
            if vad_ok
            else "MISSING from the package - partial download? re-run the installer"
        )
    else:
        vad_present = True
        vad_detail = (
            "WebRTC VAD (neural runtime unavailable on this platform)"
            if bool(_safe(_webrtc_vad_available, False))
            else "portable energy endpointing (neural runtime unavailable on this platform)"
        )
    items.append(ModelStatus("end-of-speech detection", vad_present, vad_detail, required=True))

    # 3. Custom-wake model (Vosk, per language). A default/cloud report checks
    # only the configured language and treats it as optional. The full desktop
    # installer runs before onboarding, so it must prove every selectable
    # language is cached instead of silently validating only the English default.
    if full_profile:
        supported = _safe(_supported_wake_languages, None)
        if supported:
            wake_languages: tuple[str | None, ...] = supported
        else:
            wake_languages = ()
            items.append(
                ModelStatus(
                    "custom-wake language catalog",
                    False,
                    "MISSING from the package - cannot verify the full voice profile",
                    required=True,
                )
            )
    else:
        lang = _safe(lambda: _wake_language(cfg), None) if cfg is not None else None
        wake_languages = (lang,)
    for lang in wake_languages:
        vosk_ok = bool(_safe(partial(_vosk_present, lang, data_dir), False))
        items.append(
            ModelStatus(
                f"custom-wake model '{lang or 'default'}'",
                vosk_ok,
                "ready"
                if vosk_ok
                else (
                    "not downloaded yet - re-run the installer or retry in the app"
                    if full_profile
                    else "not downloaded yet - the app fetches it on first use"
                ),
                required=full_profile,
            )
        )

    # 4. Local speech recognition (faster-whisper). It remains optional for the
    # cloud/headless report, but the advertised full profile promises this path.
    if not bool(_safe(_faster_whisper_available, False)):
        items.append(
            ModelStatus(
                "local speech model",
                False,
                (
                    "MISSING from the [full] profile - re-run the installer"
                    if full_profile
                    else "not installed - cloud speech is the default "
                    "(install the [full] profile for offline speech)"
                ),
                required=full_profile,
            )
        )
    else:
        fallback_models = ["base"] if full_profile else []
        needed = (
            _safe(lambda: _whisper_models_needed(cfg), fallback_models)
            if cfg is not None
            else fallback_models
        )
        if full_profile and not needed:
            needed = fallback_models
        for name in needed:
            cached = bool(_safe(partial(_whisper_cached, name), False))
            items.append(
                ModelStatus(
                    f"local speech model '{name}'",
                    cached,
                    "ready"
                    if cached
                    else (
                        "not downloaded yet - re-run the installer or retry in the app"
                        if full_profile
                        else "not downloaded yet - the app fetches it on first use"
                    ),
                    required=full_profile,
                )
            )

    return items


def report_complete(items: Sequence[ModelStatus]) -> bool:
    """True when every REQUIRED model is present (optional ones may be pending)."""
    return all(it.present for it in items if it.required)


def format_report(items: Sequence[ModelStatus]) -> list[str]:
    """Render the report as aligned human lines with ✓ / ✗ / — markers.

    ✓ present · ✗ required-but-missing (a real problem) · — optional-and-pending.
    """
    lines: list[str] = []
    for it in items:
        mark = "✓" if it.present else ("✗" if it.required else "—")
        lines.append(f"{mark} {it.label:<26} {it.detail}")
    return lines


__all__ = [
    "ModelStatus",
    "voice_model_report",
    "report_complete",
    "format_report",
]
