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
from jarvis.core.redact import safe_preview
from jarvis.core.turn_language import resolve_output_language
from jarvis.realtime.audio import StreamingPcm16Resampler
from jarvis.realtime.protocol import RealtimeSessionConfig
from jarvis.realtime.scrub_gate import ScrubHoldGate
from jarvis.sessions.constants import HANGUP_VOICE_PATTERN
from jarvis.speech.hangup import HANGUP_RE

log = logging.getLogger(__name__)

_TRANSCRIPT_LOOKAHEAD_S = 0.250
_TOOL_TRANSCRIPT_WAIT_S = 3.0
# Grace window for the model to finish its goodbye after an end_call tool
# call; if the provider never sends turn_complete, hang up anyway.
_END_CALL_GRACE_S = 10.0
# Gemini emits is_final per transcript CHUNK, so hang-up matching runs on a
# per-turn accumulator; the tail-trim bounds it without losing recent words.
_HANGUP_BUFFER_MAX_CHARS = 300
# Declared to the realtime model alongside the bridge tools, but handled by
# the session itself: ending the call is surface lifecycle (like the hotkey),
# not a risk-tiered Jarvis tool, and must work even without a tool bridge.
_END_CALL_DECLARATION: dict[str, Any] = {
    "name": "end_call",
    "description": (
        "End the voice call. Call ONLY when the user explicitly says goodbye "
        "or clearly asks to end the conversation."
    ),
    "parameters": {"type": "object", "properties": {}},
}
# Delegate mode: the realtime model gets ONE action function instead of the
# full router-tool set. The handler runs a complete classic router-brain turn
# (ToolExecutor risk tiers, two-turn voice confirm, spawn-worker escalation)
# and returns the spoken reply for the realtime voice to deliver. Hard budget:
# the router turn itself offloads heavy work to background missions, so a
# turn that exceeds this is stuck, not busy.
_DELEGATE_TIMEOUT_S = 90.0
_DELEGATE_DECLARATION: dict[str, Any] = {
    "name": "jarvis_action",
    "description": (
        "Execute an action for the user through the Jarvis action system: "
        "open apps or views, change settings, control the computer, manage "
        "files, start background research or coding missions, and any other "
        "operation on the user's system. Also call this to relay the user's "
        "answer to a pending confirmation question."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "The user's request in their own words.",
            }
        },
        "required": ["request"],
    },
}
_DELEGATE_ROLE_DIRECTIVE = (
    "You have ONE action function: jarvis_action. It hands the user's spoken "
    "request to the Jarvis action system, which can open apps and views, "
    "change settings, control the computer, manage files and windows, and "
    "start background research or coding missions. Whenever the user asks "
    "you to DO something on their computer or in the Jarvis app, call "
    "jarvis_action — never claim you cannot act, and never invent an "
    "outcome. The function returns spoken_reply: deliver that content to the "
    "user in your own voice, in the conversation language, without reading "
    "JSON. If spoken_reply asks a confirmation question, ask the user and "
    "call jarvis_action again with their answer. Answer pure knowledge and "
    "chat questions yourself, without the function. Use end_call only when "
    "the user says goodbye."
)
_REALTIME_SAFETY_APPENDIX = (
    "This is a realtime spoken conversation. Never read tool JSON, function-call "
    "arguments, source code, stack traces, file paths, base64, or raw URLs aloud. "
    "Speak only a concise natural-language summary."
)
_LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish"}


_TOOL_ROLE_DIRECTIVE = (
    "You have live function tools that act on the user's Jarvis app and "
    "computer. When the user asks you to DO something — create a file, write "
    "code, research, start background work, open a view, change a setting, "
    "control the computer — call the matching function instead of claiming "
    "you cannot act. Heavy multi-minute work (building files, coding, deep "
    "research) belongs to the background-agent spawn function: start it, "
    "then briefly confirm what you started. If a function asks for a spoken "
    "confirmation, relay the question and wait for the user's answer."
)


