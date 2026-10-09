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
    POST   /sessions/{id}/questions/{qid}    {index, option_index} or {index, text} -> answer
                                             one question of an agent's card
    POST   /sessions/{id}/questions/{qid}/skip  close the card: recommendations apply
    POST   /sessions/{id}/plan               {turn_id, decision: build | keep} -> answer
                                             a coding agent's plan card
    WS     /sessions/{id}/ws?after=<seq>     snapshot, then live events
    POST   /attachments                      drop/paste/pick files for the next message
    POST   /pick-folder                      the system folder dialog (desktop only)
    GET    /check-folder?path=               does the folder exist / is it a directory
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
import threading
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
from jarvis.agent_chat import runner_cli, typeahead
from jarvis.agent_chat.catalog import claude_code_models, offers, rows_for
from jarvis.agent_chat.control_types import CommandRequest, CommandResult
from jarvis.agent_chat.credential_requests import CredentialRequestBusy
from jarvis.agent_chat.effort import normalize_effort
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.permissions import (
    default_permission,
    is_permission_mode,
    ladder_key,
    normalize_permission,
    permission_modes,
    society_mode_supported,
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
from jarvis.society.credentials import CredentialError

log = logging.getLogger(__name__)

#: The Pydantic twin of ``jarvis.agent_chat.store.SURFACES`` (AP-4; the parity
#: test in tests/unit/agent_chat/test_agent_chat_surface_parity.py pins it).
SurfaceName = Literal["jarvis", "agent", "local-models", "society"]

#: The same names, as data — a multipart form field cannot be typed by a
#: ``Literal`` without turning an unknown surface into a 422 on a file the
#: person just dropped.
SURFACE_NAMES: frozenset[str] = frozenset({"jarvis", "agent", "local-models", "society"})

#: Surfaces that never appear in an unfiltered session list: an agent's
#: canonical chat belongs to its model card, not to the IDE's or the front
#: page's history.
HIDDEN_SURFACES: frozenset[str] = frozenset({"society"})

router = APIRouter(prefix="/api/agent-chat", tags=["agent-chat"])


class ChatSelectionBody(BaseModel):
    provider: str = Field(min_length=1)
    model: str = ""
    effort: str = ""
    account_id: str = ""


@router.put("/selection", summary="Remember the model for new Jarvis chats and agents")
def save_chat_selection(body: ChatSelectionBody, request: Request) -> dict[str, str]:
    from jarvis.agent_chat.store import ChatSelection

    provider = body.provider.strip().lower()
    if not offers("jarvis", provider):
        raise HTTPException(400, "This provider is not offered on the Jarvis chat")
    if body.account_id:
        from jarvis import agent_accounts
        from jarvis.agent_chat.catalog import provider_row

        account = agent_accounts.resolve(body.account_id)
        row = provider_row(provider)
        if account is None or row is None or account.platform != row.agent:
            raise HTTPException(400, "This subscription account does not belong to the provider")
    selection = ChatSelection(
        provider, body.model.strip(), normalize_effort(provider, body.effort), body.account_id
    )
    _service(request).store.save_chat_selection(selection)
    return selection.to_dict()


class VoiceChatBody(BaseModel):
    #: The Jarvis chat on stage; ``None`` = a blank page (the next call opens a new chat).
    session_id: str | None = None
    #: An archived voice chat on stage instead (``sessions.db``): calls are
    #: recorded into it and start with its history. Only with ``session_id`` unset.
    voice_session_id: str | None = None


class VoiceChatResponse(BaseModel):
    session_id: str | None
    fresh: bool
    voice_session_id: str | None = None


def _voice_chat_answer(svc: AgentChatService) -> VoiceChatResponse:
    return VoiceChatResponse(
        session_id=svc.voice_chat_id,
        fresh=svc.voice_chat_fresh,
        voice_session_id=svc.voice_session_continued,
    )


def _archived_voice_history(request: Request, voice_session_id: str) -> Any:
    """Reader for an archived voice chat's turns, or ``None`` when it does not exist."""
    from .chats_routes import _normalized_messages, _seed_pairs

    session_store = getattr(request.app.state, "session_store", None)
    chat_store = getattr(request.app.state, "chat_store", None)
    if session_store is None or session_store.get_session(voice_session_id) is None:
        return None

    def history() -> list[Any]:
        messages = _normalized_messages("voice", voice_session_id, chat_store, session_store)
        return _seed_pairs(messages or [])

    return history


@router.get("/voice-chat", summary="The Jarvis chat voice calls continue")
def get_voice_chat(request: Request) -> VoiceChatResponse:
    return _voice_chat_answer(_service(request))


@router.put("/voice-chat", summary="Continue voice calls in this Jarvis chat")
def put_voice_chat(body: VoiceChatBody, request: Request) -> VoiceChatResponse:
    """Bind the chat the front page shows: calls file into it and start with its history."""
    svc = _service(request)
    voice_session = None if body.session_id else (body.voice_session_id or "").strip() or None
    history = None
    if voice_session is not None:
        history = _archived_voice_history(request, voice_session)
        if history is None:
            raise HTTPException(status_code=404, detail="no-such-voice-chat")
    try:
        svc.bind_voice_chat(
            body.session_id or None, voice_session=voice_session, voice_history=history
        )
    except NoSuchSession as exc:
        raise HTTPException(status_code=404, detail="no-such-jarvis-chat") from exc
    return _voice_chat_answer(svc)


@router.get("/commands", summary="List chat slash commands and their availability")
def list_chat_commands(request: Request, session_id: str | None = None) -> dict[str, Any]:
    try:
        return _service(request).controls.catalog(session_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/sessions/{session_id}/control", summary="Read this chat's mode and goal state")
def get_chat_control(session_id: str, request: Request) -> dict[str, Any]:
    try:
        return _service(request).controls.state(session_id).model_dump()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post(
    "/sessions/{session_id}/commands",
    response_model=CommandResult,
    summary="Run one explicit chat command",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def run_chat_command(
    session_id: str, body: CommandRequest, request: Request
) -> CommandResult:
    try:
        return await (await _async_service(request)).controls.execute(session_id, body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


_WS_PING_S = 20.0


# ------------------------------------------------------------------ bodies


class CreateSessionBody(BaseModel):
    provider: str
    account_id: str = ""
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
    timezone: str | None = Field(default=None, max_length=100)
    #: May be empty when files are attached — dropping a screenshot and
    #: pressing Enter is a complete gesture. The service refuses a message
    #: that carries neither.
    text: str = Field(default="", max_length=200_000)
    #: What ``POST /attachments`` returned for the files going in with this
    #: message; the wire shape of ``drop_analysis.DropAnalysis``.
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    tool_choices: list[str] = Field(default_factory=list, max_length=24)


class ApprovalBody(BaseModel):
    decision: str


class CredentialBody(BaseModel):
    #: The secret the person pasted. It goes to the agent's vault and nowhere
    #: else: never logged, never echoed, never part of a chat event. No length
    #: constraint here on purpose: a validation error would echo the value in
    #: its 422 body; the vault refuses a bad value with a 400 that names none.
    value: Any = Field(default=None, repr=False)


class QuestionAnswerBody(BaseModel):
    #: Which question of the card's series this answers.
    index: int = 0
    #: The picked option (0 is the agent's recommendation) ...
    option_index: int | None = None
    #: ... or the person's own typed answer. Exactly one of the two.
    text: str | None = None


class PlanBody(BaseModel):
    #: The turn whose plan card this answers.
    turn_id: str
    #: ``build`` (switch to the build mode and go) or ``keep`` (keep planning).
    decision: str


class PickFolderBody(BaseModel):
    start: str | None = None


# ------------------------------------------------------------------ helpers


# Sync HTTP routes run in worker threads while boot and WebSockets use the
# event loop. Construction opens SQLite and schedules turn recovery, so the
# first requests must not create competing owners of the same hosted turns.
_SERVICE_BUILD_LOCK = threading.Lock()


def _service_from_state(state: Any) -> AgentChatService | None:
    """The service, built on first use from ``app.state.agent_chat_factory``."""
    svc = getattr(state, "agent_chat", None)
    if svc is not None:
        return svc
    if getattr(state, "agent_chat_factory", None) is not None:
        # Resolve the app's loop while NOT holding the lock: from a worker
        # thread this waits for the loop, and the loop may itself be waiting
        # for the lock in an async route — a deadlock that froze every route.
        from jarvis.agent_chat.service import remember_app_loop

        remember_app_loop()
    with _SERVICE_BUILD_LOCK:
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


#: How long after the server is up the boot reattach waits, so it never sits
#: on the path to a usable window (AP-26).
_REATTACH_DELAY_S: Final = 2.0


def schedule_turn_reattach(state: Any) -> asyncio.Task[None] | None:
    """Carry on the thread turns the turn host kept running, right after boot.

    Called by the two real app entry points beside the Agentic IDE's
    ``schedule_boot_restore``. Without it a reattached turn waited for the
    first window to open a chat: its output piled up in the host and an
    approval its CLI asked for stayed unanswered meanwhile. Building the
    service is what reattaches (``AgentChatService._seal_orphaned_turns``),
    so this builds it only when the host or its spool holds something.
    """
    from jarvis.agent_chat import turn_host_client

    if not turn_host_client.host_available():
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.debug("agent chat: boot reattach not scheduled — no running loop")
        return None

    async def _run() -> None:
        await asyncio.sleep(_REATTACH_DELAY_S)
        if not await asyncio.to_thread(turn_host_client.may_hold_turns):
            return
        if (await asyncio.to_thread(_service_from_state, state)) is None:
            log.warning("agent chat: thread turns wait — the chat service could not be built")

    return loop.create_task(_run(), name="agent-chat-boot-reattach")


def _service(request: Request) -> AgentChatService:
    svc = _service_from_state(request.app.state)
    if svc is None:
        raise HTTPException(status_code=503, detail="agent-chat-unavailable")
    return svc


async def _async_service(request: Request) -> AgentChatService:
    """Wait for lazy construction without holding the serving event loop."""
    service = getattr(request.app.state, "agent_chat", None)
    if service is not None:
        return service
    # Sync routes can already own the construction lock. Waiting on that lock
    # here freezes HTTP, voice, and callbacks needed by the builder itself.
    return await asyncio.to_thread(_service, request)


def _ws_service(ws: WebSocket) -> AgentChatService | None:
    app = ws.scope.get("app")
    return _service_from_state(app.state) if app is not None else None


def _cli_installed(runner: str) -> bool:
    from jarvis.agent_chat.runner_cli import cli_installed

    return cli_installed(runner)


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
async def get_catalog(
    request: Request,
    surface: SurfaceName = "agent",
    account_id: str = "",
    session_id: str = "",
    cwd: str | None = None,
) -> dict[str, Any]:
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
    svc = (await _async_service(request))
    cli_seats = kit_for(surface).cli_seats
    from jarvis.agent_chat.runner_cli import cli_catalog_scope

    if session_id:
        session = svc.store.get_session(session_id)
        if session is None or session.surface != surface:
            raise HTTPException(status_code=404, detail="Chat session not found")
        account_id = session.account_id
        cwd = session.cwd
    if account_id:
        from jarvis import agent_accounts

        if agent_accounts.resolve(account_id) is None:
            raise HTTPException(status_code=400, detail="Unknown subscription account")
    folder = _validate_cwd(cwd)
    with cli_catalog_scope(
        account_id=account_id,
        cwd=Path(folder) if folder else None,
        ignore_user_config=kit_for(surface).brain_runner and cli_seats,
    ):
        live_models = await _live_cli_models() if cli_seats else {}
    # Off the event loop: every CLI row resolves its binary on PATH, and that
    # many ``shutil.which`` walks cost ~0.3 s on a Windows PATH — a stall the
    # whole app shared on every composer open (AP-26 spirit, async-def freeze).
    rows = await asyncio.to_thread(_catalog_rows, surface, live_models)
    return {
        "providers": rows,
        "default_cwd": svc.default_cwd(surface),
        "shell": shell_label(),
        "selection": (
            selection.to_dict()
            if surface == "jarvis" and (selection := svc.store.chat_selection()) is not None
            else None
        ),
    }


def _catalog_rows(
    surface: str, live_models: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """The provider rows for ``surface`` with this machine's runner facts."""
    from jarvis.agent_chat import agent_provider_prefs

    # Every surface's rows carry the models hidden on the API Keys page, and
    # every model picker leaves them out. The on/off switch is the agents'
    # own: other surfaces keep every seat.
    prefs = agent_provider_prefs.load()
    agents = surface == agent_provider_prefs.AGENT_SURFACE
    rows: list[dict[str, Any]] = []
    for row in rows_for(surface):
        d = row.to_dict()
        runner = resolve_runner(row.id, surface=surface)
        d["runner"] = runner
        d["cli_installed"] = _cli_installed(runner) if runner not in ("api", "brain") else None
        # A CLI that publishes its own model list (agy, Codex) overrides the
        # curated fallback with what THIS account can actually pick.
        if d["cli_installed"] and runner in live_models:
            d["curated_models"] = live_models[runner]
        # The dual row: Claude Code takes its own ids and aliases; with only
        # an API key — and on a surface with no CLI seats, always — the
        # Anthropic catalog route lists the models live.
        if row.id == "claude-api":
            if runner == "claude-cli":
                d["curated_models"] = [m.to_dict() for m in claude_code_models()]
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
        d["typeahead"] = list(typeahead.triggers_for(runner, surface))
        if agents:
            d["enabled"] = prefs.enabled(row.id)
        d["hidden_models"] = list(prefs.hidden(row.id))
        rows.append(d)
    return rows


# ----------------------------------------------------------- typeahead


@router.get("/tools", openapi_extra={"x-jarvis-readonly": True})
async def get_composer_tools(
    request: Request,
    provider: str = "",
    model: str = "",
    q: str = Query("", max_length=200),
    category: str = "",
    cwd: str | None = None,
    stance: str = "ask",
) -> dict[str, Any]:
    """Discover tools for the Jarvis chat without executing any capability."""
    from jarvis.agent_chat.tool_catalog import discover

    svc = (await _async_service(request))
    if provider and not any(row.id == provider for row in rows_for("jarvis")):
        raise HTTPException(status_code=400, detail="Unknown chat provider")
    pending = asyncio.create_task(
        discover(
            provider=provider,
            model=model,
            query=q,
            category=category,
            cwd=_validate_cwd(cwd) or svc.default_cwd("jarvis"),
            stance=stance,
        )
    )
    try:
        while not pending.done():
            await asyncio.wait({pending}, timeout=0.1)
            if await request.is_disconnected():
                pending.cancel()
                raise HTTPException(status_code=499, detail="Search cancelled")
        return await pending
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@router.get("/typeahead", openapi_extra={"x-jarvis-readonly": True})
async def get_typeahead(
    request: Request,
    trigger: str = Query(..., min_length=1, max_length=1),
    surface: SurfaceName = "agent",
    provider: str = "",
    cwd: str | None = None,
    q: str = Query("", max_length=200),
    limit: int = Query(40, ge=1, le=200),
    session_id: str = "",
) -> dict[str, Any]:
    """What the composer lists after ``/``, ``@`` or ``$`` on one seat.

    The seat is (surface, provider) resolved to its runner exactly as a turn
    would be; the folder is the chat's. Rows are read from the disk that
    runner reads (``jarvis.agent_chat.typeahead``) — the account's skills,
    commands and plugins, the folder's own, or the files under it — and a
    trigger the seat does not honour answers with an empty list.
    """
    svc = (await _async_service(request))
    runner = resolve_runner(provider, surface=surface) if provider else "api"
    folder = _validate_cwd(cwd) or svc.default_cwd(surface)
    # A society chat completes its own teammates, capabilities and learned
    # skills, so the list needs to know WHICH agent is typing.
    agent_id = ""
    if surface == "society" and session_id:
        from jarvis.society.surface import agent_id_of

        agent_id = agent_id_of(session_id) or ""
    return await asyncio.to_thread(
        typeahead.suggest,
        runner=runner,
        cwd=folder,
        trigger=trigger,
        query=q,
        limit=limit,
        surface=surface,
        agent_id=agent_id,
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


#: How long one sweep stands. The sweep sends nothing to any provider (it
#: reads key presence, CLI login state and the passive record of real calls),
#: but the CLI login reads are not free, so a sweep is reused for a while — and
#: dropped the moment the health record changes (see ``get_provider_health``).
_HEALTH_TTL_S: Final[float] = 300.0
#: The whole sweep's ceiling. A row whose (local) check has not finished when
#: this runs out is reported ``unknown`` and draws no dot.
_HEALTH_SWEEP_S: Final[float] = 20.0

#: surface -> (checked_at, (health-record version, credential generation), rows).
#: Process-local, like every other short cache in the routes; a restart simply
#: re-sweeps. Keyed on both counters so a new real-call outcome AND any key
#: saved or deleted — through whichever path — drop the sweep at once.
_health_cache: dict[str, tuple[float, tuple[int, int], list[ProviderHealthRow]]] = {}

#: Vendor CLI runners. Their credential is a subscription login, not a key,
#: so the API-Keys one-token probe is the wrong check: the dual Claude row
#: is catalogued as ``claude-api``, and that probe hits Anthropic's endpoint
#: and paints "Key rejected" on a Claude Code seat that is signed in.
_CLI_RUNNERS: Final[frozenset[str]] = runner_cli.CLI_RUNNERS


def _cli_auth_status(runner: str) -> Any | None:
    """The vendor CLI's own login snapshot. ``None`` if ``runner`` is not a CLI
    this app can read the login of (OpenCode, Kimi and the DeepSeek harness
    keep theirs where no reader has been verified — see the registry)."""
    if runner == "glm-cli":
        # GLM Coding Plan is Claude Code plus a Z.ai key: the key IS the login.
        from types import SimpleNamespace

        from jarvis.workspace.agents import glm_spawn_env

        configured = glm_spawn_env() is not None
        return SimpleNamespace(
            connected=configured,
            mode="subscription",
            message="GLM Coding Plan: Z.ai key saved"
            if configured
            else "GLM Coding Plan: no Z.ai key — add one under API Keys",
        )
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
        # The CLI keeps its own login and this app has no verified reader for
        # it: no dot rather than a guessed one (the turn itself says if the
        # CLI is signed out).
        return "unknown", "self_managed", f"{runner}: keeps its own login"
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
        health = await provider_health(cfg, provider_id)
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

    API / brain seats report what the API-Keys screen's tab dots report
    (``provider_routes.provider_health``): key presence plus the outcome of
    that provider's last REAL call. Nothing here sends a request — until
    2026-09-30 every open of the chat spent a paid one-token completion per
    keyed provider. A seat never used since its key was saved is ``unknown``
    and draws no dot. A vendor CLI seat spends a subscription login, so this
    reports that login instead. The dual Claude row is why the split exists —
    its catalog id is ``claude-api``, and the API-key verdict would otherwise
    paint "Key rejected" on a Claude Code seat that is signed in.

    Nothing waits for this: the composer paints from the catalog and folds
    these in when they land. A row that does not finish inside the sweep
    ceiling comes back ``unknown`` and is drawn exactly as it was before.
    """
    (await _async_service(request))  # 503 like every other route when the chat is off
    from jarvis.brain.provider_health_ledger import ledger_version
    from jarvis.core.config import secret_generation

    now = time.monotonic()
    version = (ledger_version(), secret_generation())
    cached = _health_cache.get(surface)
    if (
        cached
        and not refresh
        and cached[1] == version
        and (now - cached[0]) < _HEALTH_TTL_S
    ):
        return ProviderHealthResponse(providers=cached[2], checked_at=cached[0], cached=True)

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
    _health_cache[surface] = (now, version, rows)
    return ProviderHealthResponse(providers=rows, checked_at=now, cached=False)


# ------------------------------------------------------------------ sessions


@router.get("/sessions")
def list_sessions(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    surface: SurfaceName | None = None,
) -> dict[str, Any]:
    svc = _service(request)
    out = []
    for s in svc.store.list_sessions(limit=limit, surface=surface):
        if surface is None and s.surface in HIDDEN_SURFACES:
            continue
        d = s.to_dict()
        d["running"] = svc.is_running(s.session_id)
        if s.surface == "agent":
            d["pending_approvals"] = svc.pending_approvals(s.session_id)
            d["cli_title"] = _cli_title(svc, s)
        out.append(d)
    _title_chats(request, svc, out)
    return {"sessions": out}


def _cli_title(svc: Any, session: Any) -> str:
    """The name the coding CLI gave a thread's conversation itself, or "".

    Claude Code and Codex title their own sessions on their own subscription;
    a thread shows that name the way a terminal pane does. A title the person
    typed wins, so the CLI's is only offered while the stored one is still
    the first message's.
    """
    if not session.vendor_session:
        return ""
    from jarvis.agent_chat.catalog import provider_row
    from jarvis.agentic_ide import cli_title

    row = provider_row(session.provider)
    agent = row.agent if row is not None else ""
    if agent not in ("claude", "codex"):
        return ""
    title = cli_title.session_title(agent, session.vendor_session, session.account_id)
    if not title or not svc.store.title_is_automatic(session):
        return ""
    return title


def _title_chats(request: Request, svc: Any, rows: list[dict[str, Any]]) -> None:
    """Give the Jarvis chats and the IDE's threads a topic title, not their first words.

    The ``jarvis`` surface — the front page's own history — and the ``agent``
    surface — the IDE's threads — are retitled. A thread whose coding CLI named
    the conversation itself keeps that name (``cli_title``); Claude Code in
    print mode never writes one, so most threads are named here. A title the
    user typed is recognised by the titler and kept.
    """
    from jarvis.agent_chat.store import _title_from
    from jarvis.sessions import chat_titles

    now = int(time.time() * 1000)
    requests: list[chat_titles.TitleRequest] = []
    for row in rows:
        surface = row.get("surface")
        if not (surface == "jarvis" or (surface == "agent" and not row.get("cli_title"))):
            continue
        sid = str(row["session_id"])

        def load(sid: str = sid) -> list[tuple[str, str]]:
            texts: list[tuple[str, str]] = []
            for event in svc.store.list_events(sid):
                text = str((event.get("payload") or {}).get("text") or "")
                if event["kind"] == "user_message":
                    texts.append(("user", text))
                elif event["kind"] == "agent_message":
                    texts.append(("agent", text))
                elif event["kind"] == "assistant_text":
                    texts.append(("assistant", text))
            return texts

        updated = int(row.get("updated_ms") or 0)
        requests.append(chat_titles.TitleRequest(
            kind=chat_titles.KIND_TYPED,
            conv_id=sid,
            version=str(int(row.get("message_count") or 0)),
            message_count=int(row.get("message_count") or 0),
            updated_ms=updated,
            settled=not row.get("running")
            and now - updated >= chat_titles.TYPED_SETTLE_S * 1000,
            seed=str(row.get("title") or ""),
            loader=load,
            auto_title=_title_from,
        ))
    if not requests:
        return
    try:
        titles = chat_titles.titler_for_state(request.app.state).titles_for(requests)
    except Exception:  # noqa: BLE001 - the stored title is a fine answer; logged
        log.warning("Jarvis chat titles unavailable, keeping the stored ones", exc_info=True)
        return
    for row in rows:
        key = (chat_titles.KIND_TYPED, str(row["session_id"]))
        # A Jarvis chat with no topic shows as what it is ("Voice chat · 09:42");
        # a thread keeps its first message rather than an empty row.
        if key in titles and (titles[key] or row.get("surface") == "jarvis"):
            row["title"] = titles[key]


@router.post("/sessions", status_code=201)
def create_session(body: CreateSessionBody, request: Request) -> dict[str, Any]:
    svc = _service(request)
    if body.account_id:
        from jarvis import agent_accounts
        from jarvis.agent_chat.catalog import provider_row

        account = agent_accounts.resolve(body.account_id)
        row = provider_row(body.provider)
        if account is None or row is None or account.platform != row.agent:
            raise HTTPException(400, "This subscription account does not belong to the provider")
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
            account_id=body.account_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if session.surface == "jarvis":
        from jarvis.agent_chat.store import ChatSelection

        svc.store.save_chat_selection(
            ChatSelection(session.provider, session.model, session.effort, session.account_id)
        )
    d = session.to_dict()
    d["running"] = False
    return d


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
    tail: int | None = Query(
        None, ge=1, le=500, description="Only the newest N events (e.g. a live preview)."
    ),
) -> dict[str, Any]:
    svc = _service(request)
    session = svc.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session.surface in ("jarvis", "society"):
        from jarvis.agent_chat.send_queue import close_orphans

        await close_orphans(svc, session_id)
    d = session.to_dict()
    d["running"] = svc.is_running(session_id)
    events = await asyncio.to_thread(svc.store.list_events, session_id, tail=tail)
    return {"session": d, "events": events}


@router.get(
    "/sessions/{session_id}/subagents",
    summary="The sub-agents a coding agent spawned, read from the CLI's own session files",
    openapi_extra={"x-jarvis-readonly": True},
)
async def list_subagents(session_id: str, request: Request) -> dict[str, Any]:
    """A thread's sub-agents whose steps the CLI does not stream (Codex).

    Claude Code streams its sub-agents into the thread itself; Codex files each
    one as a rollout of its own. ``agents`` is empty for every other CLI.
    """
    from jarvis.agent_chat.subagent_transcripts import codex_homes, codex_subagents

    svc = (await _async_service(request))
    session = svc.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    runner = resolve_runner(session.provider, surface=session.surface, runtime=session.runtime)
    if runner != "codex-cli" or not session.vendor_session:
        return {"agents": []}
    parent, since, account = session.vendor_session, session.created_ms, session.account_id
    # Reading rollouts is file work: off the event loop.
    agents = await asyncio.to_thread(
        lambda: codex_subagents(parent, since_ms=since, homes=codex_homes(account or None))
    )
    return {"agents": [agent.to_dict() for agent in agents]}


@router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str, body: PatchSessionBody, request: Request
) -> dict[str, Any]:
    svc = (await _async_service(request))
    existing = svc.store.get_session(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="session not found")
    if existing.surface == "society" and ":routine:" in session_id:
        raise HTTPException(status_code=403, detail="Routine chat is owned by its schedule")
    if existing.surface == "society" and svc.is_running(session_id):
        raise HTTPException(status_code=409, detail="Agent chat is working")
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
        if picked != existing.provider:
            fields["account_id"] = ""
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
    runner = resolve_runner(
        fields.get("provider") or current.provider,
        surface=current.surface,
        runtime=str(getattr(current, "runtime", "") or ""),
    )
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
        if current.surface == "society" and not society_mode_supported(
            runner, body.permission_mode
        ):
            raise HTTPException(
                status_code=422,
                detail=f"{runner} cannot provide an actionable approval for {body.permission_mode}",
            )
        fields["permission_mode"] = body.permission_mode
    elif "provider" in fields:
        # A provider change folds the old mode onto the new runner's ladder
        # (a no-op on the Jarvis surface, whose ladder is the same for all).
        fields["permission_mode"] = normalize_permission(ladder, current.permission_mode)
    if any(key in fields for key in ("provider", "model", "permission_mode", "account_id")):
        if fields.get("permission_mode") == "plan" and current.permission_mode not in (
            "plan",
            "read-only",
        ):
            control = svc.controls.state(session_id)
            control.previous_permission = current.permission_mode
            svc.controls.store.save(control)
        control = svc.controls.state(session_id)
        if (
            control.goal
            and control.goal.status == "active"
            or fields.get("permission_mode") == "plan"
        ):
            await svc.controls.pause(session_id, "Model or permission settings changed")
        if "provider" in fields or "account_id" in fields:
            await svc.controls._clear_saved_native(session_id)
    if current.surface == "society" and svc.is_running(session_id):
        raise HTTPException(status_code=409, detail="Agent chat is working")
    session = svc.store.update_session(session_id, **fields)
    assert session is not None
    if session.surface == "jarvis" and {"provider", "model", "effort"}.intersection(fields):
        from jarvis.agent_chat.store import ChatSelection

        svc.store.save_chat_selection(
            ChatSelection(session.provider, session.model, session.effort, session.account_id)
        )
    if current.surface == "society" and body.permission_mode is not None:
        svc.store.set_permission_override(session_id, session.permission_mode)
    if current.surface == "society":
        binder = getattr(svc, "bind_society_session", None)
        if binder is not None:
            session = await binder(session_id)
            if body.permission_mode is not None:
                # Persist the effective choice, not a requested escalation that
                # the roster narrowed during binding.
                svc.store.set_permission_override(session_id, session.permission_mode)
    changed = {key: getattr(session, key) for key in fields if key != "vendor_session"}
    if changed:
        await svc._emit(session_id, make_event("session_updated", changed))  # noqa: SLF001 — same package boundary
    d = session.to_dict()
    d["running"] = svc.is_running(session_id)
    return d


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, Any]:
    svc = (await _async_service(request))
    if svc.store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    await svc.controls.pause(session_id, "Session deleted")
    await svc.controls._clear_saved_native(session_id)
    await svc.discard_credential_requests(session_id)
    if not svc.store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    svc.controls.store.delete(session_id)
    return {"ok": True, "session_id": session_id}


# ------------------------------------------------------------------ turns


@router.post("/sessions/{session_id}/messages", status_code=202)
async def post_message(session_id: str, body: MessageBody, request: Request) -> dict[str, Any]:
    from jarvis.tasks.calendar import calendar_zone
    from jarvis.tasks.context import client_timezone

    if body.timezone is not None:
        try:
            calendar_zone(body.timezone)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    from jarvis.agent_chat.send_queue import QueueFull, send_or_queue

    svc = (await _async_service(request))
    token = client_timezone.set(body.timezone)
    try:
        # A created agent's chat queues a message behind its running turn
        # instead of refusing it; every other chat answers 409 as before.
        turn_id, queue_id = await send_or_queue(
            svc, session_id, body.text, body.attachments, tool_choices=body.tool_choices
        )
    except NoSuchSession as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except QueueFull as exc:
        raise HTTPException(status_code=409, detail="too many messages are waiting") from exc
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail="a turn is already running") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        client_timezone.reset(token)
    if queue_id:
        return {"turn_id": "", "session_id": session_id, "queued": True, "queue_id": queue_id}
    return {"turn_id": turn_id, "session_id": session_id}


@router.post(
    "/sessions/{session_id}/cancel",
    summary="Stop the turn this chat session is running",
    # Cancelling throws away work in flight: the runner is interrupted mid-turn
    # and whatever it had not yet written is lost. The CLI safety gate and the
    # Command Registry must treat it as destructive and ask before firing.
    openapi_extra={"x-jarvis-dangerous": True},
)
async def cancel_turn(session_id: str, request: Request) -> dict[str, Any]:
    svc = (await _async_service(request))
    if svc.store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    session = svc.store.get_session(session_id)
    cancelled = svc.is_running(session_id)
    # Who ended a turn is otherwise invisible: name the control that asked.
    headers = request.headers
    log.info(
        "agent chat %s: stop requested over HTTP (via=%s, running=%s, fetch-site=%s)",
        session_id,
        (headers.get("x-jarvis-stop-via") or "unnamed")[:40],
        cancelled,
        (headers.get("sec-fetch-site") or "none")[:20],
    )
    if session.surface in ("jarvis", "society"):
        await svc.controls.pause(session_id, "Stopped by the user")
    else:
        cancelled = await svc.cancel(session_id)
    # The runner can already be gone while the transcript still shows Working.
    # Closing that turn is what makes the stop button leave the chat.
    if not svc.is_running(session_id):
        cancelled = await svc.seal_stopped_turn(session_id) or cancelled
    return {"cancelled": cancelled, "session_id": session_id}


@router.post("/sessions/{session_id}/approvals/{approval_id}")
async def resolve_approval(
    session_id: str, approval_id: str, body: ApprovalBody, request: Request
) -> dict[str, Any]:
    svc = (await _async_service(request))
    if body.decision not in DECISIONS:
        raise HTTPException(status_code=400, detail=f"decision must be one of {list(DECISIONS)}")
    ok = svc.resolve_approval(session_id, approval_id, body.decision)
    if not ok:
        if await svc.close_stale_approval(session_id, approval_id):
            # The card outlived what it asked for (an app restart, a second
            # card for the same call); it is closed now and nothing ran.
            raise HTTPException(
                status_code=410,
                detail=(
                    "This request expired and nothing ran; "
                    "the agent asks again if it still needs it."
                ),
            )
        raise HTTPException(status_code=404, detail="no such pending approval")
    return {"ok": True, "approval_id": approval_id, "decision": body.decision}


@router.post(
    "/sessions/{session_id}/questions/{question_id}",
    summary="Answer an agent's multiple-choice question",
)
async def answer_question(
    session_id: str, question_id: str, body: QuestionAnswerBody, request: Request
) -> dict[str, Any]:
    svc = (await _async_service(request))
    try:
        ok = svc.resolve_question(
            session_id,
            question_id,
            index=body.index,
            option_index=body.option_index,
            text=body.text,
        )
        if not ok:
            # Not a card a running turn waits on: an end-of-turn card, whose
            # answers go to the agent as the next message.
            ok = await svc.answer_turn_question(
                session_id,
                question_id,
                index=body.index,
                option_index=body.option_index,
                text=body.text,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail="session is busy") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="no such open question")
    return {"ok": True, "question_id": question_id}


@router.post(
    "/sessions/{session_id}/questions/{question_id}/skip",
    summary="Close an agent's question card and let its recommendations apply",
)
async def skip_question(session_id: str, question_id: str, request: Request) -> dict[str, Any]:
    svc = (await _async_service(request))
    try:
        ok = svc.skip_question(session_id, question_id) or await svc.skip_turn_question(
            session_id, question_id
        )
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail="session is busy") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="no such open question")
    return {"ok": True, "question_id": question_id}


@router.post(
    "/sessions/{session_id}/credentials/{request_id}",
    summary="Save the secret an agent asked for in its secure credential field",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def submit_credential(
    session_id: str, request_id: str, body: CredentialBody, request: Request
) -> dict[str, Any]:
    svc = _service(request)
    if not isinstance(body.value, str):
        raise HTTPException(status_code=400, detail={
            "code": "invalid_token", "message": "Enter a credential as text before saving.",
        })
    try:
        ok = await svc.submit_credential(session_id, request_id, body.value)
    except CredentialRequestBusy:
        raise HTTPException(status_code=409, detail={
            "code": "request_busy",
            "message": "The credential is still being checked. Please wait.",
        }) from None
    except CredentialError as exc:
        status = {
            "network_error": 503, "validation_timeout": 504, "storage_failed": 503,
        }.get(exc.code, 400)
        raise HTTPException(status_code=status, detail={
            "code": exc.code, "message": str(exc),
        }) from None
    except Exception:
        # A third-party storage exception may include the token. Never emit
        # its message or traceback into logs, HTTP bodies or the agent timeline.
        log.warning("agent chat: credential submission failed for %s", session_id)
        raise HTTPException(status_code=503, detail={
            "code": "storage_failed", "message": "The credential could not be stored. Try again.",
        }) from None
    if not ok:
        raise HTTPException(status_code=410, detail={
            "code": "request_closed", "message": "This credential field is already closed.",
        })
    return {"ok": True, "request_id": request_id, "status": "saved"}


@router.post(
    "/sessions/{session_id}/credentials/{request_id}/decline",
    summary="Close an agent's credential field without providing the secret",
)
async def decline_credential(session_id: str, request_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    try:
        ok = await svc.decline_credential(session_id, request_id)
    except CredentialRequestBusy:
        raise HTTPException(status_code=409, detail={
            "code": "request_busy",
            "message": "The credential is still being checked. Please wait.",
        }) from None
    if not ok:
        raise HTTPException(status_code=410, detail={
            "code": "request_closed", "message": "This credential field is already closed.",
        })
    return {"ok": True, "request_id": request_id, "status": "declined"}


@router.post(
    "/sessions/{session_id}/plan",
    summary="Answer a coding agent's plan card: build it, or keep planning",
)
async def resolve_plan(session_id: str, body: PlanBody, request: Request) -> dict[str, Any]:
    svc = (await _async_service(request))
    try:
        ok = await svc.resolve_turn_plan(session_id, body.turn_id, body.decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail="session is busy") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="no such open plan")
    return {"ok": True, "turn_id": body.turn_id, "decision": body.decision}


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
    svc = (await _async_service(request))
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

    # The spooled upload file itself, not its bytes: a screen recording streams
    # to disk instead of being read into memory whole.
    uploads = [(upload.filename or "file", upload.file) for upload in files or []]

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


@router.get(
    "/attachments/file",
    summary="Show one attached picture or video in the composer",
    openapi_extra={"x-jarvis-readonly": True},
)
async def attachment_file(cwd: str, reference: str) -> Any:
    """Stream an attached image or video back for the composer's thumbnail.

    A file dragged in by path (the Appshots gallery, Explorer inside the
    desktop shell) never passes its bytes through the window, so the composer
    has nothing to draw. ``reference`` is the attachment's own reference from
    :func:`attach_files`, ``cwd`` the folder that call answered with. Only a
    picture or video that resolves INSIDE that folder is served — symlinks
    included — and never SVG, which can carry script.
    """
    from fastapi.responses import FileResponse

    from jarvis.agent_chat.media import MEDIA_TYPES
    from jarvis.agentic_ide import drops

    folder = _validate_cwd(cwd)
    relative = drops.dereference(reference)
    if not folder or not relative:
        raise HTTPException(status_code=404, detail="attachment not found")

    def _resolve() -> Path | None:
        inside = drops.within_workspace(str(Path(folder) / relative), folder)
        if inside is None:
            return None
        target = Path(folder) / inside
        return target if target.is_file() else None

    target = await asyncio.to_thread(_resolve)
    mime = MEDIA_TYPES.get(target.suffix.lower(), "") if target else ""
    if target is None or not mime.startswith(("image/", "video/")) or mime == "image/svg+xml":
        raise HTTPException(status_code=404, detail="attachment not found")
    return FileResponse(
        target,
        media_type=mime,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


# ------------------------------------------------------------------ folders


@router.post("/pick-folder")
async def pick_folder(body: PickFolderBody, request: Request) -> dict[str, Any]:
    """Open the system folder dialog (desktop only) and return the choice."""
    (await _async_service(request))
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
    svc = (await asyncio.to_thread(_ws_service, ws))
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
        if session.surface in ("jarvis", "society"):
            from jarvis.agent_chat.send_queue import close_orphans

            await close_orphans(svc, session_id)
        events = svc.store.list_events(session_id, after_seq=after)
        d = session.to_dict()
        d["running"] = svc.is_running(session_id)
        d["pending_approvals"] = svc.pending_approvals(session_id)
        d["pending_questions"] = svc.pending_questions(session_id)
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
