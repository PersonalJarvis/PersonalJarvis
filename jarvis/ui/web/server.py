"""FastAPI + WebSocket-Server für die Desktop-UI (Phase 1a).

Verantwortung:
- REST-Endpoints für Health, Config-Read-Only, Plugin-Discovery, Debug.
- WebSocket `/ws` mit Welcome-Frame, Bus-Forwarding, Input-Validation.
- Optional Static-Mount für den React-Build unter `dist/` (Production-Mode).

Explizit NICHT hier:
- Channel-Adapter-Logik (in `jarvis/channels/web.py`).
- React-Build selbst (Agent 5).
- Single-Instance-Focus-Logik (nur Placeholder-Endpoint).
"""
from __future__ import annotations

import asyncio
import os
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
    """In-Process uvicorn + FastAPI, betrieben vom Orchestrator-Loop."""

    def __init__(self, cfg: JarvisConfig, bus: EventBus | None = None) -> None:
        self.cfg = cfg
        self.bus = bus if bus is not None else get_default_bus()
        self._clients: dict[str, WebSocket] = {}
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        # PTY-Manager fuer die Desktop-App-Terminal-View. Sessions sind
        # global pro Server-Instanz — sie ueberleben WS-Reconnects, aber
        # nicht den Server-Shutdown (siehe stop()).
        self._pty = PtyManager()
        # Per-Terminal Line-Buffer fuer Audit-Events.
        self._pty_input_buffers: dict[str, str] = {}
        self._pty_shell_ids: dict[str, str] = {}
        # Skill-Registry: nach First-Run-Bootstrap auf user_skills_dir() gewatcht.
        # Watcher startet in ``start()`` sobald die Event-Loop laeuft.
        self._skill_registry: Any | None = None
        # Doc-Registry: gewatcht auf default_doc_roots(); FTS5-Index unter
        # docs_index_db_path(). Watcher startet in ``start()``.
        self._doc_registry: Any | None = None
        self._cli_registry: Any | None = None
        self._plugin_registry: Any | None = None
        # Board-Stack wird in _setup_board() befuellt (im _build_app-Pfad).
        self._board_aggregator: Any | None = None
        self._board_aggregator_task: asyncio.Task[None] | None = None
        self._board_evaluator: Any | None = None
        self._bio_scheduler: Any | None = None
        self._bio_generator: Any | None = None
        # Voice-Session-Recorder — laeuft am EventBus mit, wird in
        # _init_session_stack() befuellt. None solange Recorder disabled.
        self._session_recorder: Any | None = None
        # Phase-5 Task-Stack (Aufgaben-View). _init_task_stack() befuellt
        # alle drei; ohne Wiring liefert /api/tasks 503 (siehe BUG-007).
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
        self.app: FastAPI = self._build_app()

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
            # Swagger-UI auf ``/api/_swagger`` — der semantische ``/api/docs``-
            # Pfad gehoert dem Doc-Browser-Router (siehe docs_routes.py).
            docs_url="/api/_swagger",
            openapi_url="/api/openapi.json",
        )

        # CORS nur für den Vite-Dev-Server — Production liefert Frontend aus
        # dist/ und braucht keine Cross-Origin-Requests.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[self.cfg.ui.vite_dev_url],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self._register_rest_routes(app)
        self._register_ws_route(app)

        # Skill-Registry-Setup: Bootstrap (Builtin-Skills kopieren) + Registry
        # anlegen + reload_sync(). Der watchdog-Watcher wird erst in ``start()``
        # aktiviert, wenn eine Event-Loop laeuft.
        self._setup_skill_registry(app)

        # Doc-Registry: Markdown-Discovery unter ``docs/`` + Geschwistern,
        # FTS5-Index. Watchdog ebenfalls erst in ``start()`` aktiviert.
        self._setup_doc_registry(app)

        # CLI-Tool-Registry — haelt Katalog + Prober + Auth + UsageLog im selben
        # State-Objekt, das die REST-Routes und der Brain-Launcher teilen.
        self._setup_cli_registry(app)

        # Plugin-Tool-Registry — wired marketplace plugins as live brain tools.
        self._setup_plugin_registry(app)

        # Sub-Agent-Registry (Dashboard-Feature) — abonniert sofort den Bus.
        try:
            from jarvis.agents import SubAgentRegistry

            sub_agent_registry = SubAgentRegistry(bus=self.bus)
            sub_agent_registry.attach()
            app.state.sub_agent_registry = sub_agent_registry
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("SubAgentRegistry-Setup fehlgeschlagen")
            app.state.sub_agent_registry = None

        # MCP-, Tool-, Provider-, Profile-, Task-, Skills-, CLI- und Sub-Agents-
        # Routes einhaengen — lazy Import vermeidet Zyklen.
        from .board_routes import (
            board_router as board_meta_router,
        )
        from .board_routes import (
            router as board_router,
        )
        from .chats_routes import router as chats_router
        from .cli_routes import router as cli_router
        from .contacts_routes import router as contacts_router
        from .control_routes import router as control_router
        from .docs_routes import router as docs_router
        from .federation_proxy_routes import router as federation_proxy_router
        from .friends_routes import router as friends_router
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
        from .outputs_routes import router as outputs_router
        from .preview_routes import router as preview_router
        from .profile_routes import router as profile_router
        from .provider_routes import router as provider_router
        from .review_routes import router as review_router
        from .sessions_routes import router as sessions_router
        from .settings_routes import router as settings_router
        from .setup_routes import router as setup_router
        from .skills_routes import router as skills_router
        from .socials_routes import router as socials_router
        from .sub_agents_routes import router as sub_agents_router
        from .tasks_routes import router as tasks_router
        from .telephony_routes import router as telephony_router
        from .tools_routes import router as tools_router
        from .wiki_routes import router as wiki_router
        from .wiki_ws import router as wiki_ws_router
        from .workflows_routes import router as workflows_router
        # Conductor ist ein externes Package im selben Monorepo. Import
        # defensiv — wer das Repo ohne conductor checkt aus, kriegt sonst
        # hier einen ImportError beim Server-Boot.
        try:
            from conductor.api import router as conductor_router
        except ImportError as exc:
            logger.warning("Conductor-Modul nicht verfuegbar: {} — Conductor-View bleibt leer", exc)
            conductor_router = None
        app.include_router(mcp_router)
        app.include_router(tools_router)
        app.include_router(provider_router)
        app.include_router(control_router)
        app.include_router(profile_router)
        app.include_router(settings_router)
        app.include_router(tasks_router)
        app.include_router(skills_router)
        app.include_router(docs_router)
        app.include_router(cli_router)
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
        app.include_router(sub_agents_router)
        app.include_router(outputs_router)
        # Socials section — project social-media links (pure file store, no Brain dep).
        app.include_router(socials_router)
        # Contacts section — user-curated address book (pure file store, no Brain dep).
        app.include_router(contacts_router)
        app.include_router(workflows_router)
        if conductor_router is not None:
            app.include_router(conductor_router)
        app.include_router(preview_router)
        app.include_router(board_router)
        app.include_router(board_meta_router)
        app.include_router(federation_proxy_router)
        # Phase 8.5 — Review-Pipeline read-only UI (Plan §6.5).
        app.include_router(review_router)
        # Voice-Session-Transkriptions-View (Sidebar -> "Transkription").
        # Liefert 503 solange app.state.session_store nicht gesetzt ist.
        app.include_router(sessions_router)
        # Chats conversation manager — unified text+voice history, resume +
        # "Speak in this conversation". Reuses chat_store + session_store +
        # brain + speech_pipeline from app.state (graceful 503s when absent).
        app.include_router(chats_router)
        # Default: kein Recorder verdrahtet — _init_session_stack() in start()
        # setzt das beim Erfolg um.
        app.state.session_store = None
        # Phase-6 Mission-Stack — Auth-Token vor allen anderen, damit der
        # Browser ihn ueberhaupt holen kann; danach REST + WS + PTY.
        app.include_router(missions_auth_router)
        app.include_router(missions_router)
        app.include_router(missions_ws_router)
        app.include_router(missions_pty_router)
        # Phase B9 — Obsidian Setup Wizard (detect install + register vault).
        app.include_router(setup_router)
        # Phase B3 — Wiki view (read-only REST API over the Obsidian vault).
        app.include_router(wiki_router)
        # Phase B3 — Desktop wiki view live-reload WS endpoint.
        # Forwards WikiPageChanged events from the shared EventBus to
        # subscribed UI clients. WikiWatcher is started in start().
        app.include_router(wiki_ws_router)
        # ConnectionManager-Singleton fuer den globalen Event-Stream. Wird
        # in start() an MissionBus.subscribe_all() angehaengt.
        app.state.missions_ws_manager = _MissionsConnMgr()
        # MissionManager + Kontrollierer werden in start() lazy verdrahtet
        # (brauchen running event-loop fuer aiosqlite). Default ist None,
        # damit die REST-Routes 503 zurueckgeben statt zu crashen.
        app.state.mission_manager = None
        app.state.kontrollierer = None

        # Board-Aggregator (Personal-Mastery-Dashboard) — der Aggregator wird
        # in start() als Background-Task gelaufen lassen, der Store ist
        # read-only und sofort verfuegbar.
        self._setup_board(app)

        # Preview-Registry — subscribed auf PreviewServerStarted/Closed Events.
        try:
            from jarvis.preview.registry import PreviewRegistry

            preview_registry = PreviewRegistry(bus=self.bus)
            preview_registry.attach()
            app.state.preview_registry = preview_registry
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("PreviewRegistry-Setup fehlgeschlagen")
            app.state.preview_registry = None

        # Cfg fuer die Routes verfuegbar machen (z.B. Admin-Pass-Check in
        # skills_routes). Andere Routes nutzen es ebenfalls kuenftig.
        app.state.config = self.cfg
        app.state.bus = self.bus

        self._register_static_or_spa(app)

        return app

    def _setup_board(self, app: FastAPI) -> None:
        """Board-Store + Aggregator + Evaluator + BioGenerator initialisieren.

        Der Store ist read-only und steht sofort bereit (erzeugt leere DB
        beim ersten Query, damit der UI-Mount nicht in 500 laeuft). Der
        Aggregator laeuft in ``start()`` als Background-Task, der Evaluator
        und der BioScheduler subscriben dort auf den Bus.
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
            # most installs). Matches the [sessions] default db path used by
            # bootstrap_sessions (cwd-relative ``data/sessions.db``).
            sessions_db_path = Path("data/sessions.db")

            aggregator = BoardAggregator(
                jsonl_dir=jsonl_dir,
                db_path=db_path,
                sessions_db_path=sessions_db_path,
            )
            store = BoardStore(db_path=db_path)
            evaluator = AchievementEvaluator(db_path=db_path, bus=self.bus)
            bio_store = BioStore(db_path=db_path)

            # Optional-Datenquellen-Pfade (Awareness, Missions, Self-Mod).
            # Wenn die Datei/DB nicht existiert, faellt der Block still im
            # Prompt aus — kein Fehler. Pfade kommen aus ``user_data_dir()``,
            # nicht aus relativen Strings, damit App-Restart in einem anderen
            # CWD nicht den Datenhunger des Generators verliert.
            data_root = user_data_dir() / "data"
            recall_db = data_root / "memory.db"
            missions_db = data_root / "missions.db"
            self_mod_log = data_root / "self_mod.log"

            bio_cfg = self.cfg.board.bio
            cfg = self.cfg

            def _bio_brain_resolver() -> Any:
                # Lazy: Cfg + Bus aus Closure einfangen, sodass ein
                # spaeterer Provider-Switch via UI direkt zieht (resolver
                # invalidiert seinen Cache via ConfigReloaded).
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
                "Board vorbereitet (jsonl={}, db={}, achievements={})",
                jsonl_dir, db_path, len(evaluator.list_all()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "Board-Setup fehlgeschlagen — /board liefert leer"
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
        """First-Run-Bootstrap + SkillRegistry an ``app.state`` haengen.

        Fehlerfaelle (z.B. read-only Filesystem im Test-Runner) sind nicht
        fatal — die UI zeigt dann "Keine Skills" statt abzustuerzen.
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
            registry.reload_sync()
            self._skill_registry = registry
            app.state.skill_registry = registry
            logger.info(
                "SkillRegistry initialisiert ({} Skills geladen aus {})",
                len(registry.list()), skills_root,
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "SkillRegistry-Setup fehlgeschlagen — Skills-View bleibt leer"
            )
            app.state.skill_registry = None

    def _setup_doc_registry(self, app: FastAPI) -> None:
        """Doc-Registry hochziehen + FTS5-Index initial befuellen.

        Roots = ``default_doc_roots()`` (siehe ``jarvis/core/paths.py``).
        Index-DB liegt unter ``user_data_dir()/data/docs_index.sqlite``.

        Fehlerfaelle (read-only FS, Index-DB-Lock) sind nicht fatal — die UI
        zeigt dann "Keine Docs verfuegbar" statt zu crashen.
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
            registry.reload_sync()
            self._doc_registry = registry
            app.state.doc_registry = registry
            logger.info(
                "DocRegistry initialisiert ({} Docs aus {} Roots)",
                len(registry.list()), len(roots),
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "DocRegistry-Setup fehlgeschlagen — Docs-View bleibt leer"
            )
            app.state.doc_registry = None

    def _setup_cli_registry(self, app: FastAPI) -> None:
        """``CliToolRegistry`` aufsetzen und Katalog-Probe asynchron nachziehen.

        Der Konstruktor erstellt die Registry ohne zu proben (nichtblockierend).
        Ein asyncio-Task wird in ``start()`` geplant, der ``bootstrap()`` ausfuehrt —
        bis dahin liefern die Endpoints ``status=checking`` fuer alle Eintraege.

        Fehlerfaelle: read-only FS oder DB-Lock → kein Crash, nur leere Registry.
        """
        try:
            from jarvis.clis.registry import CliToolRegistry
            from jarvis.clis.shared import set_active_registry

            registry = CliToolRegistry(bus=self.bus)
            self._cli_registry = registry
            app.state.cli_registry = registry
            # Shared-State: ab jetzt sehen CliToolLoader und make_cli_patterns_fn
            # dieselbe Instanz — der LLM bekommt die echten verbundenen CLIs als
            # Tools, nicht eine leere Katalog-Kopie.
            set_active_registry(registry)
            logger.info(
                "CliToolRegistry erstellt ({} Katalog-Eintraege, bootstrap pending)",
                len(registry.catalog().all()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "CliToolRegistry-Setup fehlgeschlagen — CLIs-View bleibt leer"
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
            logger.info("PluginToolRegistry erstellt (bootstrap pending)")
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "PluginToolRegistry-Setup fehlgeschlagen — Plugins bleiben worker-only"
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
            # Read-only Snapshot — Secrets sind per Design nicht in der Config.
            return cfg.model_dump()

        @app.get("/api/plugins")
        async def get_plugins() -> dict[str, list[str]]:
            try:
                return list_all_plugins()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Plugin-Discovery fehlgeschlagen")
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
            # Placeholder — der eigentliche Fokus-Call landet in der Desktop-App
            # (pywebview-Shell). Hier nur ACK, damit Single-Instance-Ping einen
            # definierten Status bekommt.
            return {"ok": True, "focused": False, "note": "handled by desktop-shell"}

        @app.get("/api/brain/status")
        async def brain_status() -> dict[str, Any]:
            """Liefert den aktuell aktiven Brain-Provider + Modell.

            Frontend nutzt das beim Mount, um den Sidebar-Footer korrekt zu
            initialisieren (statt vom hartcodierten "claude-api"-Default
            auszugehen). Live-Switches kommen weiterhin via WS-Event
            ``BrainProviderChanged``.
            """
            brain = getattr(app.state, "brain", None)
            # BrainManager exposed `active_provider`. MockBrain hat nur `name`.
            provider = (
                getattr(brain, "active_provider", None)
                or getattr(brain, "name", None)
                or "unknown"
            )
            prov_cfg = cfg.brain.providers.get(provider)
            model = getattr(prov_cfg, "model", None) if prov_cfg else None
            return {"provider": provider, "model": model or "unknown"}

        @app.get("/api/openclaw/status")
        async def openclaw_status() -> dict[str, Any]:
            """OpenClaw-Bridge-Status fuer die SettingsView (Welle 3).

            Read-only Snapshot:

            * ``configured``       — Block ``[harness.openclaw]`` in jarvis.toml?
            * ``enabled``          — Bridge-Toggle aus dem Block
            * ``binary_path``      — konfigurierter Pfad
            * ``binary_detected``  — Resolver-Ergebnis (PATH + .cmd/.ps1/.exe)
            * ``version_pin``      — AD-21 Pin (None bei fehlendem Block)
            * ``brain_primary``    — aktiver SUBAGENT-Provider
              (``[brain.sub_jarvis].provider``); Fallback auf ``brain.primary``
              nur wenn kein Subagent-Provider gesetzt ist. NICHT der Router-
              Brain — der Subagent fuehrt die Heavy-Tasks aus.
            * ``provider_slug``    — OpenClaw-Slug des aktiven Subagent-
              Providers nach AD-6 (claude-api->claude-cli)
            * ``model_resolved``   — Override aus Config ODER Frontier-Deep-
              Model des aktiven Subagent-Providers
            * ``mapping``          — vollstaendige Slug-Mapping-Tabelle

            Vertrag: docs/openclaw-bridge.md §4.3 Wizard-/Setup-Erweiterung.
            Endpoint liefert KEINE Secrets — nur Boolean ob Key gesetzt ist.
            """
            import shutil

            oc_cfg = cfg.harness.openclaw
            router_primary = (cfg.brain.primary or "").lower()

            try:
                from jarvis.missions.worker_runtime.provider_map import (
                    MAPPINGS,
                    canonical_subagent_provider,
                    to_provider_slug,
                )
            except Exception:  # noqa: BLE001
                MAPPINGS = ()  # type: ignore[assignment]
                to_provider_slug = None  # type: ignore[assignment]
                canonical_subagent_provider = None  # type: ignore[assignment]

            # The HEAVY-TASK subagent runs on ``[brain.sub_jarvis].provider`` —
            # NOT on ``brain.primary`` (that is only the lightweight router
            # brain). Mark the brain that ACTUALLY executes heavy tasks as
            # active; fall back to the router brain only when no subagent
            # provider is configured (worker then uses its default chain).
            # Mirrors jarvis/missions/init.py::_worker_factory so the displayed
            # brain never drifts from the worker that runs. (Bug 2026-05-28:
            # the UI showed Gemini active while heavy work ran on Claude.)
            sub_cfg = getattr(cfg.brain, "sub_jarvis", None)
            sub_raw = (
                getattr(sub_cfg, "provider", None) if sub_cfg is not None else None
            )
            sub_provider = (
                canonical_subagent_provider(sub_raw)
                if canonical_subagent_provider is not None
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
            if to_provider_slug is not None:
                try:
                    provider_slug = to_provider_slug(primary)
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
                "grok": "grok_api_key",
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
                mapping_rows.append(
                    {
                        "jarvis": mapping.jarvis,
                        "openclaw": mapping.openclaw,
                        "env_var": mapping.env_var,
                        "env_fallback": mapping.env_fallback,
                        "key_set": key_set,
                        "is_active_brain": mapping.jarvis == primary,
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
                "model_resolved": model_resolved,
                "mapping": mapping_rows,
            }

        @app.get("/api/memory/facts")
        async def get_memory_facts() -> dict[str, Any]:
            """Liefert das Core-Memory (Persona, User-Facts, Preferences).

            Frontend zeigt das in der Notizen-View, damit Alex sieht,
            was Jarvis sich gemerkt hat. core_memory.json wird beim
            naechsten Brain-Call automatisch in den System-Prompt
            injiziert — die View ist also Read-Only-Spiegel auf den
            persistenten Memory-State.
            """
            from jarvis.core.config import DATA_DIR
            from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory

            try:
                mem = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
                return {"ok": True, "data": mem.all()}
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Memory-Lesefehler")
                return {"ok": False, "error": str(exc), "data": {}}

        @app.post("/api/memory/facts")
        async def add_memory_fact(payload: dict[str, Any]) -> dict[str, Any]:
            """User-driven Add aus der UI."""
            from jarvis.core.config import DATA_DIR
            from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory

            fact = (payload.get("fact") or "").strip()
            category = (payload.get("category") or "general").strip()
            if not fact:
                return {"ok": False, "error": "fact fehlt"}
            try:
                mem = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
                mem.add_fact(fact, category=category)
                return {"ok": True, "data": mem.all()}
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Memory-Schreibfehler")
                return {"ok": False, "error": str(exc)}

        @app.delete("/api/memory/facts")
        async def delete_memory_fact(payload: dict[str, Any]) -> dict[str, Any]:
            """User-driven Remove aus der UI."""
            from jarvis.core.config import DATA_DIR
            from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory

            fact = (payload.get("fact") or "").strip()
            category = (payload.get("category") or "general").strip()
            if not fact:
                return {"ok": False, "error": "fact fehlt"}
            try:
                mem = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
                ok = mem.remove_fact(fact, category=category)
                return {"ok": ok, "data": mem.all()}
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Memory-Loeschfehler")
                return {"ok": False, "error": str(exc)}

        @app.get("/api/terminal/shells")
        async def terminal_shells() -> dict[str, Any]:
            """Liefert alle auf diesem System installierten Shells.

            Frontend nutzt das, um das Shell-Dropdown nur mit verfuegbaren
            Optionen zu fuellen — kein "Command not found" beim Spawn.
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
                    "WS-Forward fehlgeschlagen",
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
                except Exception as exc:  # noqa: BLE001
                    logger.opt(exception=exc).warning(
                        "WS-Decode-Fehler",
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
            # Unsubscribe zur Memory-Leak-Vermeidung.
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
        """Validiert und dispatched eine eingehende WS-Frame."""
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
            logger.warning("WS-Frame-Validation", errors=exc.errors())
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
        # provider_switch/set_state laufen jetzt über REST (POST /api/brain/switch
        # bzw. POST /api/secrets/{key}). Doppelte Code-Pfade hier entfernt.

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
                    message=f"Shell {shell_id!r} nicht installiert",
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
        # Direkte Antwort-Frame mit terminal_id, damit das Frontend mehrere
        # parallele Spawn-Requests eindeutig zuordnen kann.
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
        # Audit-Log: line-buffer pflegen, bei \r/\n Command emittieren.
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
                # Backspace — letztes Zeichen aus Buffer entfernen
                buf = buf[:-1]
            elif ch >= " ":
                buf += ch
            # Andere Control-Chars (Ctrl-C etc.) ignorieren wir im Buffer.
        # Memory-Cap fuer extrem lange Pasted-Lines.
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
            "<h1>Jarvis startet…</h1>"
            "<p>Frontend wird gerade gebaut oder neu geladen. "
            "Diese Seite aktualisiert sich automatisch.</p>"
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

    async def start(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
    ) -> None:
        import uvicorn

        # Cloud-first fail-closed: never expose a non-loopback bind without a
        # Control API key — the key, not the bind address, is the boundary.
        from jarvis.core import control_key as _control_key
        from jarvis.ui.web.control_auth import assert_bind_safe

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
                    f"uvicorn server auf {host}:{resolved_port} nicht in 5s ready"
                )
            if self._serve_task.done():
                exc = self._serve_task.exception()
                if exc is not None:
                    raise exc
                raise RuntimeError("uvicorn.Server.serve() beendet vor 'started'")
            await asyncio.sleep(0.05)

        # Phase-6 Mission-Stack — MissionManager mit DB-Path aus dem
        # Memory-data_dir der Config (selber Ordner wie data/jarvis.db,
        # eigene Datei data/missions.db). Recovery laeuft im start().
        try:
            await self._init_mission_stack()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "MissionManager-Init fehlgeschlagen — /api/missions liefert 503"
            )

        # Screenshot retention: auto-delete old Vision / Flight-Recorder blobs
        # (data/flight_recorder/blobs/) after the configured window. Runs a
        # one-shot boot sweep plus a periodic background task.
        try:
            await self._init_screenshot_retention()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "Screenshot retention init failed — blobs will not be auto-pruned"
            )

        # Phase B5 wiki write-wiring — SessionRollupWorker + WikiCurator.
        # Subscribes to IdleEntered; gracefully disabled when wiki_integration
        # is not configured or enabled is False.
        try:
            await self._init_wiki_integration()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "WikiIntegration-Init fehlgeschlagen — wiki write-wiring inaktiv"
            )

        # Phase B3 wiki live-reload — start the WikiWatcher so file
        # changes in the vault publish WikiPageChanged events that the
        # /api/wiki/live WS endpoint forwards to the desktop tab.
        try:
            self._init_wiki_watcher()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "WikiWatcher-Init fehlgeschlagen — desktop wiki live-reload inaktiv"
            )

        # Voice-Session-Recorder + Store fuer die Transkriptions-View.
        # Sub-Setup: laeuft sync (SQLite-WAL, kein async-Loop noetig), wird
        # aber im start() ausgefuehrt damit der EventBus garantiert lebt.
        try:
            self._init_session_stack()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "SessionRecorder-Init fehlgeschlagen — /api/sessions liefert 503"
            )

        # Phase-5 Task-Stack (Aufgaben-View). TaskStore liegt additiv in
        # data/jarvis.db (ADR-0003), Scheduler laeuft als asyncio-Loop, der
        # vom CancelToken im stop() beendet wird.
        try:
            await self._init_task_stack()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "TaskStack-Init fehlgeschlagen — /api/tasks liefert 503"
            )

        # Skill-Hot-Reload-Watcher starten, sobald die Loop stabil laeuft.
        if self._skill_registry is not None:
            try:
                self._skill_registry.start_watcher(asyncio.get_running_loop())
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "SkillRegistry-Watcher-Start fehlgeschlagen — kein Hot-Reload"
                )

        # Doc-Hot-Reload-Watcher analog. watchdog observiert pro Root einen
        # eigenen Observer-Thread.
        if self._doc_registry is not None:
            try:
                self._doc_registry.start_watcher(asyncio.get_running_loop())
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "DocRegistry-Watcher-Start fehlgeschlagen — kein Hot-Reload"
                )

        # CLI-Registry asynchron bootstrappen — probet alle Katalog-CLIs und
        # baut Tool-Instanzen. ``asyncio.create_task`` haengt den Call als
        # Background-Task an, damit ``start()`` selbst nicht blockt.
        if self._cli_registry is not None:
            async def _bootstrap_clis() -> None:
                try:
                    await self._cli_registry.bootstrap()
                except Exception as exc:  # noqa: BLE001
                    logger.opt(exception=exc).warning(
                        "CliToolRegistry-Bootstrap fehlgeschlagen — CLIs-View leer"
                    )
            asyncio.create_task(_bootstrap_clis(), name="cli-registry-bootstrap")

        # Plugin-Registry asynchron bootstrappen — oeffnet pro verbundenem
        # Plugin einen In-Process-MCPClient und bridged dessen Tools in den
        # Live-Brain (BrainToolsChanged re-expandiert). Mirror der CLI-Registry.
        if self._plugin_registry is not None:
            async def _bootstrap_plugins() -> None:
                try:
                    await self._plugin_registry.bootstrap()
                except Exception as exc:  # noqa: BLE001
                    logger.opt(exception=exc).warning(
                        "PluginToolRegistry-Bootstrap fehlgeschlagen — Plugins worker-only"
                    )
            asyncio.create_task(_bootstrap_plugins(), name="plugin-registry-bootstrap")

        # Board-Aggregator als Endlos-Task. run_forever() macht erst einen
        # on-startup-Run und schlaeft dann 6 h (Plan §5-A Decision #2).
        if self._board_aggregator is not None:
            self._board_aggregator_task = asyncio.create_task(
                self._board_aggregator.run_forever(interval_s=6 * 3600),
                name="board-aggregator",
            )

        # Achievement-Evaluator am Bus. Phase B.
        if self._board_evaluator is not None:
            try:
                self._board_evaluator.attach()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "AchievementEvaluator.attach() fehlgeschlagen"
                )

        # Bio-Scheduler — wochentl. + master-Achievement-Trigger.
        # Der Brain wird hier noch nicht finalisiert (app.state.brain kommt
        # meist erst spaeter). BioGenerator.brain bleibt auf None, bis der
        # Caller ihn setzt — der Scheduler faengt das Brain-None sauber ab.
        if self._bio_scheduler is not None:
            try:
                self._bio_scheduler.start()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "BioScheduler.start() fehlgeschlagen"
                )

        # Friends-Stack: FriendRegistry + ChannelManager. Telegram startet nur,
        # wenn integrations.telegram.enabled=true und ein Token verfuegbar ist.
        try:
            await self._init_channel_stack()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(
                "ChannelStack-Init fehlgeschlagen — /api/friends liefert 503"
            )

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

        self._screenshot_retention_task = asyncio.create_task(
            retention_task(
                flight_recorder_dir=flight_recorder_dir,
                retention_days=retention_days,
                interval_seconds=DEFAULT_RETENTION_INTERVAL_SECONDS,
            )
        )

    async def _init_mission_stack(self) -> None:
        """Phase-6 Production-Wiring: bootstrap_missions() liefert den
        kompletten Stack (Manager, Kontrollierer, Budget, Voice-Listener,
        Cleanup-Task), inkl. Safety-Hooks und WS-Manager-Bus-Bridge.
        """
        from jarvis.missions.init import bootstrap_missions

        data_dir = Path(self.cfg.memory.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "missions.db"

        # Isolation-Root: <repo_parent>/sub-agents-outputs/. Repo-Root ist 4 Ebenen
        # ueber jarvis/ui/web/server.py: server.py -> web -> ui -> jarvis -> repo.
        repo_root = WEB_DIR.parent.parent.parent
        isolation_root = repo_root.parent / "sub-agents-outputs"

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
            tts_speak_fn=None,  # TTS-Wiring kommt aus DesktopApp wenn Voice live
            brain_caller=None,  # Decomposer arbeitet im Heuristik-only-Modus
            # Welle-4 Y: Speech-Bus fuer MissionAnnouncer durchreichen, damit
            # Mission-Completion-Events als AnnouncementRequested auf dem
            # globalen Bus landen — pipeline._on_announcement subscribed dort.
            speech_bus=self.bus,
            # Defaults aus jarvis.toml [phase6.*] (alle ueberschreibbar via cfg later)
            safety_enabled=True,
            # User mandate 2026-05-31 ("überhaupt kein Budget", frontier-quality-
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
        # outputs_routes.py uses this to render the Outputs view; it would
        # otherwise have to re-derive the same WEB_DIR.parent.parent.parent
        # walk and would silently drift if the launcher layout changes.
        self.app.state.outputs_root = isolation_root
        self._missions_voice_listener = result["voice_listener"]
        self._missions_cleanup_task = result["cleanup_task"]

        # Welle-4-Wiring: spawn_worker-Tool im Brain braucht den
        # MissionManager UND den Kontrollierer. Der Brain wird in
        # DesktopApp._start_speech_and_orb ueber build_default_brain()
        # gebaut — die Singleton-Setter machen beide dort verfuegbar
        # (Lazy-Resolve via _resolve_mission_manager / _resolve_kontrollierer).
        # Ohne den Kontrollierer-Setter wuerde der Voice-Pfad eine Mission
        # dispatchen, aber niemand triggert run_mission — die Mission bliebe
        # PENDING und der User hoerte keine Antwort (BUG-016).
        try:
            from jarvis.brain.factory import (
                set_kontrollierer,
                set_mission_manager,
                set_openclaw_bootstrap_failed,
            )

            set_mission_manager(result["manager"])
            set_kontrollierer(result["kontrollierer"])
            set_openclaw_bootstrap_failed(False)
            self.app.state.openclaw_available = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "set_mission_manager/kontrollierer-Wiring fehlgeschlagen — "
                "spawn_worker wird in diesem Run deaktiviert: %s", exc,
            )
            # Surface the failure for both downstream readers:
            #  - `self.app.state.openclaw_available` lets REST routes and
            #    UI components show an explicit "OpenClaw unavailable" hint
            #    instead of permanently rendering "loading" / "pending".
            #  - the factory-level singleton lets the Brain's spawn_worker
            #    tool short-circuit at execute()-time with an honest
            #    "konnte nicht initialisiert werden" message instead of the
            #    transient "noch nicht bereit" the in-progress path returns.
            self.app.state.openclaw_available = False
            try:
                from jarvis.brain.factory import set_openclaw_bootstrap_failed
                set_openclaw_bootstrap_failed(True)
            except Exception as inner_exc:  # noqa: BLE001
                logger.warning(
                    "set_openclaw_bootstrap_failed wiring failed: %s",
                    inner_exc,
                )

        # WS-Bridge: ConnectionManager aus Phase-4 wird auf den Bus subscribed.
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
            return

        repo = MarkdownPageRepository()
        vault_root = Path(wiki_cfg.vault_root)
        if not vault_root.is_absolute():
            # Resolve relative to the repo root (same convention as the rest
            # of the app — CWD is the repo root at runtime).
            vault_root = Path.cwd() / vault_root

        handle = await bootstrap_wiki_integration(
            bus=self.bus,
            repo=repo,
            vault_root=vault_root,
            config=wiki_cfg,
            brain_caller=None,      # curator uses BrainProviderRegistry internally
            scheduler_factory=None, # Agent D not yet merged; fallback to direct ingest
            voice_bridge_config=self.cfg.memory.wiki.voice_bridge,
        )
        self._wiki_integration_handle = handle
        logger.info("wiki_integration: bootstrap_wiki_integration succeeded")

    def _init_wiki_watcher(self) -> None:
        """Phase B3 — start the WikiWatcher for desktop live-reload.

        Watches the configured vault root and publishes WikiPageChanged
        events on the shared bus. Failures are logged but never raised;
        the desktop app must boot when the vault is empty or watchdog
        cannot start an observer.
        """
        from jarvis.memory.wiki.watcher import WikiWatcher

        wiki_cfg = self.cfg.wiki_integration
        if not wiki_cfg.enabled:
            logger.info("wiki_watcher: wiki_integration disabled — skipping")
            return

        vault_root = Path(wiki_cfg.vault_root)
        if not vault_root.is_absolute():
            vault_root = Path.cwd() / vault_root

        watcher = WikiWatcher(vault_root=vault_root, bus=self.bus)
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
        """Phase-5 Task-Queue-Wiring (BUG-007).

        Baut TaskStore + TaskRunner + TaskScheduler auf, fuehrt den
        Startup-Cleanup fuer ``running``-Tasks (App-Exit) aus und startet
        den Scheduler-Loop als Background-Task.

        Runner laeuft hier ohne Harness/TTS/Tool-Wiring — fuer reine
        ``after_delay``/``at_time``-Erinnerungen mit ``speak``- oder
        ``harness_dispatch``-Action waeren das eigene Folge-Tasks. Die UI
        wird trotzdem live (Liste + Cancel + Detail-Timeline), weil die
        Routes nur Store + Scheduler anfassen.
        """
        from jarvis.control.cancel import CancelToken
        from jarvis.tasks.runner import TaskRunner
        from jarvis.tasks.scheduler import TaskScheduler
        from jarvis.tasks.store import TaskStore

        data_dir = Path(self.cfg.memory.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        # Tasks teilen sich die DB mit Memory (ADR-0003) — additives Schema.
        db_path = data_dir / "jarvis.db"

        store = TaskStore(db_path)
        await store.init()

        # Crash-Recovery: alle running -> interrupted plus Error-Log.
        recovered = await store.cleanup_interrupted()
        if recovered:
            logger.info("TaskStack: {} interrupted Tasks vom Vorlauf bereinigt", recovered)

        runner = TaskRunner(store=store, bus=self.bus)
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

        # Routes lesen aus app.state — siehe tasks_routes.py:28
        self.app.state.task_store = store
        self.app.state.task_scheduler = scheduler
        self.app.state.task_runner = runner

        logger.info(
            "TaskStack live (db={}, recovered={}, hydrated={} scheduled)",
            db_path, recovered, len(scheduler._heap),
        )

    def _init_session_stack(self) -> None:
        """Voice-Session-Recorder + Store fuer die Transkriptions-View.

        Bootstrap analog zu ``_init_mission_stack``, aber sync — der Store
        nutzt sqlite3 + threading.Lock (siehe ``jarvis/sessions/store.py``).
        Liest Defaults aus der ``[sessions]``-Sektion in jarvis.toml; die
        Sektion ist NICHT im Pydantic-Tree (siehe Annahme A-2 im Phase-7-
        Bootstrap), daher tomllib direkt.
        """
        from jarvis.sessions.init import bootstrap_sessions

        # Defaults entsprechen jarvis.toml [sessions] (enabled=true,
        # data/sessions.db, retention 30d). User kann via TOML overriden.
        enabled = True
        rel_db_path = "data/sessions.db"
        retention_days = 30

        toml_path = Path("jarvis.toml")
        if toml_path.exists():
            try:
                import tomllib
                with toml_path.open("rb") as fh:
                    raw = tomllib.load(fh)
                section = raw.get("sessions", {}) or {}
                enabled = bool(section.get("enabled", enabled))
                rel_db_path = str(section.get("db_path", rel_db_path))
                retention_days = int(section.get("retention_days", retention_days))
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "Konnte [sessions]-Sektion aus jarvis.toml nicht lesen — Defaults"
                )

        if not enabled:
            logger.info("Voice-Session-Recorder via [sessions].enabled=false deaktiviert")
            return

        # rel_db_path ist relativ zum Repo-Root (= cwd beim Start). Falls absolut,
        # bleibt es absolut. Path() macht beides korrekt.
        db_path = Path(rel_db_path)
        if not db_path.is_absolute():
            db_path = Path(self.cfg.memory.data_dir).parent / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

        result = bootstrap_sessions(
            bus=self.bus,
            db_path=db_path,
            enabled=True,
            retention_days=retention_days,
        )
        self.app.state.session_store = result["store"]
        self._session_recorder = result["recorder"]
        logger.info("Session-Recorder online (db={}, retention={}d)", db_path, retention_days)

    async def _init_channel_stack(self) -> None:
        """Bootstrappt FriendRegistry + ChannelManager + startet alle Channels.

        Friends-UI funktioniert auch ohne Telegram (FriendRegistry ist immer
        da). TelegramChannel landet bei fehlendem Token in ``start_errors``,
        blockiert die anderen Channels nicht.
        """
        from jarvis.channels.bootstrap import bootstrap_channels
        from jarvis.channels.chat_bridge import ChannelChatBridge

        # data_dir existiert moeglicherweise nicht auf jedem Branch unter
        # cfg.memory; Fallback auf ./data damit der Code branch-portabel ist.
        data_dir = Path("data")
        try:
            mem_dir = getattr(self.cfg, "memory", None)
            if mem_dir is not None and getattr(mem_dir, "data_dir", None):
                data_dir = Path(mem_dir.data_dir)
        except Exception:  # noqa: BLE001
            pass
        data_dir.mkdir(parents=True, exist_ok=True)
        friends_db = data_dir / "friends.db"

        # Telegram/Discord configs aus integrations.* (Branch-portable mit getattr)
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
        # Skill-Watcher stoppen, sonst bleibt der watchdog-Thread als Zombie
        # haengen und verhindert Prozess-Ende.
        if self._skill_registry is not None:
            try:
                self._skill_registry.stop_watcher()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("SkillRegistry-Watcher-Stop fehlgeschlagen")

        # Doc-Watcher + FTS5-Connection schliessen.
        if self._doc_registry is not None:
            try:
                self._doc_registry.close()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("DocRegistry-Close fehlgeschlagen")

        # Shared-CLI-Registry unset'en — sonst haelt der Module-Singleton eine
        # Referenz auf eine "tote" Registry, wenn der Server neu startet.
        try:
            from jarvis.clis.shared import set_active_registry
            set_active_registry(None)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).debug("CLI-Shared-Registry Cleanup fehlgeschlagen: {}", exc)

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
                    "PluginToolRegistry-Cleanup fehlgeschlagen: {}", exc
                )
            self._plugin_registry = None
            self.app.state.plugin_registry = None

        # Board-Aggregator-Task geordnet beenden, bevor uvicorn faellt. Der
        # run_forever()-Loop faengt CancelledError selbst und propagiert es.
        if self._board_aggregator_task is not None:
            self._board_aggregator_task.cancel()
            try:
                await asyncio.wait_for(self._board_aggregator_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning("Board-Aggregator Shutdown-Fehler")
            self._board_aggregator_task = None
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
                logger.opt(exception=exc).debug("WikiIntegration.shutdown() fehlgeschlagen")
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
                    "WikiWatcher.shutdown() fehlgeschlagen"
                )
            self._wiki_watcher = None

        # PTY-Sessions terminieren bevor uvicorn faellt — verhindert dass
        # Reader-Threads in einen geschlossenen Loop schreiben wollen.
        try:
            self._pty.close_all()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("PTY-Cleanup fehlgeschlagen")

        # Phase-6 Mission-Stack ordentlich runterfahren — Connection schliesst
        # die SQLite-DB. Cleanup-Task cancellen (wenn opt-in war).
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

        mission_manager = getattr(self.app.state, "mission_manager", None)
        if mission_manager is not None:
            try:
                await mission_manager.stop()
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "MissionManager.stop() fehlgeschlagen"
                )
            self.app.state.mission_manager = None
            self.app.state.kontrollierer = None

        # Phase-5 Task-Stack runterfahren: Scheduler-Loop cancellen, laufende
        # Runner-Tasks abwarten (max 2s), Store schliessen.
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
                logger.opt(exception=exc).warning("TaskScheduler-Loop Shutdown-Fehler: {}", exc)
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
                logger.opt(exception=exc).warning("TaskStore.close() fehlgeschlagen")
        self._task_scheduler = None
        self._task_store = None
        self._task_runner = None
        self._task_cancel_token = None
        self.app.state.task_store = None
        self.app.state.task_scheduler = None
        self.app.state.task_runner = None

        # Voice-Session-Recorder vom Bus detachen + DB-Connection schliessen.
        recorder = getattr(self, "_session_recorder", None)
        store = getattr(self.app.state, "session_store", None)
        if recorder is not None or store is not None:
            try:
                from jarvis.sessions.init import shutdown_sessions
                shutdown_sessions({"store": store, "recorder": recorder})
            except Exception as exc:  # noqa: BLE001
                logger.opt(exception=exc).warning(
                    "SessionRecorder-Shutdown fehlgeschlagen"
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
                    "ChannelChatBridge-Shutdown fehlgeschlagen"
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
                    "ChannelStack-Shutdown fehlgeschlagen"
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
