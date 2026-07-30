"""On-device streaming STT: NVIDIA Nemotron 3.5 through sherpa-onnx.

The second local voice-input option next to Whisper, and a genuinely different
trade rather than a second flavour of the same one:

* **It streams.** A cache-aware FastConformer transducer consumes audio in
  fixed chunks while the person is still speaking, instead of waiting for the
  utterance to end and then transcribing it in one pass.
* **It runs on a CPU.** ONNX Runtime, int8-quantised, ~690 MB — no torch, no
  CUDA, no NVIDIA hardware despite the model's origin. That matters far more
  than raw benchmark position: the baseline install is a machine with no GPU.
* **It is multilingual** (40 locales, German among them). The English-only
  Nemotron sibling was deliberately NOT used: fed German speech it does not
  fail, it phonetically mangles it into English words — the same trap the
  Distil-Whisper checkpoints carry, and a silent wrong answer is worse than an
  honest failure because it flows straight to the brain.

Structural provider (duck-typed against ``STTProvider``, no inheritance). Both
the engine and the model are optional and absent on a base install, so every
heavy import is lazy and a missing piece produces one honest error rather than
a stack trace three layers down.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.audio.capture import pcm_bytes_to_np
from jarvis.core.protocols import AudioChunk, Transcript

log = logging.getLogger(__name__)

#: The per-call ``language`` value that REQUESTS detection. Spelled out here
#: (as in every other STT plugin) because plugins may not import from jarvis.*.
AUTO_LANGUAGE = "auto"

#: Silence appended before decoding. A streaming transducer emits its last
#: words only once enough trailing context has arrived; without this padding the
#: final syllable of an utterance is routinely cut off.
_TAIL_SILENCE_S = 0.5

#: Silence PREPENDED before the utterance — the less obvious half, and a real
#: defect without it. A cache-aware encoder starts with an empty context, so the
#: first chunk is decoded with nothing behind it and the opening word is lost.
#: Measured on the model's own German sample — "Alles hat ein Ende, nur die  # i18n-allow: quoted test-audio transcript, the evidence for this constant
#: Wurst hat zwei": 0.0 s dropped the opening word entirely, 0.3 s produced a
#: mangled version of it, and from 0.6 s the sentence came back complete
#: (measured 2026-07-29). 0.8 s keeps a margin; at ~9x
#: realtime on a CPU the extra silence costs under 100 ms. In a voice assistant
#: the first word is usually the command, so losing it is not cosmetic.
_LEAD_SILENCE_S = 0.8

#: int16 full scale — the divisor that maps PCM to the [-1, 1] floats the
#: feature extractor is configured for (``normalize_samples`` default).
_INT16_FULL_SCALE = 32768.0


class NemotronLocalSTT:
    """Local streaming speech-to-text via sherpa-onnx (keyless, on-device)."""

    name = "nemotron-local"
    # The privacy declaration the rest of the app keys on: nothing this
    # recognizer hears leaves the machine. The dictation polish pass reads it to
    # decide whether transcripts may be sent to a cloud model at all, so getting
    # it wrong in the "cloud" direction would upload text from someone who chose
    # a local recognizer precisely to prevent that.
    runs_on_device = True
    # The model streams natively; this flag describes the CALLER-facing contract,
    # and today the pipeline hands us complete VAD-segmented utterances. Turning
    # it on would promise incremental partials through ``stream_transcribe``,
    # which is a separate piece of pipeline work — claiming it before it exists
    # would be the dead-config lie this repo keeps auditing for.
    supports_streaming = False

    def __init__(
        self,
        *,
        language: str | None = None,
        model_dir: str | Path | None = None,
        num_threads: int = 2,
        provider: str = "cpu",
    ) -> None:
        self._language = (language or "").strip() or None
        self._model_dir = Path(model_dir) if model_dir else None
        self._num_threads = max(1, int(num_threads))
        self._provider = provider or "cpu"
        self._recognizer: Any | None = None
        # One decode at a time per instance. sherpa-onnx supports concurrent
        # streams by design (it ships a websocket server), but this instance is
        # a single shared object on the voice path and serialising its use costs
        # nothing here — a Jarvis turn decodes one utterance at a time — while
        # removing a whole class of native-engine race (AP-24).
        self._decode_lock = asyncio.Lock()

    # -- construction -------------------------------------------------------
    def _resolve_model_files(self) -> dict[str, Path]:
        from jarvis.speech.local_models import NEMOTRON_MODEL_ID
        from jarvis.speech.sherpa_models import model_paths

        if self._model_dir is not None:
            names = (
                "encoder.int8.onnx",
                "decoder.int8.onnx",
                "joiner.int8.onnx",
                "tokens.txt",
            )
            return {name: self._model_dir / name for name in names}
        return model_paths(NEMOTRON_MODEL_ID)

    def _ensure_model(self) -> None:
        """Build the recognizer on first use. Raises one honest error if it cannot.

        Deliberately NOT done in ``__init__``: constructing a provider must stay
        cheap and side-effect free (AP-26), and the factory builds one during
        startup where a 690 MB model load has no business being.
        """
        if self._recognizer is not None:
            return
        try:
            import sherpa_onnx  # noqa: PLC0415 — optional engine, lazy on purpose
        except ImportError as exc:
            raise RuntimeError(
                "The local speech runtime (sherpa-onnx) is not installed, so "
                "Nemotron cannot transcribe. Install it from the API-Keys view, "
                "or switch voice input to a provider you have a key for."
            ) from exc

        files = self._resolve_model_files()
        missing = [name for name, path in files.items() if not path.is_file()]
        if missing:
            raise RuntimeError(
                "The Nemotron model files are not on this machine yet "
                f"(missing: {', '.join(sorted(missing))}). Download them from "
                "the API-Keys view."
            )

        log.info(
            "Loading local Nemotron model (%s, %d threads) from %s",
            self._provider,
            self._num_threads,
            files["tokens.txt"].parent,
        )
        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(files["tokens.txt"]),
            encoder=str(files["encoder.int8.onnx"]),
            decoder=str(files["decoder.int8.onnx"]),
            joiner=str(files["joiner.int8.onnx"]),
            num_threads=self._num_threads,
            provider=self._provider,
            # The transducer family this bundle belongs to. Without it the
            # runtime guesses from the graph, and a wrong guess fails at load
            # rather than quietly — but stating it keeps the failure impossible.
            model_type="nemo_transducer",
            decoding_method="greedy_search",
        )

    # -- transcription ------------------------------------------------------
    async def transcribe(self, audio: AsyncIterator[AudioChunk]) -> Transcript:
        """Collect the whole utterance, then decode it in one pass."""
        pieces: list[np.ndarray] = []
        sample_rate = 16_000
        async for chunk in audio:
            pieces.append(pcm_bytes_to_np(chunk.pcm))
            sample_rate = chunk.sample_rate
        if not pieces:
            return Transcript(text="", language="unknown", confidence=0.0)
        return await self._decode(np.concatenate(pieces), sample_rate, None)

    async def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16_000,
        language: str | None = None,
    ) -> Transcript:
        """Direct path for VAD output: int16 PCM bytes -> transcript."""
        return await self._decode(
            pcm_bytes_to_np(pcm_bytes), sample_rate, language
        )

    async def stream_transcribe(
        self, audio: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[Transcript]:
        """One final transcript, matching ``supports_streaming = False``.

        The protocol requires the method; yielding exactly one element is the
        documented non-streaming shape. It does not pretend to emit partials.
        """
        yield await self.transcribe(audio)

    def _language_for(self, per_call: str | None) -> str | None:
        """The language for ONE call; ``None`` means "let the model detect".

        ``"auto"`` is an explicit request to DETECT and therefore clears the
        configured pin for this call — treating it as "no argument given" is
        what once let dictation inherit a German pin and write German speech in
        English.
        """
        if per_call is None or not str(per_call).strip():
            return self._language
        value = str(per_call).strip()
        return None if value.lower() == AUTO_LANGUAGE else value

    async def _decode(
        self, samples: np.ndarray, sample_rate: int, per_call_language: str | None
    ) -> Transcript:
        if samples.size == 0:
            return Transcript(text="", language="unknown", confidence=0.0)
        language = self._language_for(per_call_language)
        async with self._decode_lock:
            return await asyncio.to_thread(
                self._decode_blocking, samples, sample_rate, language
            )

    def _decode_blocking(
        self, samples: np.ndarray, sample_rate: int, language: str | None
    ) -> Transcript:
        self._ensure_model()
        recognizer = self._recognizer
        assert recognizer is not None  # _ensure_model raises otherwise

        audio = np.asarray(samples, dtype=np.float32)
        # pcm_bytes_to_np hands back int16-scaled values; the feature extractor
        # is configured for [-1, 1]. Scaling by a peak check rather than blindly
        # keeps a caller that already normalised from being divided twice.
        if np.max(np.abs(audio)) > 1.0:
            audio = audio / _INT16_FULL_SCALE

        stream = recognizer.create_stream()
        if language:
            # Per-stream language pin (the multilingual encoder's prompt input).
            # Best-effort: an engine build without the option must not take the
            # turn down — it simply auto-detects, which is the documented
            # default behaviour anyway.
            try:
                stream.set_option("language", language)
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "Nemotron language pin %r not applied (%s); auto-detecting.",
                    language,
                    exc,
                )
        stream.accept_waveform(
            sample_rate, np.zeros(int(sample_rate * _LEAD_SILENCE_S), dtype=np.float32)
        )
        stream.accept_waveform(sample_rate, audio)
        stream.accept_waveform(
            sample_rate, np.zeros(int(sample_rate * _TAIL_SILENCE_S), dtype=np.float32)
        )
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        result = recognizer.get_result(stream)
        raw = (result or "").strip()
        # Same cleanup pass every other provider runs, so the transcript a user
        # gets does not depend on which engine they picked. ``raw_text`` keeps
        # the decode for the callers that must not read an edited string (the
        # dictation lane, wake verification).
        from jarvis.plugins.stt.transcript_filter import clean_stt_text

        text = clean_stt_text(raw, language=language)
        return Transcript(
            text=text,
            raw_text=raw,
            language=language or "auto",
            # The transducer exposes no calibrated per-utterance probability;
            # inventing one would put a fabricated number in front of every
            # downstream confidence check.
            confidence=1.0 if text else 0.0,
        )

    async def aclose(self) -> None:
        """Release the recognizer so a switched-away provider frees its memory."""
        self._recognizer = None
