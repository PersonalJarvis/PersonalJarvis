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
- **Honest optionality.** ``required`` separates the models the DEFAULT voice
  path needs (bundled neural wake backbone + Silero VAD — both ship in-repo)
  from the optional ones (the per-language Vosk custom-wake model and the local
  Whisper models, which are downloaded / behind the ``[full]`` profile). A
  missing optional model is a pending download, not a failure — so
  ``report_complete`` gates only on the required set and never sabotages an
  install that legitimately runs cloud speech.

Seams (module-level functions) keep the heavy imports lazy and let tests inject
fakes without a real config, network, or model cache — same style as
``prefetch.py``.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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


def _vosk_present(language: str | None, data_dir: str | None) -> bool:
    from jarvis.speech.wake_model_fetch import vosk_model_present

    return vosk_model_present(language, data_dir=data_dir)


def _faster_whisper_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def _whisper_cached(name: str) -> bool:
    """True when faster-whisper model ``name`` is already in the local HF cache.

    ``local_files_only`` keeps this a pure cache lookup — it never reaches the
    network. Any failure (not cached, lib missing, odd cache layout) reads as
    "not present".
    """
    from faster_whisper.utils import download_model  # type: ignore[import-not-found]

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


def voice_model_report(cfg: Any | None = None, *, data_dir: str | None = None) -> list[ModelStatus]:
    """Verify, read-only, which voice models are actually ready on disk.

    ``cfg`` defaults to the live config (loaded via the same path the runtime
    uses); pass one explicitly to keep a caller/test hermetic.
    """
    if cfg is None:
        cfg = _safe(_load_config, None)

    items: list[ModelStatus] = []

    # 1. Neural wake-word backbone — bundled in-repo, core path.
    wake_ok = bool(_safe(_wake_backbone_present, False))
    items.append(
        ModelStatus(
            "wake word (neural)",
            wake_ok,
            "bundled with the app"
            if wake_ok
            else "MISSING from the package - partial download? re-run the installer",
            required=True,
        )
    )

    # 2. End-of-speech detection (Silero VAD) — bundled in-repo, core path.
    vad_ok = bool(_safe(_vad_present, False))
    items.append(
        ModelStatus(
            "end-of-speech detection",
            vad_ok,
            "bundled with the app"
            if vad_ok
            else "MISSING from the package - partial download? re-run the installer",
            required=True,
        )
    )

    # 3. Custom-wake model (Vosk, per language) — downloaded once, optional.
    lang = _safe(lambda: _wake_language(cfg), None) if cfg is not None else None
    vosk_ok = bool(_safe(lambda: _vosk_present(lang, data_dir), False))
    items.append(
        ModelStatus(
            f"custom-wake model '{lang or 'default'}'",
            vosk_ok,
            "ready" if vosk_ok else "not downloaded yet - the app fetches it on first use",
            required=False,
        )
    )

    # 4. Local speech recognition (faster-whisper) — optional; cloud is default.
    if not bool(_safe(_faster_whisper_available, False)):
        items.append(
            ModelStatus(
                "local speech model",
                False,
                "not installed - cloud speech is the default "
                "(install the [full] profile for offline speech)",
                required=False,
            )
        )
    else:
        needed = _safe(lambda: _whisper_models_needed(cfg), []) if cfg is not None else []
        for name in needed:
            cached = bool(_safe(lambda n=name: _whisper_cached(n), False))
            items.append(
                ModelStatus(
                    f"local speech model '{name}'",
                    cached,
                    "ready" if cached else "not downloaded yet - the app fetches it on first use",
                    required=False,
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
