"""On-device text-to-speech: Piper voices played through sherpa-onnx.

The speaking half of the local voice path — the first TTS in this repo that
needs no key and sends no text anywhere. Piper is the de-facto standard for
offline neural speech: small VITS voices exported to ONNX, real time on a
Raspberry Pi, and the only widely-used open family with genuine German voices
(Kokoro, the prettier alternative, ships nine languages and German is not one).

Two decisions worth stating, because both are easy to get wrong later:

**Licensing.** Piper's own runtime moved from MIT to GPL-3 in October 2025
(the project now lives at OHF-Voice/piper1-gpl). This repo is MIT, so the GPL
library is NOT used: the voices are plain ONNX files, and sherpa-onnx
(Apache-2.0) loads them. Do not "simplify" this by depending on piper1-gpl.

**One voice speaks one language.** Unlike a cloud TTS, a Piper voice is
monolingual. The pipeline resolves ONE output language per turn
(``resolve_output_language``) and hands it here as a BCP-47 code; this provider
maps it to the matching voice. That mapping is the whole reason the local voice
can honour the runtime-language doctrine instead of answering German text in an
English accent.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.core.protocols import AudioChunk

log = logging.getLogger(__name__)

#: int16 full scale — Piper emits float32 in [-1, 1]; the pipeline plays int16.
_INT16_FULL_SCALE = 32767.0

#: Speed factor handed to the synthesiser. 1.0 is the voice's natural pace.
_DEFAULT_SPEED = 1.0


class PiperLocalTTS:
    """Local neural text-to-speech (keyless, on-device, multilingual by voice)."""

    name = "piper-local"
    supports_streaming = True
    #: Which voice actually spoke last — read by the per-turn transcript label,
    #: so a new provider that leaves these unset makes the label lie.
    last_voice: str | None = None
    last_voice_provider: str | None = None
    #: Nothing spoken here leaves the machine. Mirrors the STT-side declaration
    #: so a caller can ask the same question of both halves of the voice path.
    runs_on_device = True

    def __init__(
        self,
        *,
        voice: str | None = None,
        gender: str | None = None,
        num_threads: int = 2,
        speed: float = _DEFAULT_SPEED,
    ) -> None:
        self._preferred_voice = (voice or "").strip() or None
        # Voice PROFILE rather than a single voice id: the pipeline may switch
        # language between turns, and the replacement must be the same kind of
        # speaker. Without this a language switch also switches sex, which is
        # the BUG-086 complaint in a different provider.
        self._gender = (gender or "").strip().lower() or "masculine"
        self._num_threads = max(1, int(num_threads))
        self._speed = float(speed) if speed and speed > 0 else _DEFAULT_SPEED
        #: model_id -> loaded engine. Voices load lazily and stay cached; a
        #: German-only conversation never pays for the Spanish voice.
        self._engines: dict[str, Any] = {}
        self._load_lock = asyncio.Lock()

    # -- voice selection ----------------------------------------------------
    def _catalog(self) -> dict[str, Any]:
        from jarvis.speech.local_models import SHERPA_BUNDLES

        return {
            model_id: bundle
            for model_id, bundle in SHERPA_BUNDLES.items()
            if bundle.language
        }

    def _installed_voices(self) -> dict[str, Any]:
        """Catalogued voices whose files are actually on this machine.

        A voice that is merely *listed* is not offerable: picking it would fail
        at synthesis time, which in a voice assistant means silence.
        """
        from jarvis.speech.local_models import bundle_present

        return {
            model_id: bundle
            for model_id, bundle in self._catalog().items()
            if bundle_present(model_id)
        }

    def _resolve_voice(
        self, voice: str | None, language_code: str | None
    ) -> str | None:
        """Pick the voice for ONE turn: explicit request, else language + profile.

        Returns None when nothing is installed — the caller then raises the
        honest "no voice downloaded" error rather than emitting silence.
        """
        installed = self._installed_voices()
        if not installed:
            return None
        requested = (voice or self._preferred_voice or "").strip()
        if requested in installed:
            return requested

        language = (language_code or "").strip().lower().split("-", 1)[0]
        candidates = [
            model_id
            for model_id, bundle in installed.items()
            if not language or bundle.language == language
        ]
        if not candidates:
            # The turn's language has no local voice. Speaking it in another
            # language's accent is the wrong trade-off to make silently, but
            # saying nothing is worse — so fall back and say so in the log.
            log.warning(
                "No local Piper voice is installed for language %r; using another "
                "installed voice. Download the matching voice from the API-Keys "
                "view to fix the accent.",
                language or "unknown",
            )
            candidates = list(installed)
        # Same speaker profile across languages where possible.
        preferred = [
            m for m in candidates if installed[m].gender == self._gender
        ]
        return (preferred or candidates)[0]

    def list_voices(self, language: str | None = None) -> list[str]:
        """Installed voices, optionally filtered by language."""
        installed = self._installed_voices()
        if language:
            short = language.lower().split("-", 1)[0]
            return [m for m, b in installed.items() if b.language == short]
        return list(installed)

    # -- engine -------------------------------------------------------------
    def _build_engine(self, model_id: str) -> Any:
        try:
            import sherpa_onnx  # noqa: PLC0415 — optional engine, lazy on purpose
        except ImportError as exc:
            raise RuntimeError(
                "The local speech runtime (sherpa-onnx) is not installed, so the "
                "on-device voice cannot speak. Install it from the API-Keys view."
            ) from exc

        from jarvis.speech.sherpa_models import model_paths

        paths = model_paths(model_id)
        onnx = next(
            (p for name, p in paths.items() if name.endswith(".onnx")), None
        )
        tokens = paths.get("tokens.txt")
        data_dir = paths.get("espeak-ng-data")
        if onnx is None or tokens is None or data_dir is None:
            raise RuntimeError(f"Voice '{model_id}' is not catalogued correctly.")
        missing = [p.name for p in (onnx, tokens, data_dir) if not p.exists()]
        if missing:
            raise RuntimeError(
                f"The voice '{model_id}' is not fully downloaded (missing: "
                f"{', '.join(missing)}). Download it from the API-Keys view."
            )

        log.info("Loading local Piper voice %s", model_id)
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(onnx),
                    tokens=str(tokens),
                    # espeak-ng's phoneme rules: how letters become sounds. A
                    # Piper voice cannot speak without them.
                    data_dir=str(data_dir),
                ),
                num_threads=self._num_threads,
                provider="cpu",
            ),
        )
        return sherpa_onnx.OfflineTts(config)

    async def _engine_for(self, model_id: str) -> Any:
        engine = self._engines.get(model_id)
        if engine is not None:
            return engine
        async with self._load_lock:
            engine = self._engines.get(model_id)
            if engine is None:
                engine = await asyncio.to_thread(self._build_engine, model_id)
                self._engines[model_id] = engine
        return engine

    # -- synthesis ----------------------------------------------------------
    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Speak *text* with the voice matching this turn's output language."""
        spoken = (text or "").strip()
        if not spoken:
            return
        model_id = self._resolve_voice(voice, language_code)
        if model_id is None:
            raise RuntimeError(
                "No local voice is downloaded yet, so the on-device voice cannot "
                "speak. Download the voices from the API-Keys view, or switch "
                "voice output to a provider you have a key for."
            )
        engine = await self._engine_for(model_id)
        sample_rate = int(getattr(engine, "sample_rate", 0) or 0)
        chunks: queue.Queue[bytes | object] = queue.Queue(maxsize=8)
        end = object()
        stopped = threading.Event()
        failure: list[Exception] = []
        emitted = threading.Event()

        def _pcm(samples: Any) -> bytes:
            values = np.asarray(samples, dtype=np.float32)
            if values.size == 0:
                return b""
            return (
                np.clip(values, -1.0, 1.0) * _INT16_FULL_SCALE
            ).astype(np.int16).tobytes()

        def _put(value: bytes | object) -> bool:
            while not stopped.is_set():
                try:
                    chunks.put(value, timeout=0.05)
                    return True
                except queue.Full:
                    # Consumer is just behind — retry until stopped, not an error.
                    continue
            return False

        def _callback(samples: Any, _progress: float) -> int:
            data = _pcm(samples)
            if data:
                emitted.set()
                if not _put(data):
                    return 1
            return 1 if stopped.is_set() else 0

        nonlocal_rate = [sample_rate]

        def _generate() -> None:
            try:
                try:
                    audio = engine.generate(
                        spoken, 0, self._speed, callback=_callback
                    )
                except TypeError as exc:
                    # Older sherpa-onnx builds and lightweight test engines do
                    # not expose the callback overload. Keep the compatible
                    # whole-sentence path instead of failing silent.
                    detail = str(exc).lower()
                    if not any(
                        marker in detail
                        for marker in (
                            "callback",
                            "incompatible function arguments",
                            "unexpected keyword",
                        )
                    ):
                        raise
                    audio = engine.generate(spoken, 0, self._speed)
                if not sample_rate:
                    rate = int(getattr(audio, "sample_rate", 0) or 0)
                    if rate:
                        nonlocal_rate[0] = rate
                if not emitted.is_set():
                    data = _pcm(getattr(audio, "samples", ()))
                    if data:
                        emitted.set()
                        _put(data)
            except Exception as exc:  # noqa: BLE001 - forwarded to async owner
                failure.append(exc)
            finally:
                _put(end)

        worker = asyncio.create_task(asyncio.to_thread(_generate), name="piper-tts")
        self.last_voice = model_id
        self.last_voice_provider = self.name
        try:
            while True:
                item = await asyncio.to_thread(chunks.get)
                if item is end:
                    break
                assert isinstance(item, bytes)
                rate = nonlocal_rate[0] or int(
                    getattr(engine, "sample_rate", 0) or 0
                )
                if rate <= 0:
                    raise RuntimeError("The local voice returned no sample rate.")
                yield AudioChunk(
                    pcm=item,
                    sample_rate=rate,
                    timestamp_ns=0,
                    channels=1,
                )
            await worker
            if failure:
                raise failure[0]
            if not emitted.is_set() and chunks.empty():
                log.warning("Local voice %s produced no audio for the turn.", model_id)
        finally:
            stopped.set()
            if not worker.done():
                with suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(worker), timeout=1.0)

    async def _ensure_client(self) -> None:
        """Warm-up hook the speech pipeline calls; loads the default voice.

        Kept cheap and failure-tolerant: warming is an optimisation, and a host
        with no voices downloaded must still boot and tell the user why it
        cannot speak when a turn actually needs it.
        """
        model_id = self._resolve_voice(None, None)
        if model_id is None:
            return
        try:
            await self._engine_for(model_id)
        except Exception as exc:  # noqa: BLE001 — warm-up must never break boot
            log.debug("Local voice warm-up skipped (%s).", exc)

    async def aclose(self) -> None:
        """Drop the loaded voices so a switched-away provider frees its memory."""
        self._engines.clear()


def voice_files(model_id: str) -> dict[str, Path]:
    """Paths of one voice's files — exposed for diagnostics and tests."""
    from jarvis.speech.sherpa_models import model_paths

    return model_paths(model_id)