def _session_instructions(
    language: str,
    *,
    provider: str = "",
    model: str = "",
    language_is_pinned: bool = True,
    tool_directive: str = "",
) -> str:
    from jarvis.brain.persona_loader import load_effective_persona_prompt

    persona = load_effective_persona_prompt().strip()
    language_name = _LANGUAGE_NAMES.get(language, "the user's language")
    if language_is_pinned:
        language_directive = f"Reply only in {language_name} for this turn."
    else:
        language_directive = (
            "Reply in the language of the user's current spoken turn. If the "
            "turn is only a one- or two-word interjection, keep replying in "
            f"{language_name}, the current conversation language."
        )
    parts = [
        persona,
        tool_directive,
        _REALTIME_SAFETY_APPENDIX,
        (
            "Runtime identity: this voice session is using the Realtime engine"
            + (f", provider {provider}" if provider else "")
            + (f", model {model}" if model else "")
            + ". If the user asks which engine, provider, or model is active, "
            "answer from this runtime identity exactly; do not describe the "
            "classic text brain configuration."
        ),
        language_directive,
    ]
    return "\n\n".join(part for part in parts if part)


class RealtimeVoiceSession:
    """One duplex conversation shared by browser and desktop surfaces."""

    is_realtime = True

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
        brain: Any = None,
        tool_bridge: Any = None,
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

        brain_config = getattr(self._config, "brain", None)
        reply_language = str(
            getattr(brain_config, "reply_language", "auto") or "auto"
        ).strip().lower()
        self._language_is_pinned = reply_language in _LANGUAGE_NAMES
        self._initial_conversation_language = str(
            getattr(brain, "conversation_language", "") or ""
        ).strip().lower()
        self._stt_language = getattr(
            getattr(self._config, "stt", None), "language", "unknown"
        )
        self._language = self._resolve_lang(text="")
        self._brain = brain
        mode = str(
            getattr(
                getattr(self._config, "voice", None), "realtime_tool_mode", "delegate"
            )
            or "delegate"
        ).strip().lower()
        if mode not in {"delegate", "direct"}:
            mode = "delegate"
        # Delegate mode needs only a callable brain (the boot proxy and the
        # real BrainManager both qualify); an explicitly injected bridge
        # always wins so existing callers/tests keep today's behavior.
        self._delegate_enabled = (
            mode == "delegate" and tool_bridge is None and callable(brain)
        )
        if tool_bridge is None and brain is not None and not self._delegate_enabled:
            try:
                from jarvis.realtime.tools import RealtimeToolBridge

                tool_bridge = RealtimeToolBridge.from_brain(
                    brain, language=self._language
                )
            except Exception:  # noqa: BLE001 — conversation still works without tools
                log.warning("Realtime tool bridge is unavailable", exc_info=True)
        self._tool_bridge = tool_bridge
        self._delegate_tasks: set[asyncio.Task[None]] = set()
        # from_brain returns None SILENTLY when the brain object carries no
        # _tools/_tool_executor_ref (e.g. a bare callback was passed) — say so,
        # or a tool-less session is indistinguishable from a healthy one.
        if self._delegate_enabled:
            log.info(
                "realtime[%s] tool mode: delegate — one action function "
                "backed by the router brain",
                session_id,
            )
        elif tool_bridge is not None:
            log.info(
                "realtime[%s] tool bridge active: %d tools",
                session_id,
                len(tool_bridge.declarations),
            )
        elif brain is not None:
            log.warning(
                "realtime[%s] brain provided but NO tool bridge — object has "
                "no usable _tools/_tool_executor_ref; session runs tool-less",
                session_id,
            )
        self._gate = ScrubHoldGate(self._language)
        self._session: Any = None
        self._pump_task: asyncio.Task[None] | None = None
        self._release_task: asyncio.Task[None] | None = None
        self._output_samples_sent = 0
        self._ended = False
        self._provider_errors: list[str] = []
        self._failed = asyncio.Event()
        self._failure_detail = ""
        self._active_model = ""
        self._turn_id = ""
        self._turn_index = 0
        self._last_user_text = ""
        self._output_transcript: list[str] = []
        self._executed_tool_names: set[str] = set()
        self._pending_tool_events: list[Any] = []
        self._tool_transcript_task: asyncio.Task[None] | None = None
        self._response_requested_for_turn = False
        self._hangup_reason = ""
        self._turn_final_text = ""
        self._end_after_turn = False
        self._end_call_timer: asyncio.Task[None] | None = None

    def _resolve_lang(self, *, text: str) -> str:
        brain = getattr(self._config, "brain", None)
        pin = getattr(brain, "reply_language", "auto")
        return resolve_output_language(
            pin,
            self._stt_language,
            text,
            conversation_language=(
                getattr(self, "_language", "")
                or self._initial_conversation_language
            ),
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
                    "model": self._active_model,
                    "input_sample_rate": self._input_sample_rate,
                    "output_sample_rate": int(
                        getattr(self._provider, "output_sample_rate", 24_000) or 24_000
                    ),
                }
            )
            if self._surface == "browser":
                await self._publish_browser_session_started()
            await self._publish_ready()
            self._start_pump()
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
                instructions=_session_instructions(
                    self._language,
                    provider=str(getattr(provider, "name", "") or ""),
                    model=model,
                    language_is_pinned=self._language_is_pinned,
                    tool_directive=self._tool_directive(),
                ),
                language=self._language,
                language_is_pinned=self._language_is_pinned,
                model=model,
                voice=voice,
                input_sample_rate=input_rate,
                output_sample_rate=output_rate,
                modalities=("audio",),
                tools=self._declared_tools(),
            )
            try:
                session = await provider.open_session(session_config)
            except Exception as exc:  # noqa: BLE001 — cross to the next family
                provider_id = str(getattr(provider, "name", "unknown") or "unknown")
                detail = f"{type(exc).__name__}: {safe_preview(exc, max_chars=700)}"
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
            return

        summary = "; ".join(self._provider_errors) or "no provider could open a session"
        await self._publish_error("RealtimeHandshakeError", summary, recoverable=True)
        raise RuntimeError(f"No realtime provider could open a session: {summary}")

    def _start_pump(self) -> None:
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(
                self._pump(), name=f"rt-pump-{self.session_id}"
            )

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
                if event.type == "input_transcript":
                    transcript = str(event.text or "").strip()
                    new_language = self._language
                    if transcript:
                        new_language = self._resolve_lang(text=transcript)
                        if new_language != self._language:
                            self._language = new_language
                            self._gate = ScrubHoldGate(new_language)
                            if self._tool_bridge is not None:
                                self._tool_bridge.set_language(new_language)
                    if event.is_final:
                        await self._session.update_session(
                            instructions=_session_instructions(
                                new_language,
                                provider=self.active_provider,
                                model=self._active_model,
                                language_is_pinned=True,
                                tool_directive=self._tool_directive(),
                            ),
                            language=new_language,
                        )
                    if transcript or event.is_final:
                        mark_phase(LatencyPhase.REALTIME_INPUT_COMMITTED)
                    if transcript:
                        self._last_user_text = transcript
                    if self._tool_bridge is not None and event.is_final and transcript:
                        await self._tool_bridge.handle_user_transcript(transcript)
                    if (transcript or event.is_final) and not self._turn_id:
                        self._turn_id = str(uuid4())
                        self._turn_index += 1
                        await self._publish_turn_started()
                    if transcript:
                        await self._publish_transcription(
                            transcript, bool(event.is_final)
                        )
                        await self._send_json(
                            {
                                "type": "transcript",
                                "role": "user",
                                "text": transcript,
                                "is_final": bool(event.is_final),
                            }
                        )
                    elif event.is_final and event.error:
                        message = safe_preview(event.error, max_chars=800)
                        log.warning(
                            "realtime[%s] input transcription unavailable: %s",
                            self.session_id,
                            message,
                        )
                        await self._publish_error(
                            "RealtimeTranscriptionError",
                            message,
                            recoverable=True,
                        )
                    if transcript and event.is_final:
                        # Per-turn accumulator: Gemini emits is_final per
                        # transcript chunk, so "auflegen" may arrive split
                        # across finals. The space-join reconstructs the
                        # spoken sequence; turn_complete resets the buffer so
                        # words never match across turn boundaries.
                        self._turn_final_text = (
                            f"{self._turn_final_text} {transcript}".strip()
                        )[-_HANGUP_BUFFER_MAX_CHARS:]
                        if HANGUP_RE.search(self._turn_final_text):
                            log.info(
                                "realtime[%s] voice hang-up phrase matched",
                                self.session_id,
                            )
                            await self._finish_with_hangup()
                            break
                    if event.is_final and self._pending_tool_events:
                        self._cancel_tool_transcript_wait()
                        pending = self._pending_tool_events
                        self._pending_tool_events = []
                        for pending_event in pending:
                            if transcript:
                                await self._handle_tool_call(pending_event)
                            else:
                                await self._reject_untranscribed_tool_call(
                                    pending_event
                                )
                    if event.is_final and not self._response_requested_for_turn:
                        await self._session.request_response()
                        self._response_requested_for_turn = True
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
                elif event.type == "tool_call":
                    if str(getattr(event, "tool_name", "") or "") == "end_call":
                        # Session lifecycle, not a bridge tool: works without
                        # a tool bridge and must not be held back by the
                        # missing-transcript guard below.
                        await self._handle_end_call(event)
                    elif not self._last_user_text:
                        self._pending_tool_events.append(event)
                        if self._tool_transcript_task is None:
                            self._tool_transcript_task = asyncio.create_task(
                                self._reject_pending_tools_after_timeout(),
                                name=f"rt-tool-transcript-{self.session_id}",
                            )
                    else:
                        await self._handle_tool_call(event)
                elif event.type == "turn_complete":
                    if self._pending_tool_events:
                        self._cancel_tool_transcript_wait()
                        pending = self._pending_tool_events
                        self._pending_tool_events = []
                        for pending_event in pending:
                            await self._reject_untranscribed_tool_call(pending_event)
                    self._cancel_release_task()
                    for chunk in self._gate.release_available():
                        await self._emit_audio(chunk)
                    self._gate.drain()
                    await self._send_json({"type": "turn_complete"})
                    await self._publish_turn_completed()
                    self._output_active = False
                    self._output_samples_sent = 0
                    self._response_requested_for_turn = False
                    self._turn_final_text = ""
                    if self._end_after_turn:
                        # end_call was acknowledged; the model has now spoken
                        # its goodbye to the end — hang up.
                        await self._finish_with_hangup()
                        break
                elif event.type == "error":
                    message = safe_preview(
                        event.error or "provider error", max_chars=800
                    )
                    self._failure_detail = message
                    self._failed.set()
                    log.warning("realtime[%s] provider error: %s", self.session_id, message)
                    await self._publish_error(
                        "RealtimeProviderError", message, recoverable=True
                    )
                    await self._send_json({"type": "provider_error", "error": message})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — AP-20: pump error is terminal
            message = safe_preview(exc, max_chars=800) or "Realtime receive loop ended"
            self._failure_detail = message
            self._failed.set()
            log.warning("realtime[%s] pump ended", self.session_id, exc_info=True)
            await self._publish_error(
                type(exc).__name__,
                message,
                recoverable=True,
            )
            try:
                await self._send_json(
                    {"type": "provider_error", "error": message}
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
                        tool_calls=tuple(sorted(self._executed_tool_names)),
                    )
                )
            except Exception:  # noqa: BLE001, S110
                pass
        self._turn_id = ""
        self._last_user_text = ""
        self._output_transcript.clear()
        self._executed_tool_names.clear()

    def _declared_tools(self) -> tuple[dict[str, Any], ...]:
        if self._delegate_enabled:
            return (_DELEGATE_DECLARATION, _END_CALL_DECLARATION)
        if self._tool_bridge is not None:
            return (*self._tool_bridge.declarations, _END_CALL_DECLARATION)
        return (_END_CALL_DECLARATION,)

    def _tool_directive(self) -> str:
        if self._delegate_enabled:
            return _DELEGATE_ROLE_DIRECTIVE
        if self._tool_bridge is not None:
            return _TOOL_ROLE_DIRECTIVE
        return ""

    async def _handle_tool_call(self, event: Any) -> None:
        if self._session is None:
            return
        call_id = str(getattr(event, "call_id", "") or "")
        wire_name = str(getattr(event, "tool_name", "") or "")
        arguments = getattr(event, "tool_args", None)
        if not isinstance(arguments, dict):
            arguments = {}
        if (
            self._delegate_enabled
            and call_id
            and wire_name == str(_DELEGATE_DECLARATION["name"])
        ):
            # Routed HERE (not in the pump branch) so the untranscribed-call
            # guard and pending flush keep applying to delegate calls too.
            self._start_delegate(call_id, wire_name, arguments)
            return
        if not call_id or not wire_name or self._tool_bridge is None:
            await self._session.send_tool_result(
                call_id,
                wire_name,
                {"success": False, "error": "Tool call is not available."},
            )
            return
        try:
            original_name, result = await self._tool_bridge.execute(
                wire_name=wire_name,
                arguments=arguments,
            )
        except Exception:  # noqa: BLE001 -- a failed tool must not kill duplex audio
            log.warning("realtime tool execution failed: %s", wire_name, exc_info=True)
            await self._publish_error(
                "RealtimeToolError",
                f"Realtime tool execution failed: {wire_name}",
                recoverable=True,
            )
            original_name = wire_name
            result = {
                "success": False,
                "error": "The tool failed safely and was not completed.",
            }
        if result.get("success"):
            self._executed_tool_names.add(original_name)
        await self._session.send_tool_result(call_id, wire_name, result)

    async def _handle_end_call(self, event: Any) -> None:
        if self._session is not None:
            try:
                await self._session.send_tool_result(
                    str(getattr(event, "call_id", "") or ""),
                    "end_call",
                    {"success": True},
                )
            except Exception:  # noqa: BLE001 — still hang up on a dead wire
                log.debug("end_call tool result send failed", exc_info=True)
        self._end_after_turn = True
        if self._end_call_timer is None or self._end_call_timer.done():
            self._end_call_timer = asyncio.create_task(
                self._finish_hangup_after_grace(),
                name=f"rt-end-call-{self.session_id}",
            )

    def _start_delegate(
        self, call_id: str, wire_name: str, arguments: dict[str, Any]
    ) -> None:
        request = str(arguments.get("request", "") or "")
        # Dispatch the RAW final transcript: the model may paraphrase into
        # English, but the router's language resolver and intent matchers
        # need the user's own words. Snapshot NOW — turn state resets later.
        user_text = self._last_user_text or request
        log.info(
            "realtime[%s] delegate call: dispatching user turn to the router brain",
            self.session_id,
        )
        task = asyncio.create_task(
            self._run_delegate(call_id, wire_name, user_text),
            name=f"rt-delegate-{self.session_id}",
        )
        self._delegate_tasks.add(task)
        task.add_done_callback(self._delegate_tasks.discard)

    async def _run_delegate(
        self, call_id: str, wire_name: str, user_text: str
    ) -> None:
        try:
            reply = (
                await asyncio.wait_for(
                    self._dispatch_brain_turn(user_text),
                    timeout=_DELEGATE_TIMEOUT_S,
                )
                or ""
            ).strip()
            if reply:
                result: dict[str, Any] = {"success": True, "spoken_reply": reply}
            else:
                result = {
                    "success": True,
                    "spoken_reply": "",
                    "note": (
                        "The action completed without a spoken reply; "
                        "briefly confirm it to the user."
                    ),
                }
            self._executed_tool_names.add(str(_DELEGATE_DECLARATION["name"]))
        except TimeoutError:
            result = {
                "success": False,
                "error": (
                    "The action did not finish in time. Tell the user it may "
                    "still be running and offer to check later."
                ),
            }
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed delegation must not kill audio
            log.warning(
                "realtime[%s] delegate turn failed", self.session_id, exc_info=True
            )
            await self._publish_error(
                "RealtimeDelegateError", "Delegated brain turn failed", recoverable=True
            )
            result = {
                "success": False,
                "error": "The action failed safely and was not completed.",
            }
        if self._ended or self._session is None:
            return
        try:
            await self._session.send_tool_result(call_id, wire_name, result)
        except Exception:  # noqa: BLE001 — late result on a torn-down wire
            log.debug(
                "realtime[%s] delegate result send failed",
                self.session_id,
                exc_info=True,
            )

    async def _dispatch_brain_turn(self, text: str) -> str:
        # allow_voice_confirm=True is load-bearing: without it an ask-tier
        # tool blocks on a UI approval no voice user can give (the classic
        # pipeline passes the same flag). prefer_tool_model routes the
        # delegated turn onto the Tool-Model pick. Two-step degrade: an older
        # brain that rejects the new kwarg must keep voice-confirm rather
        # than dropping straight to the bare call.
        generate = getattr(self._brain, "generate", None)
        if callable(generate):
            for kwargs in (
                {"allow_voice_confirm": True, "prefer_tool_model": True},
                {"allow_voice_confirm": True},
            ):
                try:
                    return str(await generate(text, **kwargs) or "")
                except TypeError:
                    continue
        return str(await self._brain(text) or "")

    async def _finish_with_hangup(self) -> None:
        """Mark this session as ended by voice and notify the surface.

        The pump caller breaks right after; the surface (desktop loop or
        browser client) reads ``hangup_reason`` to end the call instead of
        falling back into the classic pipeline.
        """
        self._hangup_reason = HANGUP_VOICE_PATTERN
        try:
            await self._send_json(
                {"type": "hangup", "reason": HANGUP_VOICE_PATTERN}
            )
        except Exception:  # noqa: BLE001, S110 — surface notify is best-effort
            pass

    async def _finish_hangup_after_grace(self) -> None:
        try:
            await asyncio.sleep(_END_CALL_GRACE_S)
            if self._ended or self._hangup_reason:
                return
            log.info(
                "realtime[%s] end_call grace expired without turn_complete",
                self.session_id,
            )
            await self._finish_with_hangup()
            if self._pump_task is not None and not self._pump_task.done():
                self._pump_task.cancel()
        except asyncio.CancelledError:
            raise
        finally:
            self._end_call_timer = None

    async def _reject_untranscribed_tool_call(self, event: Any) -> None:
        if self._session is None:
            return
        await self._session.send_tool_result(
            str(getattr(event, "call_id", "") or ""),
            str(getattr(event, "tool_name", "") or ""),
            {
                "success": False,
                "error": (
                    "The input transcript was unavailable, so the action was not "
                    "executed. Ask the user to repeat the request."
                ),
            },
        )

    async def _reject_pending_tools_after_timeout(self) -> None:
        try:
            await asyncio.sleep(_TOOL_TRANSCRIPT_WAIT_S)
            pending = self._pending_tool_events
            self._pending_tool_events = []
            for event in pending:
                await self._reject_untranscribed_tool_call(event)
        except asyncio.CancelledError:
            raise
        finally:
            self._tool_transcript_task = None

    def _cancel_tool_transcript_wait(self) -> None:
        task = self._tool_transcript_task
        if task is not None and not task.done():
            task.cancel()
        self._tool_transcript_task = None

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
        self._response_requested_for_turn = False
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
        self._cancel_tool_transcript_wait()
        if self._end_call_timer is not None and not self._end_call_timer.done():
            self._end_call_timer.cancel()
        self._end_call_timer = None
        for task in tuple(self._delegate_tasks):
            if not task.done():
                task.cancel()
        self._delegate_tasks.clear()
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
        if self._tool_bridge is not None:
            try:
                await self._tool_bridge.close()
            except Exception:  # noqa: BLE001, S110 — teardown is best-effort
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

    @property
    def hangup_reason(self) -> str:
        """Non-empty once the user ended the call by voice (regex or end_call)."""
        return self._hangup_reason

    @property
    def failed(self) -> bool:
        """Whether the accepted duplex stream became unusable mid-session."""
        return self._failed.is_set()

    @property
    def failure_detail(self) -> str:
        return self._failure_detail

    async def wait_finished(self) -> None:
        task = self._pump_task
        if task is not None:
            await task
