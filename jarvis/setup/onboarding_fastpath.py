"""Stdlib-only onboarding API for the serve-first fast-boot window.

The desktop onboarding gate must render from the FIRST second of a fresh
install, but the real FastAPI app (which mounts
``jarvis/ui/web/onboarding_routes.py``) only registers after the heavy warmup.
This module answers the same ``/api/onboarding/*`` surface as a raw ASGI
handler with ZERO heavy imports (no fastapi/pydantic/config), so
``jarvis.ui.web.fast_bootstrap`` can delegate to it while warming (AP-26:
nothing heavy on the boot path).

The real routes stay authoritative once the app is up — the bootstrap only
consults this handler before ``set_app``. Both layers share the payload
builder below and the ``jarvis.setup.state`` store, so they can never
disagree.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from jarvis.setup import state as st
from jarvis.setup.onboarding_meta import (
    CURRENT_TERMS_VERSION,
    ONBOARDING_STEPS,
    WAKE_WORD_LEGAL_REFERENCES,
    read_terms_text,
)

log = logging.getLogger(__name__)

# Tests override this to redirect the state file (mirrors onboarding_routes).
_STATE_PATH_OVERRIDE: Path | None = None

_PREFIX = "/api/onboarding"


def _path() -> Path | None:
    return _STATE_PATH_OVERRIDE


def state_payload(path: Path | None = None) -> dict:
    """Build the GET /state payload. Never raises — fails open to 'incomplete'.

    Single source of truth shared with ``jarvis.ui.web.onboarding_routes``:
    the legacy ``.setup-complete`` marker is resolved via ``jarvis.setup.state``
    so this stays importable without the heavy config module.
    """
    try:
        s = st.get_onboarding_state(path)
    except Exception as exc:  # noqa: BLE001 — UI must keep working
        log.warning("onboarding_get_state_failed: %s", exc, exc_info=True)
        s = {
            "completed_at": None, "current_step": None, "skipped_steps": [],
            "terms_accepted_at": None, "terms_version": None,
            "wake_word_acknowledged_at": None,
        }

    legacy_done = False
    try:
        legacy_done = st.setup_complete_marker_exists(path)
    except Exception as exc:  # noqa: BLE001
        log.debug("onboarding: marker probe failed: %s", exc)

    completed = (s["completed_at"] is not None) or legacy_done
    if os.environ.get("JARVIS_FORCE_ONBOARDING"):
        completed = False

    return {
        "completed": completed,
        "current_step": s["current_step"],
        "skipped_steps": s["skipped_steps"],
        "terms": {
            "accepted": s["terms_accepted_at"] is not None,
            "accepted_version": s["terms_version"],
            "current_version": CURRENT_TERMS_VERSION,
        },
        "wake_word_acknowledged": s["wake_word_acknowledged_at"] is not None,
        "legal_references": WAKE_WORD_LEGAL_REFERENCES,
        "steps": ONBOARDING_STEPS,
    }


async def _read_body(receive: Any) -> bytes:
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            return body


async def _send_json(send: Any, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"cache-control", b"no-store, max-age=0"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def handle(scope: dict, receive: Any, send: Any) -> bool:
    """Answer an ``/api/onboarding/*`` request; True iff handled.

    Mirrors ``onboarding_routes.py`` in payload shape and semantics; every
    write is fail-open (the state helpers never raise). One deliberate
    simplification: any unknown sub-path OR wrong method returns 404 (the
    FastAPI router would answer 405 for a wrong method) — nothing in the
    frontend depends on that distinction, and a flat 404 keeps this handler
    trivially small.
    """
    if scope.get("type") != "http":
        return False
    path = scope.get("path", "")
    if not path.startswith(_PREFIX):
        return False
    method, sub = scope.get("method", "GET"), path[len(_PREFIX):]

    if method == "GET" and sub == "/state":
        await _send_json(send, state_payload(_path()))
        return True
    if method == "GET" and sub == "/terms":
        await _send_json(send, {"version": CURRENT_TERMS_VERSION, "text": read_terms_text()})
        return True
    if method == "POST" and sub == "/step":
        raw = await _read_body(receive)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            data = {}
        step = data.get("step") if isinstance(data, dict) else None
        if isinstance(step, str) and step:
            skipped = data.get("skipped")
            st.set_onboarding_step(
                step,
                skipped=list(skipped) if isinstance(skipped, list) else None,
                path=_path(),
            )
            await _send_json(send, {"ok": True})
        else:
            await _send_json(send, {"ok": False, "error": "missing step"}, status=422)
        return True
    if method == "POST" and sub == "/accept-terms":
        st.accept_terms(CURRENT_TERMS_VERSION, path=_path())
        await _send_json(send, {"ok": True, "version": CURRENT_TERMS_VERSION})
        return True
    if method == "POST" and sub == "/acknowledge-wake-word":
        st.acknowledge_wake_word(_path())
        await _send_json(send, {"ok": True})
        return True
    if method == "POST" and sub == "/complete":
        st.mark_onboarding_complete(_path())
        await _send_json(send, {"ok": True})
        return True

    await _send_json(send, {"ok": False, "error": "unknown onboarding route"}, status=404)
    return True


__all__ = ["handle", "state_payload"]
