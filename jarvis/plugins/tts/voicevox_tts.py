"""VOICEVOX — keyless, on-device Japanese neural text-to-speech.

Talks to a local VOICEVOX Engine over HTTP on 127.0.0.1 (see
:mod:`jarvis.plugins.tts.voicevox_engine`, which starts the engine when it is
installed but not running). Two calls per sentence: ``/audio_query`` builds the
prosody for the text and speaker, ``/synthesis`` renders it to WAV.

Voices are the engine's fictional characters — no real person's voice is
cloned. Each character has its own terms of use; the common one is a credit
line such as "VOICEVOX:<character>" wherever the audio is published.

The speaker is chosen by NAME from the engine's own ``/speakers`` catalogue at
first use, never by a hardcoded numeric id (ids differ between engine builds).
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.tts import voicevox_engine as engine

log = logging.getLogger(__name__)

#: Default character + style: a calm, low male voice that suits an assistant.
#: Proper nouns from the engine's catalogue (the engine only knows them in
#: Japanese); an unknown name falls back to the catalogue's first style.
DEFAULT_SPEAKER = "\u9752\u5c71\u9f8d\u661f"  # Aoyama Ryusei
DEFAULT_STYLE = "\u30ce\u30fc\u30de\u30eb"  # "Normal"

#: One synthesis at a time. The pipeline pre-synthesises the next sentences
#: while the first is still rendering; on a CPU engine running near real time
#: those parallel jobs split the cores and the FIRST sentence finished last
#: (live 2026-09-18: 16 s to the first word). Serialised, sentence 1 is ready
#: in ~1.3 s and the rest follow while it plays.
_SYNTH_LOCK = threading.Lock()


class VoicevoxTTS:
    """Japanese speech from a local VOICEVOX Engine."""

    name = "voicevox"
    supports_streaming = False
    last_voice: str | None = None
    last_voice_provider: str | None = None
    runs_on_device = True

    def __init__(
        self,
        *,
        speaker: str | None = None,
        style: str | None = None,
        speed: float = 1.0,
        volume: float = 1.0,
    ) -> None:
        self._speaker = (speaker or "").strip() or DEFAULT_SPEAKER
        self._style = (style or "").strip() or DEFAULT_STYLE
        self._speed = float(speed) if speed and speed > 0 else 1.0
        self._volume = max(0.0, float(volume if volume is not None else 1.0))
        self._speaker_id: int | None = None
        self._ready = False

    def _prepare(self) -> int:
        """Start the engine if needed and resolve the speaker id. Blocking."""
        if not self._ready:
            if not engine.ensure_running():
                raise RuntimeError(
                    "The VOICEVOX engine is not installed or did not start. "
                    "Run scripts/install_voicevox.py, or pick another voice."
                )
            self._ready = True
        if self._speaker_id is None:
            self._speaker_id = resolve_speaker_id(
                engine.request_json("GET", "/speakers", timeout=15.0),
                self._speaker,
                self._style,
            )
            # Load the voice model now; the engine otherwise loads it inside
            # the first synthesis, which delayed the first spoken word by
            # several seconds.
            try:
                engine.request_bytes(
                    f"/initialize_speaker?speaker={self._speaker_id}&skip_reinit=true",
                    body={},
                    timeout=60.0,
                )
            except Exception as exc:  # noqa: BLE001 - synthesis still loads it lazily
                log.info("VOICEVOX speaker pre-load skipped: %s", exc)
        return self._speaker_id

    def warm(self) -> bool:
        """Start the engine and pre-load this speaker. Blocking; off-loop only."""
        try:
            with _SYNTH_LOCK:
                self._prepare()
            return True
        except Exception as exc:  # noqa: BLE001 - a warm-up failure is a log line
            log.info("VOICEVOX warm-up failed: %s", exc)
            return False

    def _synthesize_sync(self, text: str) -> tuple[bytes, int]:
        with _SYNTH_LOCK:
            return self._synthesize_locked(text)

    def _synthesize_locked(self, text: str) -> tuple[bytes, int]:
        speaker_id = self._prepare()
        from urllib.parse import quote

        query = engine.request_json(
            "POST", f"/audio_query?text={quote(text)}&speaker={speaker_id}", timeout=30.0
        )
        query["speedScale"] = self._speed
        query["volumeScale"] = self._volume
        wav = engine.request_bytes(f"/synthesis?speaker={speaker_id}", body=query)
        return wav_to_pcm(wav)

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        spoken = (text or "").strip()
        if not spoken:
            return
        pcm, rate = await asyncio.to_thread(self._synthesize_sync, spoken)
        if not pcm:
            raise RuntimeError("VOICEVOX returned no audio for this sentence.")
        self.last_voice = f"{self._speaker}/{self._style}"
        self.last_voice_provider = self.name
        yield AudioChunk(pcm=pcm, sample_rate=rate, timestamp_ns=time.monotonic_ns())

    def list_voices(self, language: str | None = None) -> list[str]:
        if not engine.is_up():
            return []
        try:
            speakers = engine.request_json("GET", "/speakers", timeout=5.0)
        except Exception as exc:  # noqa: BLE001 - a listing is best-effort; say why it is empty
            log.info("VOICEVOX speaker list unavailable: %s", exc)
            return []
        return [
            f"{s.get('name')}/{st.get('name')}"
            for s in speakers or []
            for st in s.get("styles") or []
        ]


def resolve_speaker_id(speakers: Any, name: str, style: str) -> int:
    """The style id for ``name``/``style``; the first style of ``name``; else the first style."""
    first_any: int | None = None
    first_of_name: int | None = None
    for spk in speakers or []:
        for st in spk.get("styles") or []:
            sid = st.get("id")
            if not isinstance(sid, int):
                continue
            if first_any is None:
                first_any = sid
            if spk.get("name") == name:
                if first_of_name is None:
                    first_of_name = sid
                if st.get("name") == style:
                    return sid
    if first_of_name is not None:
        return first_of_name
    if first_any is not None:
        log.info("VOICEVOX speaker %r not found; using the catalogue's first voice.", name)
        return first_any
    raise RuntimeError("The VOICEVOX engine lists no speakers.")


def wav_to_pcm(wav: bytes) -> tuple[bytes, int]:
    """16-bit mono PCM and its rate from the engine's WAV response."""
    with wave.open(io.BytesIO(wav)) as w:
        if w.getsampwidth() != 2:
            raise RuntimeError(f"Unexpected VOICEVOX sample width {w.getsampwidth()}.")
        frames = w.readframes(w.getnframes())
        rate = w.getframerate()
        if w.getnchannels() == 2:
            import numpy as np

            stereo = np.frombuffer(frames, dtype=np.int16).reshape(-1, 2)
            frames = stereo.mean(axis=1).astype(np.int16).tobytes()
        return frames, rate
