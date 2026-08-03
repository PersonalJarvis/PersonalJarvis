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
import ipaddress
import json
import logging
import secrets
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
_BROKER_AUTH_TIMEOUT_S = 3.0
_UNSAFE_FALLBACK_DETAIL = (
    "Realtime provider failed after this turn was accepted. The session was "
    "closed without replaying audio through the classic pipeline to avoid "
    "duplicate actions."
)
_USAGE_FALLBACK_DISABLED_DETAIL = (
    "Selected realtime access failed. Automatic usage-billed API fallback is "
    "disabled for this provider; configure an explicit realtime fallback or "
    "restore its subscription access."
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


def _socket_peer_is_loopback(ws: WebSocket) -> bool:
    """Trust only the ASGI socket peer for the local SDP broker boundary.

    Forwarded headers and Origin are deliberately ignored: either can be
    supplied by a remote authenticated browser. IPv4-mapped IPv6 loopback is
    accepted so desktop networking behaves consistently across operating
    systems and embedded WebView implementations.
    """
    client = ws.scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        return False
    host = str(client[0] or "").strip().strip("[]")
    if "%" in host:
        host = host.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:  # A non-IP host is a normal failed loopback capability check.
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool((mapped or address).is_loopback)


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


def _automatic_usage_fallback_allowed(cfg: Any) -> bool:
    """Read the selected provider's neutral billing-fallback capability."""
    if getattr(getattr(cfg, "voice", None), "mode", "pipeline") != "realtime":
        return True
    try:
        from jarvis.realtime.factory import (
            realtime_implicit_usage_fallback_allowed,
        )

        return realtime_implicit_usage_fallback_allowed(cfg)
    except Exception:  # noqa: BLE001 - configured realtime fails closed on ambiguity
        # Fail closed, but never silently (AP-30): refusing the metered
        # fallback ENDS calls, so the reason has to be findable in the log.
        log.warning(
            "Realtime billing-fallback capability could not be read; refusing "
            "automatic usage-billed fallback for this session",
            exc_info=True,
        )
        return False


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


async def _cancel_socket_task(task: asyncio.Task[Any]) -> None:
    """Cancel and reap one temporary WebSocket receive/wait task."""
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):  # Expected teardown.
        pass


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

    if not _automatic_usage_fallback_allowed(cfg):
        return None

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


