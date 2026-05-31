"""FastAPI routes for the Twilio telephony voice agent (contract section 4).

Base path: ``/api/telephony``.

Twilio-facing (not consumed by the UI):
  * ``POST /api/telephony/voice`` — Voice webhook; validates signature; returns
    TwiML ``<Connect><Stream>`` pointing at the media socket.
  * ``WS  /api/telephony/media``  — Media Streams socket; runs the per-call
    STT -> Brain -> TTS turn loop.

UI-facing JSON:
  * ``GET  /api/telephony/status``
  * ``GET  /api/telephony/config``
  * ``POST /api/telephony/config``
  * ``POST /api/telephony/credentials``
  * ``POST /api/telephony/test``
  * ``POST /api/telephony/selftest``
  * ``GET  /api/telephony/calls``
  * ``GET  /api/telephony/scripts``

Graceful degradation (AD-T8): when the ``twilio`` SDK is not importable the
endpoints return ``200`` with ``available=false`` flags rather than crashing.
Mutating endpoints return ``409`` with ``{"error": ...}`` when prerequisites
are missing.
"""

from __future__ import annotations

import logging
import re
import time
from typing import cast

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jarvis.core import config as cfg_mod
from jarvis.core.config import get_secret, set_secret
from jarvis.telephony import is_available
from jarvis.telephony.constants import (
    CALL_COMPLETED,
    CALL_FAILED,
    CALL_IN_PROGRESS,
    CallStatusLiteral,
)
from jarvis.telephony.security import (
    constant_time_equals,
    generate_call_secret,
    public_url_for,
    public_wss_url,
    validate_twilio_signature,
)
from jarvis.telephony.status import CallRecord, TelephonyManager
from jarvis.telephony.twiml import build_connect_stream_twiml, build_reject_twiml

log = logging.getLogger("jarvis.telephony.routes")

router = APIRouter(prefix="/api/telephony", tags=["telephony"])

