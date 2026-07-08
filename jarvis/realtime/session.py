# jarvis/realtime/session.py
"""RealtimeVoiceSession — the duplex session that slots into /ws/audio.

Implements the same duck interface (handle_audio_frame/handle_control/end) as
BrowserVoiceSession, so the existing route branches to it with no receive-loop
change. Server-VAD owns turn boundaries (no local endpointer). Model audio is
held by ScrubHoldGate until its transcript is scrub-cleared (AP-11).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.core.protocols import AudioChunk
from jarvis.core.turn_language import resolve_output_language
from jarvis.realtime.protocol import RealtimeSessionConfig
from jarvis.realtime.scrub_gate import ScrubHoldGate
from jarvis.telephony.audio import STT_SAMPLE_RATE, Resampler

log = logging.getLogger(__name__)

_INSTRUCTIONS = (
    "You are Jarvis, a concise spoken assistant. Never read tool JSON, code, "
    "stack traces, file paths, base64, or raw URLs aloud — speak only a natural "
    "summary. Reply in the user's language."
)


class RealtimeVoiceSession:
    def __init__(
        self,
        *,
        session_id: str,
        send_binary: Any,
        send_json: Any,
        provider: Any,
        config: Any,
        bus: Any = None,
        browser_sample_rate: int = 48000,
    ) -> None:
        self.session_id = session_id
        self._send_binary = send_binary
        self._send_json = send_json
        self._provider = provider
        self._config = config
        self._bus = bus
        self.browser_sample_rate = int(browser_sample_rate or STT_SAMPLE_RATE)
        self._in_resampler = Resampler(self.browser_sample_rate, STT_SAMPLE_RATE)

        self._language = self._resolve_lang(text="")
        self._gate = ScrubHoldGate(self._language)
        self._session: Any = None
        self._pump_task: asyncio.Task[None] | None = None
        self._ms_sent = 0
        self._ended = False

    def _resolve_lang(self, *, text: str) -> str:
        brain = getattr(self._config, "brain", None)
        pin = getattr(brain, "reply_language", "auto")
        return resolve_output_language(pin, "unknown", text)

    async def handle_control(self, msg: dict[str, Any]) -> None:
        kind = str(msg.get("type", ""))
        if kind == "audio_start":
            rate = int(msg.get("sample_rate", self.browser_sample_rate) or self.browser_sample_rate)
            if rate != self.browser_sample_rate:
                self.browser_sample_rate = rate
                self._in_resampler = Resampler(rate, STT_SAMPLE_RATE)
            if self._session is None:
                await self._open()
            await self._send_json({"type": "audio_ready"})
        elif kind == "barge_in":
            await self._barge_in()
        elif kind == "audio_stop":
            await self.end(reason="client_stop")

    async def _open(self) -> None:
        cfg = RealtimeSessionConfig(
            instructions=_INSTRUCTIONS,
            language=self._language,
            voice=getattr(getattr(self._config, "voice", None), "realtime_voice", "") or "",
        )
        self._session = await self._provider.open_session(cfg)
        self._pump_task = asyncio.create_task(self._pump(), name=f"rt-pump-{self.session_id}")

    async def handle_audio_frame(self, pcm_native: bytes) -> None:
        if self._ended or self._session is None or not pcm_native:
            return
        try:
            if self.browser_sample_rate == STT_SAMPLE_RATE:
                pcm16 = bytes(pcm_native)
            else:
                pcm16 = self._in_resampler.process(bytes(pcm_native))
        except Exception:  # noqa: BLE001 — malformed frame, drop it
            return
        chunk = AudioChunk(pcm=pcm16, sample_rate=STT_SAMPLE_RATE, timestamp_ns=0)
        await self._session.send_audio(chunk)

    async def _pump(self) -> None:
        from jarvis.telemetry.latency import LatencyPhase, mark_phase

        try:
            async for ev in self._session.receive():
                if ev.type == "input_transcript" and ev.text:
                    new_lang = self._resolve_lang(text=ev.text)
                    if new_lang != self._language:
                        self._language = new_lang
                        self._gate = ScrubHoldGate(new_lang)
                        await self._session.update_session(
                            instructions=_INSTRUCTIONS, language=new_lang
                        )
                    mark_phase(LatencyPhase.REALTIME_INPUT_COMMITTED)
                elif ev.type == "output_transcript_delta" and ev.text:
                    mark_phase(LatencyPhase.REALTIME_FIRST_TRANSCRIPT)
                    display = await self._gate.feed_transcript(ev.text)
                    if self._gate.hard_leak_pending():
                        await self._session.interrupt()
                        await self._send_json(
                            {"type": "error_spoken", "text": self._gate.fallback_phrase()}
                        )
                        self._gate.drain()
                        continue
                    await self._send_json(
                        {"type": "transcript", "text": display, "is_final": False}
                    )
                    # Flush audio that this now-scrub-cleared transcript covers, so a
                    # LATER segment's first audio chunk is never released before its
                    # own transcript is scrubbed (closes the T4 one-chunk-boundary
                    # residual: push_audio's "cleared" branch bundles the
                    # release-triggering chunk, so without this flush a later
                    # audio_delta could ride the same "cleared" release).
                    for chunk in self._gate.release_available():
                        await self._emit_audio(chunk)
                elif ev.type == "audio_delta" and ev.audio is not None:
                    mark_phase(LatencyPhase.REALTIME_FIRST_AUDIO)
                    for chunk in await self._gate.push_audio(ev.audio):
                        await self._emit_audio(chunk)
                elif ev.type == "speech_started":
                    await self._barge_in()
                elif ev.type == "turn_complete":
                    for chunk in self._gate.release_available():
                        await self._emit_audio(chunk)
                    self._gate.drain()
                elif ev.type == "error":
                    log.warning("realtime[%s] provider error: %s", self.session_id, ev.error)
        except Exception:  # noqa: BLE001 — AP-20: any pump error is terminal
            log.warning("realtime[%s] pump ended", self.session_id, exc_info=True)

    async def _emit_audio(self, chunk: AudioChunk) -> None:
        if self._ms_sent == 0 and self._bus is not None:
            from jarvis.core.events import AudioOutFirst

            try:
                await self._bus.publish(AudioOutFirst())
            except Exception:  # noqa: BLE001, S110 — best-effort telemetry publish
                pass
        self._ms_sent += len(chunk.pcm)
        await self._send_binary(chunk.pcm)

    async def _barge_in(self) -> None:
        self._gate.drain()
        ms_played = self._ms_sent // (24000 * 2 // 1000) if self._ms_sent else 0
        try:
            await self._session.truncate(audio_end_ms=ms_played)
        except Exception:  # noqa: BLE001, S110 — best-effort truncate on barge-in
            pass
        self._ms_sent = 0
        try:
            await self._send_json({"type": "tts_cancel"})
        except Exception:  # noqa: BLE001, S110
            pass

    async def end(self, *, reason: str = "") -> None:
        if self._ended:
            return
        self._ended = True
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001, S110 — best-effort close on teardown
                pass
        log.info("realtime[%s] ended: reason=%s", self.session_id, reason)
