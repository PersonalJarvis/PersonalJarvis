"""Continuous voice session: media, transcripts and delegated work are independent."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Literal
from uuid import uuid4

from jarvis.core.paths import user_data_dir
from jarvis.core.protocols import ContinuousVoiceStart
from jarvis.core.runtime_refs import get_supervisor_tool_gateway
from jarvis.core.turn_language import resolve_output_language
from jarvis.live.config import LiveConfig
from jarvis.live.state import LiveLedger, TranscriptFragment
from jarvis.live.tools import LiveTools, take_images
from jarvis.realtime.audio import StreamingPcm16Resampler

log = logging.getLogger(__name__)


class LiveVoiceSession:
    """Browser route facade, with no dependency on the legacy turn planner."""

    is_realtime = True
    allow_classic_fallback = False

    def __init__(
        self,
        *,
        session_id: str,
        send_binary: Any,
        send_json: Any,
        providers: list[Any],
        config: Any,
        bus: Any = None,
        brain: Any = None,
        surface: str = "browser",
        **_kwargs: Any,
    ) -> None:
        self.session_id = session_id
        self._send_binary = send_binary
        self._send_json = send_json
        self._provider = providers[0]
        self._config = config
        self._bus = bus
        self._brain = brain
        self._surface = surface
        self._connection: Any = None
        self._pump_task: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()
        self._control_tasks: set[asyncio.Task] = set()
        self._responses: dict[str, list[dict]] = {}
        self._completed: set[str] = set()
        self._response_id = ""
        self._closed = asyncio.Event()
        self._ended = False
        self._closing = False
        self._failed = False
        self._detail = ""
        self._hangup_reason = ""
        self._ledger: LiveLedger | None = None
        self._tools: LiveTools | None = None
        self._resampler = StreamingPcm16Resampler(48000, 24000)
        self._captions = {"user": "", "assistant": ""}
        self._last_role = ""
        self._last_end = {"user": -1, "assistant": -1}
        self._delegation_responses: dict[str, str] = {}
        self._voice_seconds = 0.0
        self.playback_active = False
        self._active_model = ""
        self._archive_turn_id = str(uuid4())
        self._parent_owned = False
        self._language = resolve_output_language(
            getattr(config.brain, "reply_language", "auto"),
            "auto",
            "",
        )

    @property
    def active_provider(self) -> str:
        return self._provider.name

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def failure_detail(self) -> str:
        return self._detail

    @property
    def hangup_reason(self) -> str:
        return self._hangup_reason

    @property
    def is_active(self) -> bool:
        return self._connection is not None and not self._closing

    async def wait_finished(self) -> None:
        await self._closed.wait()

    def set_playback_probe(self, _probe: Any) -> None:
        # Playback is owned by the browser; it is not a model turn boundary.
        return None

    async def handle_control(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "playback_state":
            self.playback_active = bool(message.get("active", False))
            return
        if kind == "audio_start" and self._connection is None:
            await self._start(message)
        elif kind == "audio_stop":
            await self.end(reason="client_stop")
        elif kind == "cancel_work" and self._tools is not None:
            self._tools.cancel_token.cancel("user_cancelled")
        elif kind == "text_input" and self._connection is not None:
            text = str(message.get("text", ""))[:32000]
            if self._tools is not None:
                self._tools.user_text = text
                self._tools.revision += 1
            await self._connection.send(
                {
                    "type": "response.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text}],
                    },
                }
            )
            await self._connection.send({"type": "response.create"})
        elif kind == "barge_in":
            # GPT-Live hears interruptions in the continuous input stream.
            await self._send_json({"type": "audio_clear"})

    async def _start(self, message: dict) -> None:
        self._adopt_desktop_session()
        profile = getattr(self._config, "live", LiveConfig())
        self._active_model = profile.model
        # Validate before acquiring devices, a durable store or a billed connection.
        profile.session_config(language=self._language, tools=[])
        gateway = get_supervisor_tool_gateway()
        if gateway is None:
            raise RuntimeError("Jarvis tools are still starting. Try voice again shortly.")
        root = user_data_dir()
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        self._ledger = await asyncio.to_thread(LiveLedger, root / "live.sqlite3")
        self._tools = LiveTools(
            gateway,
            self._ledger,
            self.session_id,
            language=self._language,
            backend_model=profile.backend_model,
        )
        prompt_language = getattr(self._config.brain, "reply_language", "auto")
        config = profile.session_config(language=prompt_language, tools=self._tools.declarations())
        offer = str(message.get("webrtc_offer_sdp", ""))
        self._resampler = StreamingPcm16Resampler(int(message.get("sample_rate", 48000)), 24000)
        await self._send_json({"type": "audio_starting", "provider": self.active_provider})
        try:
            from jarvis.live.runtime import claim

            claim(self.session_id)
            self._connection = await self._provider.open_session(
                ContinuousVoiceStart(session=config, offer_sdp=offer)
            )
            if not offer:
                async with asyncio.timeout(25):
                    while True:
                        event = await self._connection.receive()
                        if event.get("type") == "session.started":
                            self._connection.session_id = event["session"]["id"]
                            break
                        if event.get("type") == "error":
                            raise RuntimeError(
                                "OpenAI Live rejected session setup. "
                                "Check the selected model and account access."
                            )
            self._pump_task = asyncio.create_task(self._pump(), name="live-events")
            from jarvis.core.events import (
                RealtimeSessionReady,
                VoiceSessionStarted,
                VoiceTurnStarted,
            )
            from jarvis.live.runtime import register

            register(self)
            if self._bus is not None:
                if not self._parent_owned:
                    await self._bus.publish(
                        VoiceSessionStarted(
                            session_id=self.session_id,
                            language=self._language,
                        )
                    )
                await self._bus.publish(
                    VoiceTurnStarted(
                        session_id=self.session_id,
                        turn_id=self._archive_turn_id,
                    )
                )
                await self._bus.publish(
                    RealtimeSessionReady(
                        session_id=self.session_id,
                        provider=self.active_provider,
                        model=profile.model,
                        surface=self._surface,
                        input_sample_rate=24000,
                        output_sample_rate=24000,
                        language=self._language,
                    )
                )
            await self._send_json(
                {
                    "type": "audio_ready",
                    "provider": self.active_provider,
                    "model": profile.model,
                    "language": self._language,
                    "input_sample_rate": 24000,
                    "output_sample_rate": 24000,
                    "requires_webrtc_answer": bool(offer),
                    "webrtc_answer_sdp": self._connection.answer_sdp,
                    "continuous": True,
                }
            )
        except BaseException:
            await self.end(reason="error")
            raise

    def _adopt_desktop_session(self) -> None:
        from jarvis.core.runtime_refs import get_speech_pipeline

        pipeline = get_speech_pipeline()
        parent_id = getattr(pipeline, "_current_voice_session_id", None)
        if parent_id and getattr(pipeline, "_active_voice_mode", None) == "realtime":
            self.session_id = str(parent_id)
            self._parent_owned = True

    async def handle_audio_frame(self, pcm: bytes) -> None:
        if self._connection is None or self._closing or self._connection.answer_sdp:
            return
        audio = self._resampler.process(pcm)
        if audio:
            await self._connection.send(
                {
                    "type": "session.input_audio.append",
                    "audio": base64.b64encode(audio).decode("ascii"),
                }
            )

    async def _pump(self) -> None:
        try:
            while not self._closed.is_set():
                event = await self._connection.receive()
                await self._event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Live connection terminated")
            self._failed = True
            self._detail = "The voice connection was lost. Pending actions were not replayed."
            await self._send_json({"type": "provider_error", "error": self._detail})
        finally:
            from jarvis.live.runtime import unregister

            unregister(self.session_id)
            self._closed.set()

    async def _event(self, event: dict) -> None:
        assert self._ledger is not None and self._tools is not None
        kind = event.get("type", "")
        if kind in {"session.input_transcript.delta", "session.output_transcript.delta"}:
            role: Literal["user", "assistant"] = (
                "user" if kind == "session.input_transcript.delta" else "assistant"
            )
            delta = str(event.get("delta", ""))
            fragment = TranscriptFragment(
                self.session_id,
                event.get("event_id") or str(uuid4()),
                role,
                delta,
                int(event.get("start_ms", 0)),
                int(event.get("end_ms", 0)),
            )
            if not await asyncio.to_thread(self._ledger.append, fragment):
                return
            if fragment.start_ms - self._last_end[role] > 1500:
                self._captions[role] = ""
            self._captions[role] = (self._captions[role] + delta)[-32000:]
            current = fragment.end_ms >= self._last_end[role]
            self._last_end[role] = max(self._last_end[role], fragment.end_ms)
            if role == "user" and current:
                self._tools.user_text = self._captions[role]
                self._tools.revision += 1
            await self._send_json(
                {
                    "type": "transcript",
                    "role": role,
                    "text": self._captions[role],
                    "is_final": False,
                    "fragment": delta,
                    "event_id": fragment.event_id,
                    "start_ms": fragment.start_ms,
                    "end_ms": fragment.end_ms,
                }
            )
        elif kind == "session.output_audio.delta" and not self._connection.answer_sdp:
            await self._send_binary(base64.b64decode(event["delta"]))
        elif kind in {"session.usage.updated", "session.closed"}:
            seconds = float(event.get("usage", {}).get("seconds", self._voice_seconds))
            self._voice_seconds = max(seconds, self._voice_seconds)
            await asyncio.to_thread(
                self._ledger.usage, self.session_id, seconds, finalized=kind == "session.closed"
            )
            await self._send_json(
                {
                    "type": "live_usage",
                    "seconds": self._voice_seconds,
                    "finalized": kind == "session.closed",
                }
            )
            if kind == "session.closed":
                self._hangup_reason = str(event.get("reason", ""))
                self._closed.set()
                await self._send_json({"type": "audio_closed"})
        elif kind == "response.event":
            await self._response(event)
        elif kind == "error":
            code = event.get("error", {}).get("code")
            log.warning("Live command rejected: %s", code)
            await self._send_json(
                {
                    "type": "error",
                    "recoverable": True,
                    "message": "A voice request was rejected. "
                    "Check the selected model and account access.",
                }
            )

    async def _response(self, envelope: dict) -> None:
        assert self._ledger is not None and self._tools is not None
        event = envelope.get("event", {})
        kind = event.get("type")
        delegation = str(envelope.get("delegation_id", ""))
        if kind == "response.created":
            self._language = resolve_output_language(
                getattr(self._config.brain, "reply_language", "auto"),
                "auto",
                self._tools.user_text,
                conversation_language=self._language,
            )
            self._tools.language = self._language
            self._response_id = str(event["response"]["id"])
            self._delegation_responses[delegation] = self._response_id
            self._responses.setdefault(self._response_id, [])
        elif kind == "response.output_item.done":
            item = event.get("item", {})
            if item.get("type") == "function_call":
                rid = str(
                    event.get("response_id")
                    or self._delegation_responses.get(delegation, self._response_id)
                )
                calls = self._responses.setdefault(rid, [])
                if not any(c["call_id"] == item["call_id"] for c in calls):
                    calls.append({**item, "delegation_id": delegation})
        elif kind in {"response.completed", "response.failed", "response.incomplete"}:
            response = event.get("response", {})
            rid = str(response.get("id", self._response_id))
            if rid in self._completed:
                return
            self._completed.add(rid)
            usage = response.get("usage") or {}
            profile = getattr(self._config, "live", LiveConfig())
            await asyncio.to_thread(
                self._ledger.backend_usage, self.session_id, rid, profile.backend_model, usage
            )
            if self._bus is not None:
                from jarvis.brain.cost import calculate_cost_usd
                from jarvis.core.events import BrainTurnCompleted

                cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
                tokens_in = max(0, int(usage.get("input_tokens", 0)) - cached)
                tokens_out = int(usage.get("output_tokens", 0))
                await self._bus.publish(
                    BrainTurnCompleted(
                        provider="openai",
                        model=profile.backend_model,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        tokens_cached=cached,
                        cost_usd=calculate_cost_usd(
                            profile.backend_model, tokens_in, tokens_out, cached
                        ),
                        finish_reason="live_delegation",
                    )
                )
            await self._send_json(
                {
                    "type": "live_backend_usage",
                    "response_id": rid,
                    "usage": response.get("usage", {}),
                }
            )
            calls = self._responses.pop(rid, [])
            if calls and kind == "response.completed" and not self._closing:
                task = asyncio.create_task(
                    self._run_calls(calls, self._tools.revision), name="live-tools"
                )
                self._jobs.add(task)
                task.add_done_callback(self._jobs.discard)

    async def _run_calls(self, calls: list[dict], revision: int) -> None:
        assert self._tools is not None
        try:
            image_inputs = []
            for item in calls:
                try:
                    arguments = json.loads(item["arguments"])
                    if not isinstance(arguments, dict):
                        raise ValueError("Expected object arguments")
                    result = await self._tools.execute(
                        item["call_id"], item["name"], arguments, revision
                    )
                except (ValueError, TypeError):
                    result = {"success": False, "error": "Invalid function arguments."}
                if self._closing:
                    return
                image_inputs.extend(take_images(result))
                await self._connection.send(
                    {
                        "type": "response.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": item["call_id"],
                            "output": json.dumps(result, default=str),
                        },
                    }
                )
            if not self._closing:
                for image in image_inputs:
                    await self._connection.send(
                        {
                            "type": "response.item.create",
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_image",
                                        "image_url": f"data:{image['mime']};base64,{image['data']}",
                                    }
                                ],
                            },
                        }
                    )
                await self._connection.send({"type": "response.create"})
            if self._tools.end_requested:
                asyncio.create_task(self.end(reason="voice_pattern"), name="live-hangup")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Live tool-result delivery failed; receipt retained")

    async def deliver_announcement(self, text: str, **_kwargs: Any) -> bool:
        if not self.is_active:
            return False
        await self._connection.send(
            {"type": "session.commentary.append", "delegation_id": None, "content": text[:1000]}
        )
        return True

    async def end(self, *, reason: str = "client_stop") -> None:
        if self._ended:
            return
        self._ended = True
        from jarvis.live.runtime import unregister

        unregister(self.session_id)
        self._closing = True
        self._hangup_reason = reason
        if self._connection is not None:
            try:
                await self._send_json({"type": "audio_stopping"})
            except Exception:
                log.debug("Voice surface already disconnected", exc_info=True)
        if self._tools is not None:
            await self._tools.close()
        if self._connection is not None:
            try:
                await self._connection.send({"type": "session.close"})
                if self._pump_task is not None and not self._closed.is_set():
                    await asyncio.wait_for(self._closed.wait(), 15)
            except Exception:
                log.warning("Live session final usage is unconfirmed", exc_info=True)
            finally:
                await self._connection.close()
        if self._pump_task is not None and self._pump_task is not asyncio.current_task():
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
        if self._ledger is not None:
            if self._bus is not None:
                from jarvis.core.events import (
                    BrainTurnCompleted,
                    VoiceSessionEnded,
                    VoiceTurnCompleted,
                )

                fragments = await asyncio.to_thread(self._ledger.transcript, self.session_id)
                text = {
                    role: "".join(f["delta"] for f in fragments if f["role"] == role)
                    for role in ("user", "assistant")
                }
                if text["user"] or text["assistant"]:
                    await self._bus.publish(
                        VoiceTurnCompleted(
                            session_id=self.session_id,
                            turn_id=self._archive_turn_id,
                            user_text=text["user"],
                            jarvis_text=text["assistant"],
                            user_lang=self._language,
                            jarvis_lang=self._language,
                            provider=self.active_provider,
                            model=self._active_model,
                            tier="realtime",
                        )
                    )
                await self._bus.publish(
                    BrainTurnCompleted(
                        provider=self.active_provider,
                        model=self._active_model,
                        cost_usd=self._voice_seconds * 0.05 / 60,
                        finish_reason="realtime_usage",
                    )
                )
                await self._bus.publish(
                    VoiceSessionEnded(
                        session_id=self.session_id,
                        hangup_reason=reason,
                        duration_s=self._voice_seconds,
                    )
                )
            if self._jobs:
                from jarvis.live.runtime import retain_work

                retain_work(tuple(self._jobs), self._ledger)
            else:
                await asyncio.to_thread(self._ledger.close)
        self._closed.set()