_AUTH_TOKEN_KEY = "twilio_auth_token"  # noqa: S105 - credential-manager key name, not a value
_AUTH_TOKEN_ENV = "TWILIO_AUTH_TOKEN"  # noqa: S105 - ENV var name, not a value

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_URL_RE = re.compile(r"^https?://[^\s/]+(?:/.*)?$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_cfg(request: Request):
    cfg_attr = getattr(request.app.state, "config", None) or getattr(request.app.state, "cfg", None)
    if cfg_attr is not None:
        return cfg_attr
    try:
        return cfg_mod.load_config()
    except Exception:  # noqa: BLE001
        return None


def _twilio_cfg(request: Request):
    cfg = _resolve_cfg(request)
    if cfg is None:
        from jarvis.core.config import TwilioConfig

        return TwilioConfig()
    integrations = getattr(cfg, "integrations", None)
    twilio = getattr(integrations, "twilio", None)
    if twilio is None:
        from jarvis.core.config import TwilioConfig

        return TwilioConfig()
    return twilio


def _manager(request: Request) -> TelephonyManager:
    mgr = getattr(request.app.state, "telephony_manager", None)
    if mgr is None:
        mgr = TelephonyManager()
        request.app.state.telephony_manager = mgr
    return mgr


def _auth_token() -> str | None:
    return get_secret(_AUTH_TOKEN_KEY, _AUTH_TOKEN_ENV)


def _mask_sid(sid: str) -> str:
    if not sid:
        return ""
    if len(sid) <= 6:
        return "AC" + "•" * 4
    return sid[:2] + "•" * 6 + sid[-4:]


def _tts_info(request: Request) -> tuple[str, str]:
    cfg = _resolve_cfg(request)
    tts = getattr(cfg, "tts", None)
    provider = getattr(tts, "provider", "gemini-flash-tts") or "gemini-flash-tts"
    voice = getattr(tts, "voice_de", "Charon") or "Charon"
    return provider, voice


def _config_payload(twilio, *, auth_token_set: bool) -> dict[str, object]:
    return {
        "enabled": bool(getattr(twilio, "enabled", False)),
        "account_sid": getattr(twilio, "account_sid", "") or "",
        "phone_number": getattr(twilio, "phone_number", "") or "",
        "public_base_url": getattr(twilio, "public_base_url", "") or "",
        "greeting": getattr(twilio, "greeting", "") or "",
        "language_code": getattr(twilio, "language_code", "de-DE") or "de-DE",
        "fallback_mode": getattr(twilio, "fallback_mode", "media") or "media",
        "max_call_seconds": int(getattr(twilio, "max_call_seconds", 600) or 600),
        "auth_token_set": auth_token_set,
    }


# ---------------------------------------------------------------------------
# UI-facing: status / config / credentials
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_status(request: Request) -> JSONResponse:
    twilio = _twilio_cfg(request)
    mgr = _manager(request)
    token = _auth_token()
    available = is_available()
    account_sid = getattr(twilio, "account_sid", "") or ""
    phone_number = getattr(twilio, "phone_number", "") or ""
    public_base_url = getattr(twilio, "public_base_url", "") or ""
    configured = bool(account_sid and phone_number and token)
    tts_provider, tts_voice = _tts_info(request)
    webhook_url = public_url_for(public_base_url, "/api/telephony/voice") if public_base_url else ""
    return JSONResponse(
        {
            "available": available,
            "configured": configured,
            "enabled": bool(getattr(twilio, "enabled", False)),
            "account_sid_masked": _mask_sid(account_sid),
            "phone_number": phone_number,
            "public_base_url": public_base_url,
            "webhook_url": webhook_url,
            "auth_token_set": bool(token),
            "twilio_reachable": mgr.reachable,
            "twilio_error": mgr.reachable_error,
            "tts_provider": tts_provider,
            "tts_voice": tts_voice,
            "active_calls": mgr.active_calls,
            "max_call_seconds": int(getattr(twilio, "max_call_seconds", 600) or 600),
        }
    )


@router.get("/config")
async def get_config(request: Request) -> JSONResponse:
    twilio = _twilio_cfg(request)
    return JSONResponse(_config_payload(twilio, auth_token_set=bool(_auth_token())))


class ConfigUpdate(BaseModel):
    enabled: bool | None = None
    phone_number: str | None = None
    public_base_url: str | None = None
    greeting: str | None = None
    language_code: str | None = None
    max_call_seconds: int | None = Field(default=None, ge=10, le=7200)


@router.post("/config")
async def post_config(request: Request, body: ConfigUpdate) -> JSONResponse:
    updates: dict[str, object] = {}
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.phone_number is not None:
        pn = body.phone_number.strip()
        if pn and not _E164_RE.match(pn):
            return JSONResponse(
                {"error": "phone_number must be E.164, e.g. +49301234567"},
                status_code=422,
            )
        updates["phone_number"] = pn
    if body.public_base_url is not None:
        url = body.public_base_url.strip().rstrip("/")
        if url and not _URL_RE.match(url):
            return JSONResponse(
                {"error": "public_base_url must be an http(s) URL"},
                status_code=422,
            )
        updates["public_base_url"] = url
    if body.greeting is not None:
        updates["greeting"] = body.greeting.strip()
    if body.language_code is not None:
        updates["language_code"] = body.language_code.strip() or "de-DE"
    if body.max_call_seconds is not None:
        updates["max_call_seconds"] = int(body.max_call_seconds)

    if updates:
        try:
            from jarvis.core.config_writer import set_telephony_config

            set_telephony_config(updates)
        except FileNotFoundError:
            return JSONResponse(
                {"error": "jarvis.toml not found; cannot persist config"},
                status_code=409,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("telephony config write failed: %s", exc)
            return JSONResponse({"error": f"config write failed: {exc}"}, status_code=409)

    # Re-read so the response reflects the freshly persisted values.
    try:
        fresh = cfg_mod.load_config()
        twilio = fresh.integrations.twilio
    except Exception:  # noqa: BLE001
        twilio = _twilio_cfg(request)
    return JSONResponse(_config_payload(twilio, auth_token_set=bool(_auth_token())))


class CredentialsUpdate(BaseModel):
    account_sid: str | None = None
    auth_token: str | None = None


@router.post("/credentials")
async def post_credentials(request: Request, body: CredentialsUpdate) -> JSONResponse:
    if body.auth_token is not None and body.auth_token.strip():
        ok = set_secret(_AUTH_TOKEN_KEY, body.auth_token.strip())
        if not ok:
            return JSONResponse(
                {"error": "could not store auth token in the credential manager"},
                status_code=409,
            )
    if body.account_sid is not None and body.account_sid.strip():
        sid = body.account_sid.strip()
        if not sid.startswith("AC"):
            return JSONResponse({"error": "account_sid must start with 'AC'"}, status_code=422)
        try:
            from jarvis.core.config_writer import set_telephony_config

            set_telephony_config({"account_sid": sid})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"config write failed: {exc}"}, status_code=409)

    twilio = _twilio_cfg(request)
    try:
        fresh = cfg_mod.load_config()
        sid_now = fresh.integrations.twilio.account_sid
        phone_now = fresh.integrations.twilio.phone_number
    except Exception:  # noqa: BLE001
        sid_now = getattr(twilio, "account_sid", "")
        phone_now = getattr(twilio, "phone_number", "")
    configured = bool(sid_now and phone_now and _auth_token())
    return JSONResponse({"ok": True, "configured": configured})


