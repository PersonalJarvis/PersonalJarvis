"""REST + WebSocket routes for the agent chat (the typed front-page chat).

Prefix ``/api/agent-chat``:

    GET    /catalog?surface=                 provider rows + effort + permission ladders
    GET    /provider-health?surface=         which of those rows actually answer
    GET    /sessions?surface=                newest first; ``surface`` narrows to one chat
    POST   /sessions                         create (provider, model, effort, cwd,
                                             permission_mode, surface)
    GET    /sessions/{id}                    session + its persisted events
    PATCH  /sessions/{id}                    title/provider/model/effort/cwd/permission_mode
    DELETE /sessions/{id}
    POST   /sessions/{id}/messages           {text, attachments} -> starts a turn
    POST   /sessions/{id}/cancel
    POST   /sessions/{id}/approvals/{aid}    {decision: allow | allow_always | deny}
    WS     /sessions/{id}/ws?after=<seq>     snapshot, then live events
    POST   /attachments                      drop/paste/pick files for the next message
    POST   /pick-folder                      the system folder dialog (desktop only)
    GET    /check-folder?path=               does the folder exist / is it a directory
    GET    /typeahead?trigger=&surface=&provider=&cwd=&q=
                                             what the composer lists after "/", "@" or "$"
    GET    /typeahead?trigger=&surface=&provider=&cwd=&q=
                                             what the composer lists after "/", "@" or "$"

The service lives on ``app.state.agent_chat`` (built in ``server.py``); a
missing service answers 503 like every other optional subsystem.
Loopback-only like the rest of the web UI — no auth token.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Final, Literal

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from jarvis.agent_chat import attachments as chat_attachments
from jarvis.agent_chat import typeahead
from jarvis.agent_chat.catalog import CLAUDE_CODE_MODELS, offers, rows_for
from jarvis.agent_chat.effort import normalize_effort
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.permissions import (
    default_permission,
    is_permission_mode,
    ladder_key,
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
from jarvis.agent_chat.surface_kits import kit_for
from jarvis.agent_chat.tools import shell_label

log = logging.getLogger(__name__)

#: The Pydantic twin of ``jarvis.agent_chat.store.SURFACES`` (AP-4; the parity
#: test in tests/unit/agent_chat/test_agent_chat_surface_parity.py pins it).
SurfaceName = Literal["jarvis", "agent", "local-models"]

#: The same names, as data — a multipart form field cannot be typed by a
#: ``Literal`` without turning an unknown surface into a 422 on a file the
#: person just dropped.
SURFACE_NAMES: frozenset[str] = frozenset({"jarvis", "agent", "local-models"})

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
    #: Which chat the session belongs to — the front page ("jarvis"), the
    #: Agentic IDE's chat mode ("agent"), or the Local models section's setup
    #: assistant ("local-models"). Fixed for the session's life.
    surface: SurfaceName = "agent"


class PatchSessionBody(BaseModel):
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    cwd: str | None = None
    permission_mode: str | None = None


class MessageBody(BaseModel):
    #: May be empty when files are attached — dropping a screenshot and
    #: pressing Enter is a complete gesture. The service refuses a message
    #: that carries neither.
    text: str = Field(default="", max_length=200_000)
    #: What ``POST /attachments`` returned for the files going in with this
    #: message; the wire shape of ``drop_analysis.DropAnalysis``.
    attachments: list[dict[str, Any]] = Field(default_factory=list)


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

    One reader, shared with the workspace panes that offer the same CLIs
    (:func:`jarvis.workspace.launch_picks.live_models`) — two readers would be
    two answers to one question, and they would drift the first time an
    account gained a model.
    """
    from jarvis.workspace.launch_picks import live_models

    return await live_models()


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
async def get_catalog(request: Request, surface: SurfaceName = "agent") -> dict[str, Any]:
    """Provider rows for the composer's picker.

    Static shape from ``jarvis.agent_chat.catalog`` plus two live facts per
    row: which runner answers on this machine (for ``surface``) and whether
    that runner's binary is installed. Credential state is NOT repeated here
    — the picker reads it from ``/api/jarvis-agent/status`` like the Agents
    tab, so the two never disagree. On the Jarvis surface every row shows the
    one Jarvis ladder (``permissions.JARVIS_LADDER``).

    Which rows a surface gets is ``catalog.rows_for``: the front page's chat
    has no CLI seats, so it is offered only the providers whose own API a
    brain plugin drives — and no CLI is probed for its model list either.
    """
    svc = _service(request)
    rows: list[dict[str, Any]] = []
    cli_seats = kit_for(surface).cli_seats
    live_models = await _live_cli_models() if cli_seats else {}
    for row in rows_for(surface):
        d = row.to_dict()
        runner = resolve_runner(row.id, surface=surface)
        d["runner"] = runner
        d["cli_installed"] = _cli_installed(runner) if runner not in ("api", "brain") else None
        # A CLI that publishes its own model list (agy, Codex) overrides the
        # curated fallback with what THIS account can actually pick.
        if d["cli_installed"] and runner in live_models and live_models[runner]:
            d["curated_models"] = live_models[runner]
        # The dual row: Claude Code takes its own ids and aliases; with only
        # an API key — and on a surface with no CLI seats, always — the
        # Anthropic catalog route lists the models live.
        if row.id == "claude-api":
            if runner == "claude-cli":
                d["curated_models"] = [m.to_dict() for m in CLAUDE_CODE_MODELS]
                d["models_source"] = "curated"
            else:
                d["models_source"] = "live"
        # The permission ladder is the RUNNER's (Claude Code's modes when the
        # CLI answers, the API runner's when only a key is there), so the
        # composer shows the words the thing that runs actually understands —
        # except on the Jarvis surface, where one ladder serves every seat.
        ladder = ladder_key(surface, runner)
        d["permission_modes"] = [m.to_dict() for m in permission_modes(ladder)]
        d["default_permission_mode"] = default_permission(ladder)
        # Which characters open the composer's typeahead on this seat —
        # decided here, from the runner, so the box never offers a "/" list
        # to a seat that would read it as plain text.
        d["typeahead"] = list(typeahead.triggers_for(runner))
        rows.append(d)
    return {
        "providers": rows,
        "default_cwd": svc.default_cwd(surface),
        "shell": shell_label(),
    }


