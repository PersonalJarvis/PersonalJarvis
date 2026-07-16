"""FastAPI ``/ws/audio`` route for the browser-microphone voice bridge (B2 slice 2).

A per-connection WebSocket that receives raw int16 PCM (binary frames) + JSON
control frames from the browser and runs a :class:`BrowserVoiceSession`
(STT -> Brain -> TTS, no sounddevice). Mirrors the telephony ``/media`` route's
provider resolution (shared STT/TTS + a per-connection brain, with a test-factory
seam) and the ``/ws`` AP-20 receive-loop discipline: a ``RuntimeError`` on an
unclean disconnect is terminal — ``break``, never ``continue``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from jarvis.core import config as cfg_mod
from jarvis.core.turn_language import DEFAULT_LOCALE, resolve_output_language
from jarvis.sessions.constants import (
    HANGUP_ERROR,
    HANGUP_REALTIME_FALLBACK,
    HANGUP_WS_CLOSED,
)
from jarvis.ui.web.surface_security import credentials_valid

log = logging.getLogger("jarvis.browser_voice.route")

router = APIRouter()

_AUDIO_QUEUE_MAX_FRAMES = 32
_TRANSPORT_SEND_TIMEOUT_S = 2.0
_AUDIO_DRAIN_TIMEOUT_S = 2.0
_UNSAFE_FALLBACK_DETAIL = (
    "Realtime provider failed after this turn was accepted. The session was "
    "closed without replaying audio through the classic pipeline to avoid "
    "duplicate actions."
)

# BCP-47 from the canonical per-turn resolver (de/en/es).
_LANG_MAP = {"de": "de-DE", "en": "en-US", "es": "es-ES"}


def _browser_voice_enabled(cfg: Any) -> bool:
    """Default OFF. The socket is only served when the user has explicitly
    enabled a browser voice surface: realtime mode ([voice].mode == "realtime")
    or the classic browser bridge ([browser_voice].enabled == true).
    """
    if getattr(getattr(cfg, "voice", None), "mode", "pipeline") == "realtime":
        return True
    bv = getattr(cfg, "browser_voice", None)
    return bool(getattr(bv, "enabled", False)) if bv is not None else False


def _resolve_language(cfg: Any) -> str:
    pin = getattr(getattr(cfg, "brain", None), "reply_language", "") or ""
    language = resolve_output_language(pin, "", "", default=DEFAULT_LOCALE)
    return _LANG_MAP.get(language, _LANG_MAP[DEFAULT_LOCALE])


def _browser_voice_authorized(ws: WebSocket) -> bool:
    """Apply the shared cookie/Bearer policy as route-level defense in depth.

    A peer address is not an authentication boundary: a hostile webpage can
    connect directly to a localhost WebSocket and still appear loopback to the
    server. Every client therefore needs a registered token before a
    tool-capable voice session is constructed.
    """
    return credentials_valid(ws.scope)


def _json_commits_semantic_turn(message: dict[str, Any]) -> bool:
    """Whether an outbound status proves Realtime has accepted the turn.

    This mirrors the desktop Realtime adapter. A final user transcript may have
    already triggered a tool, while any assistant transcript or completed
    browser-speech request makes replaying captured audio unsafe.
    """
    kind = str(message.get("type", "") or "")
    if kind == "transcript":
        role = str(message.get("role", "") or "")
        return role == "assistant" or (
            role == "user" and bool(message.get("is_final", False))
        )
    return kind in {
        "thinking",
        "turn_complete",
        "hangup",
        "error_spoken",
        "tts_browser_fallback",
        "tool_result",
        "action_result",
    }


def _enqueue_audio_frame(queue: asyncio.Queue[bytes | None], data: bytes) -> bool:
    """Queue one mic frame without blocking; drop the oldest on overflow."""
    dropped = False
    if queue.full():
        try:
            queue.get_nowait()
            queue.task_done()
            dropped = True
        except asyncio.QueueEmpty:  # pragma: no cover - another task drained it
            pass
    queue.put_nowait(bytes(data))
    return dropped


def _build_browser_session(
    *, state: Any, cfg: Any, bus: Any, session_id: str, send_binary: Any, send_json: Any
) -> Any:
    """Build a BrowserVoiceSession with shared STT/TTS + a per-connection brain.

    Returns ``None`` when the speech stack can't be constructed (e.g. no provider
    key) — the caller then closes the socket cleanly. A test can inject
    ``state.browser_voice_session_factory`` to bypass the real provider build.
    """
    # Default-off Realtime branch. The registry-backed factory selects every
    # credential-ready duplex family in configured order; no provider name is
    # load-bearing. An installation without that optional module or without a
    # usable duplex credential falls through to the classic browser bridge.
    try:
        from jarvis.realtime.factory import build_realtime_session
    except ImportError:
        build_realtime_session = None  # type: ignore[assignment]

    if build_realtime_session is not None:
        rt = build_realtime_session(
            cfg=cfg,
            bus=bus,
            session_id=session_id,
            send_binary=send_binary,
            send_json=send_json,
            surface="browser",
            brain=getattr(state, "brain", None),
        )
        if rt is not None:
            return rt

    return _build_classic_browser_session(
        state=state,
        cfg=cfg,
        bus=bus,
        session_id=session_id,
        send_binary=send_binary,
        send_json=send_json,
    )


def _build_classic_browser_session(
    *, state: Any, cfg: Any, bus: Any, session_id: str, send_binary: Any, send_json: Any
) -> Any:
    """Build the key-aware STT -> brain -> TTS browser fallback lazily."""
    factory = getattr(state, "browser_voice_session_factory", None)
    if factory is not None:
        return factory(session_id=session_id, send_binary=send_binary, send_json=send_json)
    try:
        from jarvis.brain.factory import build_default_brain
        from jarvis.browser_voice.session import BrowserVoiceSession
        from jarvis.plugins.stt import build_stt_from_config
        from jarvis.plugins.tts import build_tts_from_config

        stt = build_stt_from_config(cfg.stt)
        tts = build_tts_from_config(cfg.tts)
        brain = build_default_brain(bus=bus, tier="router")
    except Exception as exc:  # noqa: BLE001 — missing key / unbuildable stack
        log.warning("browser_voice: speech stack build failed: %s", exc)
        return None
    return BrowserVoiceSession(
        session_id=session_id,
        send_binary=send_binary,
        send_json=send_json,
        stt=stt,
        brain=brain,
        tts=tts,
        language_code=_resolve_language(cfg),
        bus=bus,
        config=cfg,
    )


@router.websocket("/ws/audio")
async def browser_voice_ws(ws: WebSocket) -> None:
    """Browser-microphone voice socket: run the per-connection turn loop."""
    await ws.accept()

    if not _browser_voice_authorized(ws):
        await ws.close(code=4401, reason="unauthorized")
        return

    app = ws.scope.get("app")
    state = app.state if app is not None else None
    bus = getattr(state, "bus", None)
    cfg = getattr(state, "config", None) or getattr(state, "cfg", None)
    if cfg is None:
        try:
            cfg = cfg_mod.load_config()
        except Exception:  # noqa: BLE001
            cfg = None

    if cfg is not None and not _browser_voice_enabled(cfg):
        await ws.close(code=1008, reason="browser voice disabled")
        return

    session_id = str(uuid4())
    semantic_turn_committed = False

    async def _send_binary(data: bytes) -> None:
        nonlocal semantic_turn_committed
        if data:
            semantic_turn_committed = True
        await asyncio.wait_for(
            ws.send_bytes(data), timeout=_TRANSPORT_SEND_TIMEOUT_S
        )

    async def _send_json(msg: dict[str, Any]) -> None:
        nonlocal semantic_turn_committed
        if _json_commits_semantic_turn(msg):
            semantic_turn_committed = True
        await asyncio.wait_for(
            ws.send_json(msg), timeout=_TRANSPORT_SEND_TIMEOUT_S
        )

    session = _build_browser_session(
        state=state,
        cfg=cfg,
        bus=bus,
        session_id=session_id,
        send_binary=_send_binary,
        send_json=_send_json,
    )
    if session is None:
        await ws.close(code=1011, reason="speech stack unavailable")
        return

    audio_start_control: dict[str, Any] | None = None
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(
        maxsize=_AUDIO_QUEUE_MAX_FRAMES
    )
    dropped_audio_frames = 0
    audio_sender_failed = asyncio.Event()

    async def _switch_to_classic(reason: str) -> bool:
        nonlocal session
        if not bool(getattr(session, "is_realtime", False)):
            return False
        if semantic_turn_committed:
            log.warning(
                "browser_voice: realtime failed after a committed turn; "
                "refusing unsafe classic replay: %s",
                reason,
            )
            await session.end(reason=HANGUP_ERROR)
            try:
                await _send_json(
                    {"type": "provider_error", "error": _UNSAFE_FALLBACK_DETAIL}
                )
            except Exception:  # noqa: BLE001 -- the provider may have torn down the wire
                log.debug(
                    "browser_voice: committed-turn failure status could not be sent",
                    exc_info=True,
                )
            await ws.close(
                code=1011,
                reason="realtime failed after committed turn",
            )
            return False
        log.warning(
            "browser_voice: realtime session unavailable; using classic pipeline: %s",
            reason,
        )
        await session.end(reason=HANGUP_REALTIME_FALLBACK)
        fallback = _build_classic_browser_session(
            state=state,
            cfg=cfg,
            bus=bus,
            session_id=session_id,
            send_binary=_send_binary,
            send_json=_send_json,
        )
        if fallback is None:
            await ws.close(code=1011, reason="speech stack unavailable")
            return False
        session = fallback
        await _send_json({"type": "mode_fallback", "mode": "pipeline"})
        if audio_start_control is not None:
            try:
                await session.handle_control(audio_start_control)
            except Exception as exc:  # noqa: BLE001 — fallback is terminal
                log.warning("browser_voice: classic fallback failed: %s", exc)
                return False
        return True

    async def _send_audio_frame(data: bytes) -> None:
        nonlocal session
        if bool(getattr(session, "failed", False)):
            detail = str(getattr(session, "failure_detail", "") or "stream ended")
            if not await _switch_to_classic(detail):
                raise RuntimeError(detail)
        try:
            await asyncio.wait_for(
                session.handle_audio_frame(data),
                timeout=_TRANSPORT_SEND_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - cross to classic once
            if not await _switch_to_classic(str(exc)):
                raise
            await asyncio.wait_for(
                session.handle_audio_frame(data),
                timeout=_TRANSPORT_SEND_TIMEOUT_S,
            )

    async def _audio_sender() -> None:
        while True:
            data = await audio_queue.get()
            try:
                if data is None:
                    return
                await _send_audio_frame(data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - terminal sender failure
                log.warning("browser_voice: audio sender failed: %s", exc)
                audio_sender_failed.set()
                return
            finally:
                audio_queue.task_done()

    audio_sender_task = asyncio.create_task(
        _audio_sender(), name=f"browser-audio-sender-{session_id}"
    )

    try:
        while True:
            try:
                msg = await ws.receive()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                # AP-20: an unclean disconnect raises RuntimeError (not
                # WebSocketDisconnect) — terminal, break (never continue).
                break
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is not None:
                if audio_sender_failed.is_set():
                    break
                if _enqueue_audio_frame(audio_queue, data):
                    dropped_audio_frames += 1
                continue
            text = msg.get("text")
            if text is not None:
                try:
                    control = json.loads(text)
                except Exception:  # noqa: BLE001 — malformed control frame, drop it
                    log.debug("browser_voice: dropping malformed control frame")
                    continue
                if isinstance(control, dict):
                    if control.get("type") == "audio_start":
                        audio_start_control = dict(control)
                    try:
                        await session.handle_control(control)
                    except Exception as exc:  # noqa: BLE001 — AP-20: terminal or fallback
                        can_fallback = (
                            control.get("type") == "audio_start"
                            and bool(getattr(session, "is_realtime", False))
                        )
                        if not can_fallback:
                            log.warning("browser_voice: control handling failed: %s", exc)
                            break
                        if not await _switch_to_classic(str(exc)):
                            break
    finally:
        try:
            await asyncio.wait_for(
                audio_queue.join(), timeout=_AUDIO_DRAIN_TIMEOUT_S
            )
        except TimeoutError:
            log.warning(
                "browser_voice: audio drain timed out; dropping %d queued frames",
                audio_queue.qsize(),
            )
        if not audio_sender_task.done():
            try:
                audio_queue.put_nowait(None)
            except asyncio.QueueFull:
                audio_sender_task.cancel()
            try:
                await asyncio.wait_for(
                    audio_sender_task, timeout=_TRANSPORT_SEND_TIMEOUT_S
                )
            except (TimeoutError, asyncio.CancelledError):
                audio_sender_task.cancel()
        if dropped_audio_frames:
            log.warning(
                "browser_voice: dropped %d stale mic frames under backpressure",
                dropped_audio_frames,
            )
        # A voice hang-up ends the session with its own reason; only a plain
        # socket teardown reports ws_closed.
        end_reason = (
            str(getattr(session, "hangup_reason", "") or "") or HANGUP_WS_CLOSED
        )
        await session.end(reason=end_reason)
