"""FastAPI ``/ws/audio`` route for the browser-microphone voice bridge (B2 slice 2).

A per-connection WebSocket that receives raw int16 PCM (binary frames) + JSON
control frames from the browser and runs a :class:`BrowserVoiceSession`
(STT -> Brain -> TTS, no sounddevice). Mirrors the telephony ``/media`` route's
provider resolution (shared STT/TTS + a per-connection brain, with a test-factory
seam) and the ``/ws`` AP-20 receive-loop discipline: a ``RuntimeError`` on an
unclean disconnect is terminal — ``break``, never ``continue``.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from jarvis.core import config as cfg_mod

log = logging.getLogger("jarvis.browser_voice.route")

router = APIRouter()

# BCP-47 from the brain.reply_language pin (de/en/es) — falls back to de-DE.
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
    if not pin or pin == "auto":
        return "de-DE"
    return _LANG_MAP.get(pin, pin)


def _build_browser_session(
    *, state: Any, cfg: Any, bus: Any, session_id: str, send_binary: Any, send_json: Any
) -> Any:
    """Build a BrowserVoiceSession with shared STT/TTS + a per-connection brain.

    Returns ``None`` when the speech stack can't be constructed (e.g. no provider
    key) — the caller then closes the socket cleanly. A test can inject
    ``state.browser_voice_session_factory`` to bypass the real provider build.
    """
    # Realtime mode branch (default OFF): only when [voice].mode == "realtime"
    # AND an OpenAI key exists. Otherwise fall through to the classic bridge.
    # The realtime engine is an optional, still-internal module stripped from
    # public distribution snapshots (distribution-denylist), so its absence just
    # means "no realtime" — fall through instead of crashing the voice session.
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
        )
        if rt is not None:
            return rt

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

    async def _send_binary(data: bytes) -> None:
        await ws.send_bytes(data)

    async def _send_json(msg: dict[str, Any]) -> None:
        await ws.send_json(msg)

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
                await session.handle_audio_frame(data)
                continue
            text = msg.get("text")
            if text is not None:
                try:
                    control = json.loads(text)
                except Exception:  # noqa: BLE001 — malformed control frame, drop it
                    log.debug("browser_voice: dropping malformed control frame")
                    continue
                if isinstance(control, dict):
                    await session.handle_control(control)
    finally:
        await session.end(reason="ws_closed")
