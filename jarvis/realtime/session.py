"""Transport-neutral realtime voice session.

The browser route and desktop speech lifecycle both use this wrapper. It owns
provider fallback, input resampling, server-VAD events, language resolution,
and the scrub-before-play gate. Surfaces supply only binary-audio and JSON-like
status callbacks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from jarvis.core.protocols import AudioChunk
from jarvis.core.turn_language import resolve_output_language
from jarvis.realtime.audio import StreamingPcm16Resampler
from jarvis.realtime.protocol import RealtimeSessionConfig
from jarvis.realtime.scrub_gate import ScrubHoldGate

log = logging.getLogger(__name__)

_TRANSCRIPT_LOOKAHEAD_S = 0.250
_REALTIME_SAFETY_APPENDIX = (
    "This is a realtime spoken conversation. Never read tool JSON, function-call "
    "arguments, source code, stack traces, file paths, base64, or raw URLs aloud. "
    "Speak only a concise natural-language summary."
)
_LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish"}


def _session_instructions(language: str) -> str:
    from jarvis.brain.persona_loader import load_effective_persona_prompt

    persona = load_effective_persona_prompt().strip()
    language_name = _LANGUAGE_NAMES.get(language, "the user's language")
    parts = [
        persona,
        _REALTIME_SAFETY_APPENDIX,
        f"Reply only in {language_name} for this turn.",
    ]
    return "\n\n".join(part for part in parts if part)


class RealtimeVoiceSession:
    """One duplex conversation shared by browser and desktop surfaces."""

    def __init__(
        self,
        *,
        session_id: str,
        send_binary: Any,
        send_json: Any,
        config: Any,
        provider: Any = None,
        providers: list[Any] | None = None,
        bus: Any = None,
        browser_sample_rate: int = 48_000,
        half_duplex: bool = False,
        surface: str = "browser",
    ) -> None:
        self.session_id = session_id
        self._send_binary = send_binary
        self._send_json = send_json
        self._providers = list(providers or ([provider] if provider is not None else []))
        if not self._providers:
            raise ValueError("RealtimeVoiceSession requires at least one provider")
        self._provider = self._providers[0]
        self._config = config
        self._bus = bus
        self.browser_sample_rate = int(browser_sample_rate or 48_000)
        self._input_sample_rate = int(
            getattr(self._provider, "input_sample_rate", 16_000) or 16_000
        )
        self._in_resampler = StreamingPcm16Resampler(
            self.browser_sample_rate, self._input_sample_rate
        )
        self._half_duplex = bool(half_duplex)
        self._surface = str(surface or "unknown")
        self._output_active = False

        self._language = self._resolve_lang(text="")
        self._gate = ScrubHoldGate(self._language)
        self._session: Any = None
        self._pump_task: asyncio.Task[None] | None = None
        self._release_task: asyncio.Task[None] | None = None
        self._output_samples_sent = 0
        self._ended = False
        self._provider_errors: list[str] = []
        self._active_model = ""
        self._turn_id = ""
        self._turn_index = 0
        self._last_user_text = ""
        self._output_transcript: list[str] = []

    def _resolve_lang(self, *, text: str) -> str:
        brain = getattr(self._config, "brain", None)
        pin = getattr(brain, "reply_language", "auto")
        return resolve_output_language(
            pin,
            "unknown",
            text,
            conversation_language=getattr(self, "_language", ""),
        )

    async def handle_control(self, msg: dict[str, Any]) -> None:
        kind = str(msg.get("type", ""))
        if kind == "audio_start":
            rate = int(msg.get("sample_rate", self.browser_sample_rate) or self.browser_sample_rate)
            if rate != self.browser_sample_rate:
                self.browser_sample_rate = rate
            if self._session is None:
                await self._open()
            self._in_resampler = StreamingPcm16Resampler(
                self.browser_sample_rate, self._input_sample_rate
            )
            await self._send_json(
                {
                    "type": "audio_ready",
                    "provider": self.active_provider,
                    "input_sample_rate": self._input_sample_rate,
                    "output_sample_rate": int(
                        getattr(self._provider, "output_sample_rate", 24_000) or 24_000
                    ),
                }
            )
            await self._publish_ready()
            if self._surface == "browser":
                await self._publish_browser_session_started()
        elif kind == "barge_in":
            await self._barge_in()
        elif kind == "audio_stop":
            await self.end(reason="client_stop")

    def _active_provider_selection(self, provider: Any) -> tuple[str, str]:
        provider_id = str(getattr(provider, "name", "") or "")
        providers = getattr(getattr(self._config, "brain", None), "providers", None)
        provider_config = providers.get(provider_id) if isinstance(providers, dict) else None
        model = (
            str(getattr(provider_config, "model", "") or "")
            if provider_config is not None
            else ""
        )
        voice = (
            str(getattr(provider_config, "voice", "") or "")
            if provider_config is not None
            else ""
        )
        return model, voice

    async def _open(self) -> None:
        for provider in self._providers:
            model, voice = self._active_provider_selection(provider)
            input_rate = int(getattr(provider, "input_sample_rate", 16_000) or 16_000)
            output_rate = int(getattr(provider, "output_sample_rate", 24_000) or 24_000)
            session_config = RealtimeSessionConfig(
                instructions=_session_instructions(self._language),
                language=self._language,
                model=model,
                voice=voice,
                input_sample_rate=input_rate,
                output_sample_rate=output_rate,
                modalities=("audio",),
            )
            try:
                session = await provider.open_session(session_config)
            except Exception as exc:  # noqa: BLE001 — cross to the next family
                provider_id = str(getattr(provider, "name", "unknown") or "unknown")
                detail = f"{type(exc).__name__}: {exc}"[:800]
                self._provider_errors.append(f"{provider_id}: {detail}")
                log.warning("Realtime provider %s handshake failed: %s", provider_id, detail)
                try:
                    await self._send_json(
                        {
                            "type": "provider_fallback",
                            "provider": provider_id,
                            "error": detail,
                        }
                    )
                except Exception:  # noqa: BLE001, S110 — status is best-effort
                    pass
                continue

            self._provider = provider
            self._session = session
            self._active_model = model
            self._input_sample_rate = input_rate
            self._in_resampler = StreamingPcm16Resampler(
                self.browser_sample_rate, input_rate
            )
            self._pump_task = asyncio.create_task(
                self._pump(), name=f"rt-pump-{self.session_id}"
            )
            return

        summary = "; ".join(self._provider_errors) or "no provider could open a session"
        await self._publish_error("RealtimeHandshakeError", summary, recoverable=True)
        raise RuntimeError(f"No realtime provider could open a session: {summary}")

    async def handle_audio_frame(self, pcm_native: bytes) -> None:
        if self._ended or self._session is None or not pcm_native:
            return
        if self._half_duplex and self._output_active:
            return
        try:
            if self.browser_sample_rate == self._input_sample_rate:
                pcm16 = bytes(pcm_native)
            else:
                pcm16 = self._in_resampler.process(bytes(pcm_native))
        except Exception:  # noqa: BLE001 — malformed frame, drop it
            return
        if not pcm16:
            return
        await self._session.send_audio(
            AudioChunk(
                pcm=pcm16,
                sample_rate=self._input_sample_rate,
                timestamp_ns=0,
            )
        )

    async def _pump(self) -> None:
        from jarvis.telemetry.latency import LatencyPhase, mark_phase

        try:
            async for event in self._session.receive():
                if event.type == "input_transcript" and event.text:
                    new_language = self._resolve_lang(text=event.text)
                    if new_language != self._language:
                        self._language = new_language
                        self._gate = ScrubHoldGate(new_language)
                        await self._session.update_session(
                            instructions=_session_instructions(new_language),
                            language=new_language,
                        )
                    mark_phase(LatencyPhase.REALTIME_INPUT_COMMITTED)
                    self._last_user_text = event.text
                    if not self._turn_id:
                        self._turn_id = str(uuid4())
                        self._turn_index += 1
                        await self._publish_turn_started()
                    await self._publish_transcription(event.text, bool(event.is_final))
                    await self._send_json(
                        {
                            "type": "transcript",
                            "role": "user",
                            "text": event.text,
                            "is_final": bool(event.is_final),
                        }
                    )
                elif event.type == "output_transcript_delta" and event.text:
                    mark_phase(LatencyPhase.REALTIME_FIRST_TRANSCRIPT)
                    display = await self._gate.feed_transcript(event.text)
                    if self._gate.hard_leak_pending():
                        self._cancel_release_task()
                        await self._session.interrupt()
                        await self._send_json(
                            {"type": "error_spoken", "text": self._gate.fallback_phrase()}
                        )
                        self._gate.drain()
                        continue
                    self._output_transcript.append(display)
                    await self._send_json(
                        {
                            "type": "transcript",
                            "role": "assistant",
                            "text": display,
                            "is_final": bool(event.is_final),
                        }
                    )
                    self._cancel_release_task()
                    for chunk in self._gate.release_available():
                        await self._emit_audio(chunk)
                elif event.type == "audio_delta" and event.audio is not None:
                    mark_phase(LatencyPhase.REALTIME_FIRST_AUDIO)
                    self._output_active = True
                    released = await self._gate.push_audio(event.audio)
                    for chunk in released:
                        await self._emit_audio(chunk)
                    if not released and self._release_task is None:
                        self._release_task = asyncio.create_task(
                            self._release_after_lookahead(),
                            name=f"rt-hold-{self.session_id}",
                        )
                elif event.type in {"speech_started", "interrupted"}:
                    await self._barge_in()
                elif event.type == "turn_complete":
                    self._cancel_release_task()
                    for chunk in self._gate.release_available():
                        await self._emit_audio(chunk)
                    self._gate.drain()
                    await self._send_json({"type": "turn_complete"})
                    await self._publish_turn_completed()
                    self._output_active = False
                    self._output_samples_sent = 0
                elif event.type == "error":
                    message = str(event.error or "provider error")
                    log.warning("realtime[%s] provider error: %s", self.session_id, message)
                    await self._publish_error(
                        "RealtimeProviderError", message, recoverable=True
                    )
                    await self._send_json({"type": "provider_error", "error": message})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — AP-20: pump error is terminal
            log.warning("realtime[%s] pump ended", self.session_id, exc_info=True)
            await self._publish_error(
                type(exc).__name__,
                str(exc) or "Realtime receive loop ended",
                recoverable=True,
            )
            try:
                await self._send_json(
                    {"type": "provider_error", "error": str(exc) or "receive loop ended"}
                )
            except Exception:  # noqa: BLE001, S110
                pass

    async def _release_after_lookahead(self) -> None:
        try:
            await asyncio.sleep(_TRANSCRIPT_LOOKAHEAD_S)
            for chunk in self._gate.release_available():
                await self._emit_audio(chunk)
        except asyncio.CancelledError:
            raise
        finally:
            self._release_task = None

    def _cancel_release_task(self) -> None:
        task = self._release_task
        if task is not None and not task.done():
            task.cancel()
        self._release_task = None

    async def _publish_error(
        self, error_type: str, message: str, *, recoverable: bool
    ) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.core.events import ErrorOccurred

            await self._bus.publish(
                ErrorOccurred(
                    layer=f"realtime.{self.active_provider or 'provider'}",
                    error_type=error_type,
                    message=message[:800],
                    recoverable=recoverable,
                )
            )
        except Exception:  # noqa: BLE001, S110 — telemetry must never break voice
            pass

    async def _publish_ready(self) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.core.events import RealtimeSessionReady

            await self._bus.publish(
                RealtimeSessionReady(
                    source_layer=f"realtime.{self.active_provider}",
                    session_id=self.session_id,
                    provider=self.active_provider,
                    model=self._active_model,
                    surface=self._surface,
                    input_sample_rate=self._input_sample_rate,
                    output_sample_rate=int(
                        getattr(self._provider, "output_sample_rate", 24_000) or 24_000
                    ),
                )
            )
        except Exception:  # noqa: BLE001, S110
            pass

    async def _publish_browser_session_started(self) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.core.events import VoiceSessionStarted

            await self._bus.publish(
                VoiceSessionStarted(
                    source_layer=f"realtime.{self.active_provider}",
                    session_id=self.session_id,
                    wake_keyword="browser_microphone",
                    language=self._language,
                )
            )
        except Exception:  # noqa: BLE001, S110
            pass

    async def _publish_transcription(self, text: str, is_final: bool) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.core.events import TranscriptionUpdate

            await self._bus.publish(
                TranscriptionUpdate(
                    source_layer=f"realtime.{self.active_provider}",
                    text=text,
                    is_final=is_final,
                )
            )
        except Exception:  # noqa: BLE001, S110
            pass

    async def _publish_turn_started(self) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.core.events import VoiceTurnStarted

            await self._bus.publish(
                VoiceTurnStarted(
                    source_layer=f"realtime.{self.active_provider}",
                    session_id=self.session_id,
                    turn_id=self._turn_id,
                    turn_index=self._turn_index,
                )
            )
        except Exception:  # noqa: BLE001, S110
            pass

    async def _publish_turn_completed(self) -> None:
        if not self._turn_id:
            self._output_transcript.clear()
            self._last_user_text = ""
            return
        answer = "".join(self._output_transcript).strip()
        if self._bus is not None:
            try:
                from jarvis.core.events import ResponseGenerated, VoiceTurnCompleted

                if answer:
                    await self._bus.publish(
                        ResponseGenerated(
                            source_layer=f"realtime.{self.active_provider}",
                            text=answer,
                            language=self._language,
                        )
                    )
                await self._bus.publish(
                    VoiceTurnCompleted(
                        source_layer=f"realtime.{self.active_provider}",
                        session_id=self.session_id,
                        turn_id=self._turn_id,
                        user_text=self._last_user_text,
                        user_lang=self._language,
                        jarvis_text=answer,
                        jarvis_lang=self._language,
                        tier="realtime",
                        provider=self.active_provider,
                        model=self._active_model,
                    )
                )
            except Exception:  # noqa: BLE001, S110
                pass
        self._turn_id = ""
        self._last_user_text = ""
        self._output_transcript.clear()

    async def _emit_audio(self, chunk: Any) -> None:
        pcm = bytes(getattr(chunk, "pcm", b"") or b"")
        if not pcm:
            return
        if self._output_samples_sent == 0 and self._bus is not None:
            from jarvis.core.events import AudioOutFirst

            try:
                await self._bus.publish(AudioOutFirst())
            except Exception:  # noqa: BLE001, S110 — best-effort telemetry
                pass
        self._output_samples_sent += len(pcm) // 2
        await self._send_binary(pcm)

    async def _barge_in(self) -> None:
        self._cancel_release_task()
        self._gate.drain()
        output_rate = int(getattr(self._provider, "output_sample_rate", 24_000) or 24_000)
        audio_end_ms = (
            int(self._output_samples_sent * 1000 / output_rate)
            if self._output_samples_sent
            else 0
        )
        if self._session is not None:
            try:
                await self._session.truncate(audio_end_ms=audio_end_ms)
            except Exception:  # noqa: BLE001, S110 — best-effort context alignment
                pass
        self._output_samples_sent = 0
        self._output_active = False
        try:
            await self._send_json({"type": "tts_cancel"})
        except Exception:  # noqa: BLE001, S110
            pass

    async def end(self, *, reason: str = "") -> None:
        if self._ended:
            return
        self._ended = True
        self._cancel_release_task()
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001, S110 — best-effort teardown
                pass
        if self._surface == "browser" and self._bus is not None:
            try:
                from jarvis.core.events import VoiceSessionEnded

                await self._bus.publish(
                    VoiceSessionEnded(
                        source_layer=f"realtime.{self.active_provider}",
                        session_id=self.session_id,
                        hangup_reason=reason or "client_stop",
                        turn_count=self._turn_index,
                    )
                )
            except Exception:  # noqa: BLE001, S110
                pass
        log.info("realtime[%s] ended: reason=%s", self.session_id, reason)

    @property
    def active_provider(self) -> str:
        return str(getattr(self._provider, "name", "") or "")

    async def wait_finished(self) -> None:
        task = self._pump_task
        if task is not None:
            await task
