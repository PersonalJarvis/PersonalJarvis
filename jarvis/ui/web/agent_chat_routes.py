"""REST + WebSocket routes for the agent chat (the typed front-page chat).

Prefix ``/api/agent-chat``:

    GET    /catalog                          provider rows + effort + permission ladders
    GET    /sessions                         newest first
    POST   /sessions                         create (provider, model, effort, cwd, permission_mode)
    GET    /sessions/{id}                    session + its persisted events
    PATCH  /sessions/{id}                    title/provider/model/effort/cwd/permission_mode
    DELETE /sessions/{id}
    POST   /sessions/{id}/messages           {text} -> starts a turn, returns turn_id
    POST   /sessions/{id}/cancel
    POST   /sessions/{id}/approvals/{aid}    {decision: allow | allow_always | deny}
    WS     /sessions/{id}/ws?after=<seq>     snapshot, then live events
    POST   /pick-folder                      the system folder dialog (desktop only)
    GET    /check-folder?path=               does the folder exist / is it a directory

The service lives on ``app.state.agent_chat`` (built in ``server.py``); a
missing service answers 503 like every other optional subsystem.
Loopback-only like the rest of the web UI — no auth token.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from jarvis.agent_chat.catalog import CLAUDE_CODE_MODELS, PROVIDER_ROWS
from jarvis.agent_chat.effort import normalize_effort
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.permissions import (
    default_permission,
    is_permission_mode,
    normalize_permission,
    permission_modes,
)
from jarvis.agent_chat.service import (
    DECISIONS,
    AgentChatService,
    NoSuchSession,
    SessionBusy,
    resolve_runner,
)
from jarvis.agent_chat.tools import shell_label

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-chat", tags=["agent-chat"])

_WS_PING_S = 20.0


# ------------------------------------------------------------------ bodies


class CreateSessionBody(BaseModel):
    provider: str
    model: str = ""
    effort: str | None = None
    cwd: str | None = None
    #: "" = the runner's default mode (jarvis/agent_chat/permissions.py).
    permission_mode: str = ""
    title: str = ""


class PatchSessionBody(BaseModel):
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    cwd: str | None = None
    permission_mode: str | None = None


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)


class ApprovalBody(BaseModel):
    decision: str


class PickFolderBody(BaseModel):
    start: str | None = None


# ------------------------------------------------------------------ helpers


def _service_from_state(state: Any) -> AgentChatService | None:
    """The service, built on first use from ``app.state.agent_chat_factory``."""
    svc = getattr(state, "agent_chat", None)
    if svc is not None:
        return svc
    factory = getattr(state, "agent_chat_factory", None)
    if factory is None:
        return None
    try:
        svc = factory()
    except Exception as exc:  # noqa: BLE001 — surfaces as 503 with the reason in the log
        log.warning("agent chat: service could not be built: %s", exc)
        return None
    state.agent_chat = svc
    return svc


def _service(request: Request) -> AgentChatService:
    svc = _service_from_state(request.app.state)
    if svc is None:
        raise HTTPException(status_code=503, detail="agent-chat-unavailable")
    return svc


def _ws_service(ws: WebSocket) -> AgentChatService | None:
    app = ws.scope.get("app")
    return _service_from_state(app.state) if app is not None else None


def _cli_installed(runner: str) -> bool:
    import shutil

    names = {
        "claude-cli": ("claude", "claude.cmd", "claude.exe"),
        "codex-cli": ("codex", "codex.cmd", "codex.exe"),
        "agy-cli": ("agy", "agy.exe"),
        "grok-cli": ("grok", "grok.exe", "grok.cmd"),
    }.get(runner, ())
    return any(shutil.which(n) for n in names)


async def _live_cli_models() -> dict[str, list[dict[str, Any]]]:
    """The model lists the installed CLIs publish, keyed by runner.

    agy answers ``agy models`` (a ~2 s subprocess, cached 10 min in the
    runner module); Codex keeps ``models_cache.json`` in its home. Both are
    read off the event loop; a failure just keeps the curated fallback.
    """
    from jarvis.agent_chat.runner_cli import read_agy_models, read_codex_models

    out: dict[str, list[dict[str, Any]]] = {}
    if _cli_installed("agy-cli"):
        try:
            out["agy-cli"] = await asyncio.wait_for(asyncio.to_thread(read_agy_models), 10.0)
        except (TimeoutError, Exception) as exc:  # noqa: BLE001 — the fallback list stands in
            log.debug("agent chat: agy model list unavailable: %s", exc)
    if _cli_installed("codex-cli"):
        try:
            rows = await asyncio.to_thread(read_codex_models)
        except Exception as exc:  # noqa: BLE001 — the fallback list stands in
            log.debug("agent chat: codex model list unavailable: %s", exc)
            rows = None
        if rows:
            out["codex-cli"] = rows
    return out


def _validate_cwd(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    p = Path(os.path.expanduser(text))
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a folder: {text}")
    return str(p.resolve())


# ------------------------------------------------------------------ catalog


@router.get("/catalog")
async def get_catalog(request: Request) -> dict[str, Any]:
    """Provider rows for the composer's picker.

    Static shape from ``jarvis.agent_chat.catalog`` plus two live facts per
    row: which runner answers on this machine and whether that runner's
    binary is installed. Credential state is NOT repeated here — the
    picker reads it from ``/api/jarvis-agent/status`` like the Agents tab,
    so the two never disagree.
    """
    svc = _service(request)
    rows: list[dict[str, Any]] = []
    live_models = await _live_cli_models()
    for row in PROVIDER_ROWS:
        d = row.to_dict()
        runner = resolve_runner(row.id)
        d["runner"] = runner
        d["cli_installed"] = _cli_installed(runner) if runner != "api" else None
        # A CLI that publishes its own model list (agy, Codex) overrides the
        # curated fallback with what THIS account can actually pick.
        if d["cli_installed"] and runner in live_models and live_models[runner]:
            d["curated_models"] = live_models[runner]
        # The dual row: Claude Code takes its own ids and aliases; with only
        # an API key the Anthropic catalog route lists the models live.
        if row.id == "claude-api":
            if runner == "claude-cli":
                d["curated_models"] = [m.to_dict() for m in CLAUDE_CODE_MODELS]
                d["models_source"] = "curated"
            else:
                d["models_source"] = "live"
        # The permission ladder is the RUNNER's (Claude Code's modes when the
        # CLI answers, the API runner's when only a key is there), so the
        # composer shows the words the thing that runs actually understands.
        d["permission_modes"] = [m.to_dict() for m in permission_modes(runner)]
        d["default_permission_mode"] = default_permission(runner)
        rows.append(d)
    return {
        "providers": rows,
        "default_cwd": svc.default_cwd(),
        "shell": shell_label(),
    }


# ------------------------------------------------------------------ sessions


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    svc = _service(request)
    out = []
    for s in svc.store.list_sessions(limit=limit):
        d = s.to_dict()
        d["running"] = svc.is_running(s.session_id)
        out.append(d)
    return {"sessions": out}


@router.post("/sessions", status_code=201)
async def create_session(body: CreateSessionBody, request: Request) -> dict[str, Any]:
    svc = _service(request)
    runner = resolve_runner(body.provider)
    if body.permission_mode and not is_permission_mode(runner, body.permission_mode):
        raise HTTPException(
            status_code=400,
            detail=(
                "permission_mode must be one of "
                + ", ".join(m.id for m in permission_modes(runner))
            ),
        )
    cwd = _validate_cwd(body.cwd)
    try:
        session = svc.create_session(
            provider=body.provider,
            model=body.model,
            effort=body.effort,
            cwd=cwd,
            permission_mode=normalize_permission(runner, body.permission_mode),
            title=body.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    d = session.to_dict()
    d["running"] = False
    return d


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    session = svc.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    d = session.to_dict()
    d["running"] = svc.is_running(session_id)
    return {"session": d, "events": svc.store.list_events(session_id)}


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str, body: PatchSessionBody, request: Request
) -> dict[str, Any]:
    svc = _service(request)
    if svc.store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title.strip()[:120]
    if body.provider is not None:
        fields["provider"] = body.provider.strip().lower()
        # A provider change resets the vendor conversation: the new CLI cannot
        # resume the old one's id.
        fields["vendor_session"] = ""
    if body.model is not None:
        fields["model"] = body.model.strip()
    if body.effort is not None:
        provider = fields.get("provider") or svc.store.get_session(session_id).provider  # type: ignore[union-attr]
        fields["effort"] = normalize_effort(provider, body.effort)
    if body.cwd is not None:
        fields["cwd"] = _validate_cwd(body.cwd) or svc.default_cwd()
    current = svc.store.get_session(session_id)
    assert current is not None
    runner = resolve_runner(fields.get("provider") or current.provider)
    if body.permission_mode is not None:
        if not is_permission_mode(runner, body.permission_mode):
            raise HTTPException(
                status_code=400,
                detail=(
                    "permission_mode must be one of "
                    + ", ".join(m.id for m in permission_modes(runner))
                ),
            )
        fields["permission_mode"] = body.permission_mode
    elif "provider" in fields:
        # A provider change folds the old mode onto the new runner's ladder.
        fields["permission_mode"] = normalize_permission(runner, current.permission_mode)
    session = svc.store.update_session(session_id, **fields)
    assert session is not None
    changed = {k: v for k, v in fields.items() if k != "vendor_session"}
    if changed:
        await svc._emit(session_id, make_event("session_updated", changed))  # noqa: SLF001 — same package boundary
    d = session.to_dict()
    d["running"] = svc.is_running(session_id)
    return d


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    await svc.cancel(session_id)
    if not svc.store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True, "session_id": session_id}


# ------------------------------------------------------------------ turns


@router.post("/sessions/{session_id}/messages", status_code=202)
async def post_message(session_id: str, body: MessageBody, request: Request) -> dict[str, Any]:
    svc = _service(request)
    try:
        turn_id = await svc.send(session_id, body.text)
    except NoSuchSession as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail="a turn is already running") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"turn_id": turn_id, "session_id": session_id}


@router.post("/sessions/{session_id}/cancel")
async def cancel_turn(session_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    if svc.store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    cancelled = await svc.cancel(session_id)
    return {"cancelled": cancelled, "session_id": session_id}


@router.post("/sessions/{session_id}/approvals/{approval_id}")
async def resolve_approval(
    session_id: str, approval_id: str, body: ApprovalBody, request: Request
) -> dict[str, Any]:
    svc = _service(request)
    if body.decision not in DECISIONS:
        raise HTTPException(status_code=400, detail=f"decision must be one of {list(DECISIONS)}")
    ok = svc.resolve_approval(session_id, approval_id, body.decision)
    if not ok:
        raise HTTPException(status_code=404, detail="no such pending approval")
    return {"ok": True, "approval_id": approval_id, "decision": body.decision}


# ------------------------------------------------------------------ folders


@router.post("/pick-folder")
async def pick_folder(body: PickFolderBody, request: Request) -> dict[str, Any]:
    """Open the system folder dialog (desktop only) and return the choice."""
    _service(request)
    try:
        from jarvis.agentic_ide import native_picker
    except Exception as exc:  # noqa: BLE001 — no picker module on this install
        raise HTTPException(status_code=501, detail="no folder dialog here") from exc
    result = await asyncio.to_thread(native_picker.choose_folder, start=body.start)
    if result.error:
        raise HTTPException(status_code=501, detail=result.error)
    return {"path": result.path, "cancelled": result.cancelled}


def _folder_check(path: str) -> dict[str, Any]:
    p = Path(os.path.expanduser(path.strip())) if path.strip() else None
    ok = bool(p and p.is_dir())
    return {"ok": ok, "path": str(p.resolve()) if ok and p else path}


@router.get("/check-folder")
async def check_folder(path: str = Query(...)) -> dict[str, Any]:
    return await asyncio.to_thread(_folder_check, path)


# ------------------------------------------------------------------ stream


@router.websocket("/sessions/{session_id}/ws")
async def session_stream(ws: WebSocket, session_id: str) -> None:
    """Snapshot + live events for one session.

    First frame: ``{"type": "snapshot", "session": {...}, "events": [...]}``
    with every persisted event after ``?after=<seq>`` (default 0 = all).
    Then ``{"type": "event", "event": {...}}`` per live event, and a
    ``{"type": "ping"}`` every 20 s of silence so a proxy keeps the socket.
    """
    await ws.accept()
    svc = _ws_service(ws)
    if svc is None:
        await ws.close(code=1011, reason="agent chat not ready")
        return
    session = svc.store.get_session(session_id)
    if session is None:
        await ws.close(code=4404, reason="session not found")
        return
    try:
        after = int(ws.query_params.get("after") or 0)
    except ValueError:
        after = 0

    # Subscribe BEFORE reading the snapshot so nothing falls between the two.
    q = svc.subscribe(session_id)
    try:
        events = svc.store.list_events(session_id, after_seq=after)
        d = session.to_dict()
        d["running"] = svc.is_running(session_id)
        d["pending_approvals"] = svc.pending_approvals(session_id)
        await ws.send_json({"type": "snapshot", "session": d, "events": events})
        last_seq = events[-1]["seq"] if events else after

        async def _reader() -> None:
            # The client sends nothing meaningful; reading detects the close.
            # Any receive error ends the stream (AP-20) — swallowed here so
            # the task never carries an unretrieved exception; the loop below
            # sees ``reader.done()`` and stops.
            try:
                while True:
                    await ws.receive_text()
            except Exception as exc:  # noqa: BLE001 — a closed socket is the normal end
                log.debug("agent chat ws %s reader ended: %s", session_id, exc)

        reader = asyncio.create_task(_reader())
        try:
            while not reader.done():
                getter = asyncio.ensure_future(q.get())
                done, _ = await asyncio.wait(
                    {getter, reader}, timeout=_WS_PING_S, return_when=asyncio.FIRST_COMPLETED
                )
                if getter not in done:
                    getter.cancel()
                    if reader in done:
                        break
                    await ws.send_json({"type": "ping"})
                    continue
                ev = getter.result()
                # Persisted events carry seq; transient deltas carry 0. Skip
                # persisted ones the snapshot already had.
                seq = int(ev.get("seq") or 0)
                if seq and seq <= last_seq:
                    continue
                if seq:
                    last_seq = seq
                await ws.send_json({"type": "event", "event": ev})
        finally:
            reader.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 — any socket error ends the stream (AP-20)
        log.debug("agent chat ws %s ended: %s", session_id, exc)
    finally:
        svc.unsubscribe(session_id, q)
