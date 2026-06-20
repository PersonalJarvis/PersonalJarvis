"""STT provider plugins (faster-whisper, OpenAI, Groq, Deepgram).

This package exposes a single ``build_stt_from_config`` factory that turns an
``STTConfig`` into the configured ``STTProvider``-conformant instance via the
``jarvis.stt`` entry-point group. Local Whisper stays the fallback so the wake-
detector path keeps working when no cloud provider is registered.
"""
from __future__ import annotations

from importlib import metadata as importlib_metadata
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


__all__ = ["build_stt_from_config"]