# ----------------------------------------------------------- typeahead


@router.get("/typeahead", openapi_extra={"x-jarvis-readonly": True})
async def get_typeahead(
    request: Request,
    trigger: str = Query(..., min_length=1, max_length=1),
    surface: SurfaceName = "agent",
    provider: str = "",
    cwd: str | None = None,
    q: str = Query("", max_length=200),
    limit: int = Query(40, ge=1, le=200),
) -> dict[str, Any]:
    """What the composer lists after ``/``, ``@`` or ``$`` on one seat.

    The seat is (surface, provider) resolved to its runner exactly as a turn
    would be; the folder is the chat's. Rows are read from the disk that
    runner reads (``jarvis.agent_chat.typeahead``) — the account's skills,
    commands and plugins, the folder's own, or the files under it — and a
    trigger the seat does not honour answers with an empty list.
    """
    svc = _service(request)
    runner = resolve_runner(provider, surface=surface) if provider else "api"
    folder = _validate_cwd(cwd) or svc.default_cwd(surface)
    return await asyncio.to_thread(
        typeahead.suggest,
        runner=runner,
        cwd=folder,
        trigger=trigger,
        query=q,
        limit=limit,
    )


# ------------------------------------------------------------- health


class ProviderHealthRow(BaseModel):
    """One row's live state, in the API-Keys screen's own vocabulary."""

    provider: str
    #: ok | needs_setup | error | unknown (jarvis.brain.section_health).
    status: str = "unknown"
    #: Machine-readable cause — "bad_key", "no_credits", "not_configured",
    #: "timeout", … The UI turns it into a sentence; it is never shown raw.
    reason: str = "unknown"
    #: The provider's own words, for the tooltip and the log.
    detail: str = ""


class ProviderHealthResponse(BaseModel):
    providers: list[ProviderHealthRow]
    checked_at: float = 0.0
    cached: bool = False


#: How long one sweep stands. A key does not go bad between two clicks, and
#: the sweep costs one real request per provider — 9 s to 16 s each on the
#: maintainer's box (measured 2026-08-26), which is why nothing waits for it.
_HEALTH_TTL_S: Final[float] = 300.0
#: The whole sweep's ceiling. A provider still thinking when this runs out is
#: reported ``unknown`` and draws no dot: a seat that cannot answer a one-token
#: request inside this is not a seat someone should be told is fine, but
#: neither is a slow network proof that a key is bad.
_HEALTH_SWEEP_S: Final[float] = 20.0

