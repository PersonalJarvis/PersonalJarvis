"""Direct-tool orchestration for Gemini and compatible local voice servers."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4

from jarvis.brain.identity import name_directive
from jarvis.core.agent_brief import AGENT_BRIEF_RULE
from jarvis.core.paths import user_data_dir
from jarvis.core.runtime_refs import get_supervisor_tool_gateway
from jarvis.core.tool_budget import VOICE_TOOL_BUDGET_S
from jarvis.cu.direct import COMPUTER_CONTROL_RULES
from jarvis.live.product import PRODUCT_BRIEF
from jarvis.live.runtime import claim, register, unregister
from jarvis.live.session import LiveVoiceSession, _identity
from jarvis.live.state import LiveLedger, TranscriptFragment
from jarvis.live.tools import LiveTools, take_images
from jarvis.realtime.audio import StreamingPcm16Resampler
from jarvis.realtime.protocol import RealtimeSessionConfig, RealtimeUnavailableError

log = logging.getLogger(__name__)

# How long a provider's readiness probe may take before the call is refused
# (release SLO: an honest refusal within one second, spoken and visible).
_DUPLEX_PROBE_BUDGET_S = 1.0
# Spoken only when the provider gives no reason of its own (or its probe hangs).
_DUPLEX_REFUSAL_FALLBACK = "The selected voice engine cannot take a call right now."
# Ceiling for ONE native tool call before the live model is released with an
# honest "still running" result; the tool itself keeps running. Ported from
# ``_NATIVE_TOOL_DEADLINE_S`` in ``jarvis/realtime/session.py``. A module
# attribute so tests can pin it low.
_TOOL_DEADLINE_S = VOICE_TOOL_BUDGET_S
# Session built-ins of ``LiveTools``: always declared, whatever the budget.
_SESSION_TOOLS = frozenset(
    {"end_call", "discover_tools", "call_tool", "confirm_action", "computer", "take_appshot"}
)
# Under a declaration budget (a provider's ``tool_declaration_budget_tokens``
# or ``[voice].realtime_tool_declaration_budget_tokens``, the smaller wins), at
# most this many catalog tools are declared directly; every other tool stays
# reachable through discover_tools/call_tool. A small local model pays a full
# LLM round per discovery step, so the frequent tools come first.
_DIRECT_TOOL_LIMIT = 12
_DIRECT_TOOL_PREFERENCE = (
    "workspace-orchestrate",
    "find-app-action",
    "run-app-action",
    "search_web",
    "open_app",
    "take_appshot",
    "screen_snapshot",
    "google_calendar",
    "gmail",
    "youtube_music",
    "spotify",
    "home_assistant",
    "wiki-recall",
    "product_help",
)


def _declared_name(declaration: dict) -> str:
    """The canonical tool name behind a declaration (catalog tools are aliased)."""
    name = str(declaration.get("name", ""))
    if name in _SESSION_TOOLS:
        return name
    return str(declaration.get("description", "")).split(": ", 1)[0]


def _fit_declarations(declarations: list[dict], budget_chars: int) -> tuple[dict, ...]:
    """Session built-ins plus a curated direct set that fits ``budget_chars``."""
    session = [d for d in declarations if d.get("name") in _SESSION_TOOLS]
    rank = {name: index for index, name in enumerate(_DIRECT_TOOL_PREFERENCE)}
    catalog = sorted(
        (d for d in declarations if d.get("name") not in _SESSION_TOOLS),
        key=lambda d: rank.get(_declared_name(d), len(rank)),
    )
    used = sum(len(json.dumps(d)) for d in session)
    direct: list[dict] = []
    for declaration in catalog:
        if len(direct) >= _DIRECT_TOOL_LIMIT:
            break
        size = len(json.dumps(declaration))
        if used + size > budget_chars:
            continue
        used += size
        direct.append(declaration)
    return (*session, *direct)


def _log_late_probe(task: asyncio.Future) -> None:
    """Retrieve a readiness probe that outlived its refusal, so nothing is lost."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.warning("Native voice readiness probe failed after the refusal: %s", exc)
    else:
        log.info("Native voice readiness probe answered late: %s", task.result())


