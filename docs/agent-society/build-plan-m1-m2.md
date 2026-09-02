# Build Plan — M1 + M2 (recovered draft, revised)

Status 2026-09-02: waves 1–6 and the backend half of wave 9 are landed on `main` (see
`jarvis/society/README.md` for the module map and the commit log for the per-wave commits);
wave 2b (capability catalog + focus) and 6b (ecosystem card) landed with them. Open: wave 7–8
(frontend section, roster rail, card) and the frontend half of wave 9, wave 10 cleanup.

Status: **draft, recovered 2026-09-01** from the kickoff session's design agent — that session
hit its seat limit the moment this plan arrived, so the maintainer never saw it. It is kept as the
wave breakdown for M1 (substrate) and M2 (identity, chat, Agents section UI) and is subordinate to
[`MASTERPLAN.md`](MASTERPLAN.md); where the two disagree, the master plan wins.

## Revision 2026-09-01 (maintainer decisions after the draft was written)

1. **No Hermes code, no Hermes UI.** Every item below marked PORT / "← `hermes-agent/...`" is
   now an **own implementation from the described behavior**. Nothing is copied or adapted from
   the Hermes clone; no `third_party/hermes-agent/` LICENSE/NOTICE, no provenance headers, no
   `blobatar` dependency (a deterministic avatar comes from our own identicon code in
   `views/contacts/avatar.ts` until the M3 figure pipeline exists). The behavioral rules stay
   (bounded rooms 2–6 / ≤3 rounds / ≤10 messages, typed failure reasons, capability fingerprint,
   one canonical chat per agent with no session pointer column, three-field creation).
   Wave 1 therefore shrinks to: package skeleton + our own failure-reason vocabulary + tests.
2. **The chat lives in the model card, not as an overlay over the stage.** MASTERPLAN §4.2:
   the card is a near-full-screen overlay above the world with three columns
   Specs | 3D figure | Chat; the chat column is the ordinary agent chat (`ChatStage` +
   `AgentChatStoreProvider`, same timeline and reasoning trail as the coding panes). Wave 8's
   `AgentChatOverlay` becomes the card's chat column; Wave 9's spec-sheet drawer becomes the
   card's left column. Row click in the rail and figure click in the world open the same card.
3. **Frontend may start now on sample data.** `components/society/data.ts` (+ `mockRoster.ts`,
   `card/figure/PlaceholderFigure.tsx`, `hooks/useCanvasAwake.ts`) already exist untracked; they
   are the seed of Waves 7–9 and swap to `/api/society/agents` when Wave 5 lands.

The original draft follows unchanged so the reasoning, file anchors and risk list stay intact.

---

Here is the build plan.

---

# Build Plan — Society Stretch 1: M1 (substrate) + M2 (identity, chat, Agents section UI)

All repo paths below are relative to the repository root. (The draft referenced a local Hermes clone at commit `18a76be` as port source; per the revision above nothing is ported from it any more.) The 3D world, society WS, bounded-room *live wiring*, curator, and voice router tool are explicitly **out of scope** (M3/M4).

Ground rules for every wave: commit with explicit pathspecs (`git commit --only -- <paths>`), never `git add -A`; nothing pushes; all committed artifacts English (German/Spanish only inside locale files and `i18n-allow`-marked test quotes); every frontend wave ends with `npm run build` in `jarvis/ui/web/frontend/` (WebView, no F5); every runtime string goes through the `society.*` locale chunk in all three languages; `JarvisAgentsView` stays mounted and green until Wave 8 swaps it.

---

## 1. Wave sequence

### Wave 1 — Attribution + package skeleton + failure vocabulary (S)

**Goal:** the legal and structural floor: `third_party` attribution exists before any ported line lands; `jarvis/society/` exists with the port everything else keys off.

Create:
- `third_party/hermes-agent/LICENSE` — verbatim MIT from the clone.
- `third_party/hermes-agent/NOTICE` — modeled on the clone's `plugins/security-guidance/NOTICE`: Source repo / Subpath list / Commit `18a76be` / License MIT © 2025 Nous Research, plus "Forked content" and "Original work" sections. Grows one subpath line per later port wave.
- `jarvis/society/__init__.py` — docstring only; lazy; imported by nothing at boot (AP-26).
- `jarvis/society/README.md` — package doctrine: single-writer store, lazy init, port provenance rules, per-file header format: `# Derived from hermes-agent (<repo>), <subpath> @ 18a76be. MIT © 2025 Nous Research. See third_party/hermes-agent/NOTICE.`
- `jarvis/society/failure_reasons.py` ← **PORT** `tools/bot_failure_reasons.py` (13 typed failure codes, `retry_action()`, `classify_agent_error()` with auth-beats-quota precedence).
- `tests/unit/society/__init__.py`, `tests/unit/society/test_failure_reasons.py` (adapt the upstream tests).

