"""Brain factory: delivers a `BrainCallback` (async (str) -> str) for the pipeline.

The factory is the only authorised entry point for Phase-1b launchers
(`speech/pipeline.py` + `speech/watchdog.py`). It attempts in order:

1. **BrainManager** (Phase 5.5, tier-aware) — Router tier (Haiku) or sub tier (Opus).
2. **GeminiTestBrain** — Phase-1b fallback if BrainManager is missing/crashed.
3. **Echo** — last resort so the pipeline does not die completely.

The factory is therefore **drop-in-compatible**: regardless of which brain level
loads, the pipeline always receives something that can `async def __call__(text) -> str`.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

BrainCallback = Callable[[str], Awaitable[str]]

# Router-tier: pure-dispatcher set (grown via documented ADR-0011 amendments;
# the exact-match regression test pins the current membership) plus three
# self-mod tools (Phase 7.3, registered separately in the loader). See
# ``router.py:SYSTEM_PROMPT`` and ADR-0011 (including the Phase-7/8 amendment
# + Wave-4 amendment ``spawn-sub-jarvis`` -> ``spawn-worker``) and
# Master-Plan §22 / Persona-Mandate Phase 3.
#
# Baseline after the 2026-07-13 cleanup: run-shell, screen-snapshot, and
# spawn-worker. The legacy dispatch-to-harness, multi-spawn, and
# dispatch-with-review paths are deliberately not model-visible because each
# duplicated or bypassed the supervised Mission Manager lifecycle.
#
# REMOVED 2026-06-28 — ``dispatch-to-harness`` is intentionally NOT a
# LLM-visible router tool. Its raw ``harness`` parameter let the brain request
# a sub-agent vehicle by NAME (``harness="openclaw"``), but OpenClaw is not a
# registered harness (Wave-4 removal, ~92% hang). The operational harness
# inventory now contains only python-script and the capability-gated screenshot
# adapter. A "start a
# subagent" turn then dispatched to a phantom harness and surfaced the raw
# "Harness 'openclaw' nicht verfügbar" KeyError to voice.  # i18n-allow
# Heavy Jarvis-Agent work is ``spawn-worker`` (Mission Manager → worker);
# live desktop work
# is ``computer-use``. The tool class itself still exists for the INTERNAL,
# non-LLM local-action fast path (see ``_load_local_action_tools``, called
# programmatically with harness="screenshot"); it is just not router-selectable.
# Do NOT re-add it here — that resurrects the phantom-openclaw routing bug.
#
# Rationale: Hauptjarvis is a pure dispatcher. Direct actions outside this
# list (open_app, type_text, remember, whoami …) are delegated to a background
# worker — the router delegates them via ``spawn_worker``. Read-only lookups
# (search-web, wiki-recall, awareness-recall) and safe-gated direct actions
# (computer-use, cli-tools, plugin-tools) are router-tier by design — see the
# ADR-0011 amendments (2026-05-24 CLI, 2026-05-29 Computer-Use, 2026-06-01
# plugins, 2026-06-10 inline web search).
# This prevents the spawn-reflex behaviour documented in
# ``docs/persona-research.md`` Section 2 and keeps the tool set deterministic.
ROUTER_TOOLS = frozenset({
    "run-shell",
    "screen-snapshot",
    # NB: ``dispatch-to-harness`` deliberately absent (removed 2026-06-28) —
    # see the header comment above. Heavy work → spawn-worker; desktop →
    # computer-use. The tool remains for the internal local-action fast path.
    "spawn-worker",
    # AI Pointer (pull path): resolve the on-screen element under the mouse
    # cursor via the OS accessibility tree. Read-only, safe-tier; the brain
    # calls it on deictic questions ("what is this?"). A direct safe-gated read,
    # never a spawn — never in a worker set (AP-5/AP-14). See
    # docs/plans/ai-pointer/DESIGN.md.
    "inspect-pointer",
    # UI navigation (2026-06-02): switch the active sidebar section by voice/chat
    # ("zeig die Socials", "open settings"). Publishes NavigateSidebar; the
    # frontend listener moves the UI. Pure UI action, risk safe, NO spawn —
    # never in a worker set (AP-5/AP-14). See ADR-0011 amendment "Navigate tool".
    "navigate",
    # Command Registry executor (2026-07-09): run ONE curated app command
    # (jarvis/commands/registry.py) through the SAME REST endpoint the UI
    # uses, in-process via ASGI transport. Enum-constrained command_id +
    # schema-validated args; dangerous commands escalate to risk "ask"
    # (two-turn voice confirm). Direct gated action, never a spawn — never in
    # a worker set (AP-5/AP-14). See ADR-0011 amendment "app-command tool".
    "app-command",
    # Phase A1: synchronous state read on the AwarenessManager (Plan §5).
    # NO brain call, NO IO — property read only.
    "awareness-snapshot",
    # Phase A3 (Plan §7): BM25 full-text search across the recent episode
    # log. The plan originally placed this in a Sub-Jarvis tier; Welle 4
    # deleted that tier, so it lives here. Still read-only, still safe to
    # call without confirmation. See ADR on awareness routing for the
    # placement rationale.
    "awareness-recall",
    # Skills-Brain-Integration: Brain-callable executor for installed user
    # skills. D9-recursion-protection is structural — SkillRunner is constructed
    # without a tool_registry that would re-expose run-skill recursively.
    # Available to BOTH Router-Tier (here) and in Wave 4: bypasses OpenClaw
    # because OpenClaw itself has no skill layer.
    "run-skill",
    # Phase B5 (recall-tool): read-only keyword search over the long-term
    # Obsidian wiki vault. Router-tier only — never in SUB_TOOLS (AP-D9).
    # The brain calls this when the user asks "what do we know about X" or
    # references a past project, person, or decision by name.
    "wiki-recall",
    # Phase B5 follow-up: full-page reader for the wiki vault. Used after
    # wiki-recall when the brain has narrowed to one page and needs the
    # complete content, not the 240-char snippet.
    "wiki-page-read",
    # Grounded vault listing (2026-07-14): deterministic ground-truth answer
    # for "what is in my wiki" in ONE round. Without it the brain probed
    # blindly (index.md/SOUL.md → not found, ~14 rounds, 66 s) and recited
    # schema.md's example layout as actual content. Read-only, safe — never
    # in a worker set (AP-5/AP-14). See ADR-0011 amendment "wiki-list tool".
    "wiki-list",
    # Phase B5 follow-up: deterministic ingest path. Lets the brain
    # explicitly store a fact ("merk dir: …") rather than relying on the
    # aggressive-mode VoiceFactBridge heuristic.
    "wiki-ingest",
    # CLI-Integration (2026-05-24): virtual loader that expands to one
    # ``cli_<name>`` tool per connected & usable CLI (gcloud, gh, docker, …).
    # This is the MCP/plugin model for command-line tools: only connected
    # CLIs become tools, so the router's tool surface stays small (typically
    # 1-5). The loader resolves the SHARED, bus-connected registry, so
    # connecting a CLI in the UI re-expands the live brain via
    # BrainToolsChanged (no restart). Router-tier only — a ``cli_<name>``
    # tool is a direct safe-gated action, never a recursive spawn, so it does
    # not enter any worker tool-set (AP-5/AP-14). See ADR-0011 amendment
    # "CLI-Integration" + docs/superpowers/specs/2026-05-24-cli-integration-design.md.
    "cli-tools",
    # Marketplace plugins as live brain tools (2026-06-01). Virtual loader,
    # mirror of cli-tools: expands to one MCPToolAdapter per connected plugin
    # tool. Direct safe/risk-gated action, NEVER a spawn — must not enter any
    # worker tool-set (AP-5/AP-14). See docs/.../2026-06-01-live-plugin-tools.md.
    "plugin-tools",
    # MCP servers as live brain tools (2026-06-18). Virtual loader that
    # expands to one MCPToolAdapter per tool of every connected and running
    # MCP server (e.g. notebooklm-mcp, filesystem-mcp). Reads
    # client._tools_cache synchronously — no network I/O. Default risk_tier
    # "monitor". Live-refresh via BrainToolsChanged when a server
    # connects/disconnects. Router-tier only — never a spawn, so it never
    # enters any worker tool-set (AP-5/AP-14). See ADR-0011 amendment
    # "MCP-Tools Virtual Loader".
    "mcp-tools",
    # Gmail Marketplace plugin: native REST tool backed by the marketplace
    # OAuth token. Gmail has no MCP server block, so it must be router-visible
    # directly; otherwise connected Gmail is not callable by voice/chat.
    "gmail",
    # Vercel Marketplace plugin (2026-06-07): native REST tool, same rationale
    # as gmail — Vercel's catalog rest_wrapper transport produced zero MCP tools,
    # so it must be router-visible directly. Read-only; never a spawn (AP-5/AP-14).
    "vercel",
    # Google Calendar Marketplace plugin (2026-06-27): native bridge tool whose
    # bot logic is a Node script (calendar_bot.mjs). Same rationale as gmail — no
    # MCP server block, so it must be router-visible directly; otherwise a
    # connected calendar is not callable by voice/chat. Never a spawn (AP-5/AP-14).
    "google_calendar",
    # Computer-Use (Wave 1, 2026-05-29): first-class, clearly-described tool to
    # drive the user's LIVE desktop (open apps, click, type, scroll, operate
    # any GUI). The router previously had no honest desktop path — spawn-worker
    # runs in an isolated worktree (cannot touch the desktop) and the
    # dispatch-to-harness indirection was never described as desktop control, so
    # the model refused or hallucinated a tool for the German input
    # "öffne ein Terminal".  # i18n-allow
    # ("open a terminal"). This tool delegates to the in-process
    # computer-use harness; it is a direct
    # safe-gated action (per-action risk gating inside the loop, ADR-0008),
    # NOT a spawn — so it never enters a worker tool-set (AP-5/AP-14). See
    # ADR-0011 amendment "Computer-Use Router Tool".
    "computer-use",
    # Profile-write (2026-05-30): deterministic brain-driven writer for the
    # structured USER.md profile (the 5 clusters the Knowledge matrix + the
    # per-turn system prompt read). The legacy background Curator that used to
    # auto-write those clusters is soft-disabled (B4, 2026-05-17); the active
    # WikiCurator only writes free-form wiki prose. This tool closes that gap
    # WITHOUT resurrecting a parallel extractor — it persists a fact only when
    # the brain consciously calls it (the wiki-ingest precedent, monitor-tier,
    # direct safe-gated write, never a spawn → never in a worker set,
    # AP-5/AP-14). See ADR-0011 amendment "Profile-Write Router Tool".
    "update-profile",
    # App-Control (2026-05-31): give the brain a complete overview of the
    # Desktop App and let it change settings/providers/MCP by voice/chat.
    # `describe-app-settings` is read-only (safe); `switch-provider` and
    # `manage-mcp-server` are ask-tier (echo-confirm). All three are direct,
    # safe/ask-gated actions — NEVER a spawn, so they never enter a worker set
    # (AP-5/AP-14). Raw secret values are never accepted (AP-2): switch-provider
    # only flips the active provider, manage-mcp-server uses $SECRET placeholders.
    # See ADR-0011 amendment "App-Control Tools".
    "describe-app-settings",
    "switch-provider",
    "manage-mcp-server",
    # Masked key preview (2026-05-31, user mandate): speak first 3 + last 3 chars
    # of a stored API key ("AIz...xQ2"), never the full value. monitor-tier
    # (logged, no confirmation nag). Read-only, returns only 6 chars — a narrow,
    # safe exception to AP-2; the full-key refusal is a router-prompt rule.
    "reveal-key-preview",
    # Chunk B jarvis-contacts (2026-06-02): three contact-action tools that let
    # the brain act on a person by name. contact-lookup (safe, read-only)
    # resolves a name/alias -> e-mail/phone/address; contact-upsert (monitor,
    # deterministic write) saves a contact by voice ("merk dir Christophs
    # Nummer …"); call-contact (ask, echo-confirm before dialing a real person)
    # places a real outbound call via the telephony engine. All three are
    # direct safe/monitor/ask-gated actions, NEVER a spawn — so they never enter
    # a worker tool-set (AP-5/AP-14). The contact tools degrade gracefully when
    # Chunk A's ContactStore / Chunk C's telephony engine is absent (cloud-first
    # no-op). See ADR-0011 amendment "Contacts Tools".
    "contact-lookup",
    "contact-upsert",
    "call-contact",
    # Inline web search (2026-06-10, user mandate): answer news/knowledge/
    # research QUESTIONS inline instead of spawning a multi-minute worker
    # mission for a single lookup. The previous "research -> spawn_worker"
    # doctrine turned "what's in the news?" into a heavy mission (live
    # complaint 2026-06-10). Read-only DuckDuckGo call, risk safe, a direct
    # safe-gated action — never a spawn, so it never enters a worker tool-set
    # (AP-5/AP-14). See ADR-0011 amendment "Inline web search".
    "search-web",
})

# Phase 7.3 — self-mod tools are registered directly in the router loader in
# addition to the persona-mandate set (no entry_points, no spawn trigger).
# Plan-§AD-2: router tier only.
SELF_MOD_TOOL_NAMES_ROUTER = frozenset({
    "list_mutable_settings",
    "get_config_value",
    "set_config_value",
})


def warn_if_phantom_worker_harness(config: Any, harness_manager: Any) -> bool:
    """Log a warning when ``[harness.jarvis_agent].enabled`` is true but unregistered.

    OpenClaw was removed in Welle 4 and has no entry-point, so an
    ``enabled = true`` block is INERT (config honesty, 2026-06-28). This probe is
    advisory only: it never raises, never changes routing, and "start a subagent"
    routes to ``spawn_worker`` regardless. Returns True when a phantom config was
    detected (consumed by the regression guard in tests).

    The config field was renamed from ``harness.openclaw`` to ``harness.jarvis_agent``
    in the 2026-06-29 Jarvis-Agents rename; both TOML keys are accepted via
    AliasChoices in HarnessConfig. The harness-availability slug string "openclaw"
    is left unchanged because the external openclaw binary keeps its name.
    """
    # Field renamed openclaw → jarvis_agent; back-compat: also check old attribute
    # name in case a partially-upgraded in-memory config is passed.
    oc = getattr(getattr(config, "harness", None), "jarvis_agent", None)
    if oc is None:
        oc = getattr(getattr(config, "harness", None), "openclaw", None)
    if oc is None or not getattr(oc, "enabled", False):
        return False
    try:
        available = harness_manager.available()
    except Exception:  # noqa: BLE001 — an advisory probe must never break boot
        return False
    # The harness registration slug is still "openclaw" (external binary keeps its
    # name); if it ever gets registered, the warning becomes a no-op.
    if "openclaw" in available:
        return False
    log.warning(
        "[harness.jarvis_agent].enabled is true but 'openclaw' is not a registered "
        "harness (available: %s) — the block is inert. Heavy Jarvis-Agent work runs "
        "through spawn_worker; set [harness.jarvis_agent].enabled = false to silence "
        "this.",
        available,
    )
    return True


def _per_turn_vision_active(vision_cfg: Any) -> bool:
    """True when ``[brain.router.vision]`` requests the per-turn screenshot feed.

    This is the always-on latency tax (a screenshot injected into EVERY router
    turn). Off by default since the Wave-2 latency fix.
    """
    return vision_cfg is not None and bool(getattr(vision_cfg, "enabled", False))


def _needs_vision_engine(*, per_turn_vision: bool, cu_enabled: bool) -> bool:
    """True when a ``VisionEngine`` must be built.

    The engine is shared by two consumers: (a) the per-turn screenshot injector
    (``_vision_provider``) and (b) Computer-Use's on-demand capture. Build it
    whenever EITHER needs it — so turning the per-turn feed OFF for speed does
    NOT also disable Computer-Use ("klick auf X"). Decouples the two, which were
    previously gated on the single ``[brain.router.vision].enabled`` flag.
    """
    return per_turn_vision or cu_enabled


def _resolve_wiki_vault_root(config: Any) -> Path:
    """Resolve the wiki vault root for the router-tier context injector.

    Reads ``config.wiki_integration.vault_root`` — the SAME field every
    other wiki consumer uses (``wiki_recall._build_search_instance``,
    ``wiki_routes._resolve_vault_root``) — and resolves it through the
    canonical :func:`jarvis.memory.wiki.vault_root.resolve_vault_root`
    (spec A7), so a relative root anchors to the repo root, never the
    process CWD. Falls back to the resolver's standard
    ``<project>/wiki/obsidian-vault`` default when the config has no
    ``wiki_integration`` section (older config) or its value is empty.

    Historical bug: this previously read ``config.memory.vault_root``,
    a field that never existed on ``MemoryConfig`` — so it always
    resolved to ``None`` and a user's ``[wiki_integration].vault_root``
    was silently ignored on the voice path.
    """
    from jarvis.memory.wiki.vault_root import resolve_vault_root

    raw = getattr(getattr(config, "wiki_integration", None), "vault_root", None)
    if raw is not None and str(raw).strip() == "":
        raw = None
    return resolve_vault_root(raw).path


def _load_tools_for_tier(
    tier: str,
    *,
    bus: Any,
    executor: Any,
    harness_manager: Any,
    user_profile: Any,
    people: Any,
    config: Any,
    mission_manager: Any = None,
    awareness_manager: Any = None,
    recall_store: Any = None,
    contacts: Any = None,
) -> dict[str, Any]:
    """Load all tools for the given tier and instantiate them.

    Wave-4 migration: previously there was a Sub-Jarvis tier with its own
    SUB_TOOLS set + SubJarvisManager. After the OpenClaw-bridge migration
    (see docs/openclaw-bridge.md §11) only ``"router"`` remains as a tier;
    the heavy worker runs as an external subprocess via the Mission-Manager.

    Encapsulates entry-point discovery + special cases (dispatch-to-harness,
    spawn-worker, whoami, awareness-snapshot).

    Args:
        tier: currently only ``"router"``.
        mission_manager: MissionManager instance for ``spawn_worker``.
            When None, the tool is removed from the set.
    """
    from importlib.metadata import entry_points

    if tier != "router":
        raise ValueError(
            f"Unknown tier {tier!r}. The Sub-Jarvis tier was replaced by the "
            f"OpenClaw bridge in Welle 4 — only 'router' remains."
        )

    allow = ROUTER_TOOLS
    tools: dict[str, Any] = {}
    # ``app-command`` and the other virtual loaders can expand to a name that
    # is also provided by a purpose-built native entry point.  Entry-point
    # iteration order is not a contract, so plain ``tools[name] = tool`` made
    # the winner environment-dependent (notably Command Registry
    # ``wiki-ingest`` versus the native lazy-curator WikiIngestTool).  Track
    # provenance and make the precedence explicit: native > virtual; ties are
    # resolved by stable source name.
    tool_sources: dict[str, tuple[bool, str]] = {}

    def register_tool(tool: Any, *, source: str, virtual: bool) -> None:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            log.warning("Tool from %s has no name and was skipped", source)
            return
        candidate = (not virtual, source)
        existing = tool_sources.get(name)
        if existing is not None:
            candidate_wins = candidate[0] and not existing[0]
            if candidate[0] == existing[0]:
                candidate_wins = candidate[1] < existing[1]
            if not candidate_wins:
                log.info(
                    "Tool name collision for %r: keeping %s %r over %s %r",
                    name,
                    "native" if existing[0] else "virtual",
                    existing[1],
                    "native" if candidate[0] else "virtual",
                    candidate[1],
                )
                return
            log.info(
                "Tool name collision for %r: replacing %s %r with %s %r",
                name,
                "native" if existing[0] else "virtual",
                existing[1],
                "native" if candidate[0] else "virtual",
                candidate[1],
            )
        tools[name] = tool
        tool_sources[name] = candidate

    for ep in entry_points(group="jarvis.tool"):
        if ep.name not in allow:
            continue
        try:
            cls = ep.load()
            if ep.name == "spawn-worker":
                # Lazy-Resolver-Pattern: register the tool unconditionally and
                # let it resolve the MissionManager at execute-time. Without
                # this the Brain would freeze the tool dict at build-time, and
                # the post-bootstrap ``set_mission_manager`` call would have
                # no effect on an already-built BrainManager. See
                # docs/openclaw-bridge.md AD-OC1 + the regression in
                # tests/integration/test_openclaw_lazy_bootstrap.py.
                #
                # The Kontrollierer-Resolver mirrors the manager-resolver:
                # without it the voice path would only persist a PENDING
                # mission and never trigger run_mission, leaving the user
                # in silence (BUG-016 — voice silent after spawn_worker).
                #
                # The announcer composes the spoken spawn confirmation
                # (brain spoken_ack → flash-LLM → bilingual fallback pool);
                # build_spawn_announcer never raises and never returns None.
                inst = cls(
                    bus=bus,
                    manager=mission_manager,
                    manager_resolver=_resolve_mission_manager,
                    kontrollierer_resolver=_resolve_kontrollierer,
                    announcer=build_spawn_announcer(config),
                )
            elif ep.name == "computer-use":
                # Wave 1: wraps the harness-dispatch plumbing with a fixed
                # computer-use harness identity (see computer_use_tool.py).
                inst = cls(
                    bus=bus,
                    manager=harness_manager,
                    max_output_chars=config.harness.max_output_chars,
                )
            elif ep.name in ("verify-via-curl", "verify-localhost"):
                inst = cls()
            elif ep.name == "start-preview-server":
                inst = cls(bus=bus)
            elif ep.name == "navigate":
                # UI navigation: publishes NavigateSidebar on the shared bus,
                # which the WS forwarder streams to the frontend (event_name
                # "NavigateSidebar") to switch the active section.
                inst = cls(bus=bus)
            elif ep.name == "whoami":
                inst = cls(profile=user_profile, people=people)
            elif ep.name == "awareness-snapshot":
                if awareness_manager is None:
                    log.debug("awareness-snapshot skipped: no AwarenessManager")
                    continue
                inst = cls(manager=awareness_manager)
            elif ep.name == "awareness-recall":
                # Phase A3: the tool itself stays loaded even when the store
                # is None — its execute() returns a clean "unavailable" error
                # in that case rather than disappearing from the schema mid
                # session. That keeps the tool surface stable for the router
                # brain across awareness on/off toggles.
                inst = cls(recall_store=recall_store)
            elif ep.name == "wiki-recall":
                # Phase B5: build VaultSearch with the configured vault root.
                # The search instance is created once per brain build and
                # caches file-list + mtime for fast repeated calls.
                # Falls back to Path("wiki/obsidian-vault") when
                # cfg.wiki_integration.vault_root is absent (Agent A defines
                # that config field; if it is not yet merged we use the
                # default so the tool surface stays stable).
                from jarvis.plugins.tool.wiki_recall import _build_search_instance

                inst = cls(search=_build_search_instance())
            elif ep.name == "wiki-page-read":
                # Phase B5 follow-up: same vault-root resolution as wiki-recall.
                from jarvis.plugins.tool.wiki_page_read import _build_page_read_tool

                inst = _build_page_read_tool()
            elif ep.name == "wiki-list":
                # Grounded vault listing: same vault-root resolution as the
                # other wiki tools.
                from jarvis.plugins.tool.wiki_list import _build_wiki_list_tool

                inst = _build_wiki_list_tool()
            elif ep.name == "wiki-ingest":
                # Phase B5 follow-up: lazy curator resolver — the curator is
                # constructed by ``bootstrap_wiki_integration`` after the brain
                # is built, so the tool must defer the lookup to execute time.
                # Mirrors the spawn-worker lazy-resolver pattern.
                from jarvis.memory.wiki.integration import get_running_curator
                from jarvis.plugins.tool.wiki_ingest import WikiIngestTool

                inst = WikiIngestTool(curator_resolver=get_running_curator)
            elif ep.name == "update-profile":
                # Profile-write tool: mutate the SAME live UserProfile instance
                # the BrainManager renders from (factory passes one instance to
                # both this loader and the manager — see lines ~438/592/636), so
                # the next turn's system prompt reflects the change immediately;
                # emit ProfileUpdated on `bus` for live UI sync. Loads even when
                # user_profile is None — execute() returns a clean error then, so
                # the tool surface stays stable across sessions (mirrors
                # awareness-recall / wiki-ingest).
                inst = cls(profile_resolver=lambda: user_profile, bus=bus)
            elif ep.name in ("contact-lookup", "contact-upsert", "call-contact"):
                # Chunk B (jarvis-contacts): all three consume Contract 1
                # (ContactStore) via a lazy resolver. The store is built once in
                # _phase2_full_brain and passed in via `contacts`; it is None
                # until Chunk A merges (or if the store fails to build) — the
                # tools then return a clean "contacts unavailable" error rather
                # than disappearing from the schema mid-session (stable tool
                # surface, mirrors awareness-recall / wiki-ingest). call-contact
                # additionally lazy-loads Contract 2 (place_call) + the telephony
                # config at execute time, degrading to a clear English no-op when
                # the [telephony] extra / Chunk C is absent.
                inst = cls(store_resolver=lambda: contacts)
            else:
                inst = cls()

            if getattr(inst, "is_virtual_loader", False):
                try:
                    expanded = inst.expand()
                except Exception as exc:  # noqa: BLE001
                    log.debug("virtual-loader '%s' expand() failed: %s", ep.name, exc)
                    continue
                for tool in expanded:
                    register_tool(tool, source=ep.name, virtual=True)
            else:
                register_tool(inst, source=ep.name, virtual=False)
        except Exception as exc:  # noqa: BLE001
            log.debug("Tool %s not loadable: %s", ep.name, exc)

    # Phase 7.3 — self-mod tools are not discoverable via entry_points
    # (they require a shared state writer + PendingMutationStore). Plan-§AD-2:
    # router tier only; Sub-Jarvis does NOT receive them.
    if tier == "router":
        try:
            from jarvis.brain.tools import build_self_mod_tools

            # Pass the EventBus so the writer dispatches ConfigReloaded on a
            # SAFE-tier write (e.g. voice "switch to English"): the BrainManager
            # hot-reload subscriber then applies it to the NEXT turn with no
            # restart. Without the bus the change lands on disk but stays dormant
            # until restart — the exact symptom self-mod "doesn't work".
            #
            # auto_apply="all" (Wave 1.3): the brain-driven (voice/chat) config
            # path applies every non-forbidden change immediately, no confirm
            # round-trip ("never ask, always now"). REST/CLI keep their own
            # confirm UX (they build their store on the default "safe_only").
            self_mod_tools = build_self_mod_tools(
                writer_kwargs={"bus": bus}, auto_apply="all"
            )
            for name, inst in self_mod_tools.items():
                tools[name] = inst
        except Exception as exc:  # noqa: BLE001 — defensive, no tool block fail-stops the Brain
            log.debug("Self-mod tools not loadable (Phase 7.3): %s", exc)

    return tools


def _load_local_action_tools(
    *,
    bus: Any,
    harness_manager: Any,
    config: Any,
) -> dict[str, Any]:
    """Load hidden tools for the deterministic local-action fast path."""
    from jarvis.plugins.tool.dispatch_to_harness import DispatchToHarnessTool
    from jarvis.plugins.tool.hotkey import HotkeyTool
    from jarvis.plugins.tool.open_app import OpenAppTool
    from jarvis.plugins.tool.reset_orb_position import ResetOrbPositionTool
    from jarvis.plugins.tool.respawn_mascot import RespawnMascotTool
    from jarvis.plugins.tool.type_text import TypeTextTool

    return {
        "open_app": OpenAppTool(),
        "type_text": TypeTextTool(),
        "hotkey": HotkeyTool(),
        "dispatch_to_harness": DispatchToHarnessTool(
            bus=bus,
            manager=harness_manager,
            max_output_chars=config.harness.max_output_chars,
        ),
        # ADR-0016 L2: voice-driven orb recovery
        # ("Orb zurück" /  # i18n-allow
        # "wo bist du" / "reset orb"). Publishes OrbResetRequested
        # on the bus; the orb-side bridge handles the Tk-thread dispatch.
        "reset_orb_position": ResetOrbPositionTool(bus=bus),
        # Voice-driven mascot recovery ("Maskottchen wieder auftauchen" /
        # "Spawner" / "respawn mascot"). Calls OverlaySupervisor.force_respawn
        # so the user can get the mascot back after a cap-fire, crash, or
        # hidden-window state.
        "respawn_mascot": RespawnMascotTool(),
    }


def _build_contact_store() -> Any:
    """Build the ContactStore (Contract 1, owned by Chunk A) or return None.

    Chunk B is the integrator: it codes against the frozen Contract-1 interface
    and degrades gracefully when Chunk A is not merged. Mirrors the
    WikiContextInjector import guard in ``_phase2_full_brain``: an ``ImportError``
    (module not merged) or any constructor mismatch yields ``None``, so the
    contact tools return a clean "contacts unavailable" error and the
    ``## Contacts`` name-index block is simply omitted from the system prompt —
    never a boot crash (cloud-first €5-VPS doctrine).
    """
    try:
        from jarvis.contacts.store import ContactStore  # type: ignore[import]
    except Exception as exc:  # noqa: BLE001 — Chunk A not merged yet is expected
        log.debug("ContactStore unavailable (Chunk A not merged?): %s", exc)
        return None
    try:
        return ContactStore()
    except Exception as exc:  # noqa: BLE001 — constructor mismatch must not crash boot
        log.warning("ContactStore could not be built: %s", exc)
        return None


def _resolve_mission_manager() -> Any:
    """Resolve the MissionManager for the ``spawn_worker`` tool.

    Wave-4 migration: previously the ``SubJarvisManager`` was constructed
    directly in the ``_phase2_full_brain`` flow. Today (OpenClaw bridge) the
    server layer (``jarvis/ui/web/server.py::_init_mission_stack``) bootstraps
    the MissionManager asynchronously — it lives under ``app.state.mission_manager``.

    AD-OC1 (Lazy-Resolver): this function is passed as a closure into
    ``SpawnWorkerTool`` and queried at execute-time, NOT at Brain-build
    time. Required because the DesktopApp builds the BrainManager BEFORE
    ``server.start()`` (which runs ``_init_mission_stack`` and calls
    ``set_mission_manager``). A None return now means "not yet bootstrapped",
    not "permanently unavailable" — the tool surface stays registered, the
    user gets an honest "Mission-Manager initialisiert noch" reply on early
    spawns instead of silent fall-through.
    """
    return _MISSION_MANAGER_REF[0] if _MISSION_MANAGER_REF else None


def _resolve_kontrollierer() -> Any:
    """Resolves the Kontrollierer (mission orchestrator) for ``spawn_worker``.

    Mirrors ``_resolve_mission_manager`` — same lazy-resolver pattern,
    same bootstrap-order rationale. Without this the voice path would
    only ``manager.dispatch()`` (which persists a PENDING mission) but
    never trigger ``kontrollierer.run_mission()``, so the mission would
    sit untouched until the next app restart marked it as
    ``OrchestratorCrash`` via the recovery sweep.

    A ``None`` return is honest: the bootstrap hasn't completed yet.
    The caller logs and skips the run-trigger; the mission will then be
    picked up by the recovery path or a manual ``/missions/{id}/run``.
    """
    return _KONTROLLIERER_REF[0] if _KONTROLLIERER_REF else None


_MISSION_MANAGER_REF: list[Any] = []
_KONTROLLIERER_REF: list[Any] = []
# Sentinel that distinguishes "bootstrap not yet attempted" (default,
# transient) from "bootstrap attempted and crashed" (permanent for this
# process). spawn_worker checks this so the user gets an honest
# "OpenClaw konnte nicht initialisiert werden" instead of the misleading  # i18n-allow
# "noch nicht bereit, bitte einen Moment warten" returned during boot.  # i18n-allow
# returns when both the manager and kontrollierer singletons are None
# but the server is still booting.
_WORKER_BOOTSTRAP_FAILED: list[bool] = [False]


def set_mission_manager(manager: Any) -> None:
    """Singleton setter for the MissionManager.

    Called by the server bootstrap (``_init_mission_stack``) as soon as the
    MissionManager is ready. Subsequent ``build_default_brain`` calls pick it
    up and thread it into the ``spawn_worker`` tool.
    """
    if _MISSION_MANAGER_REF:
        _MISSION_MANAGER_REF[0] = manager
    else:
        _MISSION_MANAGER_REF.append(manager)


def set_kontrollierer(kontrollierer: Any) -> None:
    """Singleton setter for the Kontrollierer (mission orchestrator).

    Called by the server bootstrap (``_init_mission_stack``). Without this
    setter the voice path would dispatch a mission but nothing would trigger
    ``run_mission()`` — the mission would remain PENDING and the user would
    never hear a response.
    """
    if _KONTROLLIERER_REF:
        _KONTROLLIERER_REF[0] = kontrollierer
    else:
        _KONTROLLIERER_REF.append(kontrollierer)


def set_worker_bootstrap_failed(flag: bool) -> None:
    """Mark the worker-harness bootstrap as permanently broken for this process.

    Called from ``server.py::_init_mission_stack`` when the Mission-Stack
    bootstrap raised. spawn_worker reads this via
    ``is_worker_bootstrap_failed()`` and surfaces an honest "could not be
    initialized" message instead of the transient "not ready yet" the
    in-progress path returns.
    """
    _WORKER_BOOTSTRAP_FAILED[0] = bool(flag)


def is_worker_bootstrap_failed() -> bool:
    """Returns True iff the Mission-Stack bootstrap raised at startup."""
    return bool(_WORKER_BOOTSTRAP_FAILED[0])


def _register_runtime_manager(manager: Any) -> None:
    """Publish one Brain Manager and its public tool gateway for all surfaces."""
    try:
        from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
        from jarvis.core import runtime_refs

        runtime_refs.set_brain_manager(manager)
        runtime_refs.set_supervisor_tool_gateway(BrainSupervisorToolGateway(manager))
    except Exception as exc:  # noqa: BLE001 - registration never blocks boot
        log.debug("Runtime supervisor gateway registration failed: %s", exc)


def _phase2_full_brain(
    tier: Literal["router"] = "router",
    bus: Any | None = None,
) -> Any:
    """Build the BrainManager in router tier.

    Wave-4 migration: previously there was also a ``"sub_jarvis"`` tier.
    The Sub-Jarvis tier was replaced by the OpenClaw bridge (see
    docs/openclaw-bridge.md §11). The heavy worker runs as an external
    subprocess via ``MissionManager`` from ``jarvis/missions/``.

    tier="router": RouterBrain system prompt + ROUTER_TOOLS + Haiku.
    """
    from jarvis.brain.manager import BrainManager
    from jarvis.core import config as cfg
    from jarvis.core.bus import EventBus
    from jarvis.memory import (
        CORE_MEMORY_FILENAME,
        CoreMemory,
        MessageRecorder,
        PersonStore,
        RecallStore,
        Soul,
        UserProfile,
        Workspace,
    )
    from jarvis.memory.curator import Curator
    from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor

    config = cfg.load_config()
    if bus is None:
        bus = EventBus()

    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    core_memory = CoreMemory.load(cfg.DATA_DIR / CORE_MEMORY_FILENAME)
    recall = RecallStore(cfg.DATA_DIR / "jarvis.db")
    MessageRecorder(recall).attach(bus)

    workspace = Workspace.ensure(cfg.DATA_DIR / "workspace")
    user_profile: UserProfile | None
    soul: Soul | None
    people: PersonStore | None
    try:
        user_profile = UserProfile.load(workspace.user_path)
        soul = Soul.load(workspace.soul_path)
        people = PersonStore(workspace)
    except Exception as exc:  # noqa: BLE001
        log.warning("Workspace load failed: %s — continuing without profile", exc)
        user_profile = None
        soul = None
        people = None

    from jarvis.clis.risk_integration import make_cli_patterns_fn
    evaluator = RiskTierEvaluator(
        config.safety, extra_patterns_fn=make_cli_patterns_fn(),
    )
    approval = ApprovalWorkflow(bus)
    executor = ToolExecutor(bus, evaluator, approval)

    # HarnessManager for the internal harness fast path (computer-use /
    # local-action). NB: dispatch-to-harness is no longer an
    # LLM-visible router tool (removed 2026-06-28) — see the ROUTER_TOOLS header.
    from jarvis.harness.manager import HarnessManager
    harness_manager = HarnessManager(bus=bus)
    # Config honesty: warn (don't fail) if a [harness.openclaw] block is enabled
    # but the harness is unregistered — that block is inert (Welle-4 removal).
    warn_if_phantom_worker_harness(config, harness_manager)

    # Phase A1: build the AwarenessManager (DI for the awareness-snapshot tool).
    # Do NOT start it here — start()/stop() is the responsibility of the app layer
    # (DesktopApp._start_speech_and_orb or similar). Without start() the tool
    # returns an empty snapshot ("") — that is acceptable for Plan-AC; the tool
    # schema entry in the router is the actual A1 deliverable.
    awareness_manager: Any | None = None
    if config.awareness.enabled:
        from jarvis.awareness.manager import AwarenessManager

        awareness_manager = AwarenessManager(config.awareness, bus=bus)

        # Phase A2: build the Verdichter brain (Haiku) as a separate brain
        # instance and attach it to the AwarenessManager. Hard Negative §6:
        # the Verdichter is a DIRECT brain call, NOT spawn_worker. Therefore
        # its own instance via BrainProviderRegistry, NOT the router brain.
        v_cfg = config.awareness.verdichter
        if v_cfg.enabled:
            try:
                from jarvis.awareness.verdichter import Verdichter
                from jarvis.brain.provider_registry import BrainProviderRegistry

                # BUG-LIVE-04 (2026-05-14) — honour the user-mandate "no
                # Anthropic account". `AwarenessVerdichterConfig` still
                # defaults to provider="claude-api" / model="claude-haiku"
                # for legacy compatibility, but the user's
                # `[brain.primary]` is the real source of truth: every
                # other brain-call path (Critic, ack-brain, sub-jarvis
                # chain) routes through it. The verdichter is the last
                # Anthropic-hardcoded hold-out — when primary is
                # non-Claude, redirect this call too so live logs stop
                # screaming `Your credit balance is too low to access
                # the Anthropic API` every 30 seconds.
                v_provider = v_cfg.provider
                v_model = v_cfg.model
                if (
                    v_provider == "claude-api"
                    and config.brain.primary != "claude-api"
                ):
                    v_provider = config.brain.primary
                    primary_cfg = config.brain.providers.get(v_provider)
                    if primary_cfg is not None:
                        # Verdichter is short, factual, latency-sensitive
                        # — pick a lightweight model from the provider's
                        # config: prefer `model`, fall back to
                        # `deep_model` only when `model` is missing.
                        primary_model = (
                            getattr(primary_cfg, "model", None)
                            or getattr(primary_cfg, "deep_model", None)
                        )
                        if primary_model:
                            v_model = str(primary_model)
                    log.info(
                        "Verdichter provider redirected: claude-api -> %s "
                        "(brain.primary mandate, BUG-LIVE-04)",
                        v_provider,
                    )

                v_registry = BrainProviderRegistry()
                v_brain = v_registry.instantiate(v_provider, model=v_model)
                awareness_manager._verdichter = Verdichter(    # noqa: SLF001
                    brain=v_brain, config=v_cfg,
                )
                log.info(
                    "Verdichter aktiv (provider=%s model=%s timeout=%.1fs)",
                    v_provider, v_model, v_cfg.timeout_s,
                )
            except Exception as exc:    # noqa: BLE001
                log.warning("Verdichter could not be initialized: %s", exc)
                awareness_manager._verdichter = None    # noqa: SLF001

        # Phase A2: build the StoryTracker and attach it to the manager. Lifecycle
        # (start/stop) is handled by the manager in start() — analogous to Watchers.
        # Prerequisites: Verdichter + Recall + story.enabled.
        s_cfg = config.awareness.story
        v_inst = getattr(awareness_manager, "_verdichter", None)
        if s_cfg.enabled and v_inst is not None and recall is not None:
            try:
                from jarvis.awareness.story import StoryTracker

                awareness_manager._story_tracker = StoryTracker(    # noqa: SLF001
                    manager=awareness_manager,
                    bus=bus,
                    recall=recall,
                    verdichter=v_inst,
                    config=s_cfg,
                )
                log.info(
                    "StoryTracker konfiguriert (buffer_max=%d, min_dur=%ds, "
                    "hard_timer=%dmin)",
                    s_cfg.buffer_max, s_cfg.episode_min_duration_s,
                    s_cfg.hard_timer_min,
                )
            except Exception as exc:    # noqa: BLE001
                log.warning("StoryTracker could not be initialized: %s", exc)
                awareness_manager._story_tracker = None    # noqa: SLF001

        # Phase A5-Lite: probes (GitProbe + FileSystemProbe). Called by the
        # watcher drain loop via manager.probe_all() in parallel within a
        # 200 ms total budget. FileSystemProbe requires start/stop —
        # AwarenessManager wires that in start()/stop().
        p_cfg = config.awareness.probes
        if p_cfg.enabled:
            try:
                from jarvis.awareness.probes import FileSystemProbe, GitProbe

                probes_list: list = []
                if p_cfg.enable_git:
                    probes_list.append(GitProbe())
                if p_cfg.enable_filesystem:
                    fs_probe = FileSystemProbe(
                        bus=bus,
                        max_watched_roots=p_cfg.fs_max_watched_roots,
                    )
                    probes_list.append(fs_probe)
                    awareness_manager._fs_probe = fs_probe    # noqa: SLF001
                awareness_manager._probes = probes_list    # noqa: SLF001
                log.info(
                    "Awareness-Probes konfiguriert: %s (budget=%dms)",
                    [type(p).__name__ for p in probes_list], p_cfg.total_budget_ms,
                )
            except Exception as exc:    # noqa: BLE001
                log.warning("Probes could not be initialized: %s", exc)
                awareness_manager._probes = []    # noqa: SLF001
                awareness_manager._fs_probe = None    # noqa: SLF001

    # Build tools — Wave 4: no sub-tier any more; OpenClaw runs as an external
    # subprocess via MissionManager. If a MissionManager is already present in
    # ``app.state`` (server bootstrap), we pass it to ``spawn_worker``.
    # If not (e.g. standalone call via ``build_default_brain`` BEFORE the server
    # bootstrap ran), the tool is excluded from the set — the force-spawn in
    # BrainManager knows this and returns a setup message instead of silently crashing.
    mission_manager_ref: Any = _resolve_mission_manager()

    # Chunk B (jarvis-contacts): build the ContactStore once and share the SAME
    # instance with the contact tools (via the loader) and the BrainManager (for
    # the name-index render). None until Chunk A is merged — graceful no-op.
    contact_store: Any = _build_contact_store()

    tools = _load_tools_for_tier(
        tier,
        bus=bus,
        executor=executor,
        harness_manager=harness_manager,
        user_profile=user_profile,
        people=people,
        config=config,
        mission_manager=mission_manager_ref,
        awareness_manager=awareness_manager,
        recall_store=recall,
        contacts=contact_store,
    )
    local_action_tools = _load_local_action_tools(
        bus=bus,
        harness_manager=harness_manager,
        config=config,
    )

    # Provider-override logic: if the user has switched to a different provider
    # via voice/UI ("switch to gemini" -> brain.primary="gemini" persisted in
    # jarvis.toml) while [brain.router].provider still points to "claude-api",
    # we prefer brain.primary. That is the global master selection that should
    # apply to all tiers.
    startup_override: str | None = None
    tier_cfg_startup = getattr(config.brain, "router", None)
    tier_provider = tier_cfg_startup.provider if tier_cfg_startup else None
    if config.brain.primary and config.brain.primary != tier_provider:
        startup_override = config.brain.primary
        log.info(
            "Startup override: brain.primary=%s overrides [brain.router].provider=%s",
            config.brain.primary, tier_provider,
        )

    # Build the router-tier BrainManager via from_tier_config
    manager = BrainManager.from_tier_config(
        tier,
        config=config,
        bus=bus,
        provider_override=startup_override,
        tools=tools,
        local_action_tools=local_action_tools,
        tool_executor=executor,
        core_memory=core_memory,
        recall=recall,
        user_profile=user_profile,
        soul=soul,
        people=people,
        awareness_manager=awareness_manager,
        contacts=contact_store,
    )

    # Context-aware readbacks (maintainer mandate: no fixed stock phrases). The
    # composer is built in fallback-only mode when the flash path is disabled, so
    # this never changes behavior unless [ack_brain] is on. Router tier only —
    # the CU/local-action readbacks all originate on this manager.
    try:
        manager._readback_composer = build_readback_composer(config)
    except Exception as exc:  # noqa: BLE001 — never block brain wiring on this
        log.warning("Readback-Composer wiring skipped: %s", exc)

    # Live-reload marker: refresh_tools() uses these attributes to reconstruct
    # a factory call identical to this one.
    manager._tier = tier

    # App-Control (2026-05-31): register the live BrainManager so the
    # ``switch-provider`` tool (and the ``describe-app-settings`` snapshot) can
    # reach it at execute-time for a live, no-restart provider switch. Mirrors
    # the MissionManager singleton pattern; a headless build registers itself too.
    _register_runtime_manager(manager)

    # Wire live-reload on BrainToolsChanged (CLI connect/disconnect).
    try:
        manager.attach_to_bus(bus)
    except Exception as exc:  # noqa: BLE001
        log.warning("attach_to_bus failed: %s", exc)

    cu_cfg = getattr(config, "computer_use", None)
    cu_enabled = bool(cu_cfg and cu_cfg.enabled)

    # Vision wiring (Wave-2 latency fix — decoupled). The single ``VisionEngine``
    # feeds TWO independent consumers:
    #   (a) the per-turn screenshot injector ``_vision_provider`` — the always-on
    #       token tax, gated on ``[brain.router.vision].enabled`` (OFF by default);
    #   (b) Computer-Use's on-demand capture (``vision_engine_for_cu``), needed
    #       whenever ``[computer_use].enabled`` so "klick auf X" works.
    # Build the engine when EITHER consumer needs it, but attach the continuously
    # refreshing ``_vision_provider`` ONLY for (a). Previously both were gated on
    # the single vision flag, so disabling the per-turn feed also killed
    # Computer-Use (factory:924 "Vision-Engine fehlt"). They are now independent.
    manager._vision_provider = None
    vision_engine_for_cu: Any | None = None
    if tier == "router":
        router_tier = getattr(config.brain, "router", None)
        vision_cfg = getattr(router_tier, "vision", None) if router_tier is not None else None
        per_turn_vision = _per_turn_vision_active(vision_cfg)
        if _needs_vision_engine(per_turn_vision=per_turn_vision, cu_enabled=cu_enabled):
            try:
                from jarvis.vision.engine import VisionEngine

                # Computer-Use capture strategy (Problem 1, 2026-06-28). The
                # [computer_use].monitor policy ("primary" default) drives the G8
                # MOVE-to-primary hook (ctx.monitor), but the screenshot CAPTURE
                # must FOLLOW the foreground/target window — pinning it to the
                # primary made CU film an EMPTY primary and "do nothing" whenever
                # the target app sat on a secondary monitor. Following the window
                # (consistent with _capture_monitor_geometry, which already does)
                # means CU works on main when the target is moved there and on the
                # secondary when a window can't be moved, instead of freezing. The
                # negative-X absolute-click fix makes secondary clicks land. "all"
                # still captures the whole virtual desktop.
                from jarvis.vision.screenshot import cu_capture_strategy  # noqa: PLC0415

                _cu_monitor = getattr(
                    getattr(config, "computer_use", None), "monitor", "primary",
                )
                engine = VisionEngine(
                    bus=bus, monitor_strategy=cu_capture_strategy(_cu_monitor),
                )
                vision_engine_for_cu = engine
                if per_turn_vision:
                    from jarvis.vision.context_provider import VisionContextProvider

                    manager._vision_provider = VisionContextProvider(
                        engine,
                        bus=bus,
                        refresh_interval_s=vision_cfg.refresh_interval_s,
                        max_staleness_s=vision_cfg.max_staleness_s,
                        capture_mode=vision_cfg.capture_mode,
                    )
                    log.info(
                        "VisionContextProvider instantiiert (interval=%ss, mode=%s)",
                        vision_cfg.refresh_interval_s,
                        vision_cfg.capture_mode,
                    )
                else:
                    log.info(
                        "Per-turn vision injection OFF — VisionEngine kept for "
                        "Computer-Use on-demand capture only (max-speed mode)."
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("Vision engine/provider could not be built: %s", exc)
                manager._vision_provider = None
                vision_engine_for_cu = None

    # Computer-Use runs the in-process screenshot/click/keyboard loop
    # (jarvis/harness/screenshot_only_loop.py) via the ComputerUseContext
    # wired below. Requires the router tier + a vision engine.
    if tier == "router" and cu_enabled and vision_engine_for_cu is not None:
        try:
            from jarvis.harness.computer_use_context import (
                ComputerUseContext,
                set_computer_use_context,
                subscribe_context_reload,
            )

            # Computer-Use tool set: Wave 4 — previously ``sub_tools``
            # (the Sub-Jarvis tier toolbox) was used as the base. After the
            # OpenClaw-bridge migration we load all computer-use-relevant tools
            # directly from entry_points.
            cu_tools: dict[str, Any] = {}
            cu_tools.update({
                name: tool
                for name, tool in tools.items()
                if name in {"screenshot", "run_shell"}
            })
            # Action-registry actions need their corresponding tools in
            # the cu_tools dict — otherwise the brain plans actions that the
            # loop cannot execute. These tools are CU-specific and not in
            # ROUTER_TOOLS, so they are loaded directly from entry_points.
            from importlib.metadata import entry_points as _eps
            _CU_EXTRA = {
                # Verify / wait helpers already wired in earlier waves.
                "wait-for-ui-state",
                "read-visible-ui-state",
                "switch-window",
                # Primary action tools — without these every plan from the
                # CU loop otherwise fails with the historical error below:
                # "Tool '<name>' nicht im Computer-Use-Tool-Set verdrahtet".  # i18n-allow
                # action_registry.py:278-368 maps the corresponding action
                # names (type_text, click, hotkey, move_mouse, open_app) to
                # the tool.name attribute on these classes, so they must
                # be present in cu_tools.
                "type-text",
                "click",
                "hotkey",
                "move-mouse",
                "open-app",
                # Multi-step robustness primitives (Set-of-Marks ReAct loop):
                # click by UIA name (no coordinate guessing), mouse-wheel scroll
                # to reveal off-screen list items, and poll-until-element for
                # app-launch/load waits. Map to action_registry actions
                # click_element / scroll / wait_for_element.
                "click-element",
                "scroll",
                "wait-for-element",
            }
            for _ep in _eps(group="jarvis.tool"):
                if _ep.name not in _CU_EXTRA:
                    continue
                try:
                    _cls = _ep.load()
                    _inst = _cls()
                    if _inst.name not in cu_tools:
                        cu_tools[_inst.name] = _inst
                except Exception as _exc:  # noqa: BLE001
                    # WARNING, not debug: a CU tool that silently vanishes
                    # here makes every plan that names it fail at execute
                    # time with "tool not wired" — invisible at default log
                    # level for months (2026-07-06 forensic: click/
                    # click_element/move_mouse were dropped on every host
                    # without the OS-Level editable install).
                    log.warning(
                        "CU tool '%s' failed to load and is DROPPED from the "
                        "Computer-Use tool set: %s", _ep.name, _exc,
                    )
            # The `drag` action has no entry-point plugin (it was historically
            # handled inline); inject it directly so the CU loop routes drag
            # through the ToolExecutor for risk-tier / blacklist / audit parity
            # (audit #13). Best-effort: a load failure leaves the loop's inline
            # drag fallback in place.
            try:
                from jarvis.plugins.tool.drag import DragTool  # noqa: PLC0415

                cu_tools.setdefault("drag", DragTool())
            except Exception as _exc:  # noqa: BLE001
                log.warning(
                    "CU drag tool failed to load and is DROPPED from the "
                    "Computer-Use tool set: %s", _exc,
                )
            # Wave 3: optionally build the native Gemini computer_use engine.
            # Returns None unless [computer_use].prefer_native is on AND the
            # active provider is Gemini, so the default (hand-rolled) path is
            # unchanged. Any build/runtime failure inside the engine degrades
            # to the hand-rolled loop per-step.
            native_cu = None
            try:
                from jarvis.harness.native_computer_use import GeminiNativeCU
                native_cu = GeminiNativeCU.from_config(config)
                if native_cu is not None:
                    log.info("Computer-Use: native Gemini engine ENABLED (model=%s)",
                             native_cu.model)
            except Exception as _exc:  # noqa: BLE001 — never block CU bootstrap
                log.debug("native CU engine not built: %s", _exc)
            set_computer_use_context(ComputerUseContext(
                vision_engine=vision_engine_for_cu,
                brain_manager=manager,
                tool_executor=executor,
                tools=cu_tools,
                bus=bus,
                step_budget=cu_cfg.step_budget,
                per_step_timeout_s=cu_cfg.per_step_timeout_s,
                think_timeout_cap_s=getattr(cu_cfg, "think_timeout_cap_s", 10.0),
                image_max_bytes=getattr(cu_cfg, "image_max_bytes", 300_000),
                image_max_dimension=getattr(cu_cfg, "image_max_dimension", 2048),
                settle_scale=getattr(cu_cfg, "settle_scale", 1.0),
                fast_step_model=getattr(cu_cfg, "fast_step_model", ""),
                plan_model_override=cu_cfg.plan_model,
                verify_after_each_step=cu_cfg.verify_after_each_step,
                strict_verify=getattr(cu_cfg, "strict_verify", True),
                zoom_before_click=getattr(cu_cfg, "zoom_before_click", False),
                uia_click_fallback=getattr(cu_cfg, "uia_click_fallback", False),
                max_replans=cu_cfg.max_replans,
                announce_progress=getattr(cu_cfg, "announce_progress", False),
                native_cu=native_cu,
                monitor=getattr(cu_cfg, "monitor", "primary"),
                main_monitor=getattr(cu_cfg, "main_monitor", "primary"),
                coordinate_space=getattr(cu_cfg, "coordinate_space", "auto"),
                capture_scope=getattr(cu_cfg, "capture_scope", "window"),
                normalize_window=getattr(cu_cfg, "normalize_window", False),
            ))
            # Hot-reload: refresh the live context's step_budget / timeout /
            # replan knobs on ConfigReloaded so a voice-tunable change
            # ("setze Schrittlimit auf N" -> computer_use.step_budget) applies
            # to the next mission without an app restart (idempotent per bus).
            subscribe_context_reload(bus)
            log.info(
                "ComputerUseContext verdrahtet (tools=%s, step_budget=%d, "
                "verify=%s, max_replans=%d)",
                sorted(cu_tools.keys()),
                cu_cfg.step_budget,
                cu_cfg.verify_after_each_step,
                cu_cfg.max_replans,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("ComputerUseContext could not be set: %s", exc)
    elif tier == "router" and cu_cfg is not None and not cu_enabled:
        log.info("ComputerUseContext disabled ([computer_use].enabled = false)")
    elif tier == "router" and cu_enabled and vision_engine_for_cu is None:
        log.warning(
            "ComputerUseContext could not be wired up: "
            "[computer_use].enabled = true, but the vision engine is missing "
            "(check [brain.router.vision].enabled)"
        )

    # Router tier: inject system prompt
    from jarvis.brain.router import SYSTEM_PROMPT as ROUTER_SYSTEM_PROMPT
    manager._system_prompt_extra = ROUTER_SYSTEM_PROMPT

    # Curator: in Wave 4 this runs only in the router tier (the former
    # Sub-Jarvis tier is gone). Plan anchor remains: collect personal facts
    # from user turns.
    # B4 Soft-Disable (2026-05-17): gated behind
    # ``cfg.memory.legacy_curator.enabled`` (default False). The new
    # WikiCurator handles fact-extraction for the wiki vault; running both
    # in parallel produces two diverging notebooks (see
    # ``LegacyCuratorConfig`` docstring).
    legacy_enabled = bool(
        getattr(getattr(config.memory, "legacy_curator", None), "enabled", False)
    )
    if legacy_enabled and user_profile is not None and people is not None:
        try:
            fast_name = manager._active_name
            fast_brain = manager._get_brain(fast_name, manager._fast_model(fast_name))
            from jarvis.memory.curator import Curator
            manager._curator = Curator(
                brain=fast_brain,
                profile=user_profile,
                people=people,
                bus=bus,
            )
            log.info("Curator active for router tier (legacy_curator.enabled=true)")
        except Exception as exc:  # noqa: BLE001
            log.warning("Curator could not be initialized: %s", exc)
    elif not legacy_enabled:
        log.info(
            "Legacy-Curator soft-disabled (cfg.memory.legacy_curator.enabled=false) "
            "— wiki vault takes over fact extraction via WikiCurator/VoiceFactBridge"
        )

    # B5 Agent C: WikiContextInjector — router-tier only.
    # Attempt to import Agent B's VaultSearch.  If the module is not yet
    # merged (ImportError), pass search=None so the injector is a silent no-op.
    if tier == "router":
        wiki_cfg = getattr(config, "wiki_context", None)
        wiki_enabled = bool(wiki_cfg and getattr(wiki_cfg, "enabled", True))
        if wiki_enabled:
            try:
                from jarvis.brain.wiki_context import WikiContextInjector
                from jarvis.memory.wiki.search import VaultSearch

                # Resolve the vault from [wiki_integration].vault_root — the
                # single source of truth shared with wiki-recall / wiki-page-read
                # / wiki_routes. The hardcoded project path is the last-resort
                # fallback only (see _resolve_wiki_vault_root).
                vault_path = _resolve_wiki_vault_root(config)
                search = VaultSearch(vault_path)
                manager._wiki_injector = WikiContextInjector(
                    search=search,
                    max_chars=getattr(wiki_cfg, "max_chars", 1500),
                    latency_budget_ms=getattr(wiki_cfg, "latency_budget_ms", 80),
                    min_keyword_length=getattr(wiki_cfg, "min_keyword_length", 4),
                )
                log.info(
                    "WikiContextInjector active (vault=%s, budget=%dms)",
                    vault_path,
                    getattr(wiki_cfg, "latency_budget_ms", 80),
                )
            except ImportError:
                # Agent B's search module not yet merged — fallback to no-op.
                from jarvis.brain.wiki_context import WikiContextInjector
                manager._wiki_injector = WikiContextInjector(search=None)
                log.debug(
                    "WikiContextInjector in no-op mode: jarvis.memory.wiki.search "
                    "not available (Agent B not yet merged)"
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("WikiContextInjector could not be initialised: %s", exc)
                from jarvis.brain.wiki_context import WikiContextInjector
                manager._wiki_injector = WikiContextInjector(search=None)

    # B5 follow-up (2026-05-13): expose awareness_manager so the desktop-app
    # startup hook can call awareness_manager.start(). Without this, the
    # StoryTracker never subscribes to ResponseGenerated and no episode is
    # ever written, which prevents SessionRollupWorker from triggering the
    # WikiCurator at idle.
    if awareness_manager is not None:
        manager._awareness_manager = awareness_manager  # noqa: SLF001

    return manager


def _legacy_full_brain(bus: Any | None = None) -> Any:
    """Legacy BrainManager path (before tiered routing) — only via JARVIS_BRAIN=legacy."""
    from importlib.metadata import entry_points

    from jarvis.brain.manager import BrainManager
    from jarvis.core import config as cfg
    from jarvis.core.bus import EventBus
    from jarvis.memory import (
        CORE_MEMORY_FILENAME,
        CoreMemory,
        MessageRecorder,
        PersonStore,
        RecallStore,
        Soul,
        UserProfile,
        Workspace,
    )
    from jarvis.memory.curator import Curator
    from jarvis.safety import ApprovalWorkflow, RiskTierEvaluator, ToolExecutor

    config = cfg.load_config()
    if bus is None:
        bus = EventBus()

    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    core_memory = CoreMemory.load(cfg.DATA_DIR / CORE_MEMORY_FILENAME)
    recall = RecallStore(cfg.DATA_DIR / "jarvis.db")
    MessageRecorder(recall).attach(bus)

    workspace = Workspace.ensure(cfg.DATA_DIR / "workspace")
    user_profile: UserProfile | None
    soul: Soul | None
    people: PersonStore | None
    try:
        user_profile = UserProfile.load(workspace.user_path)
        soul = Soul.load(workspace.soul_path)
        people = PersonStore(workspace)
    except Exception as exc:  # noqa: BLE001
        log.warning("Workspace load failed: %s", exc)
        user_profile = None
        soul = None
        people = None

    from jarvis.clis.risk_integration import make_cli_patterns_fn
    evaluator = RiskTierEvaluator(
        config.safety, extra_patterns_fn=make_cli_patterns_fn(),
    )
    approval = ApprovalWorkflow(bus)
    executor = ToolExecutor(bus, evaluator, approval)

    tools: dict[str, Any] = {}
    active_tools = {"open-app", "type-text", "run-shell", "search-web", "remember",
                    "dispatch-to-harness", "whoami", "cli-tools", "gmail", "vercel",
                    "google_calendar"}
    from jarvis.harness.manager import HarnessManager
    harness_manager = HarnessManager(bus=bus)

    for ep in entry_points(group="jarvis.tool"):
        if ep.name not in active_tools:
            continue
        try:
            cls = ep.load()
            if ep.name == "dispatch-to-harness":
                inst = cls(
                    bus=bus,
                    manager=harness_manager,
                    max_output_chars=config.harness.max_output_chars,
                )
            elif ep.name == "whoami":
                inst = cls(profile=user_profile, people=people)
            else:
                inst = cls()
            if getattr(inst, "is_virtual_loader", False):
                try:
                    expanded = inst.expand()
                except Exception as exc:  # noqa: BLE001
                    log.debug("virtual-loader '%s' expand() failed: %s", ep.name, exc)
                    continue
                for tool in expanded:
                    tools[tool.name] = tool
            else:
                tools[inst.name] = inst
        except Exception as exc:  # noqa: BLE001
            log.debug("Tool %s not loadable: %s", ep.name, exc)

    manager = BrainManager(
        config=config,
        bus=bus,
        core_memory=core_memory,
        recall=recall,
        tools=tools,
        tool_executor=executor,
        user_profile=user_profile,
        soul=soul,
        people=people,
    )

    # Keep the legacy/headless path on the same public supervisor-tool boundary
    # as the primary router factory.
    _register_runtime_manager(manager)

    manager._vision_provider = None
    router_tier = getattr(config.brain, "router", None)
    vision_cfg = getattr(router_tier, "vision", None) if router_tier is not None else None
    if vision_cfg is not None and getattr(vision_cfg, "enabled", False):
        try:
            from jarvis.vision.context_provider import VisionContextProvider
            from jarvis.vision.engine import VisionEngine

            engine = VisionEngine(bus=bus)
            manager._vision_provider = VisionContextProvider(
                engine,
                bus=bus,
                refresh_interval_s=vision_cfg.refresh_interval_s,
                max_staleness_s=vision_cfg.max_staleness_s,
                capture_mode=vision_cfg.capture_mode,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("VisionContextProvider could not be built: %s", exc)
            manager._vision_provider = None

    # B4 Soft-Disable (2026-05-17) — see the matching gate in
    # build_default_brain above for the rationale.
    legacy_enabled = bool(
        getattr(getattr(config.memory, "legacy_curator", None), "enabled", False)
    )
    if legacy_enabled and user_profile is not None and people is not None:
        try:
            fast_name = manager._active_name
            fast_brain = manager._get_brain(fast_name, manager._fast_model(fast_name))
            manager._curator = Curator(
                brain=fast_brain,
                profile=user_profile,
                people=people,
                bus=bus,
            )
            log.info("Curator active (legacy path, legacy_curator.enabled=true)")
        except Exception as exc:  # noqa: BLE001
            log.warning("Curator could not be initialized: %s", exc)

    return manager


def _phase1_gemini_fallback() -> Any:
    from jarvis.brain.gemini_test_brain import GeminiTestBrain
    return GeminiTestBrain()


async def _echo_brain(text: str) -> str:
    return f"Echo: {text}"


def build_default_brain(
    *,
    tier: Literal["router"] = "router",
    allow_phase2: bool = True,
    bus: Any | None = None,
) -> Any:
    """Return a brain callback (async (str)->str) according to the fallback chain.

    Wave-4 migration: previously there were two tiers ``router`` and
    ``sub_jarvis``. The Sub-Jarvis tier was replaced by the OpenClaw bridge —
    see docs/openclaw-bridge.md §11.

    Args:
        tier: "router" — the Haiku delegator tier (the only tier remaining
            after Wave 4).
        allow_phase2: When ``False``, skips BrainManager and falls back
            immediately to the Phase-1 Gemini fallback. Intended for tests.
        bus: Optional ``EventBus``. ``None`` = own bus (voice-pipeline default).

    Control via ENV:
    - `JARVIS_BRAIN=echo`    → pure echo (no LLM)
    - `JARVIS_BRAIN=gemini`  → always GeminiTestBrain
    - `JARVIS_BRAIN=legacy`  → legacy BrainManager without tiered routing
    - unset / "full"         → Phase-5.5 tiered BrainManager
    """
    # Capability-coupling (ADR-0017): seed the CapabilityRegistry once, here in
    # the single authoritative brain entry point, so EVERY runtime (voice, REST,
    # headless) has a populated registry before the first turn. Without this the
    # registry stays empty and BrainManager._check_unsupported_intent rejects
    # every action utterance with the phrase below, pre-empting the provider:
    # "Das kann ich noch nicht ..."  # i18n-allow
    # deterministic force-spawn path (live bug 2026-05-25 — "Kannst du einen
    # Subagent spawnen"). seed_registry is idempotent (re-registration replaces
    # by id), so repeated brain builds AND the dynamic MCP registration in
    # jarvis.mcp.adapter coexist without clobbering each other.
    try:
        from jarvis.core.capabilities import get_registry
        from jarvis.core.capabilities_seed import seed_registry

        seed_registry(get_registry())
    except Exception as exc:  # noqa: BLE001 — a seed hiccup must not kill the brain build
        log.warning("CapabilityRegistry seed failed: %s", exc)

    # Boot-race fix (AD-S6, 2026-06-09 rebuild): the skill context used to be
    # set only late in desktop_app._start_speech_and_orb — the first voice
    # turn could build a system prompt WITHOUT the AVAILABLE SKILLS section
    # (RC2 of "Jarvis never calls a skill"). Set a minimal context here, at
    # the single authoritative brain entry point, when none exists yet. The
    # desktop app later re-sets it with the web server's shared registry —
    # an idempotent upgrade (set_skill_context also re-registers paired
    # capabilities). The empty tool_registry is fine: the instruction-skill
    # model never executes TOOL: lines through this runner.
    try:
        from jarvis.skills.bootstrap import ensure_user_skills_dir
        from jarvis.skills.prefs import load_state_overrides
        from jarvis.skills.registry import SkillRegistry
        from jarvis.skills.runner import SkillRunner
        from jarvis.skills.skill_context import (
            SkillContext,
            set_skill_context,
            try_get_skill_context,
        )

        if try_get_skill_context() is None:
            _skills_root = ensure_user_skills_dir()
            _registry = SkillRegistry(
                root=_skills_root,
                bus=bus,
                state_prefs_loader=load_state_overrides,
            )
            _registry.reload_sync()
            set_skill_context(
                SkillContext(
                    registry=_registry,
                    runner=SkillRunner(registry=_registry, tool_registry={}, bus=bus),
                )
            )
            log.info(
                "Skill context set at brain build time (%d skills from %s)",
                len(_registry.list()),
                _skills_root,
            )
    except Exception as exc:  # noqa: BLE001 — skills must never block boot
        log.warning("skill-context bootstrap at brain build failed: %s", exc)

    # Plugin<->Skill pairing (2026-06-07): after the static seed, register a
    # capability for every live paired skill so connected plugins resolve.
    # Placed after seed_registry so an explicit paired cap overrides the weak
    # MCP auto-cap for the same domain. Defensive: a missing skill context must
    # not block boot (cloud-first graceful degradation).
    try:
        from jarvis.skills.plugin_coupling import register_paired_capabilities
        from jarvis.skills.skill_context import try_get_skill_context

        _ctx = try_get_skill_context()
        if _ctx is not None:
            register_paired_capabilities(get_registry(), _ctx.registry.list())
    except Exception as exc:  # noqa: BLE001
        log.debug("paired-capability seed skipped: %s", exc)

    mode = (os.environ.get("JARVIS_BRAIN") or "").strip().lower()

    if mode == "echo":
        log.info("JARVIS_BRAIN=echo → Echo-Brain aktiv.")
        return _echo_brain

    if mode == "legacy":
        log.info("JARVIS_BRAIN=legacy → Legacy BrainManager (before tiered routing).")
        try:
            return _legacy_full_brain(bus=bus)
        except Exception as exc:  # noqa: BLE001
            log.warning("Legacy BrainManager could not be initialized: %s — falling back.", exc)
            try:
                return _phase1_gemini_fallback()
            except Exception as exc2:  # noqa: BLE001
                log.error("GeminiTestBrain fallback also failed: %s", exc2)
                return _echo_brain

    if mode == "gemini" or not allow_phase2:
        try:
            brain = _phase1_gemini_fallback()
            log.info("Brain stack: GeminiTestBrain (Phase-1b compat mode).")
            return brain
        except Exception as exc:  # noqa: BLE001
            log.warning("GeminiTestBrain not available: %s — falling back to echo.", exc)
            return _echo_brain

    # Default: full BrainManager with tier routing. No silent echo/test-brain
    # fallback in the production path: if the manager cannot be built, the UI
    # should show a genuine error state.
    try:
        brain = _phase2_full_brain(tier=tier, bus=bus)
        log.info(
            "Brain stack: BrainManager tier=%s active — provider=%s, tools=%d",
            tier,
            getattr(brain, "active_provider", "?"),
            len(getattr(brain, "_tools", {})),
        )
        return brain
    except Exception as exc:  # noqa: BLE001
        log.error("BrainManager (tier=%s) could not be initialized: %s", tier, exc)
        raise


# ----------------------------------------------------------------------
# Flash-Brain (Pre-Thinking Ack) factory helper
# ----------------------------------------------------------------------

def _flash_preferences_provider(jcfg: Any) -> Any:
    """A zero-arg callable returning the user's flash-tier preferences block.

    Wired into the ack preamble and the spawn announcement so the user's
    agent-instructions file is honored on action turns (where the spoken output
    comes from these flash tiers, not the deep brain). Reads the file fresh per
    call (via ``agent_instructions.render_for_flash``) so an edit applies without
    a restart, and degrades to ``""`` when no file is set.
    """
    from jarvis.brain import agent_instructions

    def _provide() -> str:
        try:
            return agent_instructions.render_for_flash(jcfg)
        except Exception:  # noqa: BLE001 — never break the flash call on a prefs fault
            return ""

    return _provide


def build_ack_brain(jcfg: Any | None = None) -> Any:
    """Build an AckGenerator from cfg.ack_brain, or return ``None`` when disabled.

    The Flash-Brain runs alongside the main Router-Brain on every user
    utterance and emits a single short acknowledgment within ~900 ms.
    See docs/superpowers/specs/2026-05-11-pre-thinking-ack-flash-brain-design.md.

    Returns ``None`` when:
    - ``[ack_brain].enabled = false`` in jarvis.toml (subsystem master off)
    - ``[ack_brain].preamble_enabled = false`` (the default since 2026-06-21 —
      the speculative preamble is retired; the grounded spawn announcer stays
      wired via ``build_spawn_announcer``)
    - the configured provider is missing from REGISTRY
    - construction of the provider or breaker raises

    Never raises — callers can treat the absence of an AckGenerator as
    "feature disabled" without an exception trail.
    """
    try:
        if jcfg is None:
            from jarvis.core.config import load_config
            jcfg = load_config()
        ack_cfg = getattr(jcfg, "ack_brain", None)
        if ack_cfg is None or not getattr(ack_cfg, "enabled", False):
            log.info("Flash-Brain: disabled in jarvis.toml — skipping wiring.")
            return None
        # The speculative pre-thinking preamble is gated by its own
        # sub-switch (default off, 2026-06-21). `enabled` stays the
        # subsystem master so the grounded spawn announcer keeps its LLM
        # path; only this blind preamble generator is retired here. With
        # no AckGenerator the pipeline's fire-and-forget preamble task
        # never spawns (pipeline.py guards on `_ack_brain is not None`).
        if not getattr(ack_cfg, "preamble_enabled", False):
            log.info(
                "Flash-Brain: pre-thinking preamble disabled "
                "([ack_brain].preamble_enabled=false) — spawn announcer unaffected."
            )
            return None

        from jarvis.brain.ack_brain import AckGenerator, CircuitBreaker

        provider = _build_flash_provider(jcfg, ack_cfg)
        if provider is None:
            return None
        breaker = CircuitBreaker(
            threshold=ack_cfg.circuit_breaker_threshold,
            cooldown_s=ack_cfg.circuit_breaker_cooldown_s,
        )
        # ack_cfg.provider is now the RESOLVED primary (follow_brain expanded).
        prefs_provider = _flash_preferences_provider(jcfg)
        fallback_gen = _build_ack_fallback(ack_cfg, preferences_provider=prefs_provider)
        ack_gen = AckGenerator(
            provider=provider,
            config=ack_cfg,
            breaker=breaker,
            fallback=fallback_gen,
            preferences_provider=prefs_provider,
        )
        log.info(
            "Flash-Brain: AckGenerator wired (provider=%s, timeout_ms=%d, fallback=%s).",
            ack_cfg.provider,
            ack_cfg.timeout_ms,
            getattr(fallback_gen, "_provider_name", None),
        )
        return ack_gen
    except Exception as exc:  # noqa: BLE001
        log.warning("Flash-Brain: build_ack_brain() failed: %s — disabling.", exc)
        return None


def _build_flash_provider(jcfg: Any, ack_cfg: Any) -> Any:
    """Construct the flash-LLM provider adapter for ``ack_cfg``, or ``None``.

    Shared by :func:`build_ack_brain` and :func:`build_spawn_announcer` so
    both flash consumers resolve providers identically. Handles the
    "follow_brain" meta-value: resolve to whatever ``brain.primary``
    currently points at, so the user does not have to keep two provider
    settings in sync. If the main brain is on a provider the Flash-Brain
    has no adapter for (openrouter, claude_api), fall back to the first
    REGISTRY family with a usable credential (AP-22 — a hardcoded literal
    fallback would brick this tier for every downloader whose only key is
    e.g. OpenAI). Mutates ``ack_cfg.provider`` to the resolved name so
    telemetry labels show the concrete provider.
    """
    from jarvis.brain.ack_brain.providers import REGISTRY

    provider_name = ack_cfg.provider
    if provider_name == "follow_brain":
        brain_cfg = getattr(jcfg, "brain", None)
        primary = getattr(brain_cfg, "primary", None) if brain_cfg else None
        if primary and primary in REGISTRY:
            log.info(
                "Flash-Brain: follow_brain -> %s (from brain.primary).",
                primary,
            )
            provider_name = primary
        else:
            fallback = _pick_keyed_flash_fallback(ack_cfg)
            if fallback is None:
                log.warning(
                    "Flash-Brain: brain.primary=%r has no Flash adapter "
                    "(REGISTRY=%s) and no REGISTRY provider has a usable "
                    "credential; disabling Flash-Brain.",
                    primary, sorted(REGISTRY.keys()),
                )
                return None
            log.warning(
                "Flash-Brain: brain.primary=%r has no Flash adapter "
                "(REGISTRY=%s); falling back to keyed provider %r.",
                primary, sorted(REGISTRY.keys()), fallback,
            )
            provider_name = fallback
        ack_cfg.provider = provider_name
    provider_cls = REGISTRY.get(provider_name)
    if provider_cls is None:
        log.warning(
            "Flash-Brain: provider %r not in REGISTRY (have %s); disabled.",
            provider_name,
            sorted(REGISTRY.keys()),
        )
        return None
    provider_cfg = getattr(ack_cfg.providers, provider_name)
    return provider_cls(provider_cfg)


def _pick_keyed_flash_fallback(ack_cfg: Any) -> str | None:
    """First REGISTRY provider family with a usable credential, or ``None``.

    AP-21/AP-22: never hardcode a literal provider name as the fallback — a
    downloader whose only key is e.g. OpenAI must resolve to "openai", not a
    keyless "gemini". Iterates ``REGISTRY`` in its declared order (gemini,
    openai, ollama, ...) as the tie-break so an all-keyed setup keeps the
    historical default. A provider config with no ``api_key_secret`` field
    (local Ollama) needs no credential and is always considered usable.
    """
    from jarvis.brain.ack_brain.providers import REGISTRY
    from jarvis.core.config import get_secret

    for name in REGISTRY:
        provider_cfg = getattr(ack_cfg.providers, name, None)
        if provider_cfg is None:
            continue
        secret_name = getattr(provider_cfg, "api_key_secret", None)
        if secret_name is None or get_secret(secret_name):
            return name
    return None


def _build_ack_fallback(ack_cfg: Any, preferences_provider: Any = None) -> Any:
    """Build the secondary failover AckGenerator, or ``None``.

    Wires ``[ack_brain].fallback_provider`` onto its OWN provider + circuit
    breaker so a busy primary never starves the ack (live bug 2026-06-18: the
    Gemini ack timed out while the Gemini deep brain was slow → 8 s of dead air).
    Best-effort: returns ``None`` (failover simply absent) when the fallback is
    unset, equal to the already-resolved primary, missing from REGISTRY, or
    unbuildable — it must never make the primary ack fail to wire. The returned
    generator has no fallback of its own, so delegation can never loop.
    """
    fb_name = getattr(ack_cfg, "fallback_provider", None)
    if not fb_name:
        return None
    if fb_name == ack_cfg.provider:
        log.info(
            "Flash-Brain: fallback_provider == primary (%s) — no failover wired.",
            fb_name,
        )
        return None
    try:
        from jarvis.brain.ack_brain import AckGenerator, CircuitBreaker
        from jarvis.brain.ack_brain.providers import REGISTRY

        provider_cls = REGISTRY.get(fb_name)
        if provider_cls is None:
            log.warning(
                "Flash-Brain: fallback_provider %r not in REGISTRY (have %s) — "
                "no failover wired.",
                fb_name,
                sorted(REGISTRY.keys()),
            )
            return None
        fb_provider = provider_cls(getattr(ack_cfg.providers, fb_name))
        # Own breaker + a config whose provider label is the fallback (and whose
        # own fallback is cleared, so the fallback generator never recurses).
        fb_cfg = ack_cfg.model_copy(
            update={"provider": fb_name, "fallback_provider": None}
        )
        fb_breaker = CircuitBreaker(
            threshold=ack_cfg.circuit_breaker_threshold,
            cooldown_s=ack_cfg.circuit_breaker_cooldown_s,
        )
        return AckGenerator(
            provider=fb_provider,
            config=fb_cfg,
            breaker=fb_breaker,
            preferences_provider=preferences_provider,
        )
    except Exception as exc:  # noqa: BLE001 — failover must never break wiring
        log.warning(
            "Flash-Brain: failover provider %r build failed: %s — no failover.",
            fb_name,
            exc,
        )
        return None


def _build_spawn_fallback(ack_cfg: Any) -> tuple[Any, Any]:
    """Build the spawn announcer's failover ``(provider, breaker)`` or ``(None, None)``.

    Mirrors :func:`_build_ack_fallback`, but yields a bare provider (the
    ``SpawnAnnouncementComposer`` owns the validate/pool logic, so it needs the
    provider directly, not a wrapping ``AckGenerator``). Returns ``(None, None)``
    — composer runs primary-only — when the failover is unset, equal to the
    already-resolved primary, missing from REGISTRY, or unbuildable. Must never
    break the announcer wiring.

    Call AFTER ``_build_flash_provider`` so ``ack_cfg.provider`` is the resolved
    primary (``follow_brain`` expanded) and the ``== primary`` check is accurate.
    """
    fb_name = getattr(ack_cfg, "fallback_provider", None)
    if not fb_name or fb_name == ack_cfg.provider:
        return None, None
    try:
        from jarvis.brain.ack_brain import CircuitBreaker
        from jarvis.brain.ack_brain.providers import REGISTRY

        provider_cls = REGISTRY.get(fb_name)
        if provider_cls is None:
            log.warning(
                "Spawn-Announcer: fallback_provider %r not in REGISTRY (have %s)"
                " — no failover wired.",
                fb_name,
                sorted(REGISTRY.keys()),
            )
            return None, None
        fb_provider = provider_cls(getattr(ack_cfg.providers, fb_name))
        fb_breaker = CircuitBreaker(
            threshold=ack_cfg.circuit_breaker_threshold,
            cooldown_s=ack_cfg.circuit_breaker_cooldown_s,
        )
        log.info("Spawn-Announcer: failover wired (provider=%s).", fb_name)
        return fb_provider, fb_breaker
    except Exception as exc:  # noqa: BLE001 — failover must never break wiring
        log.warning(
            "Spawn-Announcer: failover provider %r build failed: %s — no failover.",
            fb_name,
            exc,
        )
        return None, None


def build_spawn_announcer(jcfg: Any | None = None) -> Any:
    """Build the ``SpawnAnnouncementComposer`` for the ``spawn_worker`` tool.

    Never raises and never returns ``None``: when the flash-LLM path is
    unavailable (``[ack_brain].enabled = false``, ``spawn_announcements =
    false``, missing adapter, construction error) the composer is returned
    in fallback-only mode — the spoken spawn confirmation is then drawn
    from the curated bilingual no-repeat pool, so the user always hears an
    acknowledgement (AD-OE6).

    The composer gets its OWN provider instance and circuit breaker. The
    breaker state is intentionally not shared with the pre-thinking
    AckGenerator (built later in ``desktop_app``): both protect the same
    provider class, but their call sites have different latency stakes and
    a shared mutable singleton across the two build paths would couple the
    desktop-app wiring to the brain factory for marginal gain.
    """
    from jarvis.brain.ack_brain.spawn_announcement import (
        SpawnAnnouncementComposer,
    )

    try:
        if jcfg is None:
            from jarvis.core.config import load_config
            jcfg = load_config()
        ack_cfg = getattr(jcfg, "ack_brain", None)
        if (
            ack_cfg is None
            or not getattr(ack_cfg, "enabled", False)
            or not getattr(ack_cfg, "spawn_announcements", True)
        ):
            log.info(
                "Spawn-Announcer: flash path disabled — fallback pool only."
            )
            return SpawnAnnouncementComposer()

        from jarvis.brain.ack_brain import CircuitBreaker

        provider = _build_flash_provider(jcfg, ack_cfg)
        if provider is None:
            return SpawnAnnouncementComposer()
        breaker = CircuitBreaker(
            threshold=ack_cfg.circuit_breaker_threshold,
            cooldown_s=ack_cfg.circuit_breaker_cooldown_s,
        )
        log.info(
            "Spawn-Announcer: LLM composition wired (provider=%s).",
            ack_cfg.provider,
        )
        # Failover on a separate provider/key so a dead primary flash provider
        # degrades to the live secondary, not to the canned pool (2026-06-21
        # regression: gemini 429 with no spawn-path failover -> stock phrases).
        fb_provider, fb_breaker = _build_spawn_fallback(ack_cfg)
        return SpawnAnnouncementComposer(
            provider=provider,
            config=ack_cfg,
            breaker=breaker,
            fallback_provider=fb_provider,
            fallback_breaker=fb_breaker,
            preferences_provider=_flash_preferences_provider(jcfg),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "build_spawn_announcer() failed: %s — fallback pool only.", exc
        )
        return SpawnAnnouncementComposer()


def build_readback_composer(jcfg: Any | None = None) -> Any:
    """Build the ``ReadbackComposer`` for the deterministic action-path readbacks.

    The engine behind the maintainer's "no fixed stock phrases" mandate: the CU
    outcome/dispatch readbacks, budget guards, and tool-failed line are phrased
    fresh for the situation by a bounded flash call, with the EXISTING canned
    line as the instant fallback.

    Never raises and never returns ``None``: when the flash path is unavailable
    (``[ack_brain].enabled = false``, ``readback_generation = false``, missing
    adapter, construction error) the composer is returned in fallback-only mode,
    so the call sites still emit the canned line (AD-OE6, zero behavior change).
    Mirrors :func:`build_spawn_announcer` — own provider + breaker plus a
    separate-provider failover so a dead primary degrades to the live secondary,
    not straight to the canned table.
    """
    from jarvis.voice.contextual_readback import ReadbackComposer

    try:
        if jcfg is None:
            from jarvis.core.config import load_config
            jcfg = load_config()
        ack_cfg = getattr(jcfg, "ack_brain", None)
        if (
            ack_cfg is None
            or not getattr(ack_cfg, "enabled", False)
            or not getattr(ack_cfg, "readback_generation", True)
        ):
            log.info("Readback-Composer: flash path disabled — fallback-only mode.")
            return ReadbackComposer()

        from jarvis.brain.ack_brain import CircuitBreaker

        provider = _build_flash_provider(jcfg, ack_cfg)
        if provider is None:
            return ReadbackComposer()
        breaker = CircuitBreaker(
            threshold=ack_cfg.circuit_breaker_threshold,
            cooldown_s=ack_cfg.circuit_breaker_cooldown_s,
        )
        # Failover on a separate provider/key (built AFTER _build_flash_provider
        # so ack_cfg.provider is the resolved primary). Reuses the spawn-path
        # bare-provider failover builder — the composer owns its validate logic.
        fb_provider, fb_breaker = _build_spawn_fallback(ack_cfg)
        log.info(
            "Readback-Composer: LLM composition wired (provider=%s).",
            ack_cfg.provider,
        )
        return ReadbackComposer(
            provider=provider,
            config=ack_cfg,
            breaker=breaker,
            fallback_provider=fb_provider,
            fallback_breaker=fb_breaker,
            preferences_provider=_flash_preferences_provider(jcfg),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "build_readback_composer() failed: %s — fallback-only mode.", exc
        )
        return ReadbackComposer()