class NativeLiveVoiceSession(LiveVoiceSession):
    """Reuse native turn mechanics, without the legacy heuristic delegate loop."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._late_tool_results: list[tuple[int, int, str, dict]] = []
        self._native_turn_complete = True

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
        self._tools.ask_hangup = self._ask_voice_hangup
        try:
            claim(self.session_id)
            self._initial_seed = self._take_initial_context()
            settings = getattr(self._config.brain, "providers", {}).get(self.active_provider)
            # The active provider's own model only. ``[brain.realtime].model``
            # belongs to whichever card last wrote it (the OpenAI Live card)
            # and survives a provider switch, so Gemini or the local engine
            # was handed another provider's model id. Empty = adapter default.
            model = getattr(settings, "model", "") or ""
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
                    (_identity(self._config) or name_directive(""))
                    + "\n\n"
                    + PRODUCT_BRIEF
                    + " "
                    + language_rule
                    + "Use your tools directly "
                    "for actions, private information and current facts. Use discover_tools and "
                    "call_tool for any tool not declared directly. "
                    + COMPUTER_CONTROL_RULES
                    + " When the user asks for an appshot, call take_appshot. "
                    "Request confirmation for pending approvals. Use confirm_action only after "
                    "explicit approval. A started job is not complete. Never invent tool results. "
                    "Use workspace-orchestrate for coding tasks: inspect and resolve project, "
                    "workspace and agent references, then send to the returned stable IDs. "
                    "For a NEW coding agent call workspace-orchestrate create (cli, count, "
                    "prompt) in the named or visible workspace; never spawn_worker. "
                    "Explicit references override the visible workspace; ask on ambiguity. "
                    "Do not switch the UI to address another workspace. Reuse request_id on "
                    "retries and never replay uncertain delivery. "
                    + AGENT_BRIEF_RULE
                ),
                history=tuple(
                    {"role": item["role"], "text": item["delta"]} for item in self._initial_seed
                ),
                tools=self._tool_declarations(),
            )
            self._native_config = cfg
            await self._refuse_unless_duplex_ready()
            from jarvis.live.recovery import connection_permit

            await connection_permit()
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
            await self._take_startup_input(message)
            self._watch_input_mute()
            await self._send_json(
                {
                    "type": "audio_ready",
                    "sound_effects": bool(
                        getattr(getattr(self._config, "ui", None), "sound_effects", True)
                    ),
                    "provider": self.active_provider,
                    "model": model,
                    "input_sample_rate": rate,
                    "output_sample_rate": self._provider.output_sample_rate,
                    "language": self._language,
                    "requires_webrtc_answer": False,
                    "input_muted": self._input_muted,
                }
            )
            # Audio flows over this socket: results that finished before the
            # call may be offered at the first pause, which is now.
            self._notify_pause()
        except BaseException:
            await self.end(reason="error")
            raise

    def _declaration_budget_tokens(self) -> int:
        """The smaller of the config bound and the provider's own budget; 0 = none.

        Same rule as ``RealtimeVoiceSession._declaration_budget_chars``: the
        provider's budget is a capability (AP-21), never a name check.
        """
        configured = int(
            getattr(
                getattr(self._config, "voice", None),
                "realtime_tool_declaration_budget_tokens",
                0,
            )
            or 0
        )
        declared = int(getattr(self._provider, "tool_declaration_budget_tokens", 0) or 0)
        budgets = [budget for budget in (configured, declared) if budget > 0]
        return min(budgets) if budgets else 0

    def _tool_declarations(self) -> tuple[dict, ...]:
        assert self._tools is not None
        declarations = [
            {key: value for key, value in declaration.items() if key != "type"}
            for declaration in self._tools.declarations()
        ]
        budget = self._declaration_budget_tokens()
        if budget <= 0:
            return tuple(declarations)
        fitted = _fit_declarations(declarations, budget * 4)
        # AP-30: a trimmed tool set looks exactly like a complete one.
        log.info(
            "Native voice declares %d of %d tools under a %d-token budget for %s; "
            "the rest stay reachable through discover_tools",
            len(fitted),
            len(declarations),
            budget,
            self.active_provider,
        )
        return fitted

    async def _refuse_unless_duplex_ready(self) -> None:
        """Refuse within a second, in the provider's own words, when it cannot talk.

        A provider without the probe is assumed ready. A probe that outlives
        the budget keeps running in the background: it may be starting an
        engine, which must not be torn in half.
        """
        probe = getattr(self._provider, "can_open_duplex_session", None)
        if not callable(probe):
            return
        check = asyncio.ensure_future(probe())
        try:
            ready = bool(await asyncio.wait_for(asyncio.shield(check), _DUPLEX_PROBE_BUDGET_S))
        except TimeoutError:  # a late probe is logged by its callback
            check.add_done_callback(_log_late_probe)
            ready = False
        except Exception:
            log.warning("Native voice readiness probe failed", exc_info=True)
            ready = False
        if ready:
            return
        reason = (
            str(getattr(self._provider, "duplex_unavailable_reason", "") or "").strip()
            or _DUPLEX_REFUSAL_FALLBACK
        )
        self._detail = reason
        log.warning("Native voice refused to start (%s): %s", self.active_provider, reason)
        try:
            await self._send_json(
                {
                    "type": "error_spoken",
                    "text": reason,
                    "detail": reason,
                    "language": self._language,
                    "spoken_kind": "reply",
                    "provider": self.active_provider,
                }
            )
        except Exception:  # noqa: BLE001 — the refusal still propagates
            log.warning("Native voice refusal notice could not be sent", exc_info=True)
        raise RealtimeUnavailableError(reason)

    async def _interrupt_reply(self) -> None:
        """Barge-in: stop the provider's reply and flush what the browser holds."""
        interrupt = getattr(self._connection, "interrupt", None)
        if callable(interrupt):
            try:
                await interrupt()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning(
                    "Native voice interrupt failed; flushing playback anyway", exc_info=True
                )
        self.playback_active = False
        await self._emit_indicator({"type": "audio_clear"})

    async def handle_audio_frame(self, pcm: bytes) -> None:
        if (
            self._connection is not None
            and not self._closing
            and not self._recovering
            and not self._input_muted
        ):
            audio = self._resampler.process(pcm)
            if audio:
                try:
                    await self._connection.send_audio(
                        SimpleNamespace(
                            pcm=audio,
                            sample_rate=self._provider.input_sample_rate,
                        )
                    )
                except Exception:
                    log.debug("Native audio send failed; closing transport", exc_info=True)
                    await self._connection.close()

    async def _open_replacement(self, history: list[dict]) -> None:
        native_history = tuple(
            {"role": item["role"], "text": item["content"][0]["text"]} for item in history
        )
        cfg = replace(
            self._native_config,
            history=native_history,
            instructions=self._native_config.instructions
            + " Connection restored. Wait for the user before starting another task.",
        )
        self._connection = await self._provider.open_session(cfg)
        await self._send_json(
            {
                "type": "audio_ready",
                "provider": self.active_provider,
                "model": self._active_model,
                "language": self._language,
                "input_sample_rate": self._provider.input_sample_rate,
                "output_sample_rate": self._provider.output_sample_rate,
                "requires_webrtc_answer": False,
                "reconnected": True,
            }
        )

    async def _request_native_response(self, language: str = "") -> None:
        # The turn's language is decided once (``turn_language``); a provider
        # that can take it per session hears it before it answers.
        update = getattr(self._connection, "update_session", None)
        if language and callable(update):
            try:
                await update(language=language)
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._closing:
                    log.warning(
                        "Native voice language update failed; answering anyway", exc_info=True
                    )
        try:
            await self._connection.request_response()
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                log.exception("Native voice response request failed")

    async def _pump(self) -> None:
        try:
            while not self._closing:
                events = self._connection.receive().__aiter__()
                while not self._closing:
                    try:
                        event = await anext(events)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # Shutdown is quiet; active failures enter the recovery loop.
                        if self._closing:
                            return
                        if await self._wait_for_connection():
                            break
                        return
                    await self._native_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
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
            self._notify_ended()
            self._closed.set()

    async def _native_event(self, event: Any) -> None:
        assert self._tools is not None and self._ledger is not None
        if event.type in {"audio_delta", "output_transcript_delta", "tool_call"}:
            self._native_turn_complete = False
        if event.type == "audio_delta" and event.audio is not None:
            self._report_started()
            await self._note_speaking()
            await self._send_binary(event.audio.pcm)
        elif event.type in {"input_transcript", "output_transcript_delta"}:
            role: Literal["user", "assistant"] = (
                "user" if event.type == "input_transcript" else "assistant"
            )
            if role == "user":
                self._tools.user_text = event.text or ""
                if event.is_final:
                    self._tools.revision += 1
                    if self._bus is not None:
                        from jarvis.core.events import BrainTurnStarted

                        await self._bus.publish(BrainTurnStarted(
                            source_layer="live.native", trace_id=self._indicator_trace_id,
                            provider=self.active_provider, model=self._active_model,
                        ))
                    if not self._closing and not self._recovering:
                        self._resume_needs_input = False
                        self._tools.accept_new_input()
                        self._reconnect_attempts = 0
                    from jarvis.core.turn_language import resolve_output_language

                    self._language = resolve_output_language(
                        getattr(self._config.brain, "reply_language", "auto"),
                        "auto",
                        event.text or "",
                        conversation_language=self._language,
                    )
                    self._tools.language = self._language
                    if not getattr(self._connection, "creates_responses_automatically", True):
                        task = asyncio.create_task(self._request_native_response(self._language))
                        self._control_tasks.add(task)
                        task.add_done_callback(self._control_tasks.discard)
            if role == "assistant":
                self._report_started()
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
            stamp = time.monotonic_ns() // 1_000_000
            caption = self._transcript.feed(
                session_id=self.session_id, trace_id=self._indicator_trace_id,
                event_id=str(uuid4()), role=role, text=event.text or "",
                start_ms=stamp, end_ms=stamp, snapshot=role == "user",
            )
            if self._bus is not None:
                await self._bus.publish(caption)
            if role == "user" and event.is_final:
                self._transcript.finish("user")
        elif event.type == "tool_call":
            # The model is answering the report, starting with a tool.
            self._report_started()
            await self._note_thinking()
            task = asyncio.create_task(self._call(event, self._tools.revision))
            self._track_job(task)
        elif event.type in {"interrupted", "speech_started"}:
            # A barge-in into a report: the user heard its start and chose to
            # talk. The report is in the model's context for follow-ups.
            self._report_finished(delivered=True)
            barge_in = event.type == "speech_started" and (self._speaking or self.playback_active)
            self._speaking = False
            self._thinking = False
            if barge_in:
                await self._interrupt_reply()
            await self._emit_indicator({"type": "tts_cancel"})
        elif event.type == "turn_complete":
            self._native_turn_complete = True
            self._report_finished(delivered=True)
            self._transcript.finish("assistant")
            await self._note_turn_end()
            self._notify_pause()
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

    async def _call(self, event: Any, revision: int) -> None:
        assert self._tools is not None
        try:
            work = asyncio.ensure_future(
                self._tools.execute(
                    f"{self._wire_epoch}:{event.call_id or uuid4()}",
                    event.tool_name,
                    event.tool_args or {},
                    revision,
                )
            )
            try:
                # ``shield``: the deadline releases the LIVE MODEL, never the
                # tool; an action mid-flight finishes and keeps its receipt.
                result = await asyncio.wait_for(asyncio.shield(work), _TOOL_DEADLINE_S)
            except asyncio.CancelledError:
                work.cancel()  # cancelling the call still cancels its tool, as before
                raise
            except TimeoutError:  # a slow tool is released, not dropped
                self._native_turn_complete = False  # The pending answer starts another response.
                result = self._release_slow_tool(work, str(event.tool_name or ""), revision)
            if self._closing:
                return
            result = await self._send_tool_images(result)
            self._native_turn_complete = False
            await self._connection.send_tool_result(event.call_id, event.tool_name, result)
            if self._tools.end_requested:
                asyncio.create_task(self.end(reason="voice_pattern"), name="native-live-hangup")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Native tool call failed; receipt retained")

    async def _send_tool_images(self, result: dict) -> dict:
        """Keep the execution receipt even when its visual evidence cannot be delivered."""
        images = take_images(result)
        if not images:
            return result
        send_image = getattr(self._connection, "send_image", None)
        try:
            if not callable(send_image):
                raise RuntimeError("This voice server cannot receive screen images.")
            for image in images:
                await send_image(base64.b64decode(image["data"]), image["mime"])
        except Exception:
            log.warning("Native tool screenshot delivery failed", exc_info=True)
            return {
                **result,
                "success": False,
                "verified": False,
                "error": (
                    str(result.get("error") or "") + " The tool's screenshot could not be "
                    "delivered. Its execution receipt is preserved below; an action may "
                    "already have run. Do not claim visual success or repeat that action "
                    "without checking the current screen."
                ).strip(),
            }
        return result

    def _notify_pause(self) -> None:
        # The original function call already received its pending answer. Its
        # final receipt is a new report at a pause, never a second function
        # response and never a repeated action. A changed request/connection
        # invalidates the queued report; the durable ledger still keeps it.
        if self._native_turn_complete and not self._has_pending_work():
            self._thinking = False
        if self._tools is not None:
            self._late_tool_results = [
                item for item in self._late_tool_results
                if item[0] == self._wire_epoch and item[1] == self._tools.revision
                and not self._tools.cancel_token.is_cancelled()
            ]
        if self._late_tool_results and self.ready_for_report:
            item = self._late_tool_results.pop(0)
            self._report_sent()  # Reserve the pause before any asynchronous send.
            task = asyncio.create_task(self._report_late_tool(item), name="native-tool-report")
            self._control_tasks.add(task)
            task.add_done_callback(self._control_tasks.discard)
            return
        super()._notify_pause()

    async def _report_late_tool(self, item: tuple[int, int, str, dict]) -> None:
        from jarvis.realtime.report_prompt import report_update_prompt

        epoch, revision, name, result = item
        try:
            if not self._late_tool_current(epoch, revision):
                self._cancel_report_timeout()
                self._report_state = ""
                return
            result = await self._send_tool_images(result)
            if not self._late_tool_current(epoch, revision):
                self._cancel_report_timeout()
                self._report_state = ""
                return
            await self._connection.send_text(report_update_prompt(
                f"The previously pending tool {name} has finished.",
                json.dumps(result, ensure_ascii=False, default=str),
                language=self._language,
                kind="tool_completion",
            ))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Delivery may be uncertain. Keep the durable receipt, but never
            # retry an action or automatically repeat a partially sent report.
            self._cancel_report_timeout()
            self._report_state = ""
            log.warning("Late native tool report could not be delivered", exc_info=True)

    def _late_tool_current(self, epoch: int, revision: int) -> bool:
        return bool(
            self.is_active and not self._recovering and not self._input_active
            and epoch == self._wire_epoch and self._tools is not None
            and revision == self._tools.revision and not self._tools.cancel_token.is_cancelled()
        )

    def _release_slow_tool(self, work: asyncio.Future, name: str, revision: int) -> dict:
        """Answer the model honestly while the tool runs on in the background."""
        started = time.monotonic()
        epoch = self._wire_epoch
        log.warning(
            "Native tool %s still running after %.0fs; releasing the live model with a "
            "pending result, the tool finishes in the background",
            name,
            _TOOL_DEADLINE_S,
        )
        # Kept in ``_jobs``: a call that ends now still waits for its receipt.
        self._jobs.add(work)
        work.add_done_callback(self._jobs.discard)

        def _late(done: asyncio.Future) -> None:
            waited_ms = round((time.monotonic() - started) * 1000)
            if done.cancelled():
                log.info("Late native tool %s was cancelled", name)
            elif done.exception() is not None:
                log.warning("Late native tool %s failed: %s", name, done.exception())
            else:
                outcome = done.result()
                log.info(
                    "Late native tool %s finished %d ms after its release (success=%s)",
                    name,
                    waited_ms,
                    bool(isinstance(outcome, dict) and outcome.get("success")),
                )
                if isinstance(outcome, dict) and not self._closing:
                    self._late_tool_results.append((epoch, revision, name, outcome))
                    if len(self._late_tool_results) > 16:
                        self._late_tool_results.pop(0)
                        log.warning(
                            "Late native tool report queue full; oldest receipt stays in ledger"
                        )
                    self._notify_pause()

        work.add_done_callback(_late)
        return {
            "success": False,
            "pending": True,
            "error": (
                f"This tool is still running after {_TOOL_DEADLINE_S:.0f} seconds and will "
                "finish in the background. Tell the user in one short sentence that it is "
                "taking longer than usual; do not call it again in this turn and do not "
                "claim it is done."
            ),
        }

    async def handle_control(self, message: dict) -> None:
        if self._closing:
            return
        if message.get("type") == "text_input" and self._connection is not None:
            if self._tools is not None:
                self._tools.user_text = str(message.get("text", ""))
                self._tools.revision += 1
                if not self._recovering:
                    self._resume_needs_input = False
                    self._tools.accept_new_input()
                    self._reconnect_attempts = 0
            await self._connection.send_text(str(message.get("text", "")))
        else:
            await super().handle_control(message)

    async def deliver_announcement(
        self, text: str, *, report: str | None = None, **kwargs: Any
    ) -> bool:
        if not self.is_active:
            return False
        if str(report or "").strip():
            # The model reasons over the agent's full report before speaking
            # (``report_prompt``); refused mid-turn so the caller retries at
            # the next pause instead of talking over anyone.
            if not self.ready_for_report:
                return False
            from jarvis.realtime.report_prompt import report_update_prompt

            text = report_update_prompt(
                text,
                str(report),
                language=str(kwargs.get("language") or self._language),
                kind=str(kwargs.get("spoken_kind") or "completion"),
            )
            self._report_sent()
            try:
                await self._connection.send_text(text)
            except BaseException:
                self._cancel_report_timeout()
                self._report_state = ""
                raise
            return True
        await self._connection.send_text(text)
        return True

    async def attach_appshot(self, image: bytes, mime: str, note: str) -> bool:
        """Hand the appshot to the native model as a video frame, silently.

        A text turn would make the model answer right away; the frame alone is
        what it looks at when the user asks. Servers without image input
        decline, and the caller parks the appshot for the next message.
        """
        send_image = getattr(self._connection, "send_image", None)
        if not self.is_active or not callable(send_image):
            return False
        await send_image(image, mime)
        # Native transports have no silent text-input contract. The workspace
        # tool asks the model to select this scoped ID before it can hand off work.
        from jarvis.core.image_references import appshot_context

        appshot_context(self.session_id, image, mime, self._config)
        return True

    async def end(self, *, reason: str = "client_stop") -> None:
        if self._ended:
            return
        self._ended = True
        self._stop_watching_input_mute()
        self._clear_media_levels()
        self._closing = True
        self._late_tool_results.clear()
        await self._publish_phase("idle")
        self._hangup_reason = reason
        unregister(self.session_id)
        self._notify_ended()
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