**Guards:** ruff/mypy/pytest; import-cleanliness gate. No server or frontend change.
**Exit:** society test package green; `jarvis` import cost unchanged (nothing imports the new package).

### Wave 2 — society.db substrate: enums (five-layer), schema, store, roster (M)

**Goal:** the durable spine of MASTERPLAN §3.1, with AP-4 parity locked from day one.

Create:
- `jarvis/society/events.py` — the five-layer home: `MsgType` (`ASSIGN CLAIM RESULT QUERY ANSWER HOLD RELEASE PROPOSE VETO DIGEST SAY` per §2.1 **plus `ROOM_OPEN`, `ROOM_SETTLE`** per §2.2 — the room sequence is typed events in the same log), `Tier` (`lead|orchestrator|specialist`), `AgentState` (`active|paused|archived`), `Checkpoint` (`desk|meeting|archive|gate|idle` — persisted now, rendered in M3), `SocietyEnvelope` (Pydantic; `seq=None` until server-assigned).
- `jarvis/society/society_schema.sql` — mirrors `jarvis/missions/missions_schema.sql` conventions: `society_agents` (UNIQUE `name`, CHECK constraints mirroring every enum, `permission_ceiling` CHECK ∈ `safe|monitor|ask`, `daily_budget_usd`, `parent_agent_id`, `avatar_uri`, `tool_grants` JSON, **no chat-session column** — the Hermes five-hardening-waves lesson, enforced by test); `society_events` (`seq INTEGER PRIMARY KEY`, indexes `(to_agent, seq)` and `(trace_id, seq)`, `cost_usd`, `payload_json`); `society_rooms` (header + member watermarks); `knowledge` + `knowledge_fts` with taint columns (schema fixed now, populated in M4); `approvals`; `society_meta` (kill switch, schema version).
- `jarvis/society/store.py` — `SocietyEventStore`: `open/_apply_migrations/append_and_publish/events_since/events_for_trace/inbox_for`, copied structurally from `jarvis/missions/event_store.py:39-140` (aiosqlite, WAL, `busy_timeout=5000`, INSERT…RETURNING seq, persist-before-publish onto the core `EventBus` with `trace_id`).
- `jarvis/society/roster.py` — CRUD + validation: adopt-before-mint by UNIQUE name, name regex derived from Hermes `_normalize_roster_row` (provenance note), tier/ceiling validation.
- `jarvis/ui/web/frontend/src/lib/societyApi.ts` — **const twins only** this wave: `MSG_TYPES`, `TIERS`, `AGENT_STATES`, `CHECKPOINTS` as-const arrays + union types.
- `tests/unit/society/test_store.py` (append→replay, crash property), `test_roster.py` (uniqueness, adopt-before-mint, **asserts no session column exists**), `test_society_enum_parity.py` — AP-4: Python enum ↔ SQL CHECK ↔ Pydantic ↔ TS consts, modeled on `tests/unit/agent_chat/test_agent_chat_surface_parity.py`.

**Guards:** new AP-4 parity test; ruff/mypy. Still nothing mounted — no boot, no CLI-coverage impact.
**Exit:** store round-trips and replays; parity test green.

### Wave 3 — Hermes core ports: rooms, relay, capability epoch, presentation events (L)

**Goal:** land every remaining backend PORT as tested library code (live wiring of rooms is M4 by design; the modules are exercised by their ported suites and the driver).

Create (each with attribution header; NOTICE updated in the same commit):
- `jarvis/society/rooms.py` ← `gateway/hosted_room_discussion.py` (bounded engine: 2–6 members, ≤3 rounds, ≤10 messages, opt-in later rounds, delta-above-watermark prompts, deterministic `dtask:` sha ids — which become `trace_id`; ~10 constant refs swapped) + the **core** of `gateway/hosted_rooms.py` (actor-gated event-kind map at `:133` kept as append-time validation, `room_state`, capacity accounting) with persistence adapted per design decision (a). Federation half skipped.
- `jarvis/society/room_driver.py` ← `gateway/hosted_room_driver.py`, simplified to `queued|running|settled|failed`.
- `jarvis/society/relay.py` ← ~60 % of `tools/bot_relay.py`: roster validation, target resolution incl. the "ambiguous" third state, tri-state liveness, atomic envelope claim via `os.replace`. CLI waiter/subprocess transport dropped (EventBus replaces it). Turn lock reimplemented with the `msvcrt` pattern from `jarvis/cron/jobs.py:22-33` (Windows) behind a capability probe.
- `jarvis/society/capability_epoch.py` ← `tools/bot_mode_probe.py:305-410` — roster prompt section + sha256 capability fingerprint with fail-closed staleness.
- `jarvis/society/presentation_events.py` ← `gateway/stream_events.py` (frozen dataclasses; "events describe transport, never context; never persisted" invariant kept in the docstring).
- Tests: `tests/unit/society/test_rooms.py` (adapt the 749-line upstream suite — the caps 2–6 / ≤3 / ≤10 are asserted here), `test_room_driver.py`, `test_relay.py` (lock + claim on Windows), `test_capability_epoch.py`.

