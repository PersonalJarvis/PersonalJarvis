"""Download-everything prefetch shared by the installer and the app.

Called by ``python -m jarvis --prefetch`` (which ``install/installer.py``
invokes) so that when the install command finishes, the first launch has
nothing left to download. Resolution reuses the SAME config defaults the
runtime reads, so the installer and the app can never disagree about which
models are needed.

Every step is best-effort: a failed download prints an honest note and the
runtime's lazy download remains the safety net — a flaky mirror must never
brick an install (CLAUDE.md section 3). Works headless: no audio device,
GPU, or keyring is touched, only the on-disk model caches.
"""
from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable
from typing import Any


def _wakeword_bundle_present() -> bool:
    """The always-on neural wake models ship in-repo (jarvis/assets/wakeword)."""
    try:
        import jarvis.assets

        return jarvis.assets.bundled_wakeword_models() is not None
    except Exception:  # noqa: BLE001 — a probe must never crash the prefetch
        return False


def _faster_whisper_available() -> bool:
    """True when the optional local-Whisper stack is installed."""
    return importlib.util.find_spec("faster_whisper") is not None


def _load_config() -> Any:
    """Seam for tests — the heavy config import stays out of module import."""
    from jarvis.core.config import load_config

    return load_config()


def _ensure_vosk(language: str | None, **kw: Any) -> Any:
    """Seam for tests — heavy import stays out of module import."""
    from jarvis.speech.wake_model_fetch import ensure_vosk_model

    return ensure_vosk_model(language, **kw)


def _vosk_language() -> str | None:
    try:
        from jarvis.speech.wake_model_fetch import resolve_wake_language

        return resolve_wake_language(_load_config())
    except Exception:  # noqa: BLE001 — config read must never brick prefetch
        return None


def _supported_vosk_languages() -> tuple[str, ...]:
    """Every language a user can select during first-run onboarding."""
    from jarvis.speech.wake_model_fetch import VOSK_MODELS

    return tuple(VOSK_MODELS)


def _whisper_models_needed() -> list[str]:
    """The faster-whisper model names the CURRENT config would load at runtime.

    Mirrors ``jarvis/plugins/stt``: the wake-match model always
    (``stt.wake_model``, default ``base``); the utterance model only when the
    local provider is selected. Order = download order (small first).
    """
    cfg = _load_config()
    models = [cfg.stt.wake_model]
    if cfg.stt.provider == "faster-whisper" and cfg.stt.model not in models:
        models.append(cfg.stt.model)
    return models


def _download_whisper_model(name: str) -> None:
    """Fetch one faster-whisper model into the standard HuggingFace cache —
    the exact cache ``WhisperModel(name)`` resolves at runtime."""
    from faster_whisper.utils import download_model  # type: ignore[import-not-found,import-untyped]

    download_model(name)


def prefetch_all(
    echo: Callable[[str], None] = print,
    *,
    all_wake_languages: bool = False,
) -> int:
    """Prefetch every artifact the default voice path needs. 0 = complete.

    Returns 1 when at least one download failed — callers keep going (the
    runtime lazy-download remains the safety net), the exit code just keeps
    the installer's summary honest. The advertised desktop installer passes
    ``all_wake_languages=True`` because onboarding has not selected a language
    yet; the headless/base path keeps only its configured language.
    """
    # Tame the Hugging Face downloader BEFORE it is imported (both env vars
    # are read at import time): no tqdm progress bars — they shred the
    # installer's line-oriented transcript (Intel-Mac field report
    # 2026-07-16) — and no "unauthenticated requests / set HF_TOKEN" warning,
    # because anonymous downloads ARE the intended end-user path and telling
    # a downloader to create a HF token is pure confusion. ``setdefault``
    # keeps any explicit user override in force.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")

    failed = False

    if _wakeword_bundle_present():
        echo("wake-word models: bundled with the app - nothing to download")
    else:
        echo(
            "wake-word models: bundle missing - openWakeWord will auto-download "
            "on first use"
        )

    # Any-word wake model (vosk_kws): the full desktop installer runs BEFORE
    # onboarding knows whether the user will choose English, German, or Spanish,
    # so it caches every supported language. The internal headless/base path
    # keeps its configured language to preserve the small-server floor.
    languages: tuple[str | None, ...]
    if all_wake_languages:
        try:
            languages = _supported_vosk_languages()
        except Exception as exc:  # noqa: BLE001 — keep the current-language fallback
            failed = True
            echo(
                f"wake-language catalog could not be read ({exc}); "
                "prefetching the configured language only"
            )
            languages = (_vosk_language(),)
    else:
        languages = (_vosk_language(),)
    for lang in languages:
        try:
            out = _ensure_vosk(lang, echo=echo)
            if out is None:
                failed = True
        except Exception as exc:  # noqa: BLE001 — honest note, never fatal
            failed = True
            echo(
                f"wake model '{lang or 'default'}': could not provision ({exc}); "
                "it will retry at first run"
            )

    if not _faster_whisper_available():
        echo(
            "local Whisper models: skipped (faster-whisper not installed - "
            "cloud STT is the default)"
        )
        return 1 if failed else 0

    try:
        whisper_models = _whisper_models_needed()
    except Exception as exc:  # noqa: BLE001 — cache the safe packaged floor
        failed = True
        whisper_models = ["base"]
        echo(
            f"speech-model config could not be read ({exc}); "
            "prefetching the packaged 'base' wake model"
        )
    for name in whisper_models:
        echo(f"downloading speech model '{name}' (one-time, cached for every later start)")
        try:
            _download_whisper_model(name)
            echo(f"speech model '{name}': ready")
        except Exception as exc:  # noqa: BLE001 — honest note, never fatal
            failed = True
            echo(
                f"speech model '{name}' could not be downloaded ({exc}); "
                "it will download on first launch instead"
            )
    return 1 if failed else 0


__all__ = ["prefetch_all"]
