"""Direct-tool orchestration for Gemini and compatible local voice servers."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4

from jarvis.core.paths import user_data_dir
from jarvis.core.runtime_refs import get_supervisor_tool_gateway
from jarvis.live.runtime import claim, register, unregister
from jarvis.live.session import LiveVoiceSession
from jarvis.live.state import LiveLedger, TranscriptFragment
from jarvis.live.tools import LiveTools, take_images
from jarvis.realtime.audio import StreamingPcm16Resampler
from jarvis.realtime.protocol import RealtimeSessionConfig

log = logging.getLogger(__name__)


class NativeLiveVoiceSession(LiveVoiceSession):
    """Reuse native turn mechanics, without the legacy heuristic delegate loop."""

    async def _start(self, message: dict) -> None:
        self._adopt_desktop_session()
        gateway = get_supervisor_tool_gateway()
        if gateway is None:
            raise RuntimeError("Jarvis tools are still starting.")
        root = user_data_dir()
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        self._ledger = await asyncio.to_thread(LiveLedger, root / "live.sqlite3")
        self._tools = LiveTools(
            gateway, self._ledger, self.session_id, language=self._language, backend_model=""
        )
        try:
            claim(self.session_id)
            settings = getattr(self._config.brain, "providers", {}).get(self.active_provider)
            tier = self._config.brain.realtime
            model = getattr(tier, "model", "") or getattr(settings, "model", "") or ""
            self._active_model = model
            language_rule = (
                "Use the user's language and follow explicit language changes. "
                if getattr(self._config.brain, "reply_language", "auto") == "auto"
                else f"Speak {self._language}. "
            )
            cfg = RealtimeSessionConfig(
                model=model,
                language=self._language,
                voice=getattr(settings, "voice", "") or "",
                instructions=(
                    "You are Personal Jarvis. " + language_rule + "Use your tools directly "
                    "for actions, private information and current facts. Use discover_tools and "
                    "call_tool for any tool not declared directly. For computer control, capture "
                    "screen_snapshot, inspect the image, call the desktop primitives, and verify "
                    "the result with a new snapshot. Do not call a separate computer-use harness. "
                    "Request confirmation for pending approvals. Use confirm_action only after "
                    "explicit approval. A started job is not complete. Never invent tool results."
                ),
                tools=tuple(
                    {key: value for key, value in declaration.items() if key != "type"}
                    for declaration in self._tools.declarations()
                ),
            )
            self._connection = await self._provider.open_session(cfg)
            self._active_model = getattr(self._connection, "model", "") or model
            from jarvis.core.events import (
                RealtimeSessionReady,
                VoiceSessionStarted,
                VoiceTurnStarted,
            )

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
                        model=self._active_model,
                        language=self._language,
                        surface=self._surface,
                    )
                )
            rate = int(self._provider.input_sample_rate)
            self._resampler = StreamingPcm16Resampler(int(message.get("sample_rate", 48000)), rate)
            register(self)
            self._pump_task = asyncio.create_task(self._pump(), name="native-live-events")
            await self._send_json(
                {
                    "type": "audio_ready",
                    "provider": self.active_provider,
                    "model": model,
                    "input_sample_rate": rate,
                    "output_sample_rate": self._provider.output_sample_rate,
                    "language": self._language,
                    "requires_webrtc_answer": False,
                }
            )
        except BaseException:
            await self.end(reason="error")
            raise

    async def handle_audio_frame(self, pcm: bytes) -> None:
        if self._connection is not None and not self._closing:
            audio = self._resampler.process(pcm)
            if audio:
                await self._connection.send_audio(
                    SimpleNamespace(
                        pcm=audio,
                        sample_rate=self._provider.input_sample_rate,
                    )
                )

    async def _request_native_response(self) -> None:
        try:
            await self._connection.request_response()
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                log.exception("Native voice response request failed")

    async def _pump(self) -> None:
        assert self._tools is not None and self._ledger is not None
        try:
            async for event in self._connection.receive():
                if event.type == "audio_delta" and event.audio is not None:
                    await self._send_binary(event.audio.pcm)
                elif event.type in {"input_transcript", "output_transcript_delta"}:
                    role: Literal["user", "assistant"] = (
                        "user" if event.type == "input_transcript" else "assistant"
                    )
                    if role == "user":
                        self._tools.user_text = event.text or ""
                        if event.is_final:
                            self._tools.revision += 1
                            from jarvis.core.turn_language import resolve_output_language

                            self._language = resolve_output_language(
                                getattr(self._config.brain, "reply_language", "auto"),
                                "auto",
                                event.text or "",
                                conversation_language=self._language,
                            )
                            self._tools.language = self._language
                            if not getattr(
                                self._connection, "creates_responses_automatically", True
                            ):
                                task = asyncio.create_task(self._request_native_response())
                                self._control_tasks.add(task)
                                task.add_done_callback(self._control_tasks.discard)
                    if role == "assistant" or event.is_final:
                        stamp = time.monotonic_ns() // 1_000_000
                        await asyncio.to_thread(
                            self._ledger.append,
                            TranscriptFragment(
                                self.session_id,
                                str(uuid4()),
                                role,
                                event.text or "",
                                stamp,
                                stamp,
                            ),
                        )
                    await self._send_json(
                        {
                            "type": "transcript",
                            "role": role,
                            "text": event.text or "",
                            "is_final": event.is_final,
                        }
                    )
                elif event.type == "tool_call":
                    task = asyncio.create_task(self._call(event, self._tools.revision))
                    self._jobs.add(task)
                    task.add_done_callback(self._jobs.discard)
                elif event.type in {"interrupted", "speech_started"}:
                    await self._send_json({"type": "tts_cancel"})
                elif event.type == "turn_complete":
                    await self._send_json({"type": "tts_end"})
                elif event.type == "usage":
                    usage = event.usage or {}
                    await asyncio.to_thread(
                        self._ledger.backend_usage,
                        self.session_id,
                        str(uuid4()),
                        self._active_model,
                        usage,
                    )
                    if self._bus is not None:
                        from jarvis.brain.cost import calculate_realtime_cost_usd
                        from jarvis.core.events import BrainTurnCompleted

                        await self._bus.publish(
                            BrainTurnCompleted(
                                provider=self.active_provider,
                                model=self._active_model,
                                tokens_in=usage.get("input_total", 0),
                                tokens_out=usage.get("output_total", 0),
                                tokens_cached=usage.get("input_cached", 0),
                                cost_usd=calculate_realtime_cost_usd(
                                    self._active_model,
                                    usage.get("input_text", 0),
                                    usage.get("output_text", 0),
                                    usage.get("input_audio", 0),
                                    usage.get("output_audio", 0),
                                ),
                                finish_reason="realtime_usage",
                            )
                        )
                    await self._send_json({"type": "live_backend_usage", "usage": event.usage})
                elif event.type == "error":
                    if not getattr(event, "recoverable", False):
                        raise RuntimeError("Native voice connection failed")
                    log.warning("Native voice rejected a recoverable operation")
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._closing:
                # The SDK can surface a normal close as APIError(1000).
                log.debug("Native voice receiver ended during requested close")
                return
            log.exception("Native live connection terminated")
            self._failed = True
            await self._send_json(
                {
                    "type": "provider_error",
                    "error": "Voice connection lost; actions were not replayed.",
                }
            )
        finally:
            unregister(self.session_id)
            self._closed.set()

    async def _call(self, event: Any, revision: int) -> None:
        assert self._tools is not None
        try:
            result = await self._tools.execute(
                event.call_id or str(uuid4()), event.tool_name, event.tool_args or {}, revision
            )
            images = take_images(result)
            if self._closing:
                return
            send_image = getattr(self._connection, "send_image", None)
            if images and not callable(send_image):
                result = {
                    "success": False,
                    "error": "This voice server cannot receive screen images.",
                }
            if callable(send_image):
                for image in images:
                    await send_image(base64.b64decode(image["data"]), image["mime"])
            await self._connection.send_tool_result(event.call_id, event.tool_name, result)
            if self._tools.end_requested:
                asyncio.create_task(self.end(reason="voice_pattern"), name="native-live-hangup")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Native tool call failed; receipt retained")

    async def handle_control(self, message: dict) -> None:
        if message.get("type") == "text_input" and self._connection is not None:
            if self._tools is not None:
                self._tools.user_text = str(message.get("text", ""))
                self._tools.revision += 1
            await self._connection.send_text(str(message.get("text", "")))
        else:
            await super().handle_control(message)

    async def deliver_announcement(self, text: str, **_kwargs: Any) -> bool:
        if not self.is_active:
            return False
        await self._connection.send_text(text)
        return True

    async def end(self, *, reason: str = "client_stop") -> None:
        if self._ended:
            return
        self._ended = True
        self._closing = True
        self._hangup_reason = reason
        unregister(self.session_id)
        if self._tools is not None:
            await self._tools.close()
        for task in list(self._control_tasks):
            task.cancel()
        await asyncio.gather(*self._control_tasks, return_exceptions=True)
        if self._connection is not None:
            await self._connection.close()
        if self._pump_task is not None:
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
        if self._ledger is not None:
            from jarvis.live.recording import archive_session

            await archive_session(self, reason)
            if self._jobs:
                from jarvis.live.runtime import retain_work

                retain_work(tuple(self._jobs), self._ledger)
            else:
                await asyncio.to_thread(self._ledger.close)
        self._closed.set()
        await self._send_json({"type": "audio_closed"})