**Guards:** ruff/mypy on ported code (typing/formatting adaptation is part of the port); silent-handler gate (AP-30) on adapted `except` blocks.
**Exit:** all ported suites green on Windows.
**Parallel:** can run alongside Wave 4 (disjoint files).

### Wave 4 — Scheduler + mission bridge + kill switch (M)

**Goal:** dispatch as a trusted-Python privilege (§2.5), one world feed accumulating (§2.6), the master kill switch (§3.1).

Create:
- `jarvis/society/scheduler.py` — consumes `ASSIGN`: tier wall (refuse from `specialist`; max delegation depth 2 via `parent_event_id` chain; no recursion), `BudgetTracker.assert_under_limit` pre-spawn (`jarvis/missions/budget.py:144-164`) + per-agent daily bucket (today's `society_events.cost_usd` sum vs `daily_budget_usd`), society concurrency cap, kill-switch check, spawn via `MissionManager.dispatch` / `SubagentFanoutRunner.run` under the roster identity. `SAY`/`QUERY` land in the inbox only this wave (canonical-chat delivery arrives in Wave 6). Deterministic per-trace message caps.
- `jarvis/society/bridge.py` — mission/fan-out envelopes → `society_events` rows (same `trace_id`, `cost_usd` from RESULT), using the `JarvisAgentRegistry.attach_mission_bus` pattern (`jarvis/agents/registry.py:146-160`).
- `jarvis/society/runtime.py` — `SocietyRuntime` singleton with lazy `ensure_started()` (opens store, attaches bridge + scheduler subscriptions); called only from routes / chat binding, never at boot (AP-26).
- Tests: `tests/unit/society/test_scheduler.py` (tier wall, budget refusal → typed failure from `failure_reasons`, kill switch halts everything, caps), `test_bridge.py`.

**Exit:** fake-bus test proves: orchestrator ASSIGN spawns (fake manager); specialist ASSIGN refused; kill switch blocks dispatch.
**Parallel:** with Wave 3.

### Wave 5 — REST + CLI surface, os-parity, M1 contract test (M/L) → **M1 exit**

**Goal:** full headless parity (T3) and the M1 exit criterion.

Create/modify:
- `jarvis/ui/web/society_routes.py` — `APIRouter(prefix="/api/society", tags=["society"])`, lazy `SocietyRuntime` factory (the `agent_chat_factory` pattern, `server.py:558-561`). Routes per design decision (f) below.
- `jarvis/ui/web/server.py` — router import in the `:395-408` block + `include_router` in the `:418-586` block, **same commit** (or `check_cli_coverage` fails).
- Curated CLI via the `generate-cli-command` skill: `jarvis society create|list|inspect|message|kill` + `tests/unit/cli_ctl/` additions + `docs/jarvis-cli.md` entry. Dynamic `jarvis api society …` comes free from tags.
- `docs/os-parity.md` — society substrate row (T3 obligation).
- `tests/unit/ui/web/test_society_routes.py`; `tests/contract/test_society_substrate.py` — **the M1 exit test**: seed two agents over REST (TestClient), POST a message that appends a `SAY` A→B, scheduler routes it, `GET /api/society/events` and B's inbox show the typed exchange — no GPU, no audio, no LLM, fakes only.

**Guards:** `check_cli_coverage`, danger-metadata gate, tags on router, `check_boot_budget.py` run manually (server.py touched).
**Exit:** MASTERPLAN M1 exit criterion demonstrated by the contract test; `jarvis api society` reachable.

### Wave 6 — "society" agent_chat surface, chat binding, message_agent (L) → M2 backend

**Goal:** canonical per-agent chat on the existing agent_chat machinery; the Hermes double-gate; per-agent model/tools.

Modify/create (one commit for the three surface twins + parity test):
- `jarvis/agent_chat/store.py:78` — `SURFACES += "society"`; `jarvis/ui/web/agent_chat_routes.py:76,81` — `SurfaceName` Literal + `SURFACE_NAMES`; `jarvis/ui/web/frontend/src/lib/agentChatApi.ts` — `AgentChatSurface` union; `tests/unit/agent_chat/test_agent_chat_surface_parity.py` updated. Add a test that jarvis/agent session lists exclude society sessions (the `local-models` precedent).
- `jarvis/agent_chat/surface_kits.py` — `"society"` kit (`brain_runner=True, cli_seats=False, ladder=_JARVIS_LADDER, uses_stance=True, tool_origin="society", workspace_dir=_chat_workspace`) **plus** new optional session-aware fields (`session_tools` / `session_system_extra` / `session_tool_filter`) — see decision (b). Existing kits untouched.
- `jarvis/agent_chat/runner_brain.py` — `kit_payload`/`build_override` thread the session-aware builders through `_compose_filters` (`:182-195`); `_StepMirror` (`:238-369`) additionally emits a **persisted `reasoning` block at turn end** so the trail survives reopen (the known gap; additive for all brain surfaces, rendered by the existing reducer).
- `jarvis/society/chat_binding.py` — `ensure_session` modeled on `jarvis/local_models/assistant_session.py:296-352`: deterministic id `society:<agent_id>` via `create_session(session_id=…)` (`store.py:158`), adopt-before-mint, roster→session mirror of provider/model/effort, `reseat_session` (`store.py:258-273`) on card edits. Mapping is a pure function — **no pointer column**.
- `jarvis/society/agent_tools.py` — `society_message_agent` per decision (c): Hermes schema prose verbatim, both gates, appends `SAY` only.
- `jarvis/society/scheduler.py` — `SAY`/`QUERY` delivery: wake the target's canonical chat (ensure_session + injected message + turn) under budget + per-trace caps.
- `jarvis/ui/web/society_routes.py` — `POST /api/society/agents/{id}/chat` → `{session_id}` (calls ensure_session; not dangerous).
- Tests: `tests/unit/society/test_chat_binding.py`, `test_message_agent_gates.py` (schema absent on `jarvis`/`agent`/`local-models`; execution refused for non-canonical sessions; **asserts no spawn-capable tool in the society tool set** — AP-5/14), runner_brain reasoning-persistence test.

**Guards:** agent-chat surface parity (same commit), the four blocking guards stay green (`test_routing` untouched — no router change this stretch), AP-3 (handler runs only under `ToolExecutor.execute`).
**Exit:** fake-brain loop headless: message Scout's canonical chat → turn runs with roster-mirrored `TurnOverride` → Scout calls `message_agent` → `SAY` lands in `society_events` → scheduler wakes Archivist's chat.
**Parallel:** with Wave 7 (disjoint paths; only `agentChatApi.ts` is Wave 6's frontend touch).

### Wave 7 — Frontend foundation: i18n chunk, API client, roster kit + UI ports (M)

**Goal:** everything the new section needs, built and unit-tested, mounted nowhere (not user-visible → no broken intermediate state).

Create/modify:
- `src/i18n/locales/society/{en,de,es}.json` — new `society.*` namespace (roster, stage, chat, card, approvals). `src/i18n/index.ts` — `LocaleChunk` union += `"society"`, `CHUNK_LOADERS` entry. `src/i18n/society-i18n-parity.test.ts` modeled on `local-models-i18n-parity.test.ts`. **Main locale files untouched** (section keeps id `agents` and its existing nav key).
- `src/lib/societyApi.ts` — extend the Wave-2 consts with the typed fetch client for every Wave-5/6 route.
- `npm i blobatar@2.0.0` (public npm, MIT — the face generator is not Hermes code) → `package.json` + lockfile (dependency/lockfile-matrix gate).
- **PORTS** (headers + NOTICE update): `src/components/society/profileColor.ts` ← `apps/desktop/src/lib/profile-color.ts` (verbatim); `src/components/society/rosterHelpers.ts` ← `hermes-bots/{types,labels,row-helpers}.ts` slice (displayName ladder, `stripPreviewMarkdown`, A2A prefix, `ACTIVE_WINDOW_S=90`); `src/components/society/runSummary.ts` ← `assistant-ui/tool/run-summary.ts` verbatim + its test ("Thought · 4s" / "Explored 3 files"); avatar shape-grammar slice ← `hermes-bots/avatar.tsx:117-216` into `src/components/society/AgentAvatar.tsx` (blobatar + identicon fallback per `views/contacts/avatar.ts`).
- `src/components/society/RosterRail.tsx` + `RosterRow.tsx` — `ChatRowItem` anatomy from `components/home/RecentChats.tsx:146-204` (active `bg-card` + inset primary bar, mono timestamp via `formatChatWhen`), `StatusDot` from `extensions/primitives.tsx:539`; `CreateAgentPopover.tsx` (3 fields + Advanced disclosure — the no-wizard rule).
- Vitest colocated tests for row/rail/create + the runSummary port test.

**Guards:** society i18n parity test, German gate (`i18n-allow` on tests quoting de/es), bundle budget (nothing here imported from any startup module), light+dark via theme tokens only, `npm run build`.
**Exit:** vitest + build green; startup-bundle delta ≈ 0 (only the i18n union/loader lines).

### Wave 8 — SocietyView: stage + rail + expandable chat + THE SWAP (L)

**Goal:** the new Agents section replaces `JarvisAgentsView` in one atomic commit.

Create/modify:
- `src/views/society/SocietyView.tsx` — flex row: center stage (`relative flex-1 min-w-0`) + right `aside w-[320px] border-l` (mirror of `WikiView.tsx:400-412` / `VisualizationView.tsx:407-458`), resizable via `useResizablePane` + `PaneResizer` (`App.tsx:231` pattern); thin top strip (active count, kill-switch chip, today's cost).
- `src/views/society/useBoardData.ts` — **extracted** from `views/JarvisAgentsView.tsx:70-147` (missions query + outputs + tree poll + `useMissionWebSocket` tick); `JarvisAgentsView.tsx` refactored to consume the hook **in the same commit** so it stays green until deleted.
- `src/views/society/StageBoard.tsx` — `mergeBoardRows`/`missionToNode` verbatim from `views/sub-agents/missionRows.ts`, Panel/StatTile chrome, `.agents-board-stage` ground (`index.css:1176`).
- `src/components/society/AgentChatOverlay.tsx` — copy of `components/agentic/PaneChat.tsx` (never import from `AgenticGrid` — chunk hygiene): `absolute inset-0 z-20` **over the stage container only**, h-11 header, `<AgentChatStoreProvider store=…><ChatStage/>`; one `expandedAgentId` in SocietyView (the `AgenticGrid.tsx:2673` pattern), `key={agentId}` remount, session `society:<agent_id>` via the Wave-6 ensure endpoint, store singleton `createAgentChatStore("society")` (`store/agentChat.ts:224`). Reasoning trail free via `AgentTimeline` (Scratchpad "Thought", `ToolGroup` + `FOLD_STEPS_FROM=4`); composer + `/ @ $` typeahead free via `AgentComposer`.
- `src/components/layout/MainView.tsx` — lazy loader (`:87`) + switch case (`:548`) for `"agents"` now import SocietyView; `FULL_BLEED_SECTIONS` (`:397-401`) += `"agents"`. Sticky branch (`:480-524`) deliberately untouched (M3 concern). Section registry untouched — id, labels, nav keys all stay.

**Guards:** `tests/unit/ui/web/test_section_id_parity.py` + `tests/unit/plugins/tool/test_navigate.py` (green by construction — run anyway), vitest for the view, `npm run build` + bundle-consistency + bundle budget (SocietyView is a lazy chunk), light+dark, `check_boot_budget.py` manual run.
**Exit:** open Agents: board center, Hermes-style roster right, "+" creates an agent, row click expands exactly one chat over the stage with the reasoning trail, second row switches, collapse returns the stage. Old view unmounted but still compiling.

### Wave 9 — Model card, approvals queue + cards, [agent:*] routines (M/L) → **M2 exit**

**Goal:** the remaining M2 surface.

Create/modify:
- `jarvis/society/approvals.py` — queue CRUD, expiry → `blocked` (never silently drop, §2.9), re-surface on focus; `TaskAutoApprover.arm` precedent (`tasks/approval_bridge.py:37-82`); the `unattended` approval surface path enqueues here.
- `jarvis/society/routines.py` — routine creation as tagged tasks: `TaskSpec.tags=["society", "agent:<name>"]`, `created_by="society"`, title prefix `[agent:<name>]` (`tasks/schema.py:189-211`, template precedent `tasks/templates/__init__.py:326-327`); per-routine allowlist via `AgentAction.plugin_grants`. Rows appear in Automations automatically (finish-it-everywhere).
- `jarvis/ui/web/society_routes.py` — `GET /approvals`, `POST /approvals/{id}/resolve` (dangerous), `POST /agents/{id}/routines` (dangerous), `GET /agents/{id}/routines`.
- `src/components/society/AgentCard.tsx` — spec-sheet drawer (provider/model pill, effort, tools + permission badge, routines with next-fire, lifetime stats; actions Chat / Edit / Pause / Kill), opened from the roster row / chat header.
- `src/components/society/ApprovalCard.tsx` — projection in the canonical chat + on the card; badge count on the roster row.
- Tests: `tests/unit/society/test_approvals.py`, `test_routines.py`, route tests, vitest for card + approval card; `society.*` locale keys in all three chunk files.

**Guards:** danger metadata, `check_cli_coverage` (still green), i18n parity.
**Exit:** MASTERPLAN M2 exit — create "Scout", chat with it, give it a routine (visible in Automations **and** the card), approve one asked action end to end.
**Parallel:** backend and frontend halves can be two parallel commits.

### Wave 10 — Cleanup, dead code, badge, hardening (S/M)

- Delete `src/views/AgentsView.tsx` (dead today), `src/views/sub-agents/ToolsCanvas.tsx` (dead), and `src/views/JarvisAgentsView.tsx` (replaced in W8; `useBoardData`/`missionRows.ts` survive under `views/society/` / `views/sub-agents/`).
- Remove `agents_view.*` keys (5 × 3) from the three **main** locale files — the only main-locale edit of the stretch: insert-as-text discipline, identical positions, watch the en.json duplicate-key trap.
- `src/components/layout/Sidebar.tsx:317-319` — agentsCount badge becomes a real running-agent count (from `/api/society/status` or the existing mission store).
- Docs: MASTERPLAN status note (M1/M2 done), `jarvis/society/README.md` final.
- Full sweep: four blocking guards, all i18n parity tests, German gate, `check_cli_coverage`, bundle budget + consistency, `check_boot_budget.py`, ruff/mypy/pytest/vitest.

**Exit:** no orphaned agents-era files; every gate green; boot budget unchanged.

---

## 2. Key design decisions, resolved

**(a) Hermes room log vs. society event log — one DB, `society_events` owns seq.**
One database, `data/society.db`. `society_events` is the **only** event log and the **only** seq owner: `seq INTEGER PRIMARY KEY` assigned by `INSERT … RETURNING` inside `SocietyEventStore.append_and_publish` (persist-before-publish, exactly `missions/event_store.py:100-143`). The single writer is the server process; CLI and REST clients never touch SQLite directly. The ported Hermes room code keeps **no event table of its own**: `hosted_rooms`' append/read become calls into the society store with `msg_type ∈ {ROOM_OPEN, SAY, ROOM_SETTLE}` and `trace_id` = the ported deterministic `dtask:` sha. What is *state* rather than history — membership, round counter, capacity accounting, driver state, per-member watermarks — lives in `society_rooms` (header-first on create, event-first on state change, the `missions/manager.py:148-204` idiom). Watermarks translate to "last seen global seq, filtered by trace_id" — the discussion engine's delta math ports 1:1 because it only needs monotonicity. Hermes' actor-gated event-kind map (`hosted_rooms.py:133`) survives as append-time validation inside `rooms.py`.

**(b) The "society" surface, SurfaceKit, and per-agent TurnOverride.**
Identity chain: roster row (source of truth) → deterministic canonical session `society:<agent_id>` (pure function; **no pointer column** in `society_agents`, enforced by test — the Hermes AGENTS.md:923-990 invariant) → `chat_binding.ensure_session` mirrors the row's provider/model/effort onto the session at bind time and on every card PATCH (`reseat_session` keeps the transcript). `build_override` already reads `session.provider/model/effort` (`runner_brain.py:150-152`), so per-agent model choice needs zero runner surgery. Per-agent **tools and prompt** need session context the current builders lack (`ToolsBuilder`/`ExtraBuilder` are `(cfg, brain)`), so `SurfaceKit` gains three optional session-aware fields (`session_tools`, `session_system_extra`, `session_tool_filter`); `kit_payload(session, brain)` already holds the session and threads them; `build_override` composes the per-agent grant filter via the existing `_compose_filters` (`runner_brain.py:182-195`). Existing kits pass `None` and are untouched. The roster row's `tool_grants` drive `session_tool_filter`; `permission_ceiling` maps onto the Jarvis ladder stance, capped at `ask`, and can never bypass the blacklist (`permissions.py:30-33`). The roster **row in the rail** is pure REST data (`GET /api/society/agents`); expanding it calls `POST /api/society/agents/{id}/chat` → `{session_id}` → the overlay drives the ordinary agent_chat store/WS with that id.

**(c) `message_agent`: Hermes double-gate; dispatch stays a scheduler privilege.**
Gate 1 (schema injection): `society_message_agent` is built **only** by the society kit's tools builder — the society surface *is* the canonical chat, so no other surface, no mission worker set, no `ROUTER_TOOLS` entry ever contains it (deterministic per-session tool list → prompt-cache safe; `test_routing.py` stays untouched and green). Gate 2 (execution): the handler runs under `ToolExecutor.execute()` (AP-3) and re-checks containment — the calling session id must parse to a live, active roster row; the target must resolve to exactly **one** teammate via the ported relay resolution (the "ambiguous" third state returns a typed failure from `failure_reasons`); the kill switch must be off. The tool then does exactly one thing: append a `SAY` envelope (fire-and-forget; schema prose copied verbatim: "compose yourself, never forward verbatim", "one relevant teammate, no fan-out"). It never spawns and never runs a turn. Turning envelopes into activity is the **scheduler's** monopoly: it wakes the target's canonical chat within budget and deterministic per-trace message caps, and only lead/orchestrator `ASSIGN` ever becomes worker spawns (tier wall, depth ≤ 2, no recursion). AP-5/14 hold structurally and are asserted in `test_message_agent_gates.py`.

**(d) Rail + expandable chat inside the section shell.**
The section keeps id `"agents"` — zero registry churn, so `test_section_id_parity` and `test_navigate` are green by construction; the only shell edits are the lazy-loader target and `FULL_BLEED_SECTIONS += "agents"`, both in the swap wave (W8) so no intermediate state changes the live view. Layout: `SocietyView` = stage (`relative flex-1`) + resizable 320 px right rail. The chat is a `PaneChat` **copy** (`AgentChatOverlay`) absolutely positioned `inset-0 z-20` over the **stage container only** — the rail stays visible and clickable, so switching agents is one click; exactly one `expandedAgentId` (the `AgenticGrid.tsx:2673` pattern) with `key={agentId}` remount; collapse restores the stage ("the world stays the star"). The sticky-mount branch (`MainView.tsx:480-524`) is deliberately not touched until M3 needs the 3D world to survive section switches.

**(e) Center stage this stretch: the ledger board on the existing merge; society WS built once, later.**
The stage is the existing DepartureBoard content: `mergeBoardRows`/`missionToNode` verbatim plus the data plumbing extracted from `JarvisAgentsView.tsx:70-147` into `useBoardData` (three-source merge `/api/sub-agents/tree` + `/api/missions` + `/api/outputs`, refreshed by the missions WS tick). **No society WebSocket is built in this stretch.** Server-side, `bridge.py` already populates `society_events` from mission events (M1), so the log accumulates and REST (`GET /api/society/events`) serves it — but nothing subscribes live yet. The society WS (snapshot + delta, `missions_ws_routes.ConnectionManager` recipe: register-before-replay, bounded queue, gap frame) is built exactly once in M3 for the world, and the stage/ledger migrates to it then. Roster liveness/previews meanwhile are cheap REST polling of `GET /agents` (last-event preview computed server-side in the list query) — this is how we avoid building the society WS twice.

**(f) REST/CLI surface (`jarvis/ui/web/society_routes.py`).**

| Route | Method | `x-jarvis-dangerous` |
|---|---|---|
| `/api/society/agents` | POST (create: 3 fields + advanced) | no (DB row, executes nothing) |
| `/api/society/agents` | GET (roster + preview + active) | no |
| `/api/society/agents/{id}` | GET (model-card data + stats) | no |
| `/api/society/agents/{id}` | PATCH (model/tools/ceiling/budget) | no (bounded by global tiers; ceiling ≤ ask; blacklist unbypassable) |
| `/api/society/agents/{id}` | DELETE (archive, never hard-delete) | **yes** |
| `/api/society/agents/{id}/message` | POST | **yes** (triggers spend once chat is wired) |
| `/api/society/agents/{id}/kill` | POST (WorkerKilled + pause) | **yes** |
| `/api/society/agents/{id}/chat` | POST (ensure canonical session) | no |
| `/api/society/agents/{id}/routines` | POST / GET | **yes** / no |
| `/api/society/events` | GET (`after_seq`, `trace_id`, agent) | no |
| `/api/society/status` | GET (kill switch, counts, budgets) | no |
| `/api/society/kill-switch`, `/resume` | POST | **yes** (both directions) |
| `/api/society/approvals`, `/{id}/resolve` | GET / POST | no / **yes** |

CLI: dynamic `jarvis api society …` is free once the router is imported by `server.py` with `tags=["society"]` (the dangerous flag flows into the CLI safety layer via `cli_ctl/dynamic.py:124`). Curated `jarvis society create|list|inspect|message|kill` lands in W5 via the `generate-cli-command` skill (tests + `docs/jarvis-cli.md`).

---

## 3. Risk list

1. **agent_chat surface migration** (three twins + parity + session-list bleed into other chats). *Mitigation:* all three twins + updated parity test in one W6 commit; explicit test that jarvis/agent session lists exclude society sessions (the `local-models` precedent already establishes surface-filtered listing).
2. **i18n main-file edits** (5091-line hand-formatted files, en.json duplicate-key trap, German gate). *Mitigation:* all new strings live in the new lazy `society` chunk (`locales/society/*.json` — new files, own parity test); main files are touched exactly once (W10 key deletion, identical positions, all three at once); `i18n-allow` on tests quoting locale text.
3. **Section registry / navigation parity.** *Mitigation:* the id `"agents"` is never changed — no registry, label, nav-key, or `navigate.KNOWN` edits exist in the whole stretch; `test_section_id_parity` + `test_navigate` run in W8 anyway.
4. **Windows file locking in the ported relay/store** (fcntl-based upstream code; WAL contention). *Mitigation:* turn lock reimplemented on the proven `msvcrt` pattern (`jarvis/cron/jobs.py:22-33`) behind a capability probe; `os.replace` claim is already atomic on NTFS; single-writer doctrine (only the server process writes society.db; CLI goes through REST) plus `busy_timeout=5000`; W3 tests run on the Windows dev box.
5. **Boot budget (AP-26).** *Mitigation:* `jarvis/society` imported by nothing at boot; routes mount through a lazy factory; `SocietyRuntime.ensure_started()` fires on first request only; `check_boot_budget.py` run manually in W5, W8, W10.
6. **Bundle budget / chunk bleed** (blobatar, PaneChat copy dragging AgenticGrid, society locale bytes). *Mitigation:* `AgentChatOverlay` is a copy, never an import from `AgenticGrid`; every society frontend file is reachable only from the lazy SocietyView route chunk; locale strings ride the lazy chunk loader; bundle-budget + bundle-consistency gates per frontend wave with a fresh `npm run build`.
7. **Board-plumbing extraction breaking the live view mid-stretch.** *Mitigation:* `useBoardData` is extracted and consumed by **both** SocietyView and JarvisAgentsView in the same W8 commit; the old view is deleted only in W10 after the swap has soaked.
8. **Ported-code drift vs. the Hermes invariants** (canonical-chat pointer creeping back, room caps loosened, attribution lagging). *Mitigation:* schema test asserts `society_agents` has no session column; the 2–6 / ≤3 / ≤10 caps are asserted in the ported room suite; the NOTICE and per-file headers are updated in the same commit as each port wave (W1/W3/W7).

---

## 4. Size & parallelization

| Wave | Size | Parallel lane |
|---|---|---|
| W1 attribution + failure vocab | S | — (first) |
| W2 substrate (enums/schema/store/roster) | M | — (foundation) |
| W3 Hermes ports (rooms/relay/epoch/stream) | L | ∥ W4 |
| W4 scheduler + bridge + kill switch | M | ∥ W3 |
| W5 REST/CLI + os-parity + M1 contract | M/L | — (needs W2–W4) |
| W6 society surface + binding + message_agent | L | ∥ W7 |
| W7 frontend foundation (i18n/client/rail ports) | M | ∥ W6 |
| W8 SocietyView + swap | L | — |
| W9 card + approvals + routines | M/L | backend ∥ frontend halves |
| W10 cleanup + hardening | S/M | — |

Parallel waves touch disjoint path sets by construction (W3: `jarvis/society/{rooms,room_driver,relay,capability_epoch,presentation_events}.py`; W4: `jarvis/society/{scheduler,bridge,runtime}.py`; W6: `jarvis/agent_chat/*` + `jarvis/society/{chat_binding,agent_tools}.py` + `agentChatApi.ts`; W7: frontend `components/society/` + i18n + lockfile), which keeps shared-tree pathspec commits clean. M1 closes at W5, M2 closes at W9; W10 leaves the tree gate-clean for the M3 world work.

### Critical Files for Implementation
- jarvis/agent_chat/surface_kits.py
- jarvis/agent_chat/runner_brain.py
- jarvis/missions/event_store.py
- jarvis/ui/web/server.py
- jarvis/ui/web/frontend/src/components/layout/MainView.tsx
