"""Phase-6 Bootstrap — wires all mission components at app startup.

Called by ``jarvis/ui/web/server.py::start()`` (or a standalone CLI).
Returns a dict with all instantiated components that the caller can store
in ``app.state.<key>``.

Startup order per plan §"Block D":
1. cleanup.startup_sweep
2. MissionManager + start (Recovery)
3. BudgetTracker (with emitter -> store.append_and_publish)
4. WorktreeManager (driven by cfg)
5. CriticRunner
6. MissionDecomposer (optional brain)
7. Kontrollierer (wires everything + safety_enabled)
8. ConnectionManager-Bus-Bridge — already done in Phase-4 server.py
9. MissionVoiceListener (when TTS is available)
10. daily_cleanup_task (when config.daily=true)

Each step is optional via a cfg flag. Default values come from jarvis.toml
[phase6.*] (see the section defaults there).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# Speech-bus type for the optional MissionAnnouncer (Wave-4 Y).
# A lazy import via the TYPE_CHECKING pattern would be preferred, but EventBus is
# lightweight and already imported everywhere in the project.
from jarvis.core.bus import EventBus as _SpeechEventBus

from .budget import BudgetTracker
from .cleanup import daily_cleanup_task, startup_sweep
from .critic.runner import CriticRunner
from .event_bus import MissionBus
from .isolation.env import build_worker_env, read_live_claude_oauth_token
from .isolation.worktree import WorktreeManager
from .kontrollierer.decomposer import MissionDecomposer
from .kontrollierer.orchestrator import Kontrollierer
from .manager import MissionManager
from .task_bridge import MissionEventBridge
from .voice.announcer import MissionAnnouncer
from .voice.listener import MissionVoiceListener
from .voice.readback import MissionReadback
from .worker_runtime.provider_map import (
    ANTIGRAVITY_SUBAGENT_SLUGS,
    CODEX_SUBAGENT_SLUGS,
)
from .workers.api_agent_worker import ApiAgentWorker
from .workers.claude_direct_worker import ClaudeDirectWorker
from .workers.codex_direct_worker import CodexDirectWorker
from .workers.gemini_worker import GeminiWorker
from .workers.google_cli_worker import GoogleCliWorker

logger = logging.getLogger(__name__)

# Fire-and-forget boot-time cleanup sweeps keep a strong reference here so the
# event loop does not garbage-collect them mid-run; each self-discards on
# completion. These sweeps are PURE cleanup (git worktree prune + rmtree over
# stale dirs) whose result nothing downstream consumes — awaiting them only
# delayed the desktop boot before the voice pipeline could come up.
_BOOT_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _spawn_boot_cleanup(coro: Any, *, name: str) -> asyncio.Task[Any]:
    """Schedule *coro* as a tracked fire-and-forget task (no boot-path await)."""
    task = asyncio.create_task(coro, name=name)
    _BOOT_BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BOOT_BACKGROUND_TASKS.discard)
    return task


# Pure-API providers that run on THEIR OWN provider via the in-process
# ApiAgentWorker (OpenAI-compatible chat API + tool-use loop), instead of the
# legacy silent ClaudeDirectWorker fallback. Single source for the routing
# decision so the UI "runs on Claude" badge can never drift from reality.
_API_AGENT_SLUGS: frozenset[str] = frozenset({"openai", "openrouter"})


# Type aliases
TTSSpeakFn = Callable[[str, str], Awaitable[None]]
BrainCallerFn = Callable[[str], Awaitable[str]]


def _default_job_factory() -> Any:
    """Per-mission process-containment job, selected by platform.

    Delegates to the ``job_object`` factory so the OS dispatch lives in exactly
    one place: a real Windows Job Object on win32, and a session/process-group
    reaper (SIGTERM->SIGKILL on close) on macOS/Linux. The wiring used to
    hard-code the pure no-op off-Windows, which leaked worker process trees on a
    headless VPS (cross-platform audit C2).
    """
    from .isolation.job_object import WindowsJobObject

    return WindowsJobObject()


def _resolve_readback_mode(
    *, tts_speak_fn: object | None, speech_bus: object | None
) -> str:
    """Decide which mission voice-readback path is active — exactly one.

    Starting BOTH the MissionVoiceListener (direct-TTS callback) and the
    MissionAnnouncer (mission-bus -> speech-bus bridge) makes every
    completion spoken twice (announcer docstring: "gleichzeitig beide
    aktivieren = Doppel-Ansage" — 2026-05-27 finding #6). The announcer wins
    whenever a ``speech_bus`` is available because it feeds the
    ``scrub_for_voice`` UI/pipeline path; the listener is the fallback for
    callers that only supply a direct TTS callback.

    Returns ``"announcer"``, ``"listener"``, or ``"none"``.
    """
    if speech_bus is not None:
        return "announcer"
    if tts_speak_fn is not None:
        return "listener"
    return "none"


def _worker_mcp_relevance_filter_enabled() -> bool:
    """The ``[brain.routing].worker_mcp_relevance_filter`` kill-switch (default ON).

    Reading from the LIVE config (uncached) lets the maintainer flip the filter
    off without a code change. Any read failure degrades to ON — the safe default
    is to gate, exactly like the router's plugin-relevance gate. Never raises.
    """
    try:
        from jarvis.core.config import load_config

        cfg = load_config()
        return bool(
            getattr(cfg.brain.routing, "worker_mcp_relevance_filter", True)
        )
    except Exception:  # noqa: BLE001 — missing/blip config => keep the gate ON
        return True


def _live_server_tools():  # noqa: ANN202
    """Per-server tool lists (namespaced ``<server_id>/<tool>``) from the LIVE
    MCP registry, for the relevance filter.

    Returns ``{server_id: [{"name": "<sid>/<tool>", "description": ...}, ...]}``.
    The namespace prefix is what lets ``plugin_is_relevant`` mine a server's OWN
    distinctive tool nouns (its ``_derive_tool_nouns`` keys on the ``<id>/`` prefix).

    A server that is enabled but not started has no live client → it is simply
    absent from the map → the relevance gate sees an empty tool list and matches
    on the server NAME / usage card only (the correct degraded behaviour: export
    only if the task names it). Never raises.
    """
    out: dict[str, list] = {}
    try:
        from jarvis.core.runtime_refs import get_mcp_registry

        reg = get_mcp_registry()
        if reg is None:
            return out
        for sid, client in reg.active_clients().items():
            tools: list[dict] = []
            for t in getattr(client, "_tools_cache", []) or []:
                if isinstance(t, dict):
                    name = t.get("name") or ""
                    desc = t.get("description") or ""
                else:
                    name = getattr(t, "name", "") or ""
                    desc = getattr(t, "description", "") or ""
                if name:
                    tools.append({"name": f"{sid}/{name}", "description": desc})
            out[sid] = tools
    except Exception:  # noqa: BLE001 — gate must never break mission dispatch
        logger.debug("missions: live MCP tool gather failed", exc_info=True)
    return out


def _filter_servers_by_relevance(  # noqa: ANN201
    servers,  # noqa: ANN001
    *,
    task_text=None,  # noqa: ANN001
    relevance_filter=None,  # noqa: ANN001
    server_tools=None,  # noqa: ANN001
):
    """Drop MCP servers irrelevant to this mission's task, ungated otherwise.

    Mirrors the router's plugin-relevance gate one layer below it: a worker runs
    ``--permission-mode bypassPermissions`` so an exported off-topic server is
    actually reachable and re-introduces the ~35 s wrong-MCP stall. A server is
    kept only when ``plugin_is_relevant(task_text, server_id, server_tools)`` is
    True (NAMES the server, OR a usage card matches, OR a distinctive tool-noun
    matches) — the SAME definition the router uses.

    Fail-OPEN at every layer so a gate bug can NEVER silently strip a mission's
    MCPs: no task context → full export; kill-switch OFF → full export; any
    per-server or setup fault → export that server. NO silent truncation — the
    kept/dropped server ids are logged at INFO when anything is dropped.
    """
    # No task context → we cannot judge relevance → export everything (honesty
    # over guessing; this is the back-compat path for callers without a prompt).
    if not servers or not task_text or not str(task_text).strip():
        return servers

    enabled = relevance_filter
    if enabled is None:
        enabled = _worker_mcp_relevance_filter_enabled()
    if not enabled:
        return servers  # kill-switch OFF → exact prior behaviour (full export)

    try:
        from jarvis.marketplace.plugin_relevance import plugin_is_relevant

        tool_lookup = server_tools if server_tools is not None else _live_server_tools()
        kept: dict[str, dict] = {}
        dropped: list[str] = []
        for sid, entry in servers.items():
            tools = tool_lookup.get(sid, []) if hasattr(tool_lookup, "get") else []
            try:
                relevant = plugin_is_relevant(str(task_text), sid, tools)
            except Exception:  # noqa: BLE001 — a relevance fault must NOT strip
                relevant = True
            if relevant:
                kept[sid] = entry
            else:
                dropped.append(sid)
        if dropped:
            logger.info(
                "missions: MCP relevance filter kept %s, dropped %s (off-task)",
                sorted(kept), sorted(dropped),
            )
        return kept
    except Exception as exc:  # noqa: BLE001 — never break dispatch; full export
        logger.debug(
            "missions: MCP relevance filter failed (%s) — full export", exc
        )
        return servers


def _assemble_worker_mcp_servers(  # noqa: ANN201
    token_store=None,  # noqa: ANN001
    mcp_json_servers=None,  # noqa: ANN001
    *,
    task_text=None,  # noqa: ANN001
    relevance_filter=None,  # noqa: ANN001
    server_tools=None,  # noqa: ANN001
):
    """Build the claude-cli ``mcpServers`` map for the delegated worker.

    Two sources, both reaching the worker identically:
      1. Connected Marketplace plugins (saved tokens + catalog mcp_server spec).
      2. Self-added MCP servers from ``mcp.json`` (the "MCPs" section), converted
         via :func:`jarvis.mcp.claude_export.mcp_json_to_claude_servers`.

    When ``task_text`` is provided, the assembled set is filtered down to the
    servers RELEVANT to that mission's task (the same plugin-relevance gate the
    router uses) — a worker runs ``bypassPermissions`` so an exported off-topic
    MCP is reachable and would re-introduce the ~35 s wrong-MCP stall. The filter
    is reversible (``relevance_filter=False`` or the ``[brain.routing].
    worker_mcp_relevance_filter`` kill-switch) and ALWAYS degrades to exporting
    on a fault, so it can never silently strip a mission's MCPs. ``server_tools``
    is an optional per-server tool map (``{id: [tool, ...]}``) for the relevance
    decision; when omitted it is gathered from the live MCP registry.

    Never raises: a marketplace / keyring / mcp.json hiccup degrades to an
    empty (or partial) map so mission dispatch is never blocked.
    """
    try:
        from jarvis.marketplace.catalog_data import load_catalog
        from jarvis.marketplace.mcp_bridge import assemble_claude_mcp_servers
        from jarvis.marketplace.token_store import TokenStore

        store = token_store if token_store is not None else TokenStore()

        # mcp.json (self-added MCP servers) -> claude-cli shape -> extra_servers
        if mcp_json_servers is None:
            try:
                from jarvis.mcp.state import load_config as _load_mcp_json

                mcp_json_servers = (_load_mcp_json() or {}).get("mcpServers", {})
            except Exception as exc:  # noqa: BLE001
                logger.warning("missions: mcp.json read failed (%s)", exc)
                mcp_json_servers = {}
        try:
            from jarvis.mcp.claude_export import mcp_json_to_claude_servers

            extra = mcp_json_to_claude_servers(mcp_json_servers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("missions: mcp.json -> claude config failed (%s)", exc)
            extra = {}

        servers = assemble_claude_mcp_servers(
            load_catalog(), store, extra_servers=extra
        )
        return _filter_servers_by_relevance(
            servers,
            task_text=task_text,
            relevance_filter=relevance_filter,
            server_tools=server_tools,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "missions: MCP-config assembly failed (%s) — worker runs without "
            "plugins / mcp.json servers",
            exc,
        )
        return {}


def _select_subagent_worker_kind(
    sub_jarvis_provider: str | None, step_model: str
) -> str:
    """Pure routing decision for the Heavy-Task subagent worker.

    Returns one of ``"claude_direct"`` | ``"codex_direct"`` | ``"antigravity"``
    | ``"subjarvis"`` | ``"gemini"``.

    Defense-in-depth (2026-05-29, user mandate: heavy tasks run on the
    configured provider — claude-api -> Claude Max OAuth — and Gemini must
    NEVER be a silent fallback): when a subagent provider IS configured it is
    the single source of truth. A per-step ``model`` string can never divert
    heavy work to the Gemini API key — the configured provider wins. The bare
    ``gemini`` branch is reachable ONLY when no provider is configured at all,
    and the caller logs that case so a Gemini spawn is never silent.

    Note that choosing ``"gemini"`` as the *subagent provider* routes through
    OpenClaw (``"subjarvis"``), NOT the direct ``GeminiWorker`` API path — the
    latter is purely the legacy no-provider fallback.
    """
    if sub_jarvis_provider == "claude-api":
        return "claude_direct"
    if sub_jarvis_provider == "openclaw-claude":
        return "subjarvis"
    if sub_jarvis_provider in CODEX_SUBAGENT_SLUGS:
        return "codex_direct"
    # "antigravity" (Google subscription via the official agy/gemini CLI over the
    # OAuth login) is a HARD LOCK like claude-api: no per-step model can divert it
    # to the API-key Gemini path. It reuses GeminiWorker but with the API key
    # stripped from the worker env (the OAuth login then bills the subscription).
    if sub_jarvis_provider in ANTIGRAVITY_SUBAGENT_SLUGS:
        return "antigravity"
    # openai / openrouter run ON their own provider via the in-process
    # ApiAgentWorker (OpenAI-compatible chat API + tool-use loop writing files
    # into the worktree). They used to fall through to "subjarvis" ->
    # ClaudeDirectWorker, so picking them silently ran the mission on Claude
    # (violates the "selected provider must run" mandate). A HARD LOCK like
    # claude-api/antigravity: no per-step model can divert it. (xAI Grok was
    # removed as a sub-agent provider 2026-06-22 — TTS-only now.)
    if sub_jarvis_provider in _API_AGENT_SLUGS:
        return "api_agent"
    # Explicitly selecting "gemini" routes to the direct GeminiWorker so the
    # sub-agent actually runs on Gemini (the user's "selected provider must run"
    # mandate). This is NOT the anti-silent-Gemini case (2026-05-29) — that
    # forbade gemini as a *silent* fallback when something else was configured;
    # here the user deliberately picked gemini. The OpenClaw path that this used
    # to take ("subjarvis") was removed in Welle 4, so without this it silently
    # ran on Claude instead of Gemini.
    if sub_jarvis_provider == "gemini":
        return "gemini"
    if sub_jarvis_provider:
        return "subjarvis"
    # No subagent provider configured — legacy default path.
    if (step_model or "").lower().startswith("gemini"):
        return "gemini"
    return "subjarvis"


def subagent_runs_on_claude_fallback(sub_jarvis_provider: str | None) -> bool:
    """True when picking this subagent provider does NOT run heavy missions on
    THAT provider but silently falls back to the ClaudeDirectWorker (Opus).

    As of 2026-06-22 openai/openrouter resolve to the ``"api_agent"`` kind
    (the in-process ApiAgentWorker runs them on their OWN provider), so they no
    longer report a Claude fallback here — provided an API key is configured. The
    only remaining always-Claude case is the legacy ``"subjarvis"`` kind
    (openclaw-claude / unknown provider), whose dedicated OpenClaw worker was
    removed after the ~92% nested-claude hang.

    NOTE: this is the ROUTING truth, not a credential check — an api_agent
    provider with NO key still falls back to Claude at run time (``_worker_
    factory`` honest gate), which the provider card surfaces separately via its
    key-present state.

    Derived from the SINGLE routing source of truth (``_select_subagent_worker_
    kind``) so the badge can never drift from the worker that actually runs.
    """
    return _select_subagent_worker_kind(sub_jarvis_provider, "") == "subjarvis"


def _live_subagent_provider(boot_snapshot: str | None) -> str | None:
    """Re-resolve ``[brain.sub_jarvis].provider`` from the LIVE persisted config.

    The worker factory used to freeze the boot-time provider snapshot, so a
    subagent switch (UI / config / drift-guard) only took effect after an app
    restart — every mission ran the hard-coded ClaudeDirectWorker (Opus)
    fallback until then (live forensic 2026-06-21: a process booted before the
    codex pin kept routing heavy missions to Claude/Opus for hours, even though
    the persisted choice was already ``openai-codex``). Resolving per mission
    makes the running app follow the current selection without a restart.

    Two hops, both reusing tested machinery:
      1. ``refresh_persisted_env_from_user_registry()`` pulls the authoritative
         User-registry value of the persisted ``JARVIS__*`` keys (which already
         include ``JARVIS__BRAIN__SUB_JARVIS__PROVIDER``) into this process's
         ``os.environ``. Without it a stale inherited env keeps winning over the
         TOML via ``_apply_env_overrides`` (env > toml). No-op off Windows /
         when nothing changed.
      2. ``load_config()`` is uncached (re-reads TOML + env on every call), so a
         fresh read now reflects the persisted choice.

    Any failure (config blip, registry hiccup) degrades to ``boot_snapshot`` so
    a transient error can never break mission dispatch — the boot path already
    treats that snapshot as authoritative, so this is strictly safer than the
    frozen-closure behaviour it replaces.
    """
    try:
        from jarvis.core.config import (
            load_config,
            refresh_persisted_env_from_user_registry,
        )

        try:
            refresh_persisted_env_from_user_registry()
        except Exception:  # noqa: BLE001,S110 — best-effort env heal, never fatal
            pass

        cfg = load_config()
        sub_cfg = getattr(cfg.brain, "worker", None)
        raw = getattr(sub_cfg, "provider", None) if sub_cfg is not None else None
        if raw:
            resolved = str(raw).strip().lower()
            if resolved:
                return resolved
        # B6 (open-source AP-22): no explicit [brain.worker].provider → run the
        # heavy worker on the user's ACTIVE brain provider, not the legacy Claude
        # CLI. A fresh openrouter/gemini/codex install never sets sub_jarvis, so
        # without this the default ClaudeDirectWorker spawns the absent `claude`
        # binary and every mission fails.
        primary = (getattr(cfg.brain, "primary", None) or "").strip().lower()
        if primary:
            return primary
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "missions: live subagent re-resolve failed (%s) — using boot "
            "snapshot %r",
            exc,
            boot_snapshot,
        )
    return boot_snapshot


def _claude_cli_auth_viable() -> bool:
    """True when the ``claude`` CLI has a REACHABLE auth surface for a worker.

    Binary presence alone is NOT viability (2026-07-06 incident): the worker
    runs ``claude --print`` pinned to an isolated CLAUDE_CONFIG_DIR, so its
    ONLY auth surface is the credential Jarvis injects — a live non-expired
    OAuth bearer (``CLAUDE_CODE_OAUTH_TOKEN``) or a classic Anthropic API key.
    An OAuth token that expired in place (nothing refreshes ``~/.claude`` on a
    host whose interactive sessions use a different config dir) is a
    deterministic 401 on every spawn, while ``claude status`` still says
    connected (presence-only check).

    Three gates, all cheap and offline:
    1. the process-local ``claude_auth_dead`` flag (a worker PROVED the current
       credential dead this session — fingerprinted, so a fresh login/key
       re-enables Claude instantly);
    2. a live, non-expired OAuth login in ``~/.claude/.credentials.json``;
    3. failing that, a CLASSIC (non-``sk-ant-oat``) stored Anthropic API key —
       a stored ``sk-ant-oat`` is a stale OAuth copy that would be routed to
       the OAuth slot and 401.
    """
    from jarvis.claude_auth_state import claude_auth_dead, credential_fingerprint
    from jarvis.missions.isolation.env import (
        live_claude_oauth_status,
        read_live_claude_oauth_token,
    )

    if live_claude_oauth_status() == "valid":
        token = read_live_claude_oauth_token()
        return not claude_auth_dead(
            current_fingerprint=credential_fingerprint(token)
        )
    try:
        from jarvis.core.config import get_secret

        key = get_secret("anthropic_api_key", env_fallback="ANTHROPIC_API_KEY")
    except Exception:  # noqa: BLE001 — unreadable secret store => not viable
        return False
    if not key or key.startswith("sk-ant-oat"):
        return False
    return not claude_auth_dead(current_fingerprint=credential_fingerprint(key))


def _cross_family_last_resort_worker(task_text: str) -> Any | None:
    """The key-aware, cross-family LAST-resort heavy worker (open-source AP-22/23).

    The legacy last resort was always ``ClaudeDirectWorker`` — the Claude Max
    OAuth ``claude`` CLI. A downloader whose only credential is a Gemini /
    OpenRouter / OpenAI key (and who never switched ``[brain.worker].provider``
    off the ``claude-api`` default) then bricked every heavy mission: there is no
    ``claude`` binary on a fresh install and no Anthropic key. This mirrors the
    Brain's cross-family fallback chain — probe the families the user ACTUALLY
    has and run on the first reachable one, crossing families instead of
    dead-ending on Claude.

    Subscription-first ordering (no metered key before a metered key):
      1. Claude Max OAuth ``claude`` CLI, when its binary is present — this keeps
         the maintainer/subscription path unchanged (it is reached first, so a
         host WITH Claude never diverts to a metered key);
      2. ChatGPT subscription via the codex CLI OAuth login (no API key);
      3. the in-process ``ApiAgentWorker`` on whatever single API key is
         configured — ``claude-api``, ``gemini``, ``openrouter``, ``openai``, in
         the Brain chain's family order.

    Returns ``None`` ONLY when nothing is reachable (the genuine no-credential
    case); the caller then keeps the honest Claude last resort, which fails
    legibly rather than silently. Because Claude is probed first, this never
    silently diverts a working Claude to Gemini — it only rescues a host that
    has no Claude at all (the §3 single-key downloader).
    """
    # 1. Claude Max OAuth CLI — subscription, no metered key, preferred floor.
    #    Auth-aware since 2026-07-06: binary presence alone picked a claude CLI
    #    whose OAuth token had expired in place, and every mission 401'd while
    #    a healthy codex login + OpenRouter key sat unused (AP-22).
    from jarvis.missions.workers.claude_direct_worker import _resolve_claude_binary

    if _resolve_claude_binary() is not None:
        if _claude_cli_auth_viable():
            return ClaudeDirectWorker(
                mcp_servers=_assemble_worker_mcp_servers(task_text=task_text)
            )
        logger.warning(
            "Mission worker: the `claude` CLI is installed but its auth is "
            "dead/expired (no live OAuth login, no classic Anthropic key) — "
            "skipping Claude and crossing provider families. Run "
            "`claude /login` or save a fresh Anthropic key in the API-Keys "
            "view to restore Claude."
        )
    # 2. ChatGPT subscription via the codex CLI OAuth login (no API key).
    from jarvis.codex_auth_state import codex_needs_reauth
    from jarvis.missions.workers.codex_direct_worker import _codex_oauth_available

    if _codex_oauth_available() and not codex_needs_reauth():
        return CodexDirectWorker()
    # 3. In-process API worker on whatever single key the user has, crossing
    #    families — the same cross-family set the Brain fallback chain uses.
    from jarvis.core.config import get_provider_secret
    from jarvis.missions.workers.api_agent_worker import supports_api_agent_worker

    for prov in ("claude-api", "gemini", "openrouter", "openai"):
        if supports_api_agent_worker(prov) and get_provider_secret(prov):
            logger.warning(
                "Mission worker -> ApiAgentWorker(%r): no Claude CLI / Codex login "
                "reachable, crossing to the configured API-key family so the heavy "
                "mission runs instead of failing on the absent `claude` binary "
                "(open-source AP-22/AP-23, single-key downloader).",
                prov,
            )
            return ApiAgentWorker(prov)
    return None


async def bootstrap_missions(
    *,
    db_path: Path,
    isolation_root: Path,
    repo_root: Path | None = None,
    bus: MissionBus | None = None,
    tts_speak_fn: TTSSpeakFn | None = None,
    brain_caller: BrainCallerFn | None = None,
    # Safety flags (from [phase6.safety])
    safety_enabled: bool = True,
    extra_blocked_globs: tuple[str, ...] = (),
    # Budget flags (from [phase6.budget])
    per_mission_usd: float = 5.0,
    daily_usd: float = 50.0,
    warn_pct: tuple[int, ...] = (50, 80),
    # When False the BudgetTracker is a no-op (no cost cap, no abort). User
    # mandate 2026-05-31 ("no budget at all"). Default True keeps the
    # cap for other installs / the cloud-first €5-VPS profile.
    budget_enabled: bool = True,
    # Voice flags (from [phase6.voice])
    voice_announce_critic_loop: bool = False,
    voice_language_default: str = "de",
    # Cleanup flags (from [phase6.cleanup])
    cleanup_days: int = 14,
    cleanup_startup_sweep: bool = True,
    cleanup_daily: bool = False,
    # Orchestrator flags (from [phase6.orchestrator])
    max_workers: int = 5,
    # Wave-4 Y: speech bus for MissionAnnouncer (Mission-Bus -> Speech-Bus bridge).
    # When None: no announcer is started — Wave-3 behaviour.
    speech_bus: _SpeechEventBus | None = None,
    # ``speech_bus`` carries milestone + boundary events to ack_brain and
    # selects the MissionAnnouncer readback path (see _resolve_readback_mode).
    # ``brain_manager_resolver`` is a reserved lazy-resolver hook (same pattern
    # as ``_resolve_mission_manager`` in jarvis/brain/factory.py); currently
    # unused by the bootstrap but kept on the signature for callers.
    brain_manager_resolver: Callable[[], Any | None] | None = None,
    # Recovery is OPT-IN and fail-closed: only the proven primary process
    # (the launcher that holds the single-instance lock) passes True.
    # Any side-process — smoke scripts, eval harnesses, --no-lock parallel
    # sessions, or any caller that forgets to set the flag — defaults to
    # False and will NOT sweep live missions to crash_recovery.
    # The launcher sets os.environ["JARVIS_PRIMARY_INSTANCE"] = "1" exactly
    # when it holds the lock, and server.py reads that to decide whether to
    # pass recover_missions=True here.
    recover_missions: bool = False,
) -> dict[str, Any]:
    """Boot the entire Phase-6 subsystem.

    Args:
        db_path: Path to missions.db (typically ``data/missions.db``).
        isolation_root: ``sub-agents-outputs/`` (mission-root container).
        repo_root: optional, used for git-worktree operations + cleanup.
        bus: optional external bus; otherwise MissionManager uses its own.
        tts_speak_fn: optional ``async fn(text, lang) -> None`` for voice readback.
            When None: no VoiceListener is started.
        brain_caller: optional ``async fn(prompt) -> str`` for the LLM decomposer.
            When None: decomposer operates in heuristic-only mode (1-step plans).
        safety_enabled: enables the PostToolUse scanner + path guard in the orchestrator.
        ... (cfg-specific flags, default values from jarvis.toml)

    Returns:
        dict with keys: manager, kontrollierer, budget, voice_listener,
        cleanup_task, sweep_stats. The caller stores these in ``app.state.<key>``.
    """
    isolation_root.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Startup sweep (before MissionManager — do not interfere with running worktrees)
    sweep_stats: dict[str, int] = {"scanned": 0, "removed": 0, "errors": 0}
    if cleanup_startup_sweep:
        sweep_stats = await startup_sweep(
            isolation_root=isolation_root,
            cleanup_days=cleanup_days,
            repo_root=repo_root,
        )

    # 2. MissionManager + Recovery (only the primary instance sweeps — #2)
    manager = MissionManager(db_path, bus=bus)
    recovered = await manager.start(recover=recover_missions)
    if recovered:
        logger.info("Phase-6 startup-recover: %d stale missions -> FAILED", len(recovered))
    elif not recover_missions:
        logger.info(
            "Phase-6 startup-recover skipped — secondary instance "
            "(not the single-instance-lock holder)"
        )

    # 3. BudgetTracker (emitter = store.append_and_publish so warnings persist)
    budget = BudgetTracker(
        per_mission_usd=per_mission_usd,
        daily_usd=daily_usd,
        warn_pct=warn_pct,
        emitter=manager.store.append_and_publish,
        enabled=budget_enabled,
    )
    # Auto-track WorkerDraftReady events
    budget.bind_to_event_bus(manager.bus)

    # 4. WorktreeManager
    if repo_root is None:
        repo_root = Path.cwd()
    worktree_mgr = WorktreeManager(
        repo_root=repo_root,
        outputs_root=isolation_root,
    )

    # H6 (2026-05-17 audit): one-shot boot-time sweep of leaked worktree
    # dirs that crashed/force-quit/race-blocked sessions left behind.
    # Audit-4 counted ~60 of these on disk. Best-effort, never raises;
    # `prune_and_sweep_leaked` swallows its own errors and returns a
    # telemetry dict that we log for forensics.
    # Off-loop via to_thread: the sweep is blocking work (git subprocesses +
    # rmtree over deep trees) and bootstrap runs on the event loop that
    # serves /api/health at boot — blocking it here kept the desktop window
    # from appearing (10s of the 30s-launch bug, 2026-06-10).
    # Fire-and-forget: nothing below consumes ``sweep_report`` (it is only
    # logged), and the sweep is blocking cleanup (git subprocess + rmtree over
    # deep trees). Awaiting it sat on the desktop-boot path; running it as a
    # tracked background task lets the mission stack come up immediately while
    # the leaked-worktree cleanup finishes behind the boot.
    async def _bg_worktree_sweep() -> None:
        try:
            sweep_report = await asyncio.to_thread(
                worktree_mgr.prune_and_sweep_leaked, max_age_hours=6.0
            )
            if (
                sweep_report.get("swept_run_dirs", 0) > 0
                or sweep_report.get("errors", 0) > 0
            ):
                logger.info("worktree-sweep at bootstrap: %s", sweep_report)
        except Exception:  # noqa: BLE001
            logger.warning("worktree-sweep at bootstrap failed", exc_info=True)

    _spawn_boot_cleanup(_bg_worktree_sweep(), name="mission-boot-worktree-sweep")

    # 5. CriticRunner
    critic_runner = CriticRunner()

    # 6. MissionDecomposer
    decomposer = MissionDecomposer(brain=brain_caller)

    # Read brain primary AND sub_jarvis-provider once at bootstrap so
    # worker_factory + env_builder both make the same choice. Lazy-imported
    # so the missions module stays importable in test environments where
    # the global Jarvis config isn't loaded.
    brain_primary = "gemini"
    brain_deep_model = "sonnet"
    sub_jarvis_provider: str | None = None
    try:
        from jarvis.core.config import load_config

        cfg = load_config()
        brain_primary = (cfg.brain.primary or "claude-api").lower()
        # deep_model is the per-provider field; resolve via providers map.
        provider_cfg = (cfg.brain.providers or {}).get(brain_primary)
        if provider_cfg is not None:
            brain_deep_model = (
                getattr(provider_cfg, "deep_model", None)
                or getattr(provider_cfg, "model", None)
                or brain_deep_model
            )
        # [brain.worker] is the SOURCE OF TRUTH for the Jarvis-Agent worker
        # selection (post-Welle-4). If present and provider is set, every
        # mission step routes to the worker — regardless of brain.primary.
        sub_cfg = getattr(cfg.brain, "worker", None)
        if sub_cfg is not None:
            raw_provider = getattr(sub_cfg, "provider", None)
            if raw_provider:
                sub_jarvis_provider = str(raw_provider).strip().lower() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "missions: brain config lookup failed (%s) — defaulting to "
            "openclaw-backed worker routing", exc,
        )

    # B6 (open-source AP-22): no explicit sub-agent provider → default to the
    # active brain provider so the boot snapshot, env_builder, and the per-mission
    # _live_subagent_provider all agree (and never silently route to the Claude CLI).
    if not sub_jarvis_provider:
        sub_jarvis_provider = brain_primary

    # 7. Kontrollierer
    def _env_builder(mission_dir: Path) -> dict[str, str]:
        # Each worker CLI reads its own API-key env
        # var. Pull all three from Jarvis' secret store (Windows Credential
        # Manager → ENV → .env) so a config-driven worker switch doesn't
        # require a separate bootstrap pass. The relevant key actually
        # reaches the worker because build_worker_env sets ANTHROPIC_,
        # GEMINI_/GOOGLE_, OPENAI_API_KEY explicitly — none of them leak
        # to a worker that doesn't need them.
        anthropic_key: str | None = None
        openai_key: str | None = None
        gemini_key: str | None = None
        xai_key: str | None = None
        openrouter_key: str | None = None
        try:
            from jarvis.core.config import get_secret

            # Resolve the provider LIVE here too, so the injected key matches the
            # worker the factory will actually pick (both read the same current
            # persisted choice instead of the frozen boot snapshot).
            live_provider = _live_subagent_provider(sub_jarvis_provider)

            anthropic_key = get_secret(
                "anthropic_api_key", env_fallback="ANTHROPIC_API_KEY"
            )
            # Codex-as-subagent API-key path: prefer the dedicated Codex key slot
            # so OPENAI_API_KEY carries it (the OAuth path strips the key anyway —
            # see CodexDirectWorker._build_codex_env). Other subagents use the
            # general OpenAI key unchanged.
            if live_provider in CODEX_SUBAGENT_SLUGS:
                openai_key = get_secret(
                    "codex_openai_api_key", env_fallback="OPENAI_API_KEY"
                ) or get_secret("openai_api_key", env_fallback="OPENAI_API_KEY")
            else:
                openai_key = get_secret(
                    "openai_api_key", env_fallback="OPENAI_API_KEY"
                )
            # Antigravity (Google subscription) deliberately runs OAuth-only: a
            # configured Gemini API key must NOT be injected, or the CLI would
            # bill the key instead of the subscription login. Other subagents
            # keep the API-key path unchanged.
            if live_provider in ANTIGRAVITY_SUBAGENT_SLUGS:
                gemini_key = None
            else:
                gemini_key = get_secret(
                    "gemini_api_key", env_fallback="GEMINI_API_KEY"
                ) or get_secret(
                    "google_api_key", env_fallback="GOOGLE_API_KEY"
                )
            # Grok / xAI: Jarvis stores under ``grok_api_key`` in the
            # credential manager (ENV fallback ``GROK_API_KEY``); we set
            # both XAI_API_KEY + GROK_API_KEY on the worker side so
            # OpenClaw (XAI_API_KEY) and any legacy SDK (GROK_API_KEY)
            # both find it.
            xai_key = get_secret(
                "grok_api_key", env_fallback="GROK_API_KEY"
            ) or get_secret(
                "xai_api_key", env_fallback="XAI_API_KEY"
            )
            openrouter_key = get_secret(
                "openrouter_api_key", env_fallback="OPENROUTER_API_KEY"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "missions: secret lookup failed (%s) — worker will hit "
                "authentication_failed", exc,
            )

        # Subscription-first billing for Claude (mirror of Codex's "OAuth wins,
        # drop the API key"): a LIVE Claude Max OAuth login in
        # ~/.claude/.credentials.json bills the plan's included usage, so it wins
        # over a stored Anthropic API key — the key is the fallback only when no
        # subscription login is present. (Was API-key-first, which silently
        # metered a user who had both a Claude Max login AND an API key.)
        #
        # Prefer the LIVE file token: `claude` refreshes its access token in
        # place, but get_secret may return a STALE oat from the credential
        # manager / .env; pinned to an isolated CLAUDE_CONFIG_DIR the env token is
        # the only auth surface and a stale one 401s. A classic API key
        # (sk-ant-api03) is used verbatim when there is no live OAuth login.
        live_oat = read_live_claude_oauth_token()
        if live_oat:
            anthropic_key = live_oat
        elif anthropic_key and anthropic_key.startswith("sk-ant-oat"):
            # 2026-07-06: no live (non-expired) OAuth login, and the stored
            # credential is itself an OAuth bearer — i.e. a STALE copy of a
            # login that no longer exists. Injecting it is a guaranteed 401
            # ("Failed to authenticate. API Error: 401 Invalid authentication
            # credentials", missions 019f36e5 + 019f38b1). Drop it so the
            # worker either runs on a different family (the factory's
            # viability gate) or fails with the honest "Not logged in".
            anthropic_key = None

        return build_worker_env(
            run_dir=mission_dir,
            anthropic_api_key=anthropic_key,
            openai_api_key=openai_key,
            gemini_api_key=gemini_key,
            xai_api_key=xai_key,
            openrouter_api_key=openrouter_key,
        )

    def _worker_factory(step):  # noqa: ANN001 — Step type local
        # Worker routing post-Welle-4:
        #
        # 1. If ``[brain.sub_jarvis].provider`` is set in jarvis.toml,
        #    every mission step routes to SubJarvisWorker (which drives
        #    the provider-agnostic OpenClaw CLI). This is the documented
        #    OpenClaw-bridge migration path and the way the user switches
        #    between grok / gemini / openai / claude-api / openrouter as
        #    the Heavy-Worker provider without code changes.
        #
        # 2. Otherwise use SubJarvisWorker as the default heavy worker.
        #    That keeps complex work on the provider-agnostic OpenClaw path
        #    instead of spawning a vendor-specific code CLI.
        # BUG-023 fix (2026-05-16): OpenClaw 2026.5.7 silently swallows
        # `cliBackends["claude-cli"].args` injection, so Sonnet via OpenClaw
        # never gets file_write tools (verified live in mission_019e3236).
        # When the configured provider is `claude-api`, bypass OpenClaw and
        # drive the `claude` CLI directly — that path was empirically
        # proven to actually invoke Write tools (see /tmp/probe5 + /tmp/probe6
        # on 2026-05-16). Other providers (grok / gemini / openai / openrouter)
        # still go through SubJarvisWorker / OpenClaw because OpenClaw is the
        # right surface for them.
        # Welle 7 (2026-05-20): two opt-in routes coexist for the claude-cli
        # backend.
        #   - "claude-api": direct claude CLI via ClaudeDirectWorker
        #     (BUG-023-era shortcut, retained for back-compat).
        #   - "openclaw-claude": real OpenClaw subprocess (`openclaw agent
        #     --local --json --model claude-cli/<model>`). Empirically
        #     verified 2026-05-20 via the probe at ~/openclaw-probe-*: file
        #     write succeeded once SubJarvisWorker.spawn sets
        #     agents.defaults.workspace to the per-mission worktree. The
        #     SubJarvisWorker resolves "openclaw-claude" to the claude-api
        #     OpenClaw provider via _resolve_provider_chain's
        #     primary_provider remap below.
        # Welle 6 (2026-05-18): user switched from Claude Max to ChatGPT
        # subscription. ``chatgpt`` / ``openai-codex`` route through the
        # codex CLI's ChatGPT-OAuth path -- no API key, no OpenClaw, just
        # `codex exec --json --skip-git-repo-check ...`. Both slug names
        # are accepted so a future jarvis.toml renames don't break boot.
        #
        # The (provider, step_model) -> worker-kind decision is a pure,
        # unit-tested function (``_select_subagent_worker_kind``) so the
        # worker that runs can never drift from the configured provider —
        # in particular a configured ``claude-api`` is a HARD LOCK that no
        # per-step model can divert to the Gemini API key (anti-silent-Gemini
        # defense-in-depth, 2026-05-29).
        # Re-resolve the subagent provider from the LIVE persisted config on
        # every mission, instead of the frozen boot snapshot. A switch to Codex
        # then takes effect on the next mission without an app restart — the
        # 2026-06-21 incident was a process that booted before the codex pin and
        # kept routing every heavy mission to the ClaudeDirectWorker (Opus)
        # fallback for hours. Degrades to the boot snapshot on any read failure.
        live_provider = _live_subagent_provider(sub_jarvis_provider)
        kind = _select_subagent_worker_kind(
            live_provider, getattr(step, "model", "") or ""
        )
        if kind == "claude_direct":
            # B3 (open-source AP-22): an Anthropic-API-key-only user has NO `claude`
            # CLI binary — run the heavy worker IN-PROCESS via ApiAgentWorker on the
            # API key instead of failing on the missing binary. The CLI stays
            # preferred (subscription-first) whenever the binary IS present.
            from jarvis.core.config import get_provider_secret
            from jarvis.missions.workers.claude_direct_worker import (
                _resolve_claude_binary,
            )
            if _resolve_claude_binary() is None and get_provider_secret("claude-api"):
                logger.info(
                    "Mission worker -> ApiAgentWorker('claude-api'): no `claude` CLI "
                    "binary, running in-process on the Anthropic API key."
                )
                return ApiAgentWorker("claude-api")
            # Proactive quota routing (mirror of the codex_needs_reauth branch
            # below): if a Claude worker already proved the Max window exhausted
            # this session, route STRAIGHT to codex (a separate ChatGPT
            # subscription) instead of wasting a ~16 s Claude probe per mission
            # until the window resets. The cooldown self-expires, then Claude is
            # re-probed; a Claude success clears it. Guarded on codex being a
            # viable backend (oauth present, not flagged dead).
            from jarvis.claude_quota_state import claude_in_quota_cooldown

            if claude_in_quota_cooldown():
                from jarvis.codex_auth_state import codex_needs_reauth
                from jarvis.missions.workers.codex_direct_worker import (
                    _codex_oauth_available,
                )

                if _codex_oauth_available() and not codex_needs_reauth():
                    logger.warning(
                        "Mission worker -> CodexDirectWorker: Claude Max is in "
                        "quota cooldown this session — routing to codex until the "
                        "window resets (avoids a wasted Claude probe per mission)."
                    )
                    return CodexDirectWorker()
            # Open-source AP-22/AP-23: before the honest Claude last resort, try
            # the user's ACTUAL provider family. A fresh install whose only key is
            # gemini/openrouter/openai (and who never moved off the claude-api
            # default) has neither the `claude` binary nor an Anthropic key, so a
            # bare ClaudeDirectWorker here would brick the mission. The helper
            # probes Claude FIRST, so a host WITH Claude is unchanged.
            cross = _cross_family_last_resort_worker(getattr(step, "prompt", "") or "")
            if cross is not None:
                return cross
            # Give the delegated worker the connected marketplace plugins as a
            # claude-cli MCP config so it can issue the plugin tool calls (AD-OE4).
            return ClaudeDirectWorker(
                mcp_servers=_assemble_worker_mcp_servers(
                    task_text=getattr(step, "prompt", "") or ""
                )
            )
        if kind == "codex_direct":
            # If a codex subprocess already proved the ChatGPT login dead this
            # session, skip codex entirely and run on Claude Max directly (one
            # path, like grok) — re-spawning the dead provider + double-falling-
            # back every mission doubled the Claude Max load and made the critic
            # flaky (critic_loop_exhausted). Cleared on a codex success / login.
            from jarvis.codex_auth_state import codex_needs_reauth

            if codex_needs_reauth():
                logger.warning(
                    "Mission worker -> ClaudeDirectWorker: codex ChatGPT login "
                    "is flagged dead this session — running on Claude Max until "
                    "`codex login` restores it (avoids the dead-provider double "
                    "fallback)."
                )
                return ClaudeDirectWorker(
                mcp_servers=_assemble_worker_mcp_servers(
                    task_text=getattr(step, "prompt", "") or ""
                )
            )
            return CodexDirectWorker()
        if kind == "antigravity":
            # "antigravity" (Google subscription): drive the official `agy` CLI
            # over a PTY with --dangerously-skip-permissions so it can write files
            # in the worktree. The worker env has the Gemini API key stripped (see
            # the env builder), so the CLI uses the ~/.gemini OAuth login and bills
            # the subscription. GoogleCliWorker falls back to GeminiWorker when the
            # resolver finds the Gemini CLI instead of agy (it writes to a pipe; agy
            # emits 0 bytes over a pipe and needs the PTY). The previous code reused
            # GeminiWorker here, which drove `gemini` with gemini flags and never
            # invoked agy at all (the gemini consumer-OAuth was sunset 2026-06-18).
            logger.info(
                "Mission worker -> GoogleCliWorker over the Google subscription "
                "(agy over PTY, OAuth login, no API key) — billed against Antigravity."
            )
            return GoogleCliWorker()
        if kind == "api_agent":
            # grok / openai / openrouter: run ON the selected provider via the
            # in-process ApiAgentWorker. Honest credential gate — if no API key
            # is configured the provider CANNOT run, so fall back to Claude Max
            # (mission still completes) instead of spawning a guaranteed-fail
            # worker (e.g. openai/openrouter with no key).
            provider = live_provider or ""
            try:
                from jarvis.core import config as _cfg

                ep = _cfg.resolve_provider_endpoint(provider, vendor_default_base_url="")
                has_key = bool(getattr(ep, "credential", None))
            except Exception:  # noqa: BLE001 — any resolve failure => no usable key
                has_key = False
            if has_key:
                logger.info(
                    "Mission worker -> ApiAgentWorker on %r (in-process tool-use "
                    "loop over the provider's own API, writes files in the worktree).",
                    provider,
                )
                return ApiAgentWorker(provider)
            logger.warning(
                "Mission worker: subagent provider %r has no API key configured, "
                "so it cannot run — trying the user's other provider families "
                "before the Claude last resort (open-source AP-22/AP-23).",
                provider,
            )
            cross = _cross_family_last_resort_worker(getattr(step, "prompt", "") or "")
            if cross is not None:
                return cross
            return ClaudeDirectWorker(
                mcp_servers=_assemble_worker_mcp_servers(
                    task_text=getattr(step, "prompt", "") or ""
                )
            )
        if kind == "gemini":
            # B4 (open-source AP-22): no Gemini CLI but a Gemini API key → run the
            # heavy worker IN-PROCESS via ApiAgentWorker instead of failing on the
            # missing npm `gemini` CLI binary.
            import shutil

            from jarvis.core.config import get_provider_secret
            if not (
                shutil.which("gemini") or shutil.which("gemini.cmd")
            ) and get_provider_secret("gemini"):
                logger.info(
                    "Mission worker -> ApiAgentWorker('gemini'): no Gemini CLI, "
                    "running in-process on the Gemini API key."
                )
                return ApiAgentWorker("gemini")
            # Reached when [brain.sub_jarvis].provider == "gemini" was selected
            # (the user's "selected provider must run" mandate) OR, legacy, when
            # no provider is configured but the step model is a gemini model.
            # Make it LOUD either way — this runs on the Gemini API key, NOT the
            # Claude Max subscription (so a key/quota issue is the user's to fix).
            logger.warning(
                "Mission worker -> GeminiWorker (step model=%r) — running on the "
                "Gemini API key (GEMINI_API_KEY/GOOGLE_API_KEY), NOT the Claude "
                "Max subscription.",
                getattr(step, "model", ""),
            )
            return GeminiWorker()
        # The legacy ``"subjarvis"`` kind (openclaw-claude / unknown provider /
        # unset default) routed to the OpenClaw subprocess worker, which was
        # removed (it caused the ~92% nested-claude hang; see docs/BUGS.md).
        # Open-source AP-22/AP-23: try the user's ACTUAL provider family before
        # the Claude last resort, so an openrouter/gemini/openai-only downloader
        # is not dead-ended on the absent `claude` binary.
        cross = _cross_family_last_resort_worker(getattr(step, "prompt", "") or "")
        if cross is not None:
            return cross
        return ClaudeDirectWorker(
            mcp_servers=_assemble_worker_mcp_servers(
                task_text=getattr(step, "prompt", "") or ""
            )
        )

    kontrollierer = Kontrollierer(
        manager=manager,
        decomposer=decomposer,
        critic_runner=critic_runner,
        worktree_mgr=worktree_mgr,
        env_builder=_env_builder,
        budget=budget,
        worker_factory=_worker_factory,
        job_factory=_default_job_factory,
        isolation_root=isolation_root,
        max_workers=max_workers,
        safety_enabled=safety_enabled,
        extra_blocked_globs=extra_blocked_globs,
    )

    # 8. VoiceListener / Announcer — mutually exclusive (2026-05-27 finding
    # #6): exactly one readback path runs, else every completion is spoken
    # twice. The announcer wins when a speech_bus is present (production),
    # the listener is the direct-TTS fallback.
    _readback_mode = _resolve_readback_mode(
        tts_speak_fn=tts_speak_fn, speech_bus=speech_bus
    )

    # Context-aware readback composer (maintainer mandate: no fixed stock
    # phrases). One instance shared by whichever readback path is active. Built
    # in fallback-only mode when the flash path is off, so the spoken line is the
    # existing canned/signed text unless [ack_brain] is enabled. Lazy import +
    # best-effort so an import cycle / wiring fault never blocks the bootstrap.
    _readback_composer = None
    try:
        from jarvis.brain.factory import build_readback_composer
        _readback_composer = build_readback_composer()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Mission readback composer wiring skipped: %s", exc)

    voice_listener: MissionVoiceListener | None = None
    if _readback_mode == "listener":
        voice_listener = MissionVoiceListener(
            bus=manager.bus,
            store=manager.store,
            readback=MissionReadback(),
            tts_speak_fn=tts_speak_fn,
            announce_critic_loop=voice_announce_critic_loop,
            language_default=voice_language_default,  # type: ignore[arg-type]
            readback_composer=_readback_composer,
        )
        await voice_listener.start()
        logger.info("Phase-6 voice listener active (direct-TTS readback)")
    elif _readback_mode == "announcer" and tts_speak_fn is not None:
        logger.info(
            "Phase-6 voice listener suppressed — speech_bus present, "
            "MissionAnnouncer owns readback (avoids double announcement)"
        )
    else:
        logger.info("Phase-6 voice listener disabled (no tts_speak_fn provided)")

    # 8b. MissionAnnouncer (Wave-4 Y): Mission-Bus -> Speech-Bus bridge for
    # AnnouncementRequested events. Activates only when a speech bus is provided
    # (typically the global UI/pipeline bus). Without this bridge, mission-completion
    # events never reach the scrub_for_voice path.
    mission_announcer: MissionAnnouncer | None = None
    if _readback_mode == "announcer":
        mission_announcer = MissionAnnouncer(
            bus=manager.bus,
            store=manager.store,
            speech_bus=speech_bus,
            announce_critic_loop=voice_announce_critic_loop,
            language_default=voice_language_default,  # type: ignore[arg-type]
            readback_composer=_readback_composer,
        )
        await mission_announcer.start()
        logger.info("Phase-6 mission-announcer active (mission-bus -> speech-bus)")
    else:
        logger.info("Phase-6 mission-announcer disabled (no speech_bus provided)")

    # 8c. MissionEventBridge: Mission-Bus -> global-bus MissionCompleted signal
    # that drives the When-Then Tasks rules (on_event triggers). Independent of
    # the announcer (different event class — no double announcement). Activates
    # whenever a global bus is present (speech_bus IS the global EventBus the
    # Tasks scheduler binds to). The bridge only emits a machine-readable signal;
    # it never speaks.
    mission_event_bridge: MissionEventBridge | None = None
    if speech_bus is not None:
        mission_event_bridge = MissionEventBridge(
            bus=manager.bus,
            global_bus=speech_bus,
        )
        await mission_event_bridge.start()
        logger.info("Phase-6 mission-event-bridge active (mission-bus -> global MissionCompleted)")
    else:
        logger.info("Phase-6 mission-event-bridge disabled (no global bus provided)")

    # 9. Daily cleanup task (opt-in)
    cleanup_task: asyncio.Task[None] | None = None
    if cleanup_daily:
        cleanup_task = asyncio.create_task(
            daily_cleanup_task(
                isolation_root=isolation_root,
                cleanup_days=cleanup_days,
                repo_root=repo_root,
            ),
            name="phase6-daily-cleanup",
        )
        logger.info("Phase-6 daily-cleanup-task scheduled")

    return {
        "manager": manager,
        "kontrollierer": kontrollierer,
        "budget": budget,
        "voice_listener": voice_listener,
        "mission_announcer": mission_announcer,
        "mission_event_bridge": mission_event_bridge,
        "cleanup_task": cleanup_task,
        "sweep_stats": sweep_stats,
        "recovered_mission_ids": recovered,
        "decomposer": decomposer,
        "worktree_manager": worktree_mgr,
    }


async def shutdown_missions(bootstrap_result: dict[str, Any]) -> None:
    """Clean shutdown of the Phase-6 stack.

    Cancels the cleanup task and closes the MissionManager (DB).
    """
    cleanup_task = bootstrap_result.get("cleanup_task")
    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    bridge = bootstrap_result.get("mission_event_bridge")
    if bridge is not None:
        bridge.stop()

    announcer = bootstrap_result.get("mission_announcer")
    if announcer is not None:
        announcer.stop()

    manager = bootstrap_result.get("manager")
    if manager is not None:
        await manager.stop()


__all__ = ["bootstrap_missions", "shutdown_missions"]