#: surface -> (checked_at, rows). Process-local, like every other short cache
#: in the routes; a restart simply re-sweeps on first use.
_health_cache: dict[str, tuple[float, list[ProviderHealthRow]]] = {}

#: Vendor CLI runners. Their credential is a subscription login, not a key,
#: so the API-Keys one-token probe is the wrong check: the dual Claude row
#: is catalogued as ``claude-api``, and that probe hits Anthropic's endpoint
#: and paints "Key rejected" on a Claude Code seat that is signed in.
_CLI_RUNNERS: Final[frozenset[str]] = frozenset({"claude-cli", "codex-cli", "agy-cli", "grok-cli"})


def _cli_auth_status(runner: str) -> Any | None:
    """The vendor CLI's own login snapshot. ``None`` if ``runner`` is not a CLI."""
    if runner == "claude-cli":
        from jarvis.claude_auth import ClaudeAuthService

        return ClaudeAuthService().status()
    if runner == "codex-cli":
        from jarvis.codex_auth import CodexAuthService

        return CodexAuthService().status()
    if runner == "agy-cli":
        from jarvis.google_cli.auth_service import GoogleCliAuthService

        return GoogleCliAuthService().status()
    if runner == "grok-cli":
        from jarvis.grok_build_auth import GrokBuildAuthService

        return GrokBuildAuthService().status()
    return None


def _cli_subscription_connected(runner: str, st: Any) -> bool:
    """Whether ``st`` is a login this CLI seat can actually spend.

    Claude and Grok Build report ``connected=True`` for a stored API key as
    well; that key belongs to a different picker row. Antigravity's
    ``api_key`` mode is the Gemini key, same split. Codex's ``connected``
    is the CLI's own auth.json (ChatGPT login or a key the CLI itself
    holds), which is what ``codex exec`` uses.
    """
    connected = bool(getattr(st, "connected", False))
    mode = (getattr(st, "mode", None) or "").strip().lower()
    if runner == "claude-cli":
        return connected and mode == "subscription"
    if runner == "grok-cli":
        return connected and mode == "subscription"
    if runner == "agy-cli":
        return connected and mode == "oauth-personal"
    return connected


def _cli_login_snapshot(runner: str) -> tuple[str, str, str]:
    """Login presence for a vendor CLI seat. Never a live API-key call.

    Returns ``(status, reason, detail)``. A signed-in subscription is
    ``ok``; anything else is ``needs_setup`` so the picker does not borrow
    API-key words ("Key rejected") for a seat that has no key.
    """
    try:
        st = _cli_auth_status(runner)
    except Exception as exc:  # noqa: BLE001 — one bad CLI must not lose the sweep
        log.info("agent chat: CLI login check for %s failed: %s", runner, exc)
        return (
            "unknown",
            "check_failed",
            f"The check itself failed ({type(exc).__name__})",
        )
    if st is None:
        return "unknown", "unknown_provider", f"{runner}: not a CLI seat"
    connected = _cli_subscription_connected(runner, st)
    detail = (getattr(st, "message", None) or "").strip() or (
        f"{runner}: signed in" if connected else f"{runner}: not signed in"
    )
    if connected:
        return "ok", "ok", detail
    return "needs_setup", "not_configured", detail


