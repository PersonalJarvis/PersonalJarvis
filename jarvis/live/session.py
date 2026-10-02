"""Continuous voice session: media, transcripts and delegated work are independent."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
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

#: Smallest gap between two live snapshots of one streaming reasoning summary.
#: The summary arrives token by token; the bus sees a few snapshots a second.
REASONING_SNAPSHOT_INTERVAL_S = 0.3

#: Reasoning items whose summary may be streaming at once (a bound, not a goal).
_REASONING_ITEMS_MAX = 16


def _pipeline_input_muted() -> bool:
    """Jarvis's microphone mute. The speech pipeline is its only writer."""
    from jarvis.core.runtime_refs import get_speech_pipeline

    return bool(getattr(get_speech_pipeline(), "is_muted", False))


def _pipeline_input_held() -> bool:
    """Whether a dictation beside the call holds the user's audio back."""
    from jarvis.core.runtime_refs import get_speech_pipeline

    return bool(getattr(get_speech_pipeline(), "is_voice_input_held", False))


def _summary_index(event: dict) -> int:
    """The summary part a reasoning-summary event belongs to (0 when absent)."""
    try:
        return max(0, int(event.get("summary_index") or 0))
    except (TypeError, ValueError):  # Malformed optional indexes use the first summary part.
        return 0


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
        self._response_revisions: dict[str, int] = {}
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
        from jarvis.live.transcript import LiveTranscript

        self._transcript = LiveTranscript()
        self._last_role = ""
        self._last_end = {"user": -1, "assistant": -1}
        self._delegation_responses: dict[str, str] = {}
        # The thinking model's reasoning summary while it streams: per
        # reasoning item, its summary parts by index, and when the item last
        # reached the bus (see ``_on_reasoning_delta``).
        self._reasoning_parts: dict[str, dict[int, str]] = {}
        self._reasoning_published_at: dict[str, float] = {}
        self._voice_seconds = 0.0
        self.playback_active = False
        self._speaking = False
        self._thinking = False
        self._indicator_phase = "idle"
        self._indicator_trace_id = uuid4()
        self._media_received = False
        self._mic_feedback_owned = False
        self._mic_feedback_warning = False
        self._input_active = False
        # The mute on the pet strip, the Jarvis Bar or the orb. A browser call
        # owns its microphone in the WebView, out of reach of the pipeline's own
        # capture gate, so the session drops the frames and tells the page.
        # ``_input_muted`` is what the frame gate and the page obey: the
        # user's mute OR a dictation holding the input (see ``VoiceInputHeld``).
        self._input_muted = False
        self._user_muted = False
        self._input_held = False
        self._watching_input_mute = False
        self._media_timeout: asyncio.TimerHandle | None = None
        self._active_model = ""
        self._archive_turn_id = str(uuid4())
        self._parent_owned = False
        self._recovering = False
        self._reconnect_attempts = 0
        self._resume_needs_input = False
        self._base_session_config: dict = {}
        self._using_webrtc = False
        self._offer_request = ""
        self._offer_future: asyncio.Future | None = None
        self._past_voice_seconds = 0.0
        self._wire_seconds = 0.0
        self._wire_epoch = 0
        self._timeline_offset = 0
        self._had_unconfirmed_wire = False
        self._initial_seed: list[dict] = []
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

    @property
    def phase(self) -> str:
        """The surface indicator state: speaking, thinking or listening."""
        if self.playback_active or (self._speaking and not self._media_received):
            return "speaking"
        if self._input_active:
            return "listening"
        if self._thinking or (self._media_received and self._speaking):
            return "thinking"
        return "listening"

    async def _emit_indicator(self, message: dict) -> None:
        """Send a speaking/thinking indicator without breaking the session.

        Indicators are best-effort UI hints: a closing socket must never turn
        them into a session failure.
        """
        if self._closing:
            return
        if self._media_received and message.get("type") in {
            "thinking", "speaking", "listening", "tts_start", "tts_end", "turn_complete",
        }:
            message = {"type": self.phase}
        await self._publish_phase()
        try:
            await self._send_json(message)
        except Exception:  # noqa: BLE001 — indicators must not kill voice
            log.debug("Live indicator frame could not be sent", exc_info=True)

    async def _publish_phase(self, phase: str | None = None) -> None:
        """Mirror the media state to native and remote surfaces on the same bus."""
        current = phase or self.phase
        if self._closing and current != "idle":
            return
        if current == self._indicator_phase:
            return
        previous, self._indicator_phase = self._indicator_phase, current
        if self._bus is not None:
            from jarvis.core.events import SystemStateChanged

            await self._bus.publish(
                SystemStateChanged(
                    source_layer="live.session",
                    trace_id=self._indicator_trace_id,
                    new_state=current.upper(),
                    previous=previous.upper(),
                )
            )

    async def _note_thinking(self) -> None:
        if self._thinking or self._closing:
            return
        self._thinking = True
        await self._emit_indicator({"type": "thinking"})

    async def _note_speaking(self) -> None:
        if self._closing:
            return
        first = not self._speaking
        self._speaking = True
        self._thinking = False
        if first:
            await self._emit_indicator({"type": "tts_start"})
            await self._emit_indicator({"type": "speaking"})

    async def _note_turn_end(self) -> None:
        if self._closing:
            return
        ended_speech = self._speaking
        self._speaking = False
        self._thinking = self._has_pending_work()
        if ended_speech:
            await self._emit_indicator({"type": "tts_end"})
        await self._emit_indicator({"type": "turn_complete"})

    async def wait_finished(self) -> None:
        await self._closed.wait()

    def set_playback_probe(self, _probe: Any) -> None:
        # Playback is owned by the browser; it is not a model turn boundary.
        return None

    def _has_pending_work(self) -> bool:
        return bool(self._response_revisions) or any(not job.done() for job in self._jobs)

    async def _playback_changed(self, active: bool) -> None:
        first = active and not self.playback_active
        ended = self.playback_active and not active
        self.playback_active = active
        self._speaking = active
        if not active:
            self._thinking = self._has_pending_work()
        await self._emit_indicator({"type": self.phase})
        if ended and self._media_received:
            from jarvis.audio import level_tap

            level_tap.reset_playing()
        if first and self._bus is not None and not self._closing:
            from jarvis.core.events import AudioOutFirst

            # The native bar deliberately waits for audible output AFTER the
            # SPEAKING state. RTP bypasses AudioPlayer, so media owns this edge.
            await self._bus.publish(
                AudioOutFirst(source_layer="live.media", trace_id=self._indicator_trace_id)
            )

    def _clear_media_levels(self) -> None:
        if self._media_timeout is not None:
            self._media_timeout.cancel()
            self._media_timeout = None
        if self._media_received:
            from jarvis.audio import level_tap, mic_level

            level_tap.reset_playing()
            if self._mic_feedback_owned:
                mic_level.publish(0.0, owner=self.session_id)
                mic_level.release_external(self.session_id)
                self._mic_feedback_owned = False
        self._input_active = False
        self.playback_active = False
        self._speaking = False

    def _expire_media_levels(self) -> None:
        self._clear_media_levels()
        if not self._closing and not self._recovering:
            task = asyncio.create_task(
                self._emit_indicator({"type": self.phase}), name="live-media-expired"
            )
            self._control_tasks.add(task)
            task.add_done_callback(self._control_tasks.discard)

    async def _receive_media_levels(self, message: dict) -> None:
        from pydantic import ValidationError

        from jarvis.audio import level_tap, mic_level
        from jarvis.live.media import MediaLevels

        if not self.is_active:
            return
        try:
            levels = MediaLevels.model_validate(message)
        except ValidationError:
            log.debug("Ignoring invalid browser media measurements")
            return
        previous_phase = self.phase
        previous_playback = self.playback_active
        # A frontend rebuild can reach an already-running process whose eager
        # audio module predates the lazily imported Live session. Optional
        # meters must degrade instead of terminating a paid voice connection.
        microphone_ready = all(
            callable(getattr(mic_level, name, None))
            for name in ("claim_external", "release_external", "publish")
        )
        # While a dictation holds the input, its own capture drives the meter:
        # the page's track is silenced, and claiming the meter here would
        # flatten the dictation's bars with the page's zeros.
        meter_owned = microphone_ready and not self._input_held
        if meter_owned:
            mic_level.claim_external(self.session_id)
            self._mic_feedback_owned = True
        elif not microphone_ready and not self._mic_feedback_warning:
            self._mic_feedback_warning = True
            log.warning("Browser microphone meter needs an app restart; voice remains connected")
        self._media_received = True
        self._input_active = levels.input_active
        if levels.playback_active != previous_playback:
            await self._playback_changed(levels.playback_active)
        elif self.phase != previous_phase or self._indicator_phase != self.phase:
            await self._emit_indicator({"type": self.phase})
        if self._closing:
            return
        if levels.playback_active:
            level_tap.publish(levels.output_level)
            level_tap.note_playing(0.3)
        elif previous_playback:
            level_tap.reset_playing()
        if meter_owned:
            mic_level.publish(levels.input_level, owner=self.session_id)
        if self._media_timeout is not None:
            self._media_timeout.cancel()
        self._media_timeout = asyncio.get_running_loop().call_later(1.5, self._expire_media_levels)

    async def handle_control(self, message: dict) -> None:
        if self._closing:
            return
        kind = message.get("type")
        if kind == "media_levels":
            try:
                await self._receive_media_levels(message)
            except Exception:  # noqa: BLE001 — optional UI feedback must not end speech
                log.warning("Browser media feedback failed; voice remains connected", exc_info=True)
            return
        if kind == "reconnect_offer" and self._offer_future is not None:
            if message.get("request_id") == self._offer_request and not self._offer_future.done():
                from jarvis.realtime.offer_broker import validate_webrtc_offer_sdp

                self._offer_future.set_result(validate_webrtc_offer_sdp(message.get("sdp")))
            return
        if kind == "playback_state":
            if type(message.get("active")) is bool:
                await self._playback_changed(message["active"])
            return
        if kind == "audio_start" and self._connection is None:
            await self._start(message)
        elif kind == "audio_stop":
            await self.end(reason="client_stop")
        elif kind == "cancel_work" and self._tools is not None:
            await self._tools.cancel_work()
        elif kind == "text_input" and self._connection is not None:
            text = str(message.get("text", ""))[:32000]
            if self._tools is not None:
                self._tools.user_text = text
                self._tools.revision += 1
                if not self._recovering:
                    self._resume_needs_input = False
                    self._tools.accept_new_input()
                    self._reconnect_attempts = 0
            preview = text.encode("utf-8")[:192].decode("utf-8", errors="ignore")
            await self._connection.send(
                {
                    "type": "session.thinking.append",
                    "delegation_id": None,
                    "content": "Typed user input (quoted data): "
                    + json.dumps(preview, ensure_ascii=False)
                    + ". The application is submitting the full request to your backend. "
                    "Wait for its result; do not duplicate the work.",
                }
            )
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
            self._speaking = False
            self._thinking = False
            self.playback_active = False
            await self._send_json({"type": "audio_clear"})
            await self._emit_indicator({"type": "tts_cancel"})

    async def _start(self, message: dict) -> None:
        self._adopt_desktop_session()
        started_at = time.monotonic()
        profile = getattr(self._config, "live", LiveConfig())
        self._active_model = profile.model
        # Validate before acquiring devices, a durable store or a billed connection.
        profile.session_config(language=self._language, tools=[])
        gateway = get_supervisor_tool_gateway()
        if gateway is None:
            raise RuntimeError("Jarvis tools are still starting. Try voice again shortly.")
        root = user_data_dir()

        async def _open_ledger() -> LiveLedger:
            await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
            return await asyncio.to_thread(LiveLedger, root / "live.sqlite3")

        async def _take_permit() -> None:
            from jarvis.live.recovery import connection_permit

            await connection_permit()

        # The durable store and the shared connection budget are independent:
        # opening them concurrently keeps the permit's quarter-second budget
        # off the wake path instead of stacked after a cold-disk sqlite open.
        self._ledger, _ = await asyncio.gather(_open_ledger(), _take_permit())
        assert self._ledger is not None
        setup_ms = (time.monotonic() - started_at) * 1000.0
        self._tools = LiveTools(
            gateway,
            self._ledger,
            self.session_id,
            language=self._language,
            backend_model=profile.backend_model,
        )
        prompt_language = getattr(self._config.brain, "reply_language", "auto")
        config = profile.session_config(
            language=prompt_language, tools=self._tools.declarations(defer_catalog=True)
        )
        self._base_session_config = config
        offer = str(message.get("webrtc_offer_sdp", ""))
        self._using_webrtc = bool(offer)
        self._resampler = StreamingPcm16Resampler(int(message.get("sample_rate", 48000)), 24000)
        await self._send_json({"type": "audio_starting", "provider": self.active_provider})
        try:
            from jarvis.live.runtime import claim

            claim(self.session_id)
            await self._publish_phase("connecting")
            from jarvis.live.recovery import seed_messages

            self._initial_seed = self._take_initial_context()
            if self._initial_seed:
                config["input"] = seed_messages(self._initial_seed, [])

            open_started_at = time.monotonic()
            self._connection = await self._provider.open_session(
                ContinuousVoiceStart(session=config, offer_sdp=offer)
            )
            open_ms = (time.monotonic() - open_started_at) * 1000.0
            log.info(
                "Live session opening: setup %.0f ms, provider open %.0f ms.",
                setup_ms,
                open_ms,
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
            from jarvis.brain import provider_health_ledger as health_ledger
            from jarvis.core.events import (
                RealtimeSessionReady,
                VoiceSessionStarted,
                VoiceTurnStarted,
            )
            from jarvis.live.runtime import register

            register(self)
            # The started session is the Realtime tab's evidence; its status
            # dot never opens a (billed) session of its own.
            health_ledger.record_success(
                self.active_provider, health_ledger.MODALITY_REALTIME, model=profile.model
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
                        model=profile.model,
                        surface=self._surface,
                        input_sample_rate=24000,
                        output_sample_rate=24000,
                        language=self._language,
                    )
                )
            await self._publish_phase()
            if self._closing:
                return
            await self._take_startup_input(message)
            self._watch_input_mute()
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
                    "input_muted": self._input_muted,
                }
            )
        except BaseException as exc:
            if isinstance(exc, Exception):
                await self._announce_start_failure(exc)
            await self.end(reason="error")
            raise

    async def _announce_start_failure(self, exc: Exception) -> None:
        """Say why the call ends instead of hanging up in silence.

        Live 2026-09-29: an empty API balance made every wake end after a
        second with no word, which read as a broken wake word.
        """
        from jarvis.brain import provider_health_ledger as health_ledger
        from jarvis.brain.provider_test import (
            NO_CREDITS,
            RATE_LIMITED,
            classify_provider_error,
        )
        from jarvis.realtime.session import _handshake_failure_message

        status = classify_provider_error(str(exc))
        health_ledger.record_status(
            self.active_provider, health_ledger.MODALITY_REALTIME, status
        )
        cause = {NO_CREDITS: "no_credits", RATE_LIMITED: "rate_limited"}.get(
            status, "unavailable"
        )
        log.warning("Live session could not start (cause=%s): %s", cause, exc)
        try:
            await self._send_json(
                {
                    "type": "error_spoken",
                    "text": _handshake_failure_message(cause, self._language),
                    "language": self._language,
                    "spoken_kind": "reply",
                    "provider": self.active_provider,
                }
            )
        except Exception:  # noqa: BLE001 — the start failure still propagates
            log.warning("Live start failure notice could not be sent", exc_info=True)

    def _take_initial_context(self) -> list[dict]:
        from jarvis.core.runtime_refs import get_brain_manager

        brain = self._brain or get_brain_manager()
        take = getattr(brain, "take_voice_history_seed", None)
        if not callable(take):
            return []
        return [
            {"role": message.role, "delta": str(message.content)}
            for message in take()
            if message.role in {"user", "assistant"} and message.content
        ]

    def _adopt_desktop_session(self) -> None:
        from jarvis.core.runtime_refs import get_speech_pipeline

        pipeline = get_speech_pipeline()
        parent_id = getattr(pipeline, "_current_voice_session_id", None)
        if parent_id and getattr(pipeline, "_active_voice_mode", None) == "realtime":
            self.session_id = str(parent_id)
            self._parent_owned = True

    async def _take_startup_input(self, message: dict) -> None:
        from jarvis.live.startup import take

        if self._parent_owned:
            prefix = await take(self.session_id, message.get("capture_started_at_ms"))
            if prefix is not None:
                await self._send_json(prefix)

    def _watch_input_mute(self) -> None:
        """Adopt the current microphone mute and follow it until the call ends."""
        self._user_muted = _pipeline_input_muted()
        self._input_held = _pipeline_input_held()
        self._input_muted = self._user_muted or self._input_held
        if self._bus is None or self._watching_input_mute:
            return
        from jarvis.core.events import VoiceInputHeld, VoiceMuteChanged

        self._bus.subscribe(VoiceMuteChanged, self._on_input_mute_changed)
        self._bus.subscribe(VoiceInputHeld, self._on_input_held_changed)
        self._watching_input_mute = True

    def _stop_watching_input_mute(self) -> None:
        if not self._watching_input_mute or self._bus is None:
            return
        from jarvis.core.events import VoiceInputHeld, VoiceMuteChanged

        self._bus.unsubscribe(VoiceMuteChanged, self._on_input_mute_changed)
        self._bus.unsubscribe(VoiceInputHeld, self._on_input_held_changed)
        self._watching_input_mute = False

    async def _on_input_mute_changed(self, event: Any) -> None:
        """Follow the user's own microphone mute."""
        self._user_muted = bool(event.muted)
        await self._apply_input_mute()

    async def _on_input_held_changed(self, event: Any) -> None:
        """Follow a dictation that borrows the user's voice beside the call.

        The call stays open; only the audio stops reaching the model until the
        dictated text has been delivered, so it never becomes a spoken turn.
        """
        self._input_held = bool(event.held)
        log.info(
            "Live call input %s (%s).",
            "held" if self._input_held else "released",
            getattr(event, "reason", "") or "unknown",
        )
        if self._input_held:
            self._release_mic_meter()
        await self._apply_input_mute()

    def _release_mic_meter(self) -> None:
        """Hand the microphone meter back so a dictation's own levels show."""
        if not self._mic_feedback_owned:
            return
        from jarvis.audio import mic_level

        release = getattr(mic_level, "release_external", None)
        if callable(release):
            release(self.session_id)
        self._mic_feedback_owned = False

    async def _apply_input_mute(self) -> None:
        """Drop the user's audio from now on and let the page silence its track.

        Dropping frames here covers the PCM socket. A WebRTC call sends its
        audio straight to the provider, so only the page can silence that one.
        """
        self._input_muted = self._user_muted or self._input_held
        if self._closing:
            return
        try:
            await self._send_json({"type": "input_mute", "muted": self._input_muted})
        except Exception:  # noqa: BLE001 — the frames are dropped here regardless
            log.warning("Voice page missed the microphone mute", exc_info=True)

    async def handle_audio_frame(self, pcm: bytes) -> None:
        if (
            self._connection is None
            or self._closing
            or self._recovering
            or self._input_muted
            or self._connection.answer_sdp
        ):
            return
        audio = self._resampler.process(pcm)
        if audio:
            try:
                await self._connection.send(
                    {
                        "type": "session.input_audio.append",
                        "audio": base64.b64encode(audio).decode("ascii"),
                    }
                )
            except Exception:
                log.debug("Audio send failed; closing the affected transport", exc_info=True)
                await self._connection.close()

    async def _pump(self) -> None:
        try:
            while not self._closed.is_set():
                try:
                    event = await self._connection.receive()
                except Exception:
                    if self._closing:
                        return
                    self._had_unconfirmed_wire = True
                    if await self._recover():
                        continue
                    raise
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
                self._timeline_offset + int(event.get("start_ms", 0)),
                self._timeline_offset + int(event.get("end_ms", 0)),
            )
            if not await asyncio.to_thread(self._ledger.append, fragment):
                return
            caption = self._transcript.feed(
                session_id=self.session_id, trace_id=self._indicator_trace_id,
                event_id=fragment.event_id, role=role, text=delta,
                start_ms=fragment.start_ms, end_ms=fragment.end_ms,
            )
            self._captions[role] = caption.text
            if self._bus is not None:
                await self._bus.publish(caption)
            current = fragment.end_ms >= self._last_end[role]
            self._last_end[role] = max(self._last_end[role], fragment.end_ms)
            if role == "user" and current:
                self._tools.user_text = self._captions[role]
                self._tools.revision += 1
                if not self._closing and not self._recovering:
                    self._resume_needs_input = False
                    self._tools.accept_new_input()
                    self._reconnect_attempts = 0
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
            if role == "assistant" and current:
                await self._note_thinking()
        elif kind == "session.output_audio.delta":
            # With WebRTC, only measured RTP playback owns the speaking
            # state. Sideband generation can lead playback or include silence.
            if not self._connection.answer_sdp:
                await self._note_speaking()
                await self._send_binary(base64.b64decode(event["delta"]))
        elif kind in {"session.usage.updated", "session.closed"}:
            self._wire_seconds = max(
                self._wire_seconds, float(event.get("usage", {}).get("seconds", self._wire_seconds))
            )
            seconds = self._past_voice_seconds + self._wire_seconds
            self._voice_seconds = max(seconds, self._voice_seconds)
            if (
                kind == "session.closed"
                and not self._closing
                and event.get("reason") in {"expired", "connection_lost"}
                and await self._recover()
            ):
                await asyncio.to_thread(self._ledger.usage, self.session_id, seconds)
                return
            finalized = kind == "session.closed" and not self._had_unconfirmed_wire
            await asyncio.to_thread(
                self._ledger.usage, self.session_id, seconds, finalized=finalized
            )
            await self._send_json(
                {
                    "type": "live_usage",
                    "seconds": self._voice_seconds,
                    "finalized": finalized,
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

    def _reasoning_text(self, item_id: str) -> str:
        """The streamed summary of one reasoning item so far, parts in order."""
        parts = self._reasoning_parts.get(item_id, {})
        return "\n\n".join(parts[index] for index in sorted(parts) if parts[index])

    async def _publish_reasoning(self, item_id: str, text: str, *, done: bool) -> None:
        """Publish one cumulative ``ReasoningSummaryUpdated`` snapshot."""
        if self._bus is None or not text.strip():
            return
        from jarvis.core.events import ReasoningSummaryUpdated
        from jarvis.core.redact import safe_preview

        self._reasoning_published_at[item_id] = time.monotonic()
        await self._bus.publish(ReasoningSummaryUpdated(
            source_layer="live.delegation", trace_id=self._indicator_trace_id,
            response_id=item_id, text=safe_preview(text, max_chars=4000), done=done,
        ))

    async def _on_reasoning_delta(self, event: dict, delegation: str) -> None:
        """Accumulate a streamed summary token; publish a snapshot now and then.

        The thinking model's summary arrives token by token. Every mirror — the
        app's thinking steps, the desktop pet's card — wants to follow it live,
        but one bus event per token is exactly the hot-path load AP-9 forbids.
        So the tokens collect per reasoning item and the bus sees the
        cumulative text at most every ``REASONING_SNAPSHOT_INTERVAL_S``; the
        ``.done`` events that follow always publish the final text.
        """
        item_id = str(event.get("item_id") or delegation)
        delta = str(event.get("delta") or "")
        if not delta:
            return
        streaming = self._reasoning_parts
        if item_id not in streaming and len(streaming) >= _REASONING_ITEMS_MAX:
            # Items are dropped when they finish; a stream that never finished
            # must not grow this forever. The oldest one goes.
            stale = next(iter(self._reasoning_parts))
            self._reasoning_parts.pop(stale, None)
            self._reasoning_published_at.pop(stale, None)
        parts = self._reasoning_parts.setdefault(item_id, {})
        index = _summary_index(event)
        parts[index] = parts.get(index, "") + delta
        last = self._reasoning_published_at.get(item_id)
        if last is not None and time.monotonic() - last < REASONING_SNAPSHOT_INTERVAL_S:
            return
        await self._publish_reasoning(item_id, self._reasoning_text(item_id), done=False)

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
            self._response_revisions[self._response_id] = self._tools.revision
            self._delegation_responses[delegation] = self._response_id
            self._responses.setdefault(self._response_id, [])
            if self._bus is not None:
                from jarvis.core.events import BrainTurnStarted

                await self._bus.publish(BrainTurnStarted(
                    source_layer="live.delegation", trace_id=self._indicator_trace_id,
                    provider=self.active_provider, model=self._tools.backend_model,
                ))
            await self._note_thinking()
        elif kind == "response.reasoning_summary_text.delta":
            await self._on_reasoning_delta(event, delegation)
        elif kind == "response.reasoning_summary_text.done":
            if event.get("text"):
                item_id = str(event.get("item_id") or event.get("response_id") or delegation)
                parts = self._reasoning_parts.setdefault(item_id, {})
                parts[_summary_index(event)] = str(event["text"])
                # The whole summary so far, not only this part: a snapshot
                # replaces the previous one, so a part alone would erase the
                # parts before it from every mirror.
                await self._publish_reasoning(item_id, self._reasoning_text(item_id), done=True)
        elif kind == "response.output_item.done":
            item = event.get("item", {})
            if item.get("type") == "reasoning":
                item_id = str(item.get("id") or delegation)
                self._reasoning_parts.pop(item_id, None)
                self._reasoning_published_at.pop(item_id, None)
                summary = "\n\n".join(
                    str(part.get("text", "")) for part in item.get("summary", [])
                    if part.get("type") == "summary_text"
                )
                if summary:
                    await self._publish_reasoning(item_id, summary, done=True)
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
            calls = self._responses.pop(rid, [])
            revision = self._response_revisions.pop(rid, self._tools.revision)
            # A backend response completing is not a speech boundary. Its
            # tool calls still need running and RTP can still be playing.
            self._thinking = bool(
                (kind == "response.completed" and calls) or self._has_pending_work()
            )
            if self._thinking or self.playback_active:
                self._speaking = self.playback_active
                await self._emit_indicator({"type": self.phase})
            else:
                await self._note_turn_end()
            usage = response.get("usage") or {}
            backend_model = self._tools.backend_model
            await asyncio.to_thread(
                self._ledger.backend_usage, self.session_id, rid, backend_model, usage
            )
            if self._bus is not None:
                from jarvis.core.events import BrainTurnCompleted
                from jarvis.live.cost import backend_cost_usd

                cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
                tokens_in = max(0, int(usage.get("input_tokens", 0)) - cached)
                tokens_out = int(usage.get("output_tokens", 0))
                await self._bus.publish(
                    BrainTurnCompleted(
                        provider="openai",
                        model=backend_model,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        tokens_cached=cached,
                        cost_usd=backend_cost_usd(backend_model, usage),
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
            if calls and kind == "response.completed" and not self._closing:
                task = asyncio.create_task(self._run_calls(calls, revision), name="live-tools")
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
                        f"{self._wire_epoch}:{item['call_id']}", item["name"], arguments, revision
                    )
                except (ValueError, TypeError):  # The tool receives an invalid-arguments result.
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
                            "output": json.dumps(
                                result, ensure_ascii=False, separators=(",", ":"), default=str
                            ),
                        },
                    }
                )
            if not self._closing and not self._resume_needs_input:
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

    async def _recover(self) -> bool:
        from jarvis.live.recovery import connection_permit, seed_messages

        if (
            self._closing
            or self._reconnect_attempts >= 2
            or self._jobs
            or self._responses
            or self._tools is None
            or self._ledger is None
            or self._tools._pending
        ):
            return False
        safe, receipts = await asyncio.to_thread(self._ledger.recovery_state, self.session_id)
        if not safe:
            return False
        self._recovering = True
        self._resume_needs_input = True
        self._tools.accepting = False
        self._reconnect_attempts += 1
        try:
            for task in tuple(self._control_tasks):
                task.cancel()
            await asyncio.gather(*self._control_tasks, return_exceptions=True)
            await self._send_json({"type": "reconnecting", "attempt": self._reconnect_attempts})
            await self._connection.close()
            delay = random.uniform(0.1, min(4.0, 2**self._reconnect_attempts))  # noqa: S311
            await asyncio.sleep(delay)
            await connection_permit()
            fragments = await asyncio.to_thread(self._ledger.transcript, self.session_id)
            history = seed_messages(self._initial_seed + fragments, receipts)
            self._timeline_offset = max(self._last_end.values()) + 1
            self._past_voice_seconds = self._voice_seconds
            self._wire_seconds = 0.0
            self._wire_epoch += 1
            self._captions = {"user": "", "assistant": ""}
            self._resampler.reset()
            await self._open_replacement(history)
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Voice reconnection failed; no actions were replayed", exc_info=True)
            return False
        finally:
            self._recovering = False

    async def _open_replacement(self, history: list[dict]) -> None:
        config = {**self._base_session_config, "input": history}
        config["instructions"] += (
            " Connection restored. Previous tool receipts remain valid. "
            "Wait for the user to continue before starting any task."
        )
        offer = ""
        if self._using_webrtc:
            self._offer_request = str(uuid4())
            self._offer_future = asyncio.get_running_loop().create_future()
            await self._send_json({"type": "reconnect_offer", "request_id": self._offer_request})
            try:
                offer = await asyncio.wait_for(self._offer_future, 15)
            finally:
                self._offer_future = None
        self._connection = await self._provider.open_session(ContinuousVoiceStart(config, offer))
        if not offer:
            async with asyncio.timeout(25):
                while True:
                    event = await self._connection.receive()
                    if event.get("type") == "session.started":
                        self._connection.session_id = event["session"]["id"]
                        break
                    if event.get("type") == "error":
                        raise RuntimeError("The provider rejected the replacement session.")
        await self._send_json(
            {
                "type": "audio_ready",
                "provider": self.active_provider,
                "model": self._active_model,
                "language": self._language,
                "input_sample_rate": 24000,
                "output_sample_rate": 24000,
                "requires_webrtc_answer": bool(offer),
                "webrtc_answer_sdp": self._connection.answer_sdp,
                "continuous": True,
                "reconnected": True,
            }
        )

    async def deliver_announcement(
        self, text: str, *, report: str | None = None, **kwargs: Any
    ) -> bool:
        if not self.is_active:
            return False
        if str(report or "").strip():
            return await self._deliver_report(text, str(report), kwargs)
        await self._connection.send(
            {"type": "session.commentary.append", "delegation_id": None, "content": text[:1000]}
        )
        return True

    async def _deliver_report(self, text: str, report: str, kwargs: dict[str, Any]) -> bool:
        """Let the reasoning backend think about an agent's report, then speak.

        A report is not a line to relay: the backend is given the full text and
        works out what the user needs to hear (``report_prompt``). Refused while
        a turn is in flight — the caller parks it and retries at the next
        boundary — so it never talks over the user or a running answer.
        """
        if (
            self._recovering
            or self._resume_needs_input
            or self._input_active
            or self._thinking
            or self._speaking
            or self.playback_active
            or self._has_pending_work()
        ):
            return False
        from jarvis.realtime.report_prompt import report_update_prompt

        language = str(kwargs.get("language") or self._language)
        prompt = report_update_prompt(
            text,
            report,
            language=language,
            kind=str(kwargs.get("spoken_kind") or "completion"),
        )
        await self._connection.send(
            {
                "type": "response.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "[Application event, not the user speaking]\n" + prompt,
                        }
                    ],
                },
            }
        )
        await self._connection.send({"type": "response.create"})
        return True

    async def attach_appshot(self, image: bytes, mime: str, note: str) -> bool:
        """Put an appshot into the thinking backend's context, silently.

        The voice model only hears that it exists; the picture itself goes to
        the backend conversation, which answers every question about it. No
        response is requested: the user's next words are the question.
        """
        if not self.is_active or self._recovering or self._resume_needs_input:
            return False
        await self._connection.send(
            {
                "type": "session.thinking.append",
                "delegation_id": None,
                "content": (
                    "The user just took an appshot of their front window. It is "
                    "in your backend's context. Delegate any question about what "
                    "they are looking at; do not describe it yourself, and do "
                    "not comment on the appshot unless asked."
                ),
            }
        )
        await self._connection.send(
            {
                "type": "response.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": note},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime};base64,"
                            + base64.b64encode(image).decode("ascii"),
                        },
                    ],
                },
            }
        )
        return True

    async def end(self, *, reason: str = "client_stop") -> None:
        if self._ended:
            return
        self._ended = True
        self._stop_watching_input_mute()
        self._clear_media_levels()
        from jarvis.live.runtime import unregister

        unregister(self.session_id)
        self._closing = True
        await self._publish_phase("idle")
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
