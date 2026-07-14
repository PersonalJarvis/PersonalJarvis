"""FastAPI + WebSocket server for the desktop UI (Phase 1a).

Responsibilities:
- REST endpoints for health, config read-only, plugin discovery, debug.
- WebSocket `/ws` with welcome frame, bus forwarding, input validation.
- Optional static mount for the React build under `dist/` (production mode).

Explicitly NOT here:
- Channel-adapter logic (in `jarvis/channels/web.py`).
- The React build itself (Agent 5).
- Single-instance focus logic (only a placeholder endpoint).
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import ValidationError

from jarvis import __version__
from jarvis.core.bus import EventBus, get_default_bus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import (
    AnnouncementRequested,
    ErrorOccurred,
    Event,
    MessageSent,
    SystemStarted,
    TerminalClosed,
    TerminalCommandExecuted,
    TerminalOutput,
    TerminalSpawned,
    VoiceBootStatus,
)
from jarvis.core.registry import list_all_plugins
from jarvis.terminal import PtyManager, discover_shells, get_shell

from .schema import (
    WSCommand,
    WSMessageIn,
    WSWelcome,
    event_to_ws_envelope,
)

if TYPE_CHECKING:
    import uvicorn

WEB_DIR = Path(__file__).resolve().parent
DIST_DIR = WEB_DIR / "dist"
INDEX_FILE = DIST_DIR / "index.html"
ASSETS_DIR = DIST_DIR / "assets"

# Max time a single WS push to one browser client may take. A healthy
# localhost ``send_json`` is sub-50 ms; exceeding this means the client is
# stalled or half-open (TCP not yet torn down, send buffer full). Because the
# WS forwarder is a bus *wildcard* subscriber, an unbounded send would freeze
# the whole event bus and stall the voice / Computer-Use dispatch paths
# (BUG-CU-STALL, AP-18). On timeout we drop the client so the bus is not
# re-throttled every event; the browser reconnects on its own.
_WS_SEND_TIMEOUT_S = 3.0


class WebServer:
    """In-process uvicorn + FastAPI, run by the orchestrator loop."""

    def __init__(self, cfg: JarvisConfig, bus: EventBus | None = None) -> None:
        self.cfg = cfg
        self.bus = bus if bus is not None else get_default_bus()
        self._clients: dict[str, WebSocket] = {}
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        # PTY manager for the desktop-app terminal view. Sessions are
        # global per server instance — they survive WS reconnects, but
        # not a server shutdown (see stop()).
        self._pty = PtyManager()
        # Per-terminal line buffer for audit events.
        self._pty_input_buffers: dict[str, str] = {}
        self._pty_shell_ids: dict[str, str] = {}
        # Skill registry: watched on user_skills_dir() after first-run bootstrap.
        # The watcher starts in ``start()`` once the event loop is running.
        self._skill_registry: Any | None = None
        # Doc registry: watched on default_doc_roots(); FTS5 index under
        # docs_index_db_path(). Watcher also starts in ``start()``.
        self._doc_registry: Any | None = None
        self._cli_registry: Any | None = None
        self._plugin_registry: Any | None = None
        # Marketplace OAuth refresh belongs to the shared WebServer lifecycle,
        # not to one launcher. The actual scheduler is created with call_soon
        # at the end of start(), after the serving/readiness path has returned;
        # the pending handle also makes repeated start() calls idempotent.
        self._refresh_scheduler: Any | None = None
        self._refresh_scheduler_start_handle: asyncio.Handle | None = None
        self._refresh_registry_tasks: set[asyncio.Task[Any]] = set()
        self._refresh_scheduler_stopping = False
        # Board stack is populated in _setup_board() (in the _build_app path).
        self._board_aggregator: Any | None = None
        self._board_aggregator_task: asyncio.Task[None] | None = None
        self._board_evaluator: Any | None = None
        self._bio_scheduler: Any | None = None
        self._bio_generator: Any | None = None
        # Voice-session recorder — runs alongside the EventBus, populated in
        # _init_session_stack(). None while the recorder is disabled.
        self._session_recorder: Any | None = None
        # Phase-5 task stack (tasks view). _init_task_stack() populates
        # all three; without wiring /api/tasks returns 503 (see BUG-007).
        self._task_store: Any | None = None
        self._task_scheduler: Any | None = None
        self._task_runner: Any | None = None
        self._task_scheduler_task: asyncio.Task[None] | None = None
        self._task_cancel_token: Any | None = None
        # Phase B5 wiki write-wiring handle — shutdown() called in stop().
        self._wiki_integration_handle: Any | None = None
        # Phase B3 wiki live-reload watchdog handle — shutdown() called in stop().
        self._wiki_watcher: Any | None = None
        self._channel_chat_bridge: Any | None = None
        # Pre-Thinking-Ack (Flash-Brain) bridge: translate AnnouncementRequested
        # events with kind="preamble" into MessageSent(role="preamble") so the
        # chat view in the desktop UI can render them as muted pre-ack bubbles.
        # Other kinds (completion / info / None — used by MissionAnnouncer and
        # other producers) flow through the existing wildcard WS forward
        # unchanged. See docs/superpowers/specs/2026-05-11-pre-thinking-ack-
        # flash-brain-design.md §4.
        self.bus.subscribe(AnnouncementRequested, self._forward_preamble_to_chat)
        from jarvis.missions.tool_approvals import MissionToolApprovalCoordinator

        self._mission_tool_approvals = MissionToolApprovalCoordinator(self.bus)
        self.app: FastAPI = self._build_app()
        self.app.state.refresh_scheduler = None

    async def _forward_preamble_to_chat(self, event: AnnouncementRequested) -> None:
        """Bridge AnnouncementRequested(kind="preamble") → MessageSent.

        The chat WebSocket's wildcard forward already ships every bus event
        to the frontend, but the chat history view only renders MessageSent
        envelopes. Flash-Brain pre-ack output is published as
        AnnouncementRequested(kind="preamble") so the TTS pipeline picks it
        up; this handler additionally publishes a MessageSent(role="preamble",
        text=event.text) so the chat history shows the same line as a muted
        pre-ack bubble.

        Other kinds (completion / info / None) are intentionally untouched —
        MissionAnnouncer and other producers rely on the existing flow.
        """
        if event.kind != "preamble":
            return
        text = (event.text or "").strip()
        if not text:
            return
        await self.bus.publish(
            MessageSent(
                thread_id="preamble",
                role="preamble",
                text=text,
                source_layer="ui.web.preamble_bridge",
            )
        )

    # ------------------------------------------------------------------
    # App-Factory
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(
            title="Personal Jarvis — Admin/UI API",
            version=__version__,
            # Swagger UI on ``/api/_swagger`` — the semantic ``/api/docs``
            # path belongs to the doc-browser router (see docs_routes.py).
            docs_url="/api/_swagger",
            openapi_url="/api/openapi.json",
        )

        # CORS only for the Vite dev server — production serves the frontend
        # from dist/ and needs no cross-origin requests.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[self.cfg.ui.vite_dev_url],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self._register_rest_routes(app)
        self._register_ws_route(app)

        # Registries constructed here defer their blocking disk scan
        # (``reload_sync`` — globbing + FTS indexing, ~hundreds of ms) off the
        # boot critical path. The scans run after uvicorn is listening (see
        # ``start()``); nothing at BOOT_READY needs the skill/doc lists (the
        # brain builds its own skill context; the Skills/Docs views tolerate an
        # empty registry until the reload lands). Each entry is (label, registry).
        self._pending_reloads: list[tuple[str, Any]] = []

        # Skill-registry setup: bootstrap (copy builtin skills) + create the
        # registry. reload_sync() is deferred (see _pending_reloads). The
        # watchdog watcher is only activated in ``start()``.
        self._setup_skill_registry(app)

        # Doc registry: Markdown discovery under ``docs/`` + siblings,
        # FTS5 index. The watchdog is likewise only activated in ``start()``.
        self._setup_doc_registry(app)

        # CLI-tool registry — holds the catalog + prober + auth + usage log in
        # the same state object shared by the REST routes and the brain launcher.
        self._setup_cli_registry(app)

        # Plugin-Tool-Registry — wired marketplace plugins as live brain tools.
        self._setup_plugin_registry(app)

        # Sub-agent registry (dashboard feature) — subscribes to the bus immediately.
        try:
            from jarvis.agents import JarvisAgentRegistry

            sub_agent_registry = JarvisAgentRegistry(bus=self.bus)
            sub_agent_registry.attach()
            app.state.sub_agent_registry = sub_agent_registry
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("JarvisAgentRegistry setup failed")
            app.state.sub_agent_registry = None

        # Wire in the MCP, tool, provider, profile, task, skills, CLI, and
        # sub-agents routes — lazy import avoids cycles.
        from jarvis.runs.routes import router as runs_router
        from jarvis.runs.runs_ws import router as runs_ws_router

        from .antigravity_routes import router as antigravity_router
        from .board_routes import (
            board_router as board_meta_router,
        )
        from .board_routes import (
            router as board_router,
        )
        from .chats_routes import router as chats_router
        from .claude_routes import router as claude_router
        from .cli_routes import router as cli_router
        from .commands_routes import router as commands_router
        from .contacts_routes import router as contacts_router
        from .control_routes import router as control_router
        from .diagnostics_routes import router as diagnostics_router
        from .dictionary_routes import router as dictionary_router
        from .docs_routes import router as docs_router
        from .downloads_routes import router as downloads_router
        from .drop_routes import router as drop_router
        from .federation_proxy_routes import router as federation_proxy_router
        from .feedback_routes import router as feedback_router
        from .friends_routes import router as friends_router
        from .frontier_routes import router as frontier_router
        from .marketplace_routes import router as marketplace_router
        from .mcp_routes import router as mcp_router
        from .missions_auth import router as missions_auth_router
        from .missions_pty_routes import router as missions_pty_router
        from .missions_routes import router as missions_router
        from .missions_ws_routes import (
            ConnectionManager as _MissionsConnMgr,
        )
        from .missions_ws_routes import (
            router as missions_ws_router,
        )
        from .onboarding_routes import router as onboarding_router
        from .outputs_routes import router as outputs_router
        from .preview_routes import router as preview_router
        from .profile_routes import router as profile_router
        from .provider_routes import router as provider_router
        from .review_routes import router as review_router
        from .self_mod_routes import router as self_mod_router
        from .sessions_routes import router as sessions_router
        from .settings_routes import router as settings_router
        from .setup_routes import router as setup_router
        from .skills_routes import router as skills_router
        from .socials_routes import router as socials_router
        from .sub_agents_routes import router as sub_agents_router
        from .tasks_routes import router as tasks_router
        from .telephony_routes import router as telephony_router
        from .tool_model_routes import router as tool_model_router
        from .tools_routes import router as tools_router
        from .update_routes import router as update_router
        from .wiki_routes import router as wiki_router
        from .wiki_ws import router as wiki_ws_router
        from .workflows_routes import router as workflows_router
        from .workspace_routes import router as workspace_router
        # Conductor is an external package in the same monorepo. Import
        # defensively — anyone who checks out the repo without conductor would
        # otherwise get an ImportError here at server boot.
        try:
            from conductor.api import router as conductor_router
        except ImportError as exc:
            logger.warning("Conductor module not available: {} — Conductor view stays empty", exc)
            conductor_router = None
        app.include_router(mcp_router)
        app.include_router(tools_router)
        app.include_router(tool_model_router)
        # Event-loop diagnostics (read-only) — names the owner of an AP-20
        # cancellation busy-loop from inside the loop; see diagnostics_routes.
        app.include_router(diagnostics_router)
        app.include_router(provider_router)
        app.include_router(antigravity_router)
        app.include_router(claude_router)
        app.include_router(control_router)
        app.include_router(profile_router)
        app.include_router(settings_router)
        # In-app updater (GET status / POST apply). Managed-install only — see
        # jarvis/ui/web/update_routes.py; refuses to self-reset a dev checkout.
        app.include_router(update_router)
        # Frontier auto-switch modal API (GET pending / POST ack) + Self-Mod
        # read/restore API. Both were the last route modules left unmounted; the
        # 404 on /api/frontier/pending silently broke the desktop auto-switch
        # modal too, not just the `jarvis frontier` CLI command.
        app.include_router(frontier_router)
        app.include_router(self_mod_router)
        app.include_router(tasks_router)
        app.include_router(skills_router)
        app.include_router(docs_router)
        app.include_router(cli_router)
        # Command Registry — the one machine-readable catalog of app commands
        # (consumed by the app-command brain tool, the UI, CLI, and docs gen).
        app.include_router(commands_router)
        app.include_router(friends_router)
        app.include_router(marketplace_router)
        app.state.friend_registry = None
        app.state.channel_manager = None
        app.state.channel_chat_bridge = None
        # Twilio telephony voice agent (webhook + media WS + REST status/config).
        # Degrades gracefully when the `twilio` extra is not installed (AD-T8).
        app.include_router(telephony_router)
        from jarvis.telephony.status import TelephonyManager

        app.state.telephony_manager = TelephonyManager()
        # Browser-microphone voice bridge (B2): /ws/audio — the headless/VPS
        # voice path via the browser's own mic/speaker, no sounddevice. Always
        # mounted; gated by [browser_voice].enabled (default on) at connect time.
        from jarvis.browser_voice.route import router as browser_voice_router

        app.include_router(browser_voice_router)
        app.include_router(sub_agents_router)
        app.include_router(outputs_router)
        app.include_router(downloads_router)
        # Socials section — project social-media links (pure file store, no Brain dep).
        app.include_router(socials_router)
        # In-app feedback / bug-report form → Discord webhook.
        app.include_router(feedback_router)
        # "Make It Yours" multi-agent workspace (/api/workspace/*).
        # Provides agent detection, workspace launch planning, and PTY WebSocket
        # for in-app Claude Code / Codex terminals (xterm panes, not OS windows).
        app.include_router(workspace_router)
        # Contacts section — user-curated address book (pure file store, no Brain dep).
        app.include_router(contacts_router)
        app.include_router(dictionary_router)
        app.include_router(workflows_router)
        if conductor_router is not None:
            app.include_router(conductor_router)
        app.include_router(preview_router)
        app.include_router(board_router)
        app.include_router(board_meta_router)
        app.include_router(federation_proxy_router)
        # Phase 8.5 — review-pipeline read-only UI (Plan §6.5).
        app.include_router(review_router)
        # Voice-session transcription view (sidebar -> "Transcription").
        # Returns 503 as long as app.state.session_store isn't set.
        app.include_router(sessions_router)
        # Run Inspector — forensic lens over the same voice sessions. Read-only;
        # 503 until app.state.session_store is set, like sessions_router.
        app.include_router(runs_router)
        app.include_router(runs_ws_router)
        # Chats conversation manager — unified text+voice history, resume +
        # "Speak in this conversation". Reuses chat_store + session_store +
        # brain + speech_pipeline from app.state (graceful 503s when absent).
        app.include_router(chats_router)
        app.include_router(drop_router)
        # Default: no recorder wired up — _init_session_stack() in start()
        # sets this once it succeeds.
        app.state.session_store = None
        # Phase-6 mission stack — the auth token before all others, so the
        # browser can even fetch it; then REST + WS + PTY.
        app.include_router(missions_auth_router)
        # Seed the desktop session token (injected as window.__JARVIS_TOKEN by
        # the fast-boot path) into the token store. The fast-boot token is a RAW
        # secrets.token_urlsafe that is never issued via GET /token, so without
        # this it fails validate_token → 4401 on every token-gated WebSocket —
        # which hung the "Make It Yours" workspace PTY terminals forever on
        # "connecting" (missions survive via the unauthenticated main /ws).
        from .missions_auth import register_session_token_from_env

        register_session_token_from_env(self.cfg.ui.auth_token_env)
        app.include_router(missions_router)
        app.include_router(missions_ws_router)
        app.include_router(missions_pty_router)
        # Phase B9 — Obsidian Setup Wizard (detect install + register vault).
        app.include_router(setup_router)
        # First-time onboarding guide (/api/onboarding/*).
        app.include_router(onboarding_router)
        # Phase B3 — Wiki view (read-only REST API over the Obsidian vault).
        app.include_router(wiki_router)
        # Phase B3 — Desktop wiki view live-reload WS endpoint.
        # Forwards WikiPageChanged events from the shared EventBus to
        # subscribed UI clients. WikiWatcher is started in start().
        app.include_router(wiki_ws_router)
        # ConnectionManager singleton for the global event stream. Attached
        # to MissionBus.subscribe_all() in start().
        app.state.missions_ws_manager = _MissionsConnMgr()
        # MissionManager + Kontrollierer are wired lazily in start()
        # (they need a running event loop for aiosqlite). Default is None,
        # so the REST routes return 503 instead of crashing.
        app.state.mission_manager = None
        app.state.kontrollierer = None
        app.state.mission_tool_approvals = self._mission_tool_approvals

        # Board aggregator (personal-mastery dashboard) — the aggregator is
        # run as a background task in start(); the store is read-only and
        # available immediately.
        self._setup_board(app)

        # Preview registry — subscribed to PreviewServerStarted/Closed events.
        try:
            from jarvis.preview.registry import PreviewRegistry

            preview_registry = PreviewRegistry(bus=self.bus)
            preview_registry.attach()
            app.state.preview_registry = preview_registry
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("PreviewRegistry setup failed")
            app.state.preview_registry = None

        # Make the config available to the routes (e.g. admin-pass check in
        # skills_routes). Other routes will use it too going forward.
        app.state.config = self.cfg
        app.state.bus = self.bus

        # Voice boot-readiness mirror. WS events are one-shot, so a tab that
        # connects after warm-up finished would never see VoiceBootStatus.
        # Persist the latest state on this (long-lived) server instance for
        # GET /api/voice/status to read — deliberately NOT on app.state, whose
        # ASGI lifecycle could outrace the bus subscriber on shutdown.
        #
        # When the local voice stack is disabled (JARVIS_VOICE=0 — headless,
        # VPS, browser-mic-only), there is nothing to warm up and the pipeline
        # never emits VoiceBootStatus, so seed ready=True. Otherwise the
        # frontend's "starting up" banner would hang forever even though the
        # user can already type (and use browser voice). A real voice pipeline
        # starts at ready=False (warmup_start) and flips True via the subscriber
        # below, so this seed only ever sticks when voice is genuinely off.
        _voice_disabled = (
            os.environ.get("JARVIS_VOICE", "").strip().lower() in ("0", "off", "false")
        )
        self._voice_ready = _voice_disabled

        async def _track_voice_ready(event: VoiceBootStatus) -> None:
            # A bus subscriber must never raise (AP-18); setting a plain
            # instance bool cannot fail.
            self._voice_ready = bool(event.ready)

        self.bus.subscribe(VoiceBootStatus, _track_voice_ready)

        self._register_static_or_spa(app)

        # Publish the live app for in-process consumers: the app-command brain
        # tool executes Command-Registry commands through it via ASGI transport
        # (same routes + validation as the UI, no TCP).
        from jarvis.core import runtime_refs

        runtime_refs.set_web_app(app)

        return app

    async def _voice_ready_watchdog(self, deadline_s: float = 45.0) -> None:
        """Guarantee the UI never hangs on "starting up" forever.

        The startup banner + "STARTING…" status flip to listening ONLY when a
        ``VoiceBootStatus(ready=True)`` arrives (mirrored by /api/voice/status).
        The pipeline emits it at the end of warm-up — but if construction crashes
        or an un-timed model download/load wedges warm-up, that event is never
        published and the banner sticks forever even though the user can already
        type (permanent "Getting ready to listen"). This backstop fires once,
        well past a healthy cold boot (voice-usable budget ≤ 20 s, AP-26), and
        force-releases the UI. A genuine ready flips ``_voice_ready`` first, in
        which case this is a silent no-op. ``detail="watchdog_timeout"`` marks the
        signal as a degraded release (voice may be offline until restart), not a
        real "you can speak now".
        """
        try:
            await asyncio.sleep(deadline_s)
        except asyncio.CancelledError:
            return
        if self._voice_ready:
            return
        logger.warning(
            "Voice-ready watchdog fired after %.0fs — the speech pipeline never "
            "signalled ready (a construction crash or a wedged warm-up load). "
            "Force-releasing the UI from 'starting up'; voice may be offline until "
            "restart.",
            deadline_s,
        )
        # Set the endpoint mirror first so /api/voice/status is correct even if
        # the bus publish below fails; the WS event then updates live tabs.
        self._voice_ready = True
        try:
            await self.bus.publish(
                VoiceBootStatus(ready=True, detail="watchdog_timeout")
            )
        except Exception as exc:  # noqa: BLE001 — mirror already set; never crash
            logger.debug("watchdog VoiceBootStatus publish failed: %s", exc)

    def _setup_board(self, app: FastAPI) -> None:
        """Initialize the board store + aggregator + evaluator + BioGenerator.

        The store is read-only and ready immediately (creates an empty DB
        on the first query, so the UI mount doesn't hit a 500). The
        aggregator runs in ``start()`` as a background task; the evaluator
        and the BioScheduler subscribe to the bus there.
        """
        try:
            from jarvis.board.aggregator import BoardAggregator
            from jarvis.board.evaluator import AchievementEvaluator
            from jarvis.board.profile import BioGenerator, BioStore
            from jarvis.board.scheduler import BioScheduler
            from jarvis.board.store import BoardStore
            from jarvis.brain.resolver import resolve_frontier_brain
            from jarvis.core.paths import board_db_path, user_data_dir, user_logs_dir

            db_path = board_db_path()
            jsonl_dir = user_logs_dir()
            jsonl_dir.mkdir(parents=True, exist_ok=True)

            # The board's rich source is the durable voice-session store
            # (sessions.db), not the flight-recorder JSONL (which is empty on
            # most installs). Resolved exactly like bootstrap_sessions does —
            # a bare CWD-relative path would silently starve the board of its
            # source whenever the app starts from a different directory.
            sessions_db_path = self._resolve_sessions_db_path()

            aggregator = BoardAggregator(
                jsonl_dir=jsonl_dir,
                db_path=db_path,
                sessions_db_path=sessions_db_path,
            )
            store = BoardStore(db_path=db_path)
            evaluator = AchievementEvaluator(db_path=db_path, bus=self.bus)
            bio_store = BioStore(db_path=db_path)

            # Optional data-source paths (awareness, missions, self-mod).
            # If the file/DB doesn't exist, the block just silently drops out
            # of the prompt — no error. Paths come from ``user_data_dir()``,
            # not relative strings, so an app restart in a different CWD
            # doesn't starve the generator of its data.
            data_root = user_data_dir() / "data"
            recall_db = data_root / "memory.db"
            missions_db = data_root / "missions.db"
            self_mod_log = data_root / "self_mod.log"

            bio_cfg = self.cfg.board.bio
            cfg = self.cfg

            def _bio_brain_resolver() -> Any:
                # Lazy: capture cfg + bus from the closure, so a later
                # provider switch via the UI takes effect immediately
                # (the resolver invalidates its cache via ConfigReloaded).
                return resolve_frontier_brain(cfg, bus=self.bus)

            bio_generator = BioGenerator(
                brain_resolver=_bio_brain_resolver,
                store=store,
                bio_store=bio_store,
                jsonl_dir=jsonl_dir,
                recall_db_path=recall_db,
                missions_db_path=missions_db,
                self_mod_log_path=self_mod_log,
                temperature=bio_cfg.temperature,
                max_tokens=bio_cfg.max_tokens,
            )
            scheduler = BioScheduler(
                generator=bio_generator,
                db_path=db_path,
                bus=self.bus,
                bio_store=bio_store,
                board_store=store,
                cold_start_min_days=bio_cfg.cold_start_min_days,
            )

            self._board_aggregator = aggregator
            self._board_aggregator_task: asyncio.Task[None] | None = None
            self._board_evaluator = evaluator
            self._bio_scheduler = scheduler
            self._bio_generator = bio_generator
            app.state.board_aggregator = aggregator
            app.state.board_store = store
            app.state.achievement_evaluator = evaluator
            app.state.bio_generator = bio_generator
            app.state.bio_store = bio_store
            logger.info(
                "Board ready (jsonl={}, db={}, achievements={})",
                jsonl_dir, db_path, len(evaluator.list_all()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "Board setup failed — /board returns empty"
            )
            self._board_aggregator = None
            self._board_aggregator_task = None
            self._board_evaluator = None
            self._bio_scheduler = None
            self._bio_generator = None
            app.state.board_aggregator = None
            app.state.board_store = None
            app.state.achievement_evaluator = None
            app.state.bio_generator = None
            app.state.bio_store = None

    def _setup_skill_registry(self, app: FastAPI) -> None:
        """First-run bootstrap + attach the SkillRegistry to ``app.state``.

        Failure cases (e.g. a read-only filesystem in the test runner) are
        not fatal — the UI then shows "No skills" instead of crashing.
        """
        try:
            from jarvis.skills.bootstrap import ensure_user_skills_dir
            from jarvis.skills.prefs import load_state_overrides
            from jarvis.skills.registry import SkillRegistry

            skills_root = ensure_user_skills_dir()
            registry = SkillRegistry(
                root=skills_root,
                bus=self.bus,
                state_prefs_loader=load_state_overrides,
            )
            self._skill_registry = registry
            app.state.skill_registry = registry
            # reload_sync() (disk glob + parse) is deferred off the boot critical
            # path — run after uvicorn is listening (start()).
            self._pending_reloads.append(("SkillRegistry", registry))
            logger.info(
                "SkillRegistry created (scan deferred) from {}", skills_root
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "SkillRegistry setup failed — Skills view stays empty"
            )
            app.state.skill_registry = None

    def _setup_doc_registry(self, app: FastAPI) -> None:
        """Bring up the doc registry + populate the FTS5 index initially.

        Roots = ``default_doc_roots()`` (see ``jarvis/core/paths.py``).
        The index DB lives under ``user_data_dir()/data/docs_index.sqlite``.

        Failure cases (read-only FS, index-DB lock) are not fatal — the UI
        then shows "No docs available" instead of crashing.
        """
        try:
            from jarvis.core.paths import default_doc_roots, docs_index_db_path
            from jarvis.docs.registry import DocRegistry

            roots = default_doc_roots()
            registry = DocRegistry(
                roots=roots,
                index_db=docs_index_db_path(),
                bus=self.bus,
            )
            self._doc_registry = registry
            app.state.doc_registry = registry
            # reload_sync() (glob 200+ docs + FTS index build, ~hundreds of ms)
            # is deferred off the boot critical path — run after uvicorn is
            # listening (start()).
            self._pending_reloads.append(("DocRegistry", registry))
            logger.info(
                "DocRegistry created (scan deferred) for {} roots", len(roots)
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "DocRegistry setup failed — Docs view stays empty"
            )
            app.state.doc_registry = None

    def _setup_cli_registry(self, app: FastAPI) -> None:
        """Set up the ``CliToolRegistry`` and run the catalog probe asynchronously.

        The constructor creates the registry without probing (non-blocking).
        An asyncio task is scheduled in ``start()`` that runs ``bootstrap()`` —
        until then the endpoints return ``status=checking`` for all entries.

        Failure cases: read-only FS or DB lock → no crash, just an empty registry.
        """
        try:
            from jarvis.clis.registry import CliToolRegistry
            from jarvis.clis.shared import set_active_registry

            registry = CliToolRegistry(bus=self.bus)
            self._cli_registry = registry
            app.state.cli_registry = registry
            # Shared state: from here on, CliToolLoader and make_cli_patterns_fn
            # see the same instance — the LLM gets the real connected CLIs as
            # tools, not an empty catalog copy.
            set_active_registry(registry)
            logger.info(
                "CliToolRegistry created ({} catalog entries, bootstrap pending)",
                len(registry.catalog().all()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "CliToolRegistry setup failed — CLIs view stays empty"
            )
            app.state.cli_registry = None

    def _setup_plugin_registry(self, app: FastAPI) -> None:
        """Construct the PluginToolRegistry (non-blocking) and publish it.

        Mirror of _setup_cli_registry: the bootstrap (which opens an MCPClient
        per connected plugin) is scheduled as a background task in start(). The
        shared handle lets the plugin-tools loader + the marketplace routes see
        the SAME instance on the SAME bus.
        """
        try:
            from jarvis.marketplace.plugin_registry import PluginToolRegistry
            from jarvis.marketplace.plugin_shared import set_active_plugin_registry

            registry = PluginToolRegistry(bus=self.bus)
            self._plugin_registry = registry
            app.state.plugin_registry = registry
            set_active_plugin_registry(registry)
            logger.info("PluginToolRegistry created (bootstrap pending)")
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "PluginToolRegistry setup failed — plugins stay worker-only"
            )
            app.state.plugin_registry = None
            self._plugin_registry = None

    # ------------------------------------------------------------------
    # REST
    # ------------------------------------------------------------------

    def _register_rest_routes(self, app: FastAPI) -> None:
        cfg = self.cfg
        bus = self.bus

        @app.get("/api/health")
        async def health() -> dict[str, Any]:
            return {"ok": True, "version": __version__}

        @app.get("/api/config")
        async def get_config() -> dict[str, Any]:
            # Read-only snapshot — secrets are, by design, never in the config.
            return cfg.model_dump()

        @app.get("/api/plugins")
        async def get_plugins() -> dict[str, list[str]]:
            try:
                return list_all_plugins()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Plugin discovery failed")
                return {}

        @app.post("/api/debug/emit-test-event")
        async def emit_test_event() -> dict[str, Any]:
            evt = SystemStarted(version=__version__, source_layer="ui.web.debug")
            await bus.publish(evt)
            return {
                "ok": True,
                "event": "SystemStarted",
                "trace_id": str(evt.trace_id),
            }

        @app.post("/api/window/focus")
        async def window_focus() -> dict[str, Any]:
            # Placeholder — the actual focus call lands in the desktop app
            # (pywebview shell). Just an ACK here, so single-instance ping
            # gets a defined status.
            return {"ok": True, "focused": False, "note": "handled by desktop-shell"}

        @app.get("/api/brain/status")
        async def brain_status() -> dict[str, Any]:
            """Returns the currently active brain provider + model.

            The frontend uses this on mount to initialize the sidebar footer
            correctly (instead of assuming the hardcoded "claude-api"
            default). Live switches still arrive via the WS event
            ``BrainProviderChanged``.
            """
            brain = getattr(app.state, "brain", None)
            # BrainManager exposes `active_provider`. MockBrain only has `name`.
            # Fast-boot deferral: the heavy BrainManager build runs in a
            # background thread, so `app.state.brain` is None for the first
            # ~850 ms while uvicorn already serves. In that window fall back to
            # the configured primary provider — it already names the brain that
            # WILL become active — instead of "unknown", which would freeze the
            # sidebar footer on a bare "—" until a manual provider switch (the
            # mount-fetch is one-shot and nothing re-fetches once the build
            # finishes).
            provider = (
                getattr(brain, "active_provider", None)
                or getattr(brain, "name", None)
                or cfg.brain.primary
                or "unknown"
            )
            prov_cfg = cfg.brain.providers.get(provider)
            model = getattr(prov_cfg, "model", None) if prov_cfg else None
            return {"provider": provider, "model": model or "unknown"}

        @app.get("/api/voice/status")
        async def voice_status() -> dict[str, Any]:
            """Return the current voice boot-readiness flag.

            The frontend reads this on mount to initialize the "voice starting"
            badge correctly — the ``VoiceBootStatus`` WS event is one-shot, so a
            late-connecting UI would miss it. The value is maintained by the bus
            subscriber in ``_build_app`` on the server instance.
            """
            return {"ready": bool(getattr(self, "_voice_ready", False))}

        @app.get("/api/jarvis-agent/status")
        async def jarvis_agent_status() -> dict[str, Any]:
            """OpenClaw-bridge status for the settings view (Welle 3).

            Read-only snapshot:

            * ``configured``       — is the ``[harness.openclaw]`` block present in jarvis.toml?
            * ``enabled``          — bridge toggle from the block
            * ``binary_path``      — configured path
            * ``binary_detected``  — resolver result (PATH + .cmd/.ps1/.exe)
            * ``version_pin``      — AD-21 pin (None when the block is missing)
            * ``brain_primary``    — active SUBAGENT provider
              (``[brain.sub_jarvis].provider``); falls back to ``brain.primary``
              only when no subagent provider is set. NOT the router
              brain — the subagent runs the heavy tasks.
            * ``provider_slug``    — OpenClaw slug of the active subagent
              provider per AD-6 (claude-api->claude-cli)
            * ``model_resolved``   — override from config OR the frontier-deep
              model of the active subagent provider
            * ``mapping``          — the full slug-mapping table

            Contract: docs/openclaw-bridge.md §4.3 wizard/setup extension.
            The endpoint returns NO secrets — only a boolean for whether a key is set.
            """
            import shutil

            oc_cfg = cfg.harness.jarvis_agent
            router_primary = (cfg.brain.primary or "").lower()

            try:
                from jarvis.missions.worker_runtime.provider_map import (
                    MAPPINGS,
                    canonical_worker_provider,
                    to_worker_slug,
                )
            except Exception:  # noqa: BLE001
                MAPPINGS = ()  # type: ignore[assignment]
                to_worker_slug = None  # type: ignore[assignment]
                canonical_worker_provider = None  # type: ignore[assignment]

            # The HEAVY-TASK subagent runs on ``[brain.worker].provider`` —
            # NOT on ``brain.primary`` (that is only the lightweight router
            # brain). Mark the brain that ACTUALLY executes heavy tasks as
            # active; fall back to the router brain only when no subagent
            # provider is configured (worker then uses its default chain).
            # Mirrors jarvis/missions/init.py::_worker_factory so the displayed
            # brain never drifts from the worker that runs. (Bug 2026-05-28:
            # the UI showed Gemini active while heavy work ran on Claude.)
            sub_cfg = getattr(cfg.brain, "worker", None)
            sub_raw = (
                getattr(sub_cfg, "provider", None) if sub_cfg is not None else None
            )
            sub_provider = (
                canonical_worker_provider(sub_raw)
                if canonical_worker_provider is not None
                else None
            )
            primary = sub_provider or router_primary

            # Model: an explicit ``[brain.sub_jarvis].model`` wins; else the
            # active provider's deep_model (frontier) from
            # ``[brain.providers.<provider>]``.
            sub_model_override = (
                getattr(sub_cfg, "model", None) if sub_cfg is not None else None
            )
            prov_cfg = cfg.brain.providers.get(primary)
            primary_deep_model = (sub_model_override or "").strip() or (
                getattr(prov_cfg, "deep_model", None)
                or getattr(prov_cfg, "model", None)
                if prov_cfg
                else None
            )

            provider_slug: str | None = None
            if to_worker_slug is not None:
                try:
                    provider_slug = to_worker_slug(primary)
                except Exception:  # noqa: BLE001
                    provider_slug = None

            model_resolved: str | None = None
            if oc_cfg is not None and oc_cfg.model:
                model_resolved = oc_cfg.model
            elif provider_slug and primary_deep_model:
                model_resolved = f"{provider_slug}/{primary_deep_model}"

            binary_path = (
                oc_cfg.binary_path if oc_cfg is not None else "openclaw"
            )
            binary_detected: str | None = shutil.which(binary_path)
            if not binary_detected:
                for ext in (".cmd", ".ps1", ".exe"):
                    cand = shutil.which(binary_path + ext)
                    if cand:
                        binary_detected = cand
                        break

            from jarvis.core.config import get_secret

            secret_key_overrides = {
                "claude-api": "anthropic_api_key",
                "openrouter": "openrouter_api_key",
                "openai": "openai_api_key",
                "gemini": "gemini_api_key",
            }
            mapping_rows = []
            for mapping in MAPPINGS:
                secret_key = secret_key_overrides.get(
                    mapping.jarvis, f"{mapping.jarvis}_api_key"
                )
                try:
                    key_set = bool(get_secret(secret_key, mapping.env_var))
                except Exception:  # noqa: BLE001
                    key_set = False
                # Claude Max users authenticate the subagent via the LIVE OAuth
                # login in ~/.claude/.credentials.json (read by ClaudeDirectWorker),
                # not a stored API key — count that as configured so a fresh
                # Claude-Max user (only ran `claude login`) is not falsely locked.
                if not key_set and mapping.jarvis == "claude-api":
                    try:
                        from jarvis.missions.isolation.env import (
                            read_live_claude_oauth_token,
                        )

                        key_set = bool(read_live_claude_oauth_token())
                    except Exception:  # noqa: BLE001
                        key_set = False
                # claude-api is dual-billed like Codex/Antigravity: the Claude Max
                # subscription (claude CLI OAuth login) OR an Anthropic API key.
                # Every other MAPPINGS provider bills per token on an API account.
                row_billing = (
                    "subscription_or_api"
                    if mapping.jarvis == "claude-api"
                    else "api"
                )
                mapping_rows.append(
                    {
                        "jarvis": mapping.jarvis,
                        "worker_slug": mapping.worker_slug,
                        "env_var": mapping.env_var,
                        "env_fallback": mapping.env_fallback,
                        "key_set": key_set,
                        "is_active_brain": mapping.jarvis == primary,
                        "billing": row_billing,
                    }
                )

            # Codex is a DIRECT worker (CodexDirectWorker) with no OpenClaw slug,
            # so it is not in MAPPINGS. Surface it as an explicit selectable
            # subagent row. Backed by the ChatGPT subscription (OAuth) OR an
            # OpenAI API key — "key_set" is true when either is present.
            try:
                from jarvis.codex_auth import CodexAuthService

                codex_bin = (
                    getattr(getattr(cfg, "codex", None), "binary_path", "") or None
                )
                codex_connected = CodexAuthService(codex_bin).status().connected
            except Exception:  # noqa: BLE001
                codex_connected = False
            try:
                codex_key = bool(
                    get_secret("codex_openai_api_key", env_fallback="OPENAI_API_KEY")
                )
            except Exception:  # noqa: BLE001
                codex_key = False
            mapping_rows.append(
                {
                    "jarvis": "openai-codex",
                    "openclaw": "codex-cli (direct)",
                    "env_var": "ChatGPT-OAuth",
                    "env_fallback": "OPENAI_API_KEY",
                    "key_set": codex_connected or codex_key,
                    "is_active_brain": primary == "openai-codex",
                    # ChatGPT subscription OAuth OR an OpenAI API key.
                    "billing": "subscription_or_api",
                }
            )

            # Antigravity is a DIRECT worker (GoogleCliWorker over the official
            # agy/Gemini CLI) with no OpenClaw slug, so it is not in MAPPINGS —
            # the Google sibling of Codex. Dual billing, mirror of Codex: the
            # Google subscription OAuth login OR a Gemini API key (per token).
            # "key_set" is true when either is present.
            try:
                from jarvis.google_cli.auth_service import GoogleCliAuthService

                antigravity_connected = GoogleCliAuthService().status().connected
            except Exception:  # noqa: BLE001
                antigravity_connected = False
            try:
                antigravity_key = bool(
                    get_secret("gemini_api_key", env_fallback="GEMINI_API_KEY")
                )
            except Exception:  # noqa: BLE001
                antigravity_key = False
            mapping_rows.append(
                {
                    "jarvis": "antigravity",
                    "openclaw": "agy-cli (direct)",
                    "env_var": "Google-OAuth",
                    "env_fallback": "GEMINI_API_KEY",
                    "key_set": antigravity_connected or antigravity_key,
                    "is_active_brain": primary == "antigravity",
                    # Google subscription OAuth OR a Gemini API key.
                    "billing": "subscription_or_api",
                }
            )

            return {
                "configured": oc_cfg is not None,
                "enabled": bool(oc_cfg.enabled) if oc_cfg else False,
                "binary_path": binary_path,
                "binary_detected": binary_detected,
                "version_pin": oc_cfg.version if oc_cfg else None,
                "time_cap_min": oc_cfg.time_cap_min if oc_cfg else None,
                "concurrency": oc_cfg.concurrency if oc_cfg else None,
                "state_dir_root": oc_cfg.state_dir_root if oc_cfg else None,
                "brain_primary": primary,
                "provider_slug": provider_slug,
                "model_override": oc_cfg.model if oc_cfg else None,
                # The dedicated subagent LLM pin ([brain.sub_jarvis].model);
                # empty/None means "provider's deep model" (model_resolved).
                "sub_model_override": sub_model_override,
                "model_resolved": model_resolved,
                "mapping": mapping_rows,
            }

        @app.get("/api/memory/facts")
        async def get_memory_facts() -> dict[str, Any]:
            """Returns the core memory (persona, user facts, preferences).

            The frontend shows this in the notes view, so the user can see
            what Jarvis has remembered. core_memory.json is automatically
            injected into the system prompt on the next brain call — so this
            view is a read-only mirror of the persistent memory state.
            """
            from jarvis.core.config import DATA_DIR
            from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory

            try:
                mem = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
                return {"ok": True, "data": mem.all()}
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Memory read error")
                return {"ok": False, "error": str(exc), "data": {}}

        @app.post("/api/memory/facts")
        async def add_memory_fact(payload: dict[str, Any]) -> dict[str, Any]:
            """User-driven add from the UI."""
            from jarvis.core.config import DATA_DIR
            from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory

            fact = (payload.get("fact") or "").strip()
            category = (payload.get("category") or "general").strip()
            if not fact:
                return {"ok": False, "error": "fact is missing"}
            try:
                mem = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
                mem.add_fact(fact, category=category)
                return {"ok": True, "data": mem.all()}
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Memory write error")
                return {"ok": False, "error": str(exc)}

        @app.delete("/api/memory/facts")
        async def delete_memory_fact(payload: dict[str, Any]) -> dict[str, Any]:
            """User-driven remove from the UI."""
            from jarvis.core.config import DATA_DIR
            from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory

            fact = (payload.get("fact") or "").strip()
            category = (payload.get("category") or "general").strip()
            if not fact:
                return {"ok": False, "error": "fact is missing"}
            try:
                mem = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
                ok = mem.remove_fact(fact, category=category)
                return {"ok": ok, "data": mem.all()}
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Memory delete error")
                return {"ok": False, "error": str(exc)}

        @app.get("/api/terminal/shells")
        async def terminal_shells() -> dict[str, Any]:
            """Returns all shells installed on this system.

            The frontend uses this to populate the shell dropdown with only
            available options — no "command not found" on spawn.
            """
            return {
                "shells": [
                    {"id": s.id, "label": s.label}
                    for s in discover_shells()
                ]
            }

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def _register_ws_route(self, app: FastAPI) -> None:
        @app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket) -> None:
            await self._handle_ws(ws)

    async def _handle_ws(self, ws: WebSocket) -> None:
        await ws.accept()
        session_id = str(uuid4())
        self._clients[session_id] = ws

        token = os.environ.get(self.cfg.ui.auth_token_env)
        welcome = WSWelcome(session_id=session_id, version=__version__, token=token)

        send_lock = asyncio.Lock()

        def _drop_stalled_client() -> None:
            """Detach this WS client so a wedged socket cannot keep blocking
            the event bus (AP-18). The receive loop's ``finally`` does the
            same cleanup; doing it here too is idempotent and stops the bleed
            immediately instead of waiting for the OS TCP timeout."""
            self._clients.pop(session_id, None)
            try:
                self.bus._wildcard_subscribers.remove(_forward)  # type: ignore[attr-defined]
            except ValueError:
                pass

        async def _forward(event: Event) -> None:
            if session_id not in self._clients:
                return
            try:
                envelope = event_to_ws_envelope(event)
                async with send_lock:
                    # Bounded send: a stalled/half-open client must NEVER block
                    # the bus (BUG-CU-STALL). On timeout we drop the client.
                    await asyncio.wait_for(
                        ws.send_json(envelope), timeout=_WS_SEND_TIMEOUT_S
                    )
            except WebSocketDisconnect:
                _drop_stalled_client()
            except TimeoutError:
                logger.warning(
                    "WS client dropped — send stalled >{}s (a wedged tab must "
                    "never freeze the event bus) session_id={} event={}",
                    _WS_SEND_TIMEOUT_S,
                    session_id,
                    type(event).__name__,
                )
                _drop_stalled_client()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "WS forward failed",
                    session_id=session_id,
                    event=type(event).__name__,
                )

        self.bus.subscribe_all(_forward)

        try:
            async with send_lock:
                await ws.send_json(welcome.model_dump())

            while True:
                try:
                    raw = await ws.receive_json()
                except WebSocketDisconnect:
                    break
                except RuntimeError as exc:
                    # The socket is gone — e.g. starlette raises
                    # RuntimeError('WebSocket is not connected ...') after an
                    # unclean client disconnect instead of WebSocketDisconnect.
                    # `continue` here re-calls receive_json on the dead socket
                    # forever: a ~9 MB/s traceback log-storm that wedges the
                    # event loop and triggers a self-restart cancelling every
                    # in-flight mission (live incident 2026-06-14). Treat it as
                    # a disconnect and leave the loop.
                    logger.opt(exception=exc).warning(
                        "WS receive aborted; closing connection",
                        session_id=session_id,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    # Recoverable: a malformed frame from a still-connected
                    # client (bad JSON). Notify and keep listening.
                    logger.opt(exception=exc).warning(
                        "WS decode error",
                        session_id=session_id,
                    )
                    await self.bus.publish(
                        ErrorOccurred(
                            layer="ui.web.ws",
                            error_type=type(exc).__name__,
                            message=str(exc),
                            recoverable=True,
                            source_layer="ui.web.ws",
                        )
                    )
                    continue

                await self._route_incoming(session_id, raw, send_lock)

        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error(
                "WS-Handler abgebrochen",
                session_id=session_id,
            )
            await self.bus.publish(
                ErrorOccurred(
                    layer="ui.web.ws",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    recoverable=False,
                    source_layer="ui.web.ws",
                )
            )
        finally:
            # Unsubscribe to avoid a memory leak.
            try:
                self.bus._wildcard_subscribers.remove(_forward)  # type: ignore[attr-defined]
            except ValueError:
                pass
            self._clients.pop(session_id, None)
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _route_incoming(
        self,
        session_id: str,
        raw: Any,
        send_lock: asyncio.Lock,
    ) -> None:
        """Validates and dispatches an incoming WS frame."""
        if not isinstance(raw, dict):
            await self.bus.publish(
                ErrorOccurred(
                    layer="ui.web.ws",
                    error_type="InvalidFrame",
                    message=f"Expected dict, got {type(raw).__name__}",
                    recoverable=True,
                    source_layer="ui.web.ws",
                )
            )
            return

        frame_type = raw.get("type")
        try:
            if frame_type == "message":
                msg = WSMessageIn.model_validate(raw)
                thread_id = (msg.metadata or {}).get("thread_id", session_id)
                await self.bus.publish(
                    MessageSent(
                        thread_id=thread_id,
                        role="user",
                        text=msg.content,
                        source_layer="ui.web.ws",
                    )
                )
            elif frame_type == "command":
                cmd = WSCommand.model_validate(raw)
                await self._handle_command(session_id, cmd, send_lock)
            else:
                await self.bus.publish(
                    ErrorOccurred(
                        layer="ui.web.ws",
                        error_type="UnknownFrameType",
                        message=f"type={frame_type!r}",
                        recoverable=True,
                        source_layer="ui.web.ws",
                    )
                )
        except ValidationError as exc:
            logger.warning("WS frame validation error", errors=exc.errors())
            await self.bus.publish(
                ErrorOccurred(
                    layer="ui.web.ws",
                    error_type="ValidationError",
                    message=str(exc),
                    recoverable=True,
                    source_layer="ui.web.ws",
                )
            )

    async def _handle_command(
        self,
        session_id: str,
        cmd: WSCommand,
        send_lock: asyncio.Lock,
    ) -> None:
        if cmd.action == "ping":
            ws = self._clients.get(session_id)
            if ws is not None:
                async with send_lock:
                    await ws.send_json({"type": "pong", "payload": cmd.payload})
        elif cmd.action == "test_event":
            await self.bus.publish(
                SystemStarted(version=__version__, source_layer="ui.web.ws.cmd")
            )
        elif cmd.action == "terminal.spawn":
            await self._handle_terminal_spawn(session_id, cmd.payload, send_lock)
        elif cmd.action == "terminal.input":
            await self._handle_terminal_input(cmd.payload)
        elif cmd.action == "terminal.resize":
            self._handle_terminal_resize(cmd.payload)
        elif cmd.action == "terminal.close":
            self._handle_terminal_close(cmd.payload)
        elif cmd.action == "stt_dictate":
            await self._handle_dictation(cmd.payload)
        elif cmd.action == "mission.inject":
            await self._handle_mission_inject(session_id, cmd.payload)
        # provider_switch/set_state now run over REST (POST /api/brain/switch
        # or POST /api/secrets/{key}). Duplicate code paths removed here.

    async def _handle_mission_inject(
        self, session_id: str, payload: dict[str, Any]
    ) -> None:
        """Drag-drop a mission card → inject it into the live conversation.

        Composes a bounded, human-readable user turn from the card's own data
        and publishes it as a normal ``MessageSent``. The existing brain
        dispatcher then answers it (spoken on voice, shown in chat) and the
        text lands in ``BrainManager._history`` so follow-ups stay in context.
        A distinct ``source_layer`` marks the turn for traceability AND is the
        signal the router uses to exempt a recap from force-spawn — a dropped
        card is DISCUSSED inline, never re-dispatched as a new mission. The
        brain dispatcher still runs the turn (only ``"chat"``/``"brain:mock"``
        are skipped), so it answers exactly like a typed message.
        """
        from jarvis.ui.web.mission_inject import (
            MISSION_INJECT_SOURCE_LAYER,
            compose_mission_inject_text,
        )

        text = compose_mission_inject_text(payload)
        if not text:
            logger.debug("mission.inject: empty/unparseable payload — ignored")
            return
        thread_id = str(payload.get("thread_id") or session_id)
        await self.bus.publish(
            MessageSent(
                thread_id=thread_id,
                role="user",
                text=text,
                source_layer=MISSION_INJECT_SOURCE_LAYER,
            )
        )

    async def _handle_dictation(self, payload: dict[str, Any]) -> None:
        """Start/stop chat mic-dictation on the live SpeechPipeline.

        Transcribe-only: the pipeline streams ``DictationTranscript`` events
        (forwarded to the browser by the wildcard subscriber) straight into the
        chat input — it never reaches the brain. Resolves the pipeline via
        ``runtime_refs``; if there is none (headless / voice disabled) it emits a
        recoverable error + a toast instead of crashing (cloud-first no-op).
        """
        from jarvis.core.runtime_refs import get_speech_pipeline

        mode = str(payload.get("mode", "start"))
        pipeline = get_speech_pipeline()
        if pipeline is None:
            # Headless / voice disabled — no server mic. Recoverable, not fatal:
            # surface it as an ErrorOccurred (the frontend already handles that
            # event) and return. Cloud-first no-op.
            await self.bus.publish(
                ErrorOccurred(
                    layer="ui.web.dictation",
                    error_type="DictationUnavailable",
                    message="Dictation needs a server microphone (not available in this mode).",
                    recoverable=True,
                    source_layer="ui.web.ws",
                )
            )
            return
        try:
            if mode == "stop":
                pipeline.stop_dictation()
            else:
                started = pipeline.start_dictation()
                if not started:
                    await self.bus.publish(
                        ErrorOccurred(
                            layer="ui.web.dictation",
                            error_type="DictationBusy",
                            message="Mic is busy — can't start dictation right now.",
                            recoverable=True,
                            source_layer="ui.web.ws",
                        )
                    )
        except Exception as exc:  # noqa: BLE001 — never crash the WS loop
            logger.warning("dictation command failed", error=str(exc))

    # ------------------------------------------------------------------
    # Terminal-Command-Handler
    # ------------------------------------------------------------------

    async def _handle_terminal_spawn(
        self,
        session_id: str,
        payload: dict[str, Any],
        send_lock: asyncio.Lock,
    ) -> None:
        shell_id = str(payload.get("shell", "pwsh"))
        cols = int(payload.get("cols", 120) or 120)
        rows = int(payload.get("rows", 30) or 30)
        cwd = payload.get("cwd")
        cwd_str = str(cwd) if cwd else None

        shell = get_shell(shell_id)
        if shell is None:
            await self.bus.publish(
                ErrorOccurred(
                    layer="ui.web.terminal",
                    error_type="ShellNotFound",
                    message=f"Shell {shell_id!r} not installed",
                    recoverable=True,
                    source_layer="ui.web.terminal",
                )
            )
            return

        bus = self.bus

        async def _on_output(terminal_id: str, data: str) -> None:
            await bus.publish(
                TerminalOutput(
                    terminal_id=terminal_id,
                    data=data,
                    source_layer="ui.web.terminal",
                )
            )

        async def _on_closed(terminal_id: str, exit_code: int) -> None:
            self._pty_input_buffers.pop(terminal_id, None)
            self._pty_shell_ids.pop(terminal_id, None)
            await bus.publish(
                TerminalClosed(
                    terminal_id=terminal_id,
                    exit_code=exit_code,
                    source_layer="ui.web.terminal",
                )
            )

        try:
            session = await self._pty.spawn(
                shell_argv=shell.argv,
                shell_id=shell.id,
                cwd=cwd_str,
                cols=cols,
                rows=rows,
                on_output=_on_output,
                on_closed=_on_closed,
            )
        except RuntimeError as exc:
            await self.bus.publish(
                ErrorOccurred(
                    layer="ui.web.terminal",
                    error_type="PtySpawnFailed",
                    message=str(exc),
                    recoverable=True,
                    source_layer="ui.web.terminal",
                )
            )
            return

        self._pty_input_buffers[session.terminal_id] = ""
        self._pty_shell_ids[session.terminal_id] = shell.id

        await self.bus.publish(
            TerminalSpawned(
                terminal_id=session.terminal_id,
                shell_id=shell.id,
                pid=session.pid,
                source_layer="ui.web.terminal",
            )
        )
        # Direct response frame with terminal_id, so the frontend can
        # unambiguously match up multiple parallel spawn requests.
        ws = self._clients.get(session_id)
        if ws is not None:
            async with send_lock:
                await ws.send_json(
                    {
                        "type": "terminal.spawned",
                        "payload": {
                            "terminal_id": session.terminal_id,
                            "shell_id": shell.id,
                            "pid": session.pid,
                        },
                    }
                )

    async def _handle_terminal_input(self, payload: dict[str, Any]) -> None:
        terminal_id = str(payload.get("terminal_id", ""))
        data = str(payload.get("data", ""))
        if not terminal_id or not data:
            return
        if not self._pty.write(terminal_id, data):
            return
        # Audit log: maintain the line buffer, emit a command on \r/\n.
        buf = self._pty_input_buffers.get(terminal_id, "")
        for ch in data:
            if ch in ("\r", "\n"):
                line = buf.strip()
                if line:
                    await self.bus.publish(
                        TerminalCommandExecuted(
                            terminal_id=terminal_id,
                            shell_id=self._pty_shell_ids.get(terminal_id, ""),
                            command=line,
                            source_layer="ui.web.terminal.audit",
                        )
                    )
                buf = ""
            elif ch in ("\x7f", "\b"):
                # Backspace — remove the last character from the buffer
                buf = buf[:-1]
            elif ch >= " ":
                buf += ch
            # We ignore other control chars (Ctrl-C etc.) in the buffer.
        # Memory cap for extremely long pasted lines.
        if len(buf) > 4096:
            buf = buf[-4096:]
        self._pty_input_buffers[terminal_id] = buf

    def _handle_terminal_resize(self, payload: dict[str, Any]) -> None:
        terminal_id = str(payload.get("terminal_id", ""))
        cols = int(payload.get("cols", 0) or 0)
        rows = int(payload.get("rows", 0) or 0)
        if terminal_id and cols > 0 and rows > 0:
            self._pty.resize(terminal_id, cols, rows)

    def _handle_terminal_close(self, payload: dict[str, Any]) -> None:
        terminal_id = str(payload.get("terminal_id", ""))
        if terminal_id:
            self._pty.close(terminal_id)

    # ------------------------------------------------------------------
    # Static / SPA
    # ------------------------------------------------------------------

    def _register_static_or_spa(self, app: FastAPI) -> None:
        if self.cfg.ui.dev_mode:
            return

        if ASSETS_DIR.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=str(ASSETS_DIR)),
                name="assets",
            )

        @app.get("/", include_in_schema=False, response_model=None)
        async def _spa_root() -> FileResponse | HTMLResponse:
            return self._spa_index_response()

        @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
        async def _spa_fallback(
            full_path: str,
        ) -> FileResponse | HTMLResponse | JSONResponse:
            if full_path.startswith("api/") or full_path.startswith("ws"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            try:
                target = (DIST_DIR / full_path).resolve()
                dist_root = DIST_DIR.resolve()
                if target.is_file() and dist_root in target.parents:
                    return FileResponse(str(target))
            except (OSError, ValueError):
                pass
            return self._spa_index_response()

    def _spa_index_response(self) -> FileResponse | HTMLResponse:
        if INDEX_FILE.is_file():
            return FileResponse(
                str(INDEX_FILE),
                headers={
                    "Cache-Control": "no-store, max-age=0",
                    "Pragma": "no-cache",
                },
            )
        return self._spa_placeholder_response()

    @staticmethod
    def _spa_placeholder_response() -> HTMLResponse:
        body = (
            "<!doctype html><html lang=\"en\"><head>"
            "<meta charset=\"utf-8\">"
            "<title>Jarvis</title>"
            "<meta http-equiv=\"refresh\" content=\"2\">"
            "<style>html,body{margin:0;height:100%;background:#0a0e14;"
            "color:#e6e6e6;font-family:ui-sans-serif,system-ui,sans-serif;"
            "display:flex;align-items:center;justify-content:center}"
            "main{text-align:center;max-width:480px;padding:24px}"
            "h1{font-weight:500;font-size:18px;margin:0 0 12px}"
            "p{margin:0;color:#9aa3ad;font-size:14px;line-height:1.5}</style>"
            "</head><body><main>"
            "<h1>Jarvis is starting…</h1>"
            "<p>The frontend is currently being built or reloaded. "
            "This page refreshes automatically.</p>"
            "</main></body></html>"
        )
        return HTMLResponse(
            content=body,
            status_code=200,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _schedule_marketplace_refresh_scheduler(self) -> None:
        """Start marketplace token refresh after the current boot turn.

        Both desktop and headless launch through :meth:`start`. Deferring the
        small amount of catalog/keyring setup until the loop gets control back
        keeps it off the readiness-critical path, while the stored handle makes
        the operation exactly-once even if start is invoked again before the
        callback runs.
        """
        if (
            self._refresh_scheduler is not None
            or self._refresh_scheduler_start_handle is not None
        ):
            return
        self._refresh_scheduler_stopping = False
        loop = asyncio.get_running_loop()
        self._refresh_scheduler_start_handle = loop.call_soon(
            self._start_marketplace_refresh_scheduler
        )

    def _start_marketplace_refresh_scheduler(self) -> None:
        """Create the periodic OAuth refresh task (best-effort, exactly once)."""
        self._refresh_scheduler_start_handle = None
        if self._refresh_scheduler is not None:
            return
        try:
            from jarvis.marketplace.connect_helpers import (
                build_handler_from_catalog,
                connected_plugin_ids,
            )
            from jarvis.marketplace.refresh_scheduler import RefreshScheduler
            from jarvis.marketplace.token_store import TokenStore

            token_store = TokenStore()

            def _refresh_live_session(plugin_id: str) -> None:
                registry = self._plugin_registry
                if registry is None or self._refresh_scheduler_stopping:
                    return
                task = asyncio.create_task(
                    registry.refresh_plugin(plugin_id),
                    name=f"plugin-refresh:{plugin_id}",
                )
                self._refresh_registry_tasks.add(task)

                def _consume_refresh_result(done: asyncio.Task[Any]) -> None:
                    self._refresh_registry_tasks.discard(done)
                    if done.cancelled():
                        return
                    try:
                        done.result()
                    except Exception as exc:  # noqa: BLE001 -- background isolation
                        logger.opt(exception=exc).warning(
                            "Marketplace live-session refresh failed for {}.",
                            plugin_id,
                        )

                task.add_done_callback(_consume_refresh_result)

            scheduler = RefreshScheduler(
                plugin_ids_fn=lambda: connected_plugin_ids(token_store),
                store=token_store,
                build_handler=build_handler_from_catalog,
                on_refreshed=_refresh_live_session,
            )
            scheduler.start()
        except Exception as exc:  # noqa: BLE001 -- refresh must never block boot
            logger.opt(exception=exc).warning(
                "Marketplace refresh scheduler did not start; connected OAuth "
                "plugins may expire."
            )
            return

        self._refresh_scheduler = scheduler
        self.app.state.refresh_scheduler = scheduler
        logger.info(
            "Marketplace refresh scheduler started; connected OAuth tokens "
            "will be renewed in the background."
        )

    async def _stop_marketplace_refresh_scheduler(self) -> None:
        """Cancel a pending start and stop the live refresh task, if any."""
        self._refresh_scheduler_stopping = True
        handle = self._refresh_scheduler_start_handle
        self._refresh_scheduler_start_handle = None
        if handle is not None:
            handle.cancel()

        scheduler = self._refresh_scheduler
        self._refresh_scheduler = None
        self.app.state.refresh_scheduler = None
        if scheduler is not None:
            try:
                await scheduler.stop()
            except Exception as exc:  # noqa: BLE001 -- shutdown stays best-effort
                logger.opt(exception=exc).warning(
                    "Marketplace refresh scheduler stop failed."
                )

        # A successful token refresh may already have queued a live MCP-session
        # rebuild. Drain those tasks before PluginToolRegistry.stop(); otherwise
        # a late task can reconnect a client after registry shutdown and leak it.
        pending = list(self._refresh_registry_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._refresh_registry_tasks.clear()

    async def start(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        *,
        start_serving: bool = True,
    ) -> None:
        """Run the boot init chain. When ``start_serving`` is False the uvicorn
        server is NOT started — the caller already serves this app's ASGI
        callable (the fast-boot bootstrap in launcher.py serves a holding app on
        the port and delegates to ``self.app`` once this init chain completes).
        """
        import uvicorn

        # Boot profiling (opt-in via JARVIS_BOOT_PROFILE=1; zero behavior change
        # otherwise). Emits one machine-readable ``[BOOT_PROFILE] <phase>=<ms>``
        # line per phase to stdout so the boot-timing harness
        # (scripts/measure_boot.py) can attribute cold-start cost without an LLM
        # or a log-level change. NEVER on the voice critical path.
        _boot_profile = os.environ.get("JARVIS_BOOT_PROFILE") == "1"
        _boot_last = time.perf_counter()

        def _boot_mark(_name: str) -> None:
            nonlocal _boot_last
            _now = time.perf_counter()
            if _boot_profile:
                print(
                    f"[BOOT_PROFILE] {_name}={(_now - _boot_last) * 1000.0:.1f}",
                    flush=True,
                )
            _boot_last = _now

        # Cloud-first fail-closed: never expose a non-loopback bind without a
        # Control API key — the key, not the bind address, is the boundary.
        from jarvis.core import control_key as _control_key
        from jarvis.ui.web.control_auth import assert_bind_safe

        if start_serving:
            assert_bind_safe(host, _control_key.get_control_key())

            resolved_port = port if port is not None else self.cfg.ui.admin_api_port
            config = uvicorn.Config(
                app=self.app,
                host=host,
                port=resolved_port,
                log_level=self.cfg.telemetry.log_level.lower(),
                lifespan="on",
                loop="asyncio",
            )
            server = uvicorn.Server(config)
            self._server = server
            self._serve_task = asyncio.create_task(server.serve())

            deadline = asyncio.get_running_loop().time() + 5.0
            while not server.started:
                if asyncio.get_running_loop().time() > deadline:
                    raise TimeoutError(
                        f"uvicorn server on {host}:{resolved_port} not ready within 5s"
                    )
                if self._serve_task.done():
                    exc = self._serve_task.exception()
                    if exc is not None:
                        raise exc
                    raise RuntimeError("uvicorn.Server.serve() exited before 'started'")
                await asyncio.sleep(0.05)
        else:
            # Bootstrap fast-boot path: a separate server already serves on the
            # port and delegates to self.app once this init chain finishes.
            self._server = None
            self._serve_task = None

        _boot_mark("uvicorn_serve")

        # Voice-ready UI backstop (permanent "starting up" bug): the frontend's
        # startup banner + top-left "STARTING…" status clear ONLY on a
        # VoiceBootStatus(ready=True). If the speech pipeline crashes during
        # construction or an un-timed model load wedges warm-up, that event never
        # fires and the UI hangs forever. Arm a watchdog that force-releases the UI
        # after a generous deadline — but only when voice is meant to warm up
        # (else _voice_ready is already seeded True and there is nothing to wait
        # for, e.g. JARVIS_VOICE=0 / headless).
        if not self._voice_ready:
            self._voice_ready_watchdog_task = asyncio.create_task(
                self._voice_ready_watchdog(), name="voice-ready-watchdog"
            )

        # Phase-6 mission stack — MissionManager with a DB path derived from
        # the config's memory data_dir (same folder as data/jarvis.db, its
        # own file data/missions.db). Recovery runs in start().
        try:
            await self._init_mission_stack()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "MissionManager init failed — /api/missions returns 503"
            )
        _boot_mark("mission_stack")

        # Screenshot retention: auto-delete old Vision / Flight-Recorder blobs
        # (data/flight_recorder/blobs/) after the configured window. Runs a
        # one-shot boot sweep plus a periodic background task.
        try:
            await self._init_screenshot_retention()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "Screenshot retention init failed — blobs will not be auto-pruned"
            )
        _boot_mark("screenshot_retention")

        # Flight-recorder audit log: attach the wildcard JSONL event recorder so
        # there is a replayable audit trail of what Jarvis did (every event,
        # incl. every Computer-Use action). It was defined but never wired at
        # boot, so telemetry.flight_recorder=true logged nothing (audit #14).
        try:
            await self._init_flight_recorder()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "Flight recorder init failed — the audit log will be inactive"
            )
        _boot_mark("flight_recorder")

        # Phase B5 wiki write-wiring — SessionRollupWorker + WikiCurator.
        # Subscribes to IdleEntered; gracefully disabled when wiki_integration
        # is not configured or enabled is False.
        try:
            await self._init_wiki_integration()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "WikiIntegration init failed — wiki write-wiring inactive"
            )
            try:
                from jarvis.memory.wiki.health import health as _wiki_health

                _wiki_health.record_bootstrap(False, error=str(exc))
            except Exception:  # noqa: BLE001 — health recording must never break boot
                logger.debug("wiki health.record_bootstrap(False) failed", exc_info=True)
        _boot_mark("wiki_integration")

        # Reconcile the derived FTS5 index after readiness. This repairs stale
        # rows after a vault switch without extending the startup critical path.
        try:
            self._init_wiki_boot_index(background=True)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "WikiBootIndex-Init failed — vault search may return no hits "
                "until the first page write or a manual reindex"
            )
        _boot_mark("wiki_boot_index")

        # Phase B3 wiki live-reload — start the WikiWatcher so file
        # changes in the vault publish WikiPageChanged events that the
        # /api/wiki/live WS endpoint forwards to the desktop tab.
        try:
            self._init_wiki_watcher()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "WikiWatcher init failed — desktop wiki live-reload inactive"
            )
        _boot_mark("wiki_watcher")

        # Voice-session recorder + store for the transcription view.
        # Sub-setup: runs sync (SQLite-WAL, no async loop needed), but is
        # run in start() so the EventBus is guaranteed to be alive.
        try:
            self._init_session_stack()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "SessionRecorder init failed — /api/sessions returns 503"
            )
        _boot_mark("session_stack")

        # Phase-5 task stack (tasks view). TaskStore lives additively in
        # data/jarvis.db (ADR-0003); the scheduler runs as an asyncio loop
        # terminated by the CancelToken in stop().
        try:
            await self._init_task_stack()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "TaskStack init failed — /api/tasks returns 503"
            )
        _boot_mark("task_stack")

        # Start the skill hot-reload watcher once the loop is stable.
        if self._skill_registry is not None:
            try:
                self._skill_registry.start_watcher(asyncio.get_running_loop())
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "SkillRegistry watcher start failed — no hot-reload"
                )

        # Doc hot-reload watcher, likewise. watchdog observes its own
        # observer thread per root.
        if self._doc_registry is not None:
            try:
                self._doc_registry.start_watcher(asyncio.get_running_loop())
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "DocRegistry watcher start failed — no hot-reload"
                )

        # Bootstrap the CLI registry asynchronously — probes all catalog CLIs
        # and builds tool instances. ``asyncio.create_task`` runs the call as
        # a background task so ``start()`` itself doesn't block.
        if self._cli_registry is not None:
            async def _bootstrap_clis() -> None:
                try:
                    await self._cli_registry.bootstrap()
                except Exception as exc:  # noqa: BLE001
                    logger.opt(exception=exc).warning(
                        "CliToolRegistry bootstrap failed — CLIs view empty"
                    )
            asyncio.create_task(_bootstrap_clis(), name="cli-registry-bootstrap")

        # Bootstrap the plugin registry asynchronously — opens an in-process
        # MCPClient per connected plugin and bridges its tools into the
        # live brain (BrainToolsChanged re-expands). Mirrors the CLI registry.
        if self._plugin_registry is not None:
            async def _bootstrap_plugins() -> None:
                try:
                    await self._plugin_registry.bootstrap()
                except Exception as exc:  # noqa: BLE001
                    logger.opt(exception=exc).warning(
                        "PluginToolRegistry bootstrap failed — plugins worker-only"
                    )
            asyncio.create_task(_bootstrap_plugins(), name="plugin-registry-bootstrap")

        # Board aggregator as a never-ending task. run_forever() does an
        # on-startup run first and then sleeps 6h (Plan §5-A Decision #2).
        if self._board_aggregator is not None:
            self._board_aggregator_task = asyncio.create_task(
                self._board_aggregator.run_forever(interval_s=6 * 3600),
                name="board-aggregator",
            )

        # Achievement evaluator on the bus. Phase B.
        if self._board_evaluator is not None:
            try:
                self._board_evaluator.attach()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "AchievementEvaluator.attach() failed"
                )

        # Bio scheduler — weekly + master achievement trigger.
        # The brain isn't finalized here yet (app.state.brain usually
        # arrives later). BioGenerator.brain stays None until the caller
        # sets it — the scheduler handles a None brain gracefully.
        if self._bio_scheduler is not None:
            try:
                self._bio_scheduler.start()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "BioScheduler.start() failed"
                )

        # Friends-Stack: FriendRegistry + ChannelManager. Started as a BACKGROUND
        # task so a slow Telegram/Discord network connect (bot login / getUpdates
        # handshake — observed ~4 s, and a 409 retry storm on a restart overlap)
        # does not delay backend-ready and, with it, the speech warm-up. Channels
        # are background transports — nothing user-visible needs them live before
        # voice; /api/friends + /api/socials 503 until the stack is up (the route
        # guards already handle the brief app.state.channel_manager=None window).
        async def _init_channel_stack_guarded() -> None:
            try:
                await self._init_channel_stack()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "ChannelStack init failed — /api/friends returns 503"
                )

        self._channel_stack_task = asyncio.create_task(
            _init_channel_stack_guarded(), name="channel-bootstrap"
        )

        # Run the deferred registry disk scans (skill + doc reload_sync) off the
        # boot critical path. Each is a blocking glob + FTS index build (~hundreds
        # of ms) that nothing at BOOT_READY needs — the views tolerate an empty
        # registry until the scan lands. Run in a worker thread so the scan never
        # stalls the event loop; sequential so two SQLite writers don't contend.
        if self._pending_reloads:
            pending = list(self._pending_reloads)
            self._pending_reloads.clear()

            async def _run_deferred_reloads() -> None:
                # Yield until the wake model is loaded before these blocking disk
                # scans (DocRegistry FTS build ~5 s) start their worker thread —
                # otherwise they steal CPU/disk from the custom-wake base/cpu
                # model load and ~double its time (measured: base load 3.2 s
                # isolated vs ~8 s racing this scan). NO-OP on headless / voice-off
                # (returns immediately — no regress); bounded so a stuck wake load
                # never blocks the scans. The views tolerate an empty registry for
                # this brief gap.
                from jarvis.core import runtime_refs as _rr

                await _rr.await_wake_model_ready(timeout=12.0)
                for label, registry in pending:
                    try:
                        await asyncio.to_thread(registry.reload_sync)
                        logger.info(
                            "{} deferred scan complete ({} entries)",
                            label, len(registry.list()),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.opt(exception=exc).warning(
                            "{} deferred reload failed — view stays empty", label
                        )

            self._deferred_reload_task = asyncio.create_task(
                _run_deferred_reloads(), name="deferred-registry-reload"
            )

        _boot_mark("watchers_and_background")
        # Schedule last: callers regain control and publish the ready app before
        # catalog/keyring reads or refresh network calls can begin. This shared
        # hook covers normal desktop, headless, and direct WebServer starts.
        self._schedule_marketplace_refresh_scheduler()

    async def _init_screenshot_retention(self) -> None:
        """Auto-delete captured screenshot blobs older than the configured
        window (default 10 days).

        Jarvis captures screenshots into ``data/flight_recorder/blobs/`` for
        in-session context (Vision system + Flight-Recorder event blobs); they
        are throwaway afterwards and otherwise grow without bound (observed:
        ~91k files / ~38 GB). Runs a one-shot sweep at boot plus a periodic
        background task. ``flight_recorder_retention_days = 0`` disables it.
        """
        from jarvis.telemetry.retention import (
            DEFAULT_RETENTION_INTERVAL_SECONDS,
            retention_task,
            sweep_old_blobs,
        )

        retention_days = self.cfg.telemetry.flight_recorder_retention_days
        if retention_days <= 0:
            logger.info(
                "Screenshot retention disabled "
                "(flight_recorder_retention_days={})",
                retention_days,
            )
            return

        # Relative path on purpose — it must match what the writers actually
        # use, NOT cfg.memory.data_dir. The production writer is the Vision
        # ScreenshotSource (jarvis/vision/engine.py constructs it with no
        # blob_dir → falls back to the relative _DEFAULT_BLOB_DIR in
        # jarvis/vision/screenshot.py = data/flight_recorder/blobs). It ignores
        # cfg.memory.data_dir, so deriving from that key would make the sweep a
        # silent no-op whenever data_dir is overridden. Same process → same cwd
        # → same directory as the writer.
        flight_recorder_dir = Path("data") / "flight_recorder"

        # The one-shot boot sweep is pure cleanup of old blobs — nothing
        # downstream consumes its stats (they are only logged). Awaiting it sat
        # on the boot path before the voice pipeline could start. Fold it into
        # the front of the (already background) retention task so _init returns
        # immediately; the same task reference is still cancelled on shutdown.
        async def _boot_sweep_then_retain() -> None:
            try:
                stats = await sweep_old_blobs(
                    flight_recorder_dir=flight_recorder_dir,
                    retention_days=retention_days,
                )
                if stats["removed"] > 0:
                    logger.info(
                        "Screenshot retention boot sweep: removed={} freed={:.1f} MB "
                        "(cutoff={}d)",
                        stats["removed"],
                        stats["bytes_freed"] / (1024 * 1024),
                        retention_days,
                    )
                else:
                    logger.debug(
                        "Screenshot retention boot sweep: nothing older than {}d",
                        retention_days,
                    )
            except Exception:  # noqa: BLE001 — cleanup never blocks/breaks boot
                logger.warning("Screenshot retention boot sweep failed", exc_info=True)
            await retention_task(
                flight_recorder_dir=flight_recorder_dir,
                retention_days=retention_days,
                interval_seconds=DEFAULT_RETENTION_INTERVAL_SECONDS,
            )

        self._screenshot_retention_task = asyncio.create_task(
            _boot_sweep_then_retain()
        )

    async def _init_flight_recorder(self) -> None:
        """Attach the flight-recorder audit log to the EventBus (ADR-0007).

        Writes every event as a JSONL line under ``data/flight_recorder/`` — a
        replayable audit trail of what Jarvis did, including every Computer-Use
        action. Gated on ``telemetry.flight_recorder`` (default on); the recorder
        is held on ``app.state`` for later flush/close on shutdown. The recorder
        auto-flushes every second, and oversized event blobs land under
        ``blobs/`` which the screenshot-retention task already prunes.
        """
        from jarvis.telemetry.recorder import attach_flight_recorder

        rec = attach_flight_recorder(
            self.bus,
            enabled=self.cfg.telemetry.flight_recorder,
            data_dir=Path("data") / "flight_recorder",
        )
        if rec is None:
            logger.info("Flight recorder disabled (telemetry.flight_recorder=false)")
            return
        self._flight_recorder = rec
        self.app.state.flight_recorder = rec
        logger.info(
            "Flight recorder attached — events -> data/flight_recorder/*.jsonl"
        )

    async def _init_mission_stack(self) -> None:
        """Phase-6 production wiring: bootstrap_missions() returns the
        complete stack (manager, Kontrollierer, budget, voice listener,
        cleanup task), including safety hooks and the WS-manager bus bridge.
        """
        from jarvis.missions.init import bootstrap_missions

        data_dir = Path(self.cfg.memory.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "missions.db"

        # Isolation root: <repo_parent>/jarvis-agent-outputs/ (preferred; falls back
        # to <repo_parent>/sub-agents-outputs/ if only the old dir exists — see
        # resolve_outputs_root). The repo root is 4 levels above jarvis/ui/web/server.py:
        # server.py -> web -> ui -> jarvis -> repo.
        repo_root = WEB_DIR.parent.parent.parent
        # Test/benchmark isolation seam: an explicit JARVIS_ISOLATION_ROOT
        # redirects the mission worktree container (and, with it, the startup
        # cleanup sweep) away from the SHARED production outputs dir.
        # Unset in production → unchanged behavior. Critical because
        # ``startup_sweep`` is filesystem-driven (removes any entry older than
        # cleanup_days by mtime, not DB-gated): without this seam an isolated
        # headless boot (e.g. scripts/measure_boot.py) would sweep real mission
        # outputs older than 14 days.
        _iso_override = os.environ.get("JARVIS_ISOLATION_ROOT")
        if _iso_override:
            isolation_root = Path(_iso_override)
        else:
            from jarvis.missions.isolation.worktree import resolve_outputs_root
            isolation_root = resolve_outputs_root(repo_root)

        # Fail-closed primary-instance gate: POSITIVE proof is required.
        # Only the launcher process, after confirming it holds the
        # single-instance lock, sets JARVIS_PRIMARY_INSTANCE="1".
        # Any other caller — smoke scripts, eval harnesses, --no-lock parallel
        # sessions, or anything that simply does not set the variable — gets
        # _is_primary=False and will NOT run the crash_recovery sweep.
        # This prevents side-processes from sweeping the desktop app's live
        # missions to FAILED('crash_recovery') (the 98-of-286 false-failure
        # bucket, forensic 2026-05-31, missions 019e7095 / 019e6fea).
        _is_primary = os.environ.get("JARVIS_PRIMARY_INSTANCE") == "1"
        result = await bootstrap_missions(
            db_path=db_path,
            isolation_root=isolation_root,
            repo_root=repo_root,
            recover_missions=_is_primary,
            tts_speak_fn=None,  # TTS wiring comes from DesktopApp once voice is live
            brain_caller=None,  # The decomposer runs in heuristic-only mode
            # Welle-4 Y: pass the speech bus through for MissionAnnouncer, so
            # mission-completion events land as AnnouncementRequested on the
            # global bus — pipeline._on_announcement subscribes there.
            speech_bus=self.bus,
            # Defaults from jarvis.toml [phase6.*] (all overridable via cfg later)
            safety_enabled=True,
            # User mandate 2026-05-31 ("no budget at all", frontier-quality-
            # over-cost): the per-mission/daily cost cap is DISABLED so a long,
            # high-quality Opus mission is never aborted mid-work for cost. The
            # per_mission_usd/daily_usd values below are inert while disabled.
            budget_enabled=False,
            per_mission_usd=5.0,
            daily_usd=50.0,
            cleanup_days=14,
            cleanup_startup_sweep=True,
            cleanup_daily=False,
            max_workers=5,
        )

        self.app.state.mission_manager = result["manager"]
        self.app.state.kontrollierer = result["kontrollierer"]
        self.app.state.missions_budget = result["budget"]
        self.app.state.mission_announcer = result["mission_announcer"]
        # Mission-Bus -> global-bus bridge that re-publishes terminal missions
        # as MissionCompleted so the Tasks scheduler can drive When-Then rules.
        self.app.state.mission_event_bridge = result["mission_event_bridge"]
        # outputs_routes.py uses this to render the Outputs view; it would
        # otherwise have to re-derive the same WEB_DIR.parent.parent.parent
        # walk and would silently drift if the launcher layout changes.
        self.app.state.outputs_root = isolation_root
        self._missions_voice_listener = result["voice_listener"]
        self._missions_cleanup_task = result["cleanup_task"]

        # Welle-4 wiring: the spawn_worker tool in the brain needs both the
        # MissionManager AND the Kontrollierer. The brain is built in
        # DesktopApp._start_speech_and_orb via build_default_brain() — the
        # singleton setters make both available there (lazy resolve via
        # _resolve_mission_manager / _resolve_kontrollierer).
        # Without the Kontrollierer setter, the voice path would dispatch a
        # mission but nothing would trigger run_mission — the mission would
        # stay PENDING and the user would hear no answer (BUG-016).
        try:
            from jarvis.brain.factory import (
                set_kontrollierer,
                set_mission_manager,
                set_worker_bootstrap_failed,
            )

            set_mission_manager(result["manager"])
            set_kontrollierer(result["kontrollierer"])
            set_worker_bootstrap_failed(False)
            self.app.state.worker_available = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "set_mission_manager/kontrollierer wiring failed — "
                "spawn_worker is disabled for this run: %s", exc,
            )
            # Surface the failure for both downstream readers:
            #  - `self.app.state.worker_available` lets REST routes and
            #    UI components show an explicit "worker unavailable" hint
            #    instead of permanently rendering "loading" / "pending".
            #  - the factory-level singleton lets the Brain's spawn_worker
            #    tool short-circuit at execute()-time with an honest
            #    "could not be initialized" message instead of the
            #    transient "not ready yet" the in-progress path returns.
            self.app.state.worker_available = False
            try:
                from jarvis.brain.factory import set_worker_bootstrap_failed
                set_worker_bootstrap_failed(True)
            except Exception as inner_exc:  # noqa: BLE001
                logger.warning(
                    "set_worker_bootstrap_failed wiring failed: %s",
                    inner_exc,
                )

        # WS bridge: the Phase-4 ConnectionManager is subscribed to the bus.
        ws_mgr = getattr(self.app.state, "missions_ws_manager", None)
        if ws_mgr is not None:
            result["manager"].bus.subscribe_all(ws_mgr.fanout)

        # Welle-4 follow-up: bridge MissionBus -> SubAgentRegistry so the
        # Sub-Agents board lights up. The legacy publishers for
        # OpenClawTaskStarted/Completed were removed in the migration; without
        # this hook the dashboard stays empty even while missions are flowing.
        registry = getattr(self.app.state, "sub_agent_registry", None)
        if registry is not None:
            registry.attach_mission_bus(result["manager"].bus)

        recovered = result["recovered_mission_ids"]
        sweep = result["sweep_stats"]
        logger.info(
            "Phase-6 stack online (db={}, recovered={}, sweep={}/{}/{} scanned/removed/errors)",
            db_path, len(recovered),
            sweep["scanned"], sweep["removed"], sweep["errors"],
        )

        # Periodic recovery re-sweep (2026-06-10, mission 019eb25c): boot
        # recovery runs ONCE and — correctly — skips a mission whose owner still
        # looks live (active-guard). But that guard is boot-only: when the owning
        # instance dies AFTER boot, the orphan stays non-terminal (e.g.
        # CRITIQUING) in the DB and UI forever ("missions never find an end").
        # This timer re-runs the SAME conservative, active-guarded sweep so an
        # orphan is finalized on the next tick once it crosses the unchanged
        # staleness threshold. Gated on the same primary-instance flag as boot
        # recovery — a secondary/--no-lock instance must never sweep a primary's
        # live missions. Cancelled on shutdown (see start()'s cleanup path).
        if _is_primary:
            from jarvis.missions.recovery import periodic_recovery_sweep

            self._missions_resweep_task = asyncio.create_task(
                periodic_recovery_sweep(result["manager"].store),
                name="mission-recovery-resweep",
            )

    async def _init_wiki_integration(self) -> None:
        """Phase B5 wiki write-wiring: bootstrap SessionRollupWorker + WikiCurator.

        Subscribes to ``IdleEntered``; writes session digests into the
        Obsidian vault at session end.  No-op when
        ``cfg.wiki_integration.enabled`` is False.
        """
        from jarvis.memory.wiki.integration import bootstrap_wiki_integration
        from jarvis.memory.wiki.page import MarkdownPageRepository

        wiki_cfg = self.cfg.wiki_integration
        if not wiki_cfg.enabled:
            logger.info("wiki_integration: disabled — skipping bootstrap")
            try:
                from jarvis.memory.wiki.health import health as _wiki_health

                _wiki_health.record_bootstrap(False, error="disabled in config")
            except Exception:  # noqa: BLE001 — health recording must never break boot
                logger.debug("wiki health.record_bootstrap(disabled) failed", exc_info=True)
            return

        repo = MarkdownPageRepository()
        from jarvis.memory.wiki.vault_root import resolve_vault_root

        vault_root = resolve_vault_root(wiki_cfg.vault_root).path

        def _wiki_scheduler_factory(*, curator):  # noqa: ANN001, ANN202
            """Build the CuratorScheduler (cooldown + VaultLock, Wave-2 B4).

            Gates the Stage-2 consolidation drain; the consolidator itself is
            attached later via ``scheduler.attach_consolidator``.
            """
            from jarvis.memory.wiki.lock import VaultLock
            from jarvis.memory.wiki.scheduler import CuratorScheduler

            sched_cfg = self.cfg.wiki_scheduler
            lock_path = Path(sched_cfg.lock_path)
            if not lock_path.is_absolute():
                lock_path = Path.cwd() / lock_path
            return CuratorScheduler(
                curator=curator,
                lock=VaultLock(
                    lock_path,
                    stale_after_seconds=int(sched_cfg.lock_stale_after_seconds),
                ),
                config=sched_cfg,
            )

        handle = await bootstrap_wiki_integration(
            bus=self.bus,
            repo=repo,
            vault_root=vault_root,
            config=wiki_cfg,
            brain_caller=None,      # curator uses BrainProviderRegistry internally
            scheduler_factory=_wiki_scheduler_factory,
            voice_bridge_config=self.cfg.memory.wiki.voice_bridge,
        )
        self._wiki_integration_handle = handle
        logger.info("wiki_integration: bootstrap_wiki_integration succeeded")
        try:
            from jarvis.memory.wiki.health import health as _wiki_health

            _wiki_health.record_bootstrap(True)
        except Exception:  # noqa: BLE001 — health recording must never break boot
            logger.debug("wiki health.record_bootstrap(True) failed", exc_info=True)

    def _init_wiki_boot_index(self, *, background: bool = False) -> None:
        """Rebuild the derived FTS view against the active vault.

        Production uses a daemon thread after app readiness (AP-26). Tests and
        explicit callers may keep the synchronous default for deterministic
        verification.
        """
        import sqlite3
        import threading

        from jarvis.memory.wiki.db_path import resolve_wiki_db_path
        from jarvis.memory.wiki.fts_index import rebuild_index

        wiki_cfg = self.cfg.wiki_integration
        if not wiki_cfg.enabled:
            return

        from jarvis.memory.wiki.vault_root import resolve_vault_root

        vault_root = resolve_vault_root(wiki_cfg.vault_root).path
        if not vault_root.is_dir():
            logger.info("wiki_boot_index: vault missing — skipping ({})", vault_root)
            return

        db_path = resolve_wiki_db_path(self.cfg.memory.data_dir)

        def _reconcile() -> None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            try:
                indexed = rebuild_index(vault_root, conn)
                logger.info(
                    "wiki_boot_index: reconciled {} page(s) from {} into {}",
                    indexed,
                    vault_root,
                    db_path,
                )
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "wiki_boot_index: background reconciliation failed"
                )
                try:
                    from jarvis.memory.wiki.health import health as _wiki_health

                    _wiki_health.record_chain_failure(
                        f"wiki index reconciliation failed: {exc}"
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("wiki index health recording failed", exc_info=True)
            finally:
                conn.close()

        if background:
            thread = threading.Thread(
                target=_reconcile,
                name="jarvis-wiki-index-reconcile",
                daemon=True,
            )
            thread.start()
            self._wiki_index_thread = thread
            return
        _reconcile()

    def _init_wiki_watcher(self) -> None:
        """Phase B3 — start the WikiWatcher for desktop live-reload.

        Watches the configured vault root and publishes WikiPageChanged
        events on the shared bus. Failures are logged but never raised;
        the desktop app must boot when the vault is empty or watchdog
        cannot start an observer.
        """
        from jarvis.memory.wiki.db_path import resolve_wiki_db_path
        from jarvis.memory.wiki.vault_root import resolve_vault_root
        from jarvis.memory.wiki.watcher import WikiWatcher

        wiki_cfg = self.cfg.wiki_integration
        if not wiki_cfg.enabled:
            logger.info("wiki_watcher: wiki_integration disabled — skipping")
            return

        vault_root = resolve_vault_root(wiki_cfg.vault_root).path
        db_path = resolve_wiki_db_path(self.cfg.memory.data_dir)

        watcher = WikiWatcher(
            vault_root=vault_root,
            bus=self.bus,
            db_path=db_path,
        )
        try:
            started = watcher.start()
        except FileNotFoundError as exc:
            logger.warning("wiki_watcher: vault missing: {}", exc)
            return
        except PermissionError as exc:
            logger.warning("wiki_watcher: permission denied: {}", exc)
            return

        if not started:
            logger.info("wiki_watcher: did not start — live-reload inactive")
            return

        self._wiki_watcher = watcher
        self.app.state.wiki_watcher = watcher
        logger.info("wiki_watcher: started for vault {}", vault_root)

    async def _init_task_stack(self) -> None:
        """Phase-5 task-queue wiring (BUG-007).

        Builds the TaskStore + TaskRunner + TaskScheduler, runs the
        startup cleanup for ``running`` tasks (app exit), and starts
        the scheduler loop as a background task.

        The runner is wired with the brain so agentic (``agent``) tasks run a
        tool-restricted turn unattended (read-only/monitor-tier plugins pass;
        ask-tier still gates). ``speak``/``harness_dispatch`` actions still
        have no TTS/harness here — those remain follow-up wiring. The UI is
        live regardless (list + cancel + detail timeline) since the routes
        only touch store + scheduler.
        """
        from jarvis.control.cancel import CancelToken
        from jarvis.tasks.approval_bridge import TaskAutoApprover
        from jarvis.tasks.runner import TaskRunner
        from jarvis.tasks.scheduler import TaskScheduler
        from jarvis.tasks.store import TaskStore

        data_dir = Path(self.cfg.memory.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        # Tasks share the DB with memory (ADR-0003) — an additive schema.
        db_path = data_dir / "jarvis.db"

        store = TaskStore(db_path)
        await store.init()

        # Crash recovery: all running -> interrupted, plus an error log entry.
        recovered = await store.cleanup_interrupted()
        if recovered:
            logger.info("TaskStack: cleaned up {} interrupted tasks from the previous run", recovered)

        # Wire the brain so agentic (`agent`) tasks can run a tool-restricted
        # turn unattended. app.state.brain is set before server.start() (see
        # launcher.py); a headless Mock-Brain without run_task falls back to
        # None → agent tasks fail cleanly instead of crashing.
        brain = getattr(self.app.state, "brain", None)
        agent_brain = brain if (brain is not None and hasattr(brain, "run_task")) else None
        # Unattended pre-authorization: agent tasks whose plugins were toggled
        # write/full auto-approve those ask-tier calls for their own turn.
        auto_approver = TaskAutoApprover(self.bus)

        runner = TaskRunner(
            store=store,
            bus=self.bus,
            agent_brain=agent_brain,
            auto_approver=auto_approver,
        )
        scheduler = TaskScheduler(store=store, bus=self.bus, runner=runner)
        scheduler.bind_bus()
        await scheduler.hydrate()

        cancel_token = CancelToken()
        scheduler_task = asyncio.create_task(
            scheduler.run(cancel_token), name="task-scheduler-loop"
        )

        self._task_store = store
        self._task_runner = runner
        self._task_scheduler = scheduler
        self._task_cancel_token = cancel_token
        self._task_scheduler_task = scheduler_task

        # Routes read from app.state — see tasks_routes.py:28
        self.app.state.task_store = store
        self.app.state.task_scheduler = scheduler
        self.app.state.task_runner = runner

        logger.info(
            "TaskStack live (db={}, recovered={}, hydrated={} scheduled)",
            db_path, recovered, len(scheduler._heap),
        )

    def _read_sessions_toml_section(self) -> dict:
        """The raw ``[sessions]`` section from jarvis.toml (may be empty).

        That section is NOT in the Pydantic tree (see assumption A-2 in the
        Phase-7 bootstrap), hence tomllib directly.
        """
        toml_path = Path("jarvis.toml")
        if not toml_path.exists():
            return {}
        try:
            import tomllib
            with toml_path.open("rb") as fh:
                raw = tomllib.load(fh)
            return raw.get("sessions", {}) or {}
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "Could not read the [sessions] section from jarvis.toml — using defaults"
            )
            return {}

    def _resolve_sessions_db_path(self) -> Path:
        """Absolute path to sessions.db, shared by recorder and board.

        A relative ``[sessions].db_path`` is anchored at the data-dir parent
        (the repo root), never at the process CWD, so every consumer sees the
        SAME database regardless of where the app was launched from.
        """
        section = self._read_sessions_toml_section()
        db_path = Path(str(section.get("db_path", "data/sessions.db")))
        if not db_path.is_absolute():
            db_path = Path(self.cfg.memory.data_dir).parent / db_path
        return db_path

    def _init_session_stack(self) -> None:
        """Voice-session recorder + store for the transcription view.

        Bootstrap analogous to ``_init_mission_stack``, but sync — the store
        uses sqlite3 + threading.Lock (see ``jarvis/sessions/store.py``).
        """
        from jarvis.sessions.init import bootstrap_sessions

        # Defaults match jarvis.toml [sessions] (enabled=true,
        # data/sessions.db, retention 30d). User can override via TOML.
        section = self._read_sessions_toml_section()
        enabled = bool(section.get("enabled", True))
        try:
            retention_days = int(section.get("retention_days", 30))
        except (TypeError, ValueError):
            retention_days = 30

        if not enabled:
            logger.info("Voice-session recorder disabled via [sessions].enabled=false")
            return

        db_path = self._resolve_sessions_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        result = bootstrap_sessions(
            bus=self.bus,
            db_path=db_path,
            enabled=True,
            retention_days=retention_days,
        )
        self.app.state.session_store = result["store"]
        self._session_recorder = result["recorder"]
        logger.info("Session recorder online (db={}, retention={}d)", db_path, retention_days)

    async def _init_channel_stack(self) -> None:
        """Bootstraps FriendRegistry + ChannelManager + starts all channels.

        The Friends UI works even without Telegram (FriendRegistry is always
        there). TelegramChannel lands in ``start_errors`` when the token is
        missing, without blocking the other channels.
        """
        from jarvis.channels.bootstrap import bootstrap_channels
        from jarvis.channels.chat_bridge import ChannelChatBridge

        # data_dir might not exist on every branch under cfg.memory;
        # fall back to ./data so the code stays branch-portable.
        data_dir = Path("data")
        try:
            mem_dir = getattr(self.cfg, "memory", None)
            if mem_dir is not None and getattr(mem_dir, "data_dir", None):
                data_dir = Path(mem_dir.data_dir)
        except Exception:  # noqa: BLE001
            pass
        data_dir.mkdir(parents=True, exist_ok=True)
        friends_db = data_dir / "friends.db"

        # Telegram/Discord configs from integrations.* (branch-portable via getattr)
        tg_cfg = None
        dc_cfg = None
        integrations = getattr(self.cfg, "integrations", None)
        if integrations is not None:
            tg_cfg = getattr(integrations, "telegram", None)
            dc_cfg = getattr(integrations, "discord", None)

        manager, registry = await bootstrap_channels(
            bus=self.bus,
            telegram_config=tg_cfg,
            discord_config=dc_cfg,
            friends_db_path=friends_db,
            auto_start=True,
        )
        self.app.state.friend_registry = registry
        self.app.state.channel_manager = manager
        bridge = ChannelChatBridge(bus=self.bus, manager=manager)
        bridge.start()
        self._channel_chat_bridge = bridge
        self.app.state.channel_chat_bridge = bridge

        if "telegram" in manager.started():
            logger.info("Friends-Stack live: Telegram-Channel aktiv")
        else:
            errs = manager.start_errors().get("telegram")
            if errs:
                logger.info(
                    "Friends-Stack live: Telegram-Channel disabled ({})", errs
                )
            else:
                logger.info(
                    "Friends-Stack live: Telegram-Channel disabled (config off)"
                )

    async def stop(self) -> None:
        # Stop token refresh before the plugin registry so an in-flight refresh
        # cannot enqueue a live-session rebuild while that registry is closing.
        await self._stop_marketplace_refresh_scheduler()

        # Stop the skill watcher, otherwise the watchdog thread stays behind
        # as a zombie and prevents the process from exiting.
        if self._skill_registry is not None:
            try:
                self._skill_registry.stop_watcher()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("SkillRegistry watcher stop failed")

        # Close the doc watcher + FTS5 connection.
        if self._doc_registry is not None:
            try:
                self._doc_registry.close()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("DocRegistry close failed")

        # Unset the shared CLI registry — otherwise the module singleton keeps
        # a reference to a "dead" registry when the server restarts.
        try:
            from jarvis.clis.shared import set_active_registry
            set_active_registry(None)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).debug("CLI shared-registry cleanup failed: {}", exc)

        # Symmetric teardown for the plugin registry: unset the module singleton
        # AND stop the registry so each connected plugin's MCPClient (with its
        # AsyncExitStack / subprocess) is closed cleanly. Without this, an
        # in-process restart (--no-lock parallel dev, test teardown) would leave
        # a stale shared handle and leaked MCP sessions.
        if self._plugin_registry is not None:
            try:
                from jarvis.marketplace.plugin_shared import set_active_plugin_registry
                set_active_plugin_registry(None)
                await self._plugin_registry.stop()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).debug(
                    "PluginToolRegistry cleanup failed: {}", exc
                )
            self._plugin_registry = None
            self.app.state.plugin_registry = None

        # Cleanly stop the board-aggregator task before uvicorn goes down. The
        # run_forever() loop catches CancelledError itself and re-raises it.
        if self._board_aggregator_task is not None:
            self._board_aggregator_task.cancel()
            try:
                await asyncio.wait_for(self._board_aggregator_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Board-aggregator shutdown error")
            self._board_aggregator_task = None
        # Voice-ready backstop watchdog: usually already finished (it fires once
        # and returns), but cancel it if a shutdown lands inside its deadline so
        # it is never orphaned as a pending task at loop close.
        _watchdog_task = getattr(self, "_voice_ready_watchdog_task", None)
        if _watchdog_task is not None:
            _watchdog_task.cancel()
            try:
                await asyncio.wait_for(_watchdog_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).debug("voice-ready watchdog shutdown: {}", exc)
            self._voice_ready_watchdog_task = None
        if self._bio_scheduler is not None:
            try:
                await self._bio_scheduler.stop()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).debug("BioScheduler.stop(): {}", exc)
        if self._board_evaluator is not None:
            try:
                self._board_evaluator.close()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).debug("AchievementEvaluator.close(): {}", exc)
        if self._board_aggregator is not None:
            try:
                self._board_aggregator.close()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).debug("Board-Aggregator close(): {}", exc)

        # Phase B5 wiki write-wiring: unsubscribe + drain in-flight rollup task.
        wiki_handle = getattr(self, "_wiki_integration_handle", None)
        if wiki_handle is not None:
            try:
                await wiki_handle.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).debug("WikiIntegration.shutdown() failed")
            self._wiki_integration_handle = None

        # Phase B3 wiki live-reload: stop the watchdog observer cleanly
        # before uvicorn unwinds. The bus is still alive at this point
        # so any final debounce-fired events can publish without raising.
        wiki_watcher = getattr(self, "_wiki_watcher", None)
        if wiki_watcher is not None:
            try:
                await wiki_watcher.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).debug(
                    "WikiWatcher.shutdown() failed"
                )
            self._wiki_watcher = None

        # Terminate PTY sessions before uvicorn goes down — prevents reader
        # threads from trying to write into a closed loop.
        try:
            self._pty.close_all()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("PTY cleanup failed")

        # Cleanly shut down the Phase-6 mission stack — closing the
        # connection closes the SQLite DB. Cancel the periodic recovery
        # re-sweep + cleanup task BEFORE the store closes (otherwise the
        # re-sweep ticks against a closed connection).
        resweep_task = getattr(self, "_missions_resweep_task", None)
        if resweep_task is not None:
            resweep_task.cancel()
            try:
                await asyncio.wait_for(resweep_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._missions_resweep_task = None

        cleanup_task = getattr(self, "_missions_cleanup_task", None)
        if cleanup_task is not None:
            cleanup_task.cancel()
            try:
                await asyncio.wait_for(cleanup_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._missions_cleanup_task = None

        # Screenshot-retention background task (opt-in via retention_days > 0).
        screenshot_task = getattr(self, "_screenshot_retention_task", None)
        if screenshot_task is not None:
            screenshot_task.cancel()
            try:
                await asyncio.wait_for(screenshot_task, timeout=2.0)
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                logger.warning(
                    "retention_task: did not stop within 2s — may be blocking on I/O"
                )
            self._screenshot_retention_task = None

        # Flush + close the flight-recorder audit log so the last buffered events
        # reach disk before shutdown (it otherwise auto-flushes every ~1s).
        recorder = getattr(self, "_flight_recorder", None)
        if recorder is not None:
            try:
                await recorder.flush()
                await recorder.close()
            except Exception as exc:  # noqa: BLE001 — shutdown best-effort
                logger.opt(exception=exc).debug("Flight recorder close: {}", exc)
            self._flight_recorder = None

        # Finalize in-flight missions BEFORE the store closes: a restart used
        # to kill the process with missions still running, leaving them
        # non-terminal until the recovery re-sweep buried them 30 min later as
        # opaque crash_recovery/ERROR cards (live missions 019eb27f/019eb288,
        # 2026-06-10 19:24). cancel_all_running flips each to an honest
        # CANCELLED('app_shutdown') and awaits the dying run tasks briefly.
        kontrollierer = getattr(self.app.state, "kontrollierer", None)
        cancel_all = getattr(kontrollierer, "cancel_all_running", None)
        if cancel_all is not None:
            try:
                await cancel_all(reason="app_shutdown")
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "In-flight mission finalize on shutdown failed"
                )

        try:
            await self._mission_tool_approvals.deny_all(reason="app_shutdown")
        except Exception as exc:  # noqa: BLE001 - shutdown remains best-effort
            logger.opt(exception=exc).warning(
                "Pending mission tool approval cleanup failed"
            )

        mission_manager = getattr(self.app.state, "mission_manager", None)
        if mission_manager is not None:
            try:
                await mission_manager.stop()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "MissionManager.stop() failed"
                )
            self.app.state.mission_manager = None
            self.app.state.kontrollierer = None

        # Shut down the Phase-5 task stack: cancel the scheduler loop, wait
        # for running runner tasks (max 2s), close the store.
        if self._task_scheduler is not None and self._task_cancel_token is not None:
            try:
                self._task_cancel_token.cancel("server_shutdown")
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).debug("Task-CancelToken.cancel(): {}", exc)
        if self._task_scheduler_task is not None:
            self._task_scheduler_task.cancel()
            try:
                await asyncio.wait_for(self._task_scheduler_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("TaskScheduler loop shutdown error: {}", exc)
            self._task_scheduler_task = None
        if self._task_scheduler is not None:
            try:
                await self._task_scheduler.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).debug("TaskScheduler.shutdown(): {}", exc)
        if self._task_store is not None:
            try:
                await self._task_store.close()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("TaskStore.close() failed")
        self._task_scheduler = None
        self._task_store = None
        self._task_runner = None
        self._task_cancel_token = None
        self.app.state.task_store = None
        self.app.state.task_scheduler = None
        self.app.state.task_runner = None

        # Detach the voice-session recorder from the bus + close the DB connection.
        recorder = getattr(self, "_session_recorder", None)
        store = getattr(self.app.state, "session_store", None)
        if recorder is not None or store is not None:
            try:
                from jarvis.sessions.init import shutdown_sessions
                shutdown_sessions({"store": store, "recorder": recorder})
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "SessionRecorder shutdown failed"
                )
            self._session_recorder = None
            self.app.state.session_store = None

        channel_manager = getattr(self.app.state, "channel_manager", None)
        friend_registry = getattr(self.app.state, "friend_registry", None)
        channel_bridge = getattr(self, "_channel_chat_bridge", None)
        if channel_bridge is not None:
            try:
                await channel_bridge.stop()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "ChannelChatBridge shutdown failed"
                )
            self._channel_chat_bridge = None
            self.app.state.channel_chat_bridge = None
        if channel_manager is not None or friend_registry is not None:
            try:
                from jarvis.channels.bootstrap import shutdown_channels

                if channel_manager is not None and friend_registry is not None:
                    await shutdown_channels(channel_manager, friend_registry)
                elif channel_manager is not None:
                    await channel_manager.stop_all()
                elif friend_registry is not None:
                    await friend_registry.close()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "ChannelStack shutdown failed"
                )
            self.app.state.channel_manager = None
            self.app.state.friend_registry = None

        if self._server is None:
            return
        self._server.should_exit = True
        if self._serve_task is not None:
            try:
                await asyncio.wait_for(self._serve_task, timeout=5.0)
            except TimeoutError:
                logger.warning("uvicorn shutdown timeout — force-cancel")
                self._serve_task.cancel()
        self._server = None
        self._serve_task = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.started
