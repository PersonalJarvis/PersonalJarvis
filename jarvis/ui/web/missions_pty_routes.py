"""Per-Worker PTY-Tail-WebSocket (MVP-Stub fuer Phase-4).

Endpoint: ``/api/missions/pty/{worker_id}``.

Aktueller Stand:
- Hello-Handshake + Token-Auth identisch zu ``missions_ws_routes``.
- Wenn der Worker keine bekannte Log-Datei hat: close ``4404``.
- Die echte Tail-Loop (mit Backpressure ueber pause/resume-Frames) wird in
  Phase-5 verdrahtet, sobald die Worker-Log-Map am ``Kontrollierer`` haengt.

Backpressure-Konstanten sind hier dokumentiert, damit Phase-5 sie ohne
Refactor uebernehmen kann.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Final

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .missions_auth import validate_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/missions/pty", tags=["missions-pty"])


# Phase-5 wird diese Werte beim echten Tail-Loop nutzen.
PAUSE_BYTES: Final[int] = 128 * 1024
RESUME_BYTES: Final[int] = 16 * 1024
_HELLO_TIMEOUT_S: Final[float] = 5.0


def _resolve_worker_log(worker_id: str, app_state: Any) -> Path | None:
    """Worker-Log-Path aufloesen (TODO: in Phase-5 mit Kontrollierer-Map ersetzen).

    Aktuell pruefen wir nur, ob ein optionaler State-Eintrag
    ``app.state.mission_worker_logs`` existiert (dict[worker_id, Path]).
    Sonst None → 4404.
    """
    log_map = getattr(app_state, "mission_worker_logs", None)
    if not isinstance(log_map, dict):
        return None
    candidate = log_map.get(worker_id)
    if candidate is None:
        return None
    p = Path(str(candidate))
    return p if p.exists() else None


@router.websocket("/{worker_id}")
async def pty_ws(ws: WebSocket, worker_id: str) -> None:
    """PTY-Tail-WebSocket fuer einen Worker. MVP gibt 4404 zurueck."""
    await ws.accept()

    # Hello + Auth (gleiche Konvention wie /api/missions/ws).
    try:
        first = await asyncio.wait_for(
            ws.receive_json(), timeout=_HELLO_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        await ws.close(code=4400, reason="hello timeout")
        return
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("pty_ws: hello-decode failed: %s", exc)
        await ws.close(code=4400, reason="hello decode error")
        return

    if not isinstance(first, dict) or first.get("type") != "hello":
        await ws.close(code=4400, reason="expected hello frame first")
        return

    token = str(first.get("token", ""))
    if not validate_token(token):
        await ws.close(code=4401, reason="unauthorized")
        return

    log_path = _resolve_worker_log(worker_id, ws.scope["app"].state)
    if log_path is None:
        await ws.close(code=4404, reason=f"unknown worker_id={worker_id}")
        return

    # TODO: wire to actual log-tail in Phase-5
    # - Open log file, seek end
    # - Async-read 4KB chunks, send_text
    # - Track sum(bytes pending), send {"type": "pause"} if > PAUSE_BYTES,
    #   {"type": "resume"} if drops below RESUME_BYTES
    # - Reader-Task fuer pause/resume control vom Client
    await ws.send_json(
        {
            "type": "info",
            "message": "pty tail not implemented yet (Phase-5)",
            "worker_id": worker_id,
            "log_path": str(log_path),
        }
    )
    try:
        while True:
            try:
                _ = await ws.receive_json()
            except WebSocketDisconnect:
                break
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["PAUSE_BYTES", "RESUME_BYTES", "router"]
