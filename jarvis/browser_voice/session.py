"""Per-connection browser-microphone voice session (B2).

Ports ``jarvis.telephony.session.TelephonyCallSession`` to browser audio. Raw
int16 PCM frames arrive over a WebSocket from an AudioWorklet (at the browser's
sample rate), are resampled to 16 kHz, endpointed by the torch-free
``EnergyEndpointer``, transcribed (STT), routed to the brain, scrubbed for voice,
synthesized (TTS, 24 kHz int16) and the audio bytes are streamed straight back as
binary WS frames for Web Audio playback.

Transport-decoupled like the telephony session: ``send_binary`` / ``send_json``
are injected async callables, so this is fully testable with fakes and a sink —
no socket, no models, no API key. Stdlib-only on the audio path: it NEVER imports
sounddevice, so a headless €5 VPS reaches the full voice experience.

Pipeline per turn (mirrors the mic + phone paths, AD-OE seams):

    inbound int16 PCM @ browser rate
      -> Resampler(browser_rate -> 16 kHz)
      -> EnergyEndpointer (VAD): accumulate until end-of-turn
      -> STT.transcribe_pcm(pcm, 16000) -> text
      -> ... brain turn ...
      -> scrub_for_voice (regex only, AP-11)
      -> TTS.synthesize(scrubbed) -> 24 kHz int16 PCM
      -> send_binary(pcm) per chunk  (+ tts_start / tts_end control frames)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.brain.output_filter import scrub_for_voice
from jarvis.browser_voice.audio import (
    STT_SAMPLE_RATE,
    TTS_SAMPLE_RATE,
    EnergyEndpointer,
    Resampler,
)

log = logging.getLogger("jarvis.browser_voice")

SendBinary = Callable[[bytes], Awaitable[None]]
SendJson = Callable[[dict[str, Any]], Awaitable[None]]


class BrowserVoiceSession:
    """One browser microphone/speaker session over a ``/ws/audio`` socket.

    Args:
        session_id: opaque id for logging.
        send_binary: async sink for raw TTS PCM frames -> the browser.
        send_json: async sink for JSON control frames -> the browser.
        stt: object exposing ``transcribe_pcm(pcm, sample_rate=16000)``.
        brain: object exposing ``generate_stream(text) -> AsyncIterator[str]``.
        tts: object exposing ``synthesize(text, language_code=...) ->
            AsyncIterator[AudioChunk]`` (``.pcm`` int16, ``.sample_rate``).
        browser_sample_rate: the AudioWorklet capture rate (usually 48000).
        language_code: BCP-47 language pin for TTS.
        max_session_seconds: hard cap (advisory; the route enforces it).
        bus: optional EventBus (best-effort; unused on the hot path).
    """

    def __init__(
        self,
        *,
        session_id: str,
        send_binary: SendBinary,
        send_json: SendJson,
        stt: Any,
        brain: Any,
        tts: Any,
        browser_sample_rate: int = 48_000,
        language_code: str = "de-DE",
        max_session_seconds: int = 1800,
        bus: Any = None,
    ) -> None:
        self.session_id = session_id
        self._send_binary = send_binary
        self._send_json = send_json
        self._stt = stt
        self._brain = brain
        self._tts = tts
        self.browser_sample_rate = int(browser_sample_rate or STT_SAMPLE_RATE)
        self.language_code = language_code or "de-DE"
        self.max_session_seconds = max_session_seconds
        self._bus = bus

        self._in_resampler = Resampler(self.browser_sample_rate, STT_SAMPLE_RATE)
        self._endpointer = EnergyEndpointer(sample_rate=STT_SAMPLE_RATE)

        self._started_at = time.time()
        self._turns = 0
        self._speaking = False
        self._processing = False
        self._tts_task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._ended = False

    # -- public properties -------------------------------------------------

    @property
    def turns(self) -> int:
        return self._turns

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def duration_s(self) -> float:
        return time.time() - self._started_at

    # -- inbound audio -----------------------------------------------------

    async def handle_audio_frame(self, pcm_native: bytes) -> None:
        """Process one inbound binary WS frame (raw int16 at the browser rate)."""
        if self._ended or not pcm_native:
            return
        try:
            if self.browser_sample_rate == STT_SAMPLE_RATE:
                pcm16 = bytes(pcm_native)
            else:
                pcm16 = self._in_resampler.process(bytes(pcm_native))
        except Exception:  # noqa: BLE001 — malformed/odd-length frame, drop it
            return
        utterance = self._endpointer.push(pcm16)
        if utterance is not None and not self._processing:
            # Run the turn off the inbound-frame path so we keep draining audio.
            self._turn_task = asyncio.create_task(
                self._run_turn(utterance), name=f"bv-turn-{self.session_id}"
            )
            self._turn_task.add_done_callback(self._on_turn_done)

    async def handle_control(self, msg: dict[str, Any]) -> None:
        """Process a JSON control frame from the browser."""
        kind = str(msg.get("type", ""))
        if kind == "audio_start":
            rate = int(msg.get("sample_rate", self.browser_sample_rate) or self.browser_sample_rate)
            if rate != self.browser_sample_rate:
                self.browser_sample_rate = rate
                self._in_resampler = Resampler(rate, STT_SAMPLE_RATE)
            # Drop any audio buffered at the old rate so STT never sees a
            # rate-mismatched blob across a reconnect / rate change.
            self._endpointer.reset()
            lang = msg.get("language")
            if isinstance(lang, str) and lang:
                self.language_code = lang
            await self._send_json({"type": "audio_ready"})
        elif kind == "barge_in":
            await self._barge_in()
        elif kind == "audio_stop":
            await self.end(reason="client_stop")

    def _on_turn_done(self, task: asyncio.Task[None]) -> None:
        """Surface a turn task that died outside _run_turn's own guard (a bare
        create_task otherwise loses the traceback with no session context)."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.warning("browser_voice[%s] turn task raised", self.session_id, exc_info=exc)

    # -- turn loop ---------------------------------------------------------

    async def _run_turn(self, utterance_pcm16: bytes) -> None:
        if self._ended:
            return
        self._processing = True
        try:
            transcript = await self._transcribe(utterance_pcm16)
            text = (transcript or "").strip()
            if not text:
                # End-of-turn fired but nothing transcribable — let the browser
                # reset any "thinking" affordance (AD-OE6: never a silent drop).
                await self._send_json({"type": "vad_silence"})
                return
            log.info("browser_voice[%s] user: %s", self.session_id, text)
            await self._send_json({"type": "transcript", "text": text, "is_final": True})

            response = await self._think(text)
            spoken = scrub_for_voice(response, language=self._lang_short()).cleaned
            if not spoken.strip():
                return
            self._tts_task = asyncio.create_task(self._speak(spoken))
            try:
                await self._tts_task
            except asyncio.CancelledError:
                pass  # barge-in cancelled playback mid-stream — normal
            # A turn is "complete" once the response was (at least partly) spoken,
            # mirroring the telephony session's count semantics.
            self._turns += 1
        except Exception:  # noqa: BLE001 — a turn failure must never kill the session
            log.warning("browser_voice[%s] turn failed", self.session_id, exc_info=True)
        finally:
            self._processing = False

    async def _transcribe(self, pcm16: bytes) -> str:
        result = await self._stt.transcribe_pcm(pcm16, sample_rate=STT_SAMPLE_RATE)
        return getattr(result, "text", "") or ""

    async def _think(self, text: str) -> str:
        chunks: list[str] = []
        async for chunk in self._brain.generate_stream(text):
            if chunk:
                chunks.append(chunk)
        return "".join(chunks)

    async def _speak(self, text: str) -> int:
        """Synthesize ``text`` and stream the raw PCM straight back as binary WS
        frames. The browser plays them via Web Audio at ``TTS_SAMPLE_RATE`` — no
        server resample (off-rate chunks are normalized to that rate). Returns
        the number of binary frames sent. Cancellable: a barge-in cancels the
        underlying task mid-stream.
        """
        from jarvis.browser_voice.audio import resample_pcm16  # noqa: PLC0415 — off-rate fallback

        self._speaking = True
        sent = 0
        try:
            await self._send_json({"type": "tts_start", "sample_rate": TTS_SAMPLE_RATE})
            async for chunk in self._tts.synthesize(text, language_code=self.language_code):
                pcm = getattr(chunk, "pcm", b"")
                rate = int(getattr(chunk, "sample_rate", TTS_SAMPLE_RATE) or TTS_SAMPLE_RATE)
                if not pcm:
                    continue
                if rate != TTS_SAMPLE_RATE:
                    pcm = resample_pcm16(pcm, rate, TTS_SAMPLE_RATE)
                await self._send_binary(pcm)
                sent += 1
            await self._send_json({"type": "tts_end"})
        finally:
            self._speaking = False
        return sent

    # -- barge-in / lifecycle ---------------------------------------------

    async def _barge_in(self) -> None:
        """Cancel in-flight TTS playback and tell the browser to flush its queue.

        On a mid-stream cancel the ``_speak`` coroutine never reaches its
        ``tts_end``; without a signal the browser's playback queue would hang
        (review BLOCKER). The canceller emits ``tts_cancel`` — best-effort,
        since the socket may already be closing.
        """
        if self._tts_task is not None and not self._tts_task.done():
            self._tts_task.cancel()
        self._speaking = False
        try:
            await self._send_json({"type": "tts_cancel"})
        except Exception:  # noqa: BLE001, S110 — best-effort on a closing socket
            pass

    async def end(self, *, reason: str = "") -> None:
        """End the session: cancel playback, mark ended."""
        if self._ended:
            return
        self._ended = True
        if self._tts_task is not None and not self._tts_task.done():
            self._tts_task.cancel()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
        log.info(
            "browser_voice[%s] ended: reason=%s turns=%d dur=%.1fs",
            self.session_id,
            reason,
            self._turns,
            self.duration_s,
        )

    def _lang_short(self) -> str:
        # scrub_for_voice only knows the runtime locales de/en/es (AP-11, regex
        # only); an unknown tag would silently apply the wrong blacklist.
        tag = (self.language_code or "de-DE").split("-", 1)[0].lower()
        return tag if tag in {"de", "en", "es"} else "de"