async def _one_provider_health(cfg: Any, provider_id: str, *, surface: str) -> ProviderHealthRow:
    """One row, never raising: a broken check is ``unknown``, not a 500."""
    runner = resolve_runner(provider_id, surface=surface)
    if runner in _CLI_RUNNERS:
        try:
            status, reason, detail = await asyncio.to_thread(_cli_login_snapshot, runner)
        except Exception as exc:  # noqa: BLE001 — one bad row must not lose the sweep
            log.info("agent chat: CLI health check for %s failed: %s", provider_id, exc)
            return ProviderHealthRow(
                provider=provider_id,
                status="unknown",
                reason="check_failed",
                detail=f"The check itself failed ({type(exc).__name__})",
            )
        return ProviderHealthRow(provider=provider_id, status=status, reason=reason, detail=detail)

    from jarvis.ui.web.provider_routes import provider_health

    try:
        health = await provider_health(cfg, provider_id, probe=True)
    except TimeoutError:
        return ProviderHealthRow(
            provider=provider_id, status="unknown", reason="timeout", detail="No answer in time"
        )
    except Exception as exc:  # noqa: BLE001 — one bad row must not lose the sweep
        log.info("agent chat: health check for %s failed: %s", provider_id, exc)
        return ProviderHealthRow(
            provider=provider_id,
            status="unknown",
            reason="check_failed",
            detail=f"The check itself failed ({type(exc).__name__})",
        )
    return ProviderHealthRow(
        provider=provider_id,
        status=health.status,
        reason=health.reason,
        detail=health.detail,
    )


@router.get("/provider-health")
async def get_provider_health(
    request: Request, surface: SurfaceName = "agent", refresh: bool = False
) -> ProviderHealthResponse:
    """Which of ``surface``'s rows actually answer right now.

    The composer shows whether a provider is CONNECTED — a key is saved. That
    is not the same as usable: a key can be revoked, an account can run out of
    credits, an endpoint can be down. On the maintainer's own box on
    2026-08-26, four of nine connected rows were in one of those states, and
    the picker offered all nine as if they were equal.

    API / brain seats run the real one-token check the API-Keys screen's tab
    dots use (``provider_routes.provider_health``). A vendor CLI seat does
    not: it spends a subscription login, so this reports that login instead.
    The dual Claude row is why the split exists — its catalog id is
    ``claude-api``, and the API-Keys probe would otherwise paint "Key
    rejected" on a Claude Code seat that is signed in.

    Nothing waits for this: the composer paints from the catalog and folds
    these in when they land. A row that does not finish inside the sweep
    ceiling comes back ``unknown`` and is drawn exactly as it was before.
    """
    _service(request)  # 503 like every other route when the chat is off
    now = time.monotonic()
    cached = _health_cache.get(surface)
    if cached and not refresh and (now - cached[0]) < _HEALTH_TTL_S:
        return ProviderHealthResponse(providers=cached[1], checked_at=cached[0], cached=True)

    from jarvis.ui.web.provider_routes import _resolve_cfg

    cfg = _resolve_cfg(request)
    ids = [row.id for row in rows_for(surface)]
    tasks = {
        pid: asyncio.create_task(_one_provider_health(cfg, pid, surface=surface)) for pid in ids
    }
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks.values(), return_exceptions=True), timeout=_HEALTH_SWEEP_S
        )
    except TimeoutError:
        log.info("agent chat: provider health sweep hit its %.0fs ceiling", _HEALTH_SWEEP_S)
    rows: list[ProviderHealthRow] = []
    for pid, task in tasks.items():
        if task.done() and not task.cancelled():
            exc = task.exception()
            if exc is None:
                rows.append(task.result())
                continue
        task.cancel()
        rows.append(
            ProviderHealthRow(
                provider=pid, status="unknown", reason="timeout", detail="No answer in time"
            )
        )
    _health_cache[surface] = (now, rows)
    return ProviderHealthResponse(providers=rows, checked_at=now, cached=False)


# ------------------------------------------------------------------ sessions


@router.get("/sessions")
async def list_sessions(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    surface: SurfaceName | None = None,
) -> dict[str, Any]:
    svc = _service(request)
    out = []
    for s in svc.store.list_sessions(limit=limit, surface=surface):
        d = s.to_dict()
        d["running"] = svc.is_running(s.session_id)
        out.append(d)
    return {"sessions": out}