@router.websocket("/ws/realtime-transport")
async def realtime_transport_ws(ws: WebSocket) -> None:
    """Broker a local desktop WebRTC offer for subscription voice.

    Audio remains on Jarvis's existing native or ``/ws/audio`` path. This
    socket carries only one-shot SDP offers and answers. Because an answered
    offer can receive native-call audio, this broker is additionally restricted
    to the actual loopback socket peer; authentication alone is insufficient.
    """
    await ws.accept()
    if not _socket_peer_is_loopback(ws):
        await ws.close(code=4403, reason="local desktop transport required")
        return
    if not _browser_voice_authorized(ws):
        await ws.close(code=4401, reason="unauthorized")
        return

    app = ws.scope.get("app")
    state = app.state if app is not None else None
    cfg = getattr(state, "config", None) or getattr(state, "cfg", None)
    if cfg is None:
        try:
            cfg = cfg_mod.load_config()
        except Exception:  # noqa: BLE001 - route still fails closed below
            log.warning("realtime transport config could not be loaded", exc_info=True)
            cfg = None
    if cfg is None or getattr(getattr(cfg, "voice", None), "mode", "pipeline") != (
        "realtime"
    ):
        await ws.close(code=1008, reason="realtime voice disabled")
        return

    from jarvis.realtime.offer_broker import (
        get_realtime_transport_offer_broker,
    )

    broker = get_realtime_transport_offer_broker()
    registration: Any = None
    queued_message: dict[str, Any] | None = None
    socket_closed = False
    try:
        desktop_capability = str(
            getattr(state, "realtime_transport_broker_token", "") or ""
        )
        if not desktop_capability:
            await ws.close(code=4401, reason="desktop capability unavailable")
            return
        try:
            auth_message = await asyncio.wait_for(
                ws.receive(), timeout=_BROKER_AUTH_TIMEOUT_S
            )
        except (TimeoutError, WebSocketDisconnect, RuntimeError):  # Reject failed capability proof.
            await ws.close(code=4401, reason="desktop capability required")
            return
        auth_text = auth_message.get("text")
        try:
            auth_payload = json.loads(auth_text) if auth_text is not None else None
        except (TypeError, ValueError):  # Malformed authentication is handled as absent.
            auth_payload = None
        supplied_capability = (
            str(auth_payload.get("desktop_capability", "") or "")
            if isinstance(auth_payload, dict)
            and auth_payload.get("type") == "authenticate"
            else ""
        )
        if not supplied_capability or not secrets.compare_digest(
            supplied_capability, desktop_capability
        ):
            await ws.close(code=4401, reason="invalid desktop capability")
            return
        state.realtime_transport_broker_error = ""
        log.info("Embedded desktop Realtime transport broker authenticated.")
        owner_id = f"broker-ws-{id(ws):x}"
        while not socket_closed:
            if queued_message is None:
                try:
                    message = await ws.receive()
                except (WebSocketDisconnect, RuntimeError):  # Socket teardown ends this receiver.
                    break
            else:
                message = queued_message
                queued_message = None
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            if text is None:
                continue
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                log.debug("realtime transport dropped malformed JSON")
                continue
            if isinstance(payload, dict) and payload.get("type") == "unavailable":
                state.realtime_transport_broker_error = (
                    "The embedded desktop cannot create the WebRTC offer required "
                    "for subscription Realtime voice."
                )
                log.warning(
                    "Embedded desktop cannot create the WebRTC offer required "
                    "for subscription Realtime voice."
                )
                await ws.close(code=1011, reason="WebRTC offer unavailable")
                socket_closed = True
                break
            if not isinstance(payload, dict) or payload.get("type") != "offer":
                if isinstance(payload, dict) and payload.get("type") == "authenticate":
                    await ws.close(code=4401, reason="desktop capability replay")
                    socket_closed = True
                continue

            offer_id = str(payload.get("offer_id", "") or "").strip()
            offer_sdp = str(payload.get("webrtc_offer_sdp", "") or "")
            try:
                registration = await broker.register(
                    offer_id, offer_sdp, owner_id=owner_id
                )
            except ValueError as exc:
                log.warning("realtime transport rejected offer: %s", exc)
                await ws.send_json({"type": "release", "offer_id": offer_id})
                continue
            state.realtime_transport_broker_error = ""
            log.info("Embedded desktop registered a Realtime WebRTC offer.")

            while registration is not None:
                result_task = asyncio.create_task(
                    registration.wait(), name=f"realtime-offer-{offer_id}"
                )
                receive_task = asyncio.create_task(
                    ws.receive(), name=f"realtime-offer-receive-{offer_id}"
                )
                done, _pending = await asyncio.wait(
                    {result_task, receive_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if result_task in done:
                    await _cancel_socket_task(receive_task)
                    result = result_task.result()
                    if result.type == "answer":
                        await ws.send_json(
                            {
                                "type": "answer",
                                "offer_id": result.offer_id,
                                "webrtc_answer_sdp": result.answer_sdp,
                            }
                        )
                        # Keep the registration alive until provider close so
                        # the UI knows when to recycle its PeerConnection and
                        # publish a fresh offer for the next native call.
                        continue
                    await ws.send_json(
                        {"type": "release", "offer_id": result.offer_id}
                    )
                    registration = None
                    break

                await _cancel_socket_task(result_task)
                await registration.cancel()
                registration = None
                try:
                    next_message = receive_task.result()
                except (WebSocketDisconnect, RuntimeError):  # Socket teardown ends this offer.
                    socket_closed = True
                    break
                if next_message.get("type") == "websocket.disconnect":
                    socket_closed = True
                    break
                # A refreshed offer can replace an abandoned one on the same
                # socket. Process that already-read frame in the outer loop.
                queued_message = next_message
                break
    finally:
        if registration is not None:
            await registration.cancel()


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
        except Exception:  # noqa: BLE001 - Config failure degrades to unavailable voice.
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
        reason = (
            "realtime access unavailable"
            if not _automatic_usage_fallback_allowed(cfg)
            else "speech stack unavailable"
        )
        await ws.close(code=1011, reason=reason)
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
        if not bool(getattr(session, "allow_classic_fallback", True)):
            log.warning(
                "browser_voice: selected realtime provider failed; automatic "
                "usage-billed fallback is disabled: %s",
                reason,
            )
            await session.end(reason=HANGUP_ERROR)
            try:
                await _send_json(
                    {
                        "type": "provider_error",
                        "error": _USAGE_FALLBACK_DISABLED_DETAIL,
                    }
                )
            except Exception:  # noqa: BLE001 - surface may already be gone
                log.debug(
                    "browser_voice: subscription failure status could not be sent",
                    exc_info=True,
                )
            await ws.close(code=1011, reason="automatic API fallback disabled")
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
            except WebSocketDisconnect:  # A peer disconnect normally terminates the receive loop.
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
                        raw_offer = control.get("webrtc_offer_sdp")
                        if raw_offer is not None:
                            try:
                                from jarvis.realtime.offer_broker import (
                                    validate_webrtc_offer_sdp,
                                )

                                control["webrtc_offer_sdp"] = (
                                    validate_webrtc_offer_sdp(raw_offer)
                                )
                            except ValueError as exc:
                                log.warning(
                                    "browser_voice: rejected invalid WebRTC offer: %s",
                                    exc,
                                )
                                await ws.close(code=1008, reason="invalid WebRTC offer")
                                break
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
            except asyncio.QueueFull:  # Cancellation already makes the terminal sentinel redundant.
                audio_sender_task.cancel()
            try:
                await asyncio.wait_for(
                    audio_sender_task, timeout=_TRANSPORT_SEND_TIMEOUT_S
                )
            except (TimeoutError, asyncio.CancelledError):
                # Bounded teardown cancels a stuck sender.
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