@router.post("/test")
async def post_test(request: Request) -> JSONResponse:
    if not is_available():
        return JSONResponse(
            {
                "ok": False,
                "reachable": False,
                "error": "twilio package not installed (pip install -e .[telephony])",
            }
        )
    twilio = _twilio_cfg(request)
    account_sid = getattr(twilio, "account_sid", "") or ""
    token = _auth_token()
    mgr = _manager(request)
    if not account_sid or not token:
        mgr.set_reachable(False, "account_sid or auth_token missing")
        return JSONResponse(
            {"ok": False, "reachable": False, "error": "account_sid or auth_token missing"}
        )
    try:
        from jarvis.telephony.provisioning import verify_credentials

        info = verify_credentials(account_sid, token)
        mgr.set_reachable(True, None)
        return JSONResponse(
            {
                "ok": True,
                "reachable": True,
                "account_status": info.get("account_status", ""),
            }
        )
    except Exception as exc:  # noqa: BLE001
        mgr.set_reachable(False, str(exc))
        return JSONResponse({"ok": False, "reachable": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# UI-facing: selftest (no real call)
# ---------------------------------------------------------------------------


@router.post("/selftest")
async def post_selftest(request: Request) -> JSONResponse:
    """Run a fixed utterance through STT->Brain->TTS shape (no PSTN call).

    Proves the chain and that the brain answer is NOT truncated. To stay
    corpus-free and key-free, the canned transcript is fed straight to the
    configured brain; if the real brain/TTS cannot be built (no key in this
    env), we fall back to a deterministic echo so the button still proves the
    transcode + framing path. STT-on-a-WAV-fixture is a separate slow test.
    """
    transcript = "Hallo Jarvis, funktioniert das Telefon?"
    response_text = ""
    audio_bytes = 0
    error: str | None = None

    cfg = _resolve_cfg(request)
    language_code = getattr(getattr(cfg, "tts", None), "language_code", "de-DE") or "de-DE"

    # Brain
    try:
        brain = getattr(request.app.state, "brain", None)
        if brain is None:
            from jarvis.brain.factory import build_default_brain

            bus = getattr(request.app.state, "bus", None)
            brain = build_default_brain(bus=bus, tier="router")
        chunks: list[str] = []
        async for chunk in brain.generate_stream(transcript):
            if chunk:
                chunks.append(chunk)
        response_text = "".join(chunks).strip()
    except Exception as exc:  # noqa: BLE001
        error = f"brain: {exc}"

    if not response_text:
        response_text = "Ja, das Telefon funktioniert. Ich höre dich klar und deutlich."

    # Scrub + TTS -> count synthesized bytes after transcode to Twilio mu-law.
    try:
        from jarvis.brain.output_filter import scrub_for_voice
        from jarvis.plugins.tts import build_tts_from_config
        from jarvis.telephony.audio import tts_pcm_to_twilio_ulaw

        spoken = scrub_for_voice(
            response_text, language="en" if language_code.lower().startswith("en") else "de"
        ).cleaned
        tts = build_tts_from_config(cfg.tts) if cfg is not None else None
        if tts is not None:
            async for chunk in tts.synthesize(spoken, language_code=language_code):
                pcm = getattr(chunk, "pcm", b"")
                rate = getattr(chunk, "sample_rate", 24_000)
                if pcm:
                    audio_bytes += len(tts_pcm_to_twilio_ulaw(pcm, source_rate=rate))
    except Exception as exc:  # noqa: BLE001
        if error is None:
            error = f"tts: {exc}"

    return JSONResponse(
        {
            "ok": error is None and bool(response_text),
            "transcript": transcript,
            "response_text": response_text,
            "audio_bytes": audio_bytes,
            "error": error,
        }
    )


# ---------------------------------------------------------------------------
# UI-facing: calls + scripts
# ---------------------------------------------------------------------------


@router.get("/calls")
async def get_calls(request: Request, limit: int = 20) -> JSONResponse:
    mgr = _manager(request)
    limit = max(1, min(limit, 200))
    return JSONResponse({"calls": mgr.recent_calls(limit)})


@router.get("/scripts")
async def get_scripts(request: Request) -> JSONResponse:
    twilio = _twilio_cfg(request)
    port = 8765
    base = getattr(twilio, "public_base_url", "") or "https://jarvis.example.com"
    webhook = public_url_for(base, "/api/telephony/voice")
    scripts = [
        {
            "name": "Public tunnel (dev)",
            "path": "scripts/telephony-tunnel.ps1",
            "description": (
                "Start a cloudflared tunnel to the local FastAPI port and print "
                "the public HTTPS URL to paste into 'public_base_url'. Home-PC "
                "development path."
            ),
            "command": f"pwsh scripts/telephony-tunnel.ps1 -Port {port}",
        },
        {
            "name": "Provision number",
            "path": "scripts/telephony_provision.py",
            "description": (
                "List buyable numbers, buy one, or point an owned number's voice webhook at Jarvis."
            ),
            "command": (
                "python scripts/telephony_provision.py set-webhook "
                f'--number "{getattr(twilio, "phone_number", "") or "+49..."}" '
                f'--url "{webhook}"'
            ),
        },
        {
            "name": "E2E probe",
            "path": "scripts/probe_telephony_e2e.py",
            "description": (
                "Drive the media WS handler with a synthetic Twilio call and print "
                "transcript + response + outbound-frame count. No real account needed."
            ),
            "command": "python scripts/probe_telephony_e2e.py",
        },
        {
            "name": "Caddy reverse proxy (VPS, recommended)",
            "path": "docs/telephony.md",
            "description": (
                "Caddyfile snippet that terminates TLS (Let's Encrypt) and proxies "
                "Twilio to the local FastAPI port. The cloud-first default path."
            ),
            # Real newlines so the UI (whitespace-pre-wrap) renders a proper
            # multi-line Caddyfile block and the copy button yields valid config.
            "command": (f"jarvis.example.com {{\n    reverse_proxy localhost:{port}\n}}"),
        },
    ]
    return JSONResponse({"scripts": scripts})


# ---------------------------------------------------------------------------
# Twilio-facing: voice webhook
# ---------------------------------------------------------------------------


@router.post("/voice")
async def post_voice(request: Request) -> Response:
    """Twilio Voice webhook: validate signature, return <Connect><Stream> TwiML."""
    twilio = _twilio_cfg(request)
    mgr = _manager(request)

    if not getattr(twilio, "enabled", False):
        return Response(
            content=build_reject_twiml("Telephony is currently disabled."),
            media_type="text/xml",
        )

    public_base_url = getattr(twilio, "public_base_url", "") or ""
    if not public_base_url:
        return Response(
            content=build_reject_twiml("Telephony is not configured."),
            media_type="text/xml",
        )

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    # Signature validation against the PUBLIC url (AD-T9). Skipped only when an
    # explicit test override flag is set on app.state (integration tests).
    skip_check = bool(getattr(request.app.state, "telephony_skip_signature", False))
    if not skip_check:
        signature = request.headers.get("X-Twilio-Signature", "")
        public_voice_url = public_url_for(public_base_url, "/api/telephony/voice")
        valid = validate_twilio_signature(
            auth_token=_auth_token(),
            signature=signature,
            url=public_voice_url,
            params=params,
        )
        if not valid:
            log.warning("telephony: rejected /voice with invalid signature")
            return Response(content=build_reject_twiml(), media_type="text/xml", status_code=403)

    call_sid = params.get("CallSid", "")
    from_number = params.get("From", "")
    to_number = params.get("To", "")
    secret = generate_call_secret()
    mgr.register_pending(call_sid, secret, from_number=from_number, to_number=to_number)

    wss_url = public_wss_url(public_base_url, "/api/telephony/media")
    twiml = build_connect_stream_twiml(
        wss_url=wss_url,
        secret=secret,
        call_sid=call_sid,
        language_code=getattr(twilio, "language_code", "de-DE") or "de-DE",
    )
    return Response(content=twiml, media_type="text/xml")


# ---------------------------------------------------------------------------
# Twilio-facing: media WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/media")
async def media_socket(ws: WebSocket) -> None:
    """Twilio Media Streams socket: run the per-call turn loop."""
    await ws.accept()

    app = ws.scope.get("app")
    state = app.state if app is not None else None
    mgr: TelephonyManager | None = getattr(state, "telephony_manager", None)
    if mgr is None:
        mgr = TelephonyManager()
        if state is not None:
            state.telephony_manager = mgr

    bus = getattr(state, "bus", None)
    cfg = getattr(state, "config", None) or getattr(state, "cfg", None)
    if cfg is None:
        try:
            cfg = cfg_mod.load_config()
        except Exception:  # noqa: BLE001
            cfg = None
    twilio = getattr(getattr(cfg, "integrations", None), "twilio", None)

    session = None
    started_at = time.time()
    call_sid = ""
    record_status = CALL_IN_PROGRESS

    async def _send(msg: dict[str, object]) -> None:
        await ws.send_json(msg)

    try:
        while True:
            try:
                data = await ws.receive_json()
            except WebSocketDisconnect:
                break
            event = data.get("event")

            if event == "connected":
                continue

            if event == "start":
                start = data.get("start", {})
                stream_sid = start.get("streamSid", "") or data.get("streamSid", "")
                custom = start.get("customParameters", {}) or {}
                call_sid = custom.get("call_sid", "") or start.get("callSid", "")
                secret = custom.get("secret", "")
                language = custom.get("language", "") or (
                    getattr(twilio, "language_code", "de-DE") if twilio else "de-DE"
                )

                # Validate the per-call WS secret unless skipped for tests.
                skip = bool(getattr(state, "telephony_skip_signature", False))
                if not skip:
                    pending = mgr.consume_pending(call_sid, secret)
                    if pending is None or not constant_time_equals(
                        pending.secret if pending else "", secret
                    ):
                        log.warning("telephony: media socket secret mismatch for %s", call_sid)
                        record_status = CALL_FAILED
                        await ws.close(code=1008, reason="bad secret")
                        break
                    from_number = pending.from_number
                    to_number = pending.to_number
                else:
                    pending = mgr.peek_pending(call_sid)
                    from_number = pending.from_number if pending else custom.get("from", "")
                    to_number = pending.to_number if pending else custom.get("to", "")

                session = _build_session(
                    state=state,
                    cfg=cfg,
                    twilio=twilio,
                    bus=bus,
                    call_sid=call_sid,
                    stream_sid=stream_sid,
                    from_number=from_number,
                    to_number=to_number,
                    language_code=language,
                    send=_send,
                )
                if session is None:
                    record_status = CALL_FAILED
                    await ws.close(code=1011, reason="speech stack unavailable")
                    break
                mgr.register_active(call_sid, session)
                started_at = time.time()
                _publish_start(bus, call_sid, from_number, to_number, stream_sid)
                # Speak the greeting off the receive loop so we keep draining
                # inbound media (and can detect barge-in). Awaiting it inline
                # would backpressure the socket against a client that is still
                # sending audio.
                import asyncio as _asyncio

                async def _greet(sess=session) -> None:
                    try:
                        await sess.speak_greeting()
                    except Exception as exc:  # noqa: BLE001
                        log.debug("telephony greeting failed: %s", exc)

                _asyncio.create_task(_greet())
                continue

            if event == "media" and session is not None:
                payload = data.get("media", {}).get("payload", "")
                if payload:
                    await session.handle_media(payload)
                if session.check_time_cap():
                    await session.end(reason="max_call_seconds", status=CALL_COMPLETED)
                    record_status = session.status
                    break
                if session.ended:
                    record_status = session.status
                    break
                continue

            if event == "mark":
                continue

            if event == "stop":
                if session is not None:
                    await session.end(reason="twilio_stop", status=CALL_COMPLETED)
                    record_status = session.status
                break
    finally:
        if session is not None and not session.ended:
            await session.end(reason="socket_closed", status=record_status)
            record_status = session.status
        if call_sid:
            mgr.unregister_active(call_sid)
            duration = session.duration_s if session is not None else time.time() - started_at
            turns = session.turns if session is not None else 0
            from_number = getattr(session, "from_number", "") if session else ""
            to_number = getattr(session, "to_number", "") if session else ""
            try:
                mgr.record_call(
                    CallRecord(
                        call_sid=call_sid,
                        from_number=from_number,
                        to_number=to_number,
                        started_at=started_at,
                        ended_at=time.time(),
                        duration_s=duration,
                        # CallRecord runtime-asserts membership in CALL_STATUSES;
                        # the cast only satisfies the static Literal annotation.
                        status=cast(CallStatusLiteral, record_status),
                        turns=turns,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("telephony record_call failed: %s", exc)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001, S110 - socket may already be closed
            pass


def _build_session(
    *,
    state,
    cfg,
    twilio,
    bus,
    call_sid: str,
    stream_sid: str,
    from_number: str,
    to_number: str,
    language_code: str,
    send,
):
    """Build a TelephonyCallSession with per-call brain + shared STT/TTS.

    Returns ``None`` when the speech stack cannot be constructed (e.g. no
    provider key) — the caller then closes the socket cleanly.
    """
    from jarvis.telephony.session import TelephonyCallSession

    # An integration test can inject a pre-built session factory.
    factory = getattr(state, "telephony_session_factory", None)
    if factory is not None:
        return factory(
            call_sid=call_sid,
            stream_sid=stream_sid,
            from_number=from_number,
            to_number=to_number,
            language_code=language_code,
            send=send,
        )

    try:
        from jarvis.brain.factory import build_default_brain
        from jarvis.plugins.stt import build_stt_from_config
        from jarvis.plugins.tts import build_tts_from_config

        stt = build_stt_from_config(cfg.stt)
        tts = build_tts_from_config(cfg.tts)
        brain = build_default_brain(bus=bus, tier="router")
    except Exception as exc:  # noqa: BLE001
        log.warning("telephony: speech stack build failed: %s", exc)
        return None

    return TelephonyCallSession(
        call_sid=call_sid,
        stream_sid=stream_sid,
        send=send,
        stt=stt,
        brain=brain,
        tts=tts,
        from_number=from_number,
        to_number=to_number,
        language_code=language_code or "de-DE",
        greeting=getattr(twilio, "greeting", "") if twilio else "",
        max_call_seconds=int(getattr(twilio, "max_call_seconds", 600) or 600) if twilio else 600,
        bus=bus,
    )


def _publish_start(bus, call_sid, from_number, to_number, stream_sid) -> None:
    if bus is None:
        return
    try:
        from jarvis.telephony.events import TelephonyCallStarted

        bus.publish(
            TelephonyCallStarted(
                call_sid=call_sid,
                from_number=from_number,
                to_number=to_number,
                stream_sid=stream_sid,
            )
        )
    except Exception:  # noqa: BLE001, S110 - bus publish is best-effort
        pass


__all__ = ["router"]