@router.post("/sessions", status_code=201)
async def create_session(body: CreateSessionBody, request: Request) -> dict[str, Any]:
    svc = _service(request)
    ladder = ladder_key(body.surface, resolve_runner(body.provider, surface=body.surface))
    if body.permission_mode and not is_permission_mode(ladder, body.permission_mode):
        raise HTTPException(
            status_code=400,
            detail=(
                "permission_mode must be one of "
                + ", ".join(m.id for m in permission_modes(ladder))
            ),
        )
    cwd = _validate_cwd(body.cwd)
    try:
        session = svc.create_session(
            provider=body.provider,
            model=body.model,
            effort=body.effort,
            cwd=cwd,
            permission_mode=normalize_permission(ladder, body.permission_mode),
            title=body.title,
            surface=body.surface,
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
    existing = svc.store.get_session(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="session not found")
    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title.strip()[:120]
    if body.provider is not None:
        picked = body.provider.strip().lower()
        if not offers(existing.surface, picked):
            raise HTTPException(
                status_code=400,
                detail=f"provider {picked!r} is not offered on the {existing.surface!r} chat",
            )
        fields["provider"] = picked
        # A provider change resets the vendor conversation: the new CLI cannot
        # resume the old one's id.
        fields["vendor_session"] = ""
    if body.model is not None:
        fields["model"] = body.model.strip()
    if body.effort is not None:
        provider = fields.get("provider") or svc.store.get_session(session_id).provider  # type: ignore[union-attr]
        fields["effort"] = normalize_effort(provider, body.effort)
    current = svc.store.get_session(session_id)
    assert current is not None
    if body.cwd is not None:
        fields["cwd"] = _validate_cwd(body.cwd) or svc.default_cwd(current.surface)
    runner = resolve_runner(fields.get("provider") or current.provider, surface=current.surface)
    ladder = ladder_key(current.surface, runner)
    if body.permission_mode is not None:
        if not is_permission_mode(ladder, body.permission_mode):
            raise HTTPException(
                status_code=400,
                detail=(
                    "permission_mode must be one of "
                    + ", ".join(m.id for m in permission_modes(ladder))
                ),
            )
        fields["permission_mode"] = body.permission_mode
    elif "provider" in fields:
        # A provider change folds the old mode onto the new runner's ladder
        # (a no-op on the Jarvis surface, whose ladder is the same for all).
        fields["permission_mode"] = normalize_permission(ladder, current.permission_mode)
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
        turn_id = await svc.send(session_id, body.text, body.attachments)
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


# ------------------------------------------------------------------ attachments


@router.post("/attachments", summary="Drop, paste or pick files into a chat composer")
async def attach_files(
    request: Request,
    files: list[UploadFile] | None = File(default=None),  # noqa: B008
    paths: str | None = Form(default=None),  # noqa: B008
    session_id: str | None = Form(default=None),  # noqa: B008
    cwd: str | None = Form(default=None),  # noqa: B008
    provider: str = Form(default=""),  # noqa: B008
    surface: str = Form(default="agent"),  # noqa: B008
) -> dict[str, Any]:
    """Hold files for the message the person is still typing.

    Two inputs, either or both:

    * ``paths`` — newline-separated real locations. An Explorer or Finder drag
      usually carries them, and inside the desktop shell the host resolves one
      for every dropped file (``jarvis/ui/native_drop.py``). A path already
      inside the chat's folder is referenced where it lies; anything else is
      copied in.
    * ``files`` — raw bytes, for everything with no path at all: a screenshot
      pasted from the clipboard, an image dragged off a web page.

    Nothing is sent. Each file is stored, then READ — an image described by a
    vision-capable model, a document extracted — and the result comes back for
    the composer to hold and post with the message. That reading is the whole
    point: a chat can be answered by a coding CLI or a text-only model, so
    without it the person drops a picture, types "what is wrong here", and the
    model receives a filename.

    Which folder the copies land in, in order: the open session's, the
    composer's own ``cwd``, then the surface's default working directory. So an
    attach works before the first message, when no session exists yet.
    """
    svc = _service(request)
    folder = ""
    if session_id:
        session = svc.store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        folder = session.cwd or ""
    if not folder:
        try:
            folder = _validate_cwd(cwd) or ""
        except HTTPException:
            # A composer whose remembered folder has gone (a moved checkout, a
            # detached drive) must still be able to take a file — the surface's
            # own directory below always exists.
            folder = ""
    if not folder:
        folder = svc.default_cwd(surface if surface in SURFACE_NAMES else "agent")

    uploads: list[tuple[str, bytes]] = []
    for upload in files or []:
        uploads.append((upload.filename or "file", await upload.read()))

    try:
        found = await chat_attachments.ingest(
            folder,
            paths=(paths or "").splitlines(),
            uploads=uploads,
            provider=provider,
        )
    except chat_attachments.AttachmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"attachments": [item.to_dict() for item in found], "cwd": folder}


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
