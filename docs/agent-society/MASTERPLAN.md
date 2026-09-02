# Agent Society — Master Plan

Status: **building. Nothing ships or gets pushed until the maintainer says so.** Working
codename: `society`; the section keeps the name "Jarvis Agents" (§10).

Progress (2026-09-01/02): **M1 done** — `jarvis/society/` substrate, `/api/society`, the
headless contract test. **M2 backend done** — the `society` chat surface with per-agent hands
and briefing, canonical chat binding, `society_message_agent` / `society_wiki_note`, the
approvals queue, `[agent:*]` routines. **M2 frontend open** — the section (sidebar | stage |
roster rail), the model card (Specs | 3D | Chat) on `components/society/data.ts`, seed
proposals. **M3 world** is the maintainer's parallel track. M4 (rooms live under the scheduler,
curator, `delegate-to-agent` voice tool) not started.

The research behind every claim here lives in [`research/`](research/):
[Branch A — product & UI](research/branch-a-product-ui.md) (Hermes Agent, Grok Bot, 3D stack,
codebase reuse map), [Branch B — societies, memory, cost, safety](research/branch-b-society-memory-cost.md)
(DeepMind/Hutter paper, the reported OpenAI/Hugging Face incident, frameworks, knowledge store,
cost model), and the [gap check](research/gap-check.md) that found the contradictions §2 resolves.

---

## 1. Vision

Replace the current "Jarvis Agents" board with a real agent society: the user creates and fully
customizes named, persistent agents (model/provider per agent — any connected provider incl. local
models — tools, plugins, permissions, avatar, a "model card" profile). Agents work autonomously in
the background, message each other, and form a small hierarchy with Jarvis as the voice-steered
lead. The signature surface is a **3D retro pixel world** (bird's-eye/isometric, Minecraft-like
voxel figures) where the society visibly lives and works: desks for missions, a meeting table for
group discussions, an archive for the shared knowledge store, a gate for routines and outbound
actions. All agents share one knowledge store. The system accumulates skills and memory over time
(artifact self-improvement, never uncontrolled). It must be cheap to run on consumer token budgets,
and it must be **harmless by construction — it never attacks, spams, or harms anyone or anything.**

Competitive frame (verified 2026-09-01): Hermes Bot Mode and Grok Bot both ship "named bots +
canonical chat + routines". Nobody ships the world. The flat list is their face; visible life is ours.

## 2. Decision memo — the nine contradictions, resolved

The gap check found the two research branches disagree on the society's spine. These decisions are
binding; changing one means editing this section first.

1. **Message protocol: typed envelope with prose payload.** The substrate is Branch B's typed,
   append-only event (`msg_type ∈ ASSIGN | CLAIM | RESULT | QUERY | ANSWER | HOLD | RELEASE |
   PROPOSE | VETO | DIGEST | SAY`), carrying a compact JSON payload that MAY include human-readable
   `text`. Chat surfaces render payload text; the world and the scheduler act on the type. Never
   free-form chat as the coordination mechanism (the incident's lesson: agents converge on terse
   codes anyway — we ship the enum on day one).
2. **Group discussions exist, but as bounded typed rooms.** Hermes' constitutional caps are adopted
   verbatim — 2–6 members, ≤3 serial rounds, ≤10 messages, silence allowed — implemented as a typed
   event sequence (`ROOM_OPEN → SAY* → ROOM_SETTLE`) driven by a deterministic policy, not an open
   LLM loop. This is the meeting-table moment in the world and the token-budget mechanism.
3. **An agent at runtime is a durable roster row, never a resident process.** Identity = a row in
   `society.db`. Work = ephemeral mission/fan-out workers running *under that identity*. The
   canonical chat is an `agent_chat` session bound lazily when the user opens it. Idle agents are
   rows woken by bus events — zero processes, zero RAM, zero tokens.
4. **One identity store: `society.db` roster.** No `[agents.*]` schema in `jarvis.toml` (user
   content does not belong in system config; avoids AP-16/AP-31 traps). Existing agent-account
   directories (CLI seats) stay what they are — runner plumbing referenced *by* a roster row.
5. **Hierarchy: three tiers; dispatch is a scheduler privilege, not a tool.** Tiers: `lead`
   (Jarvis, exactly one) → `orchestrator` → `specialist`. Only lead and orchestrators may emit
   `ASSIGN`; the **society scheduler** (trusted Python, not an LLM) translates ASSIGN into worker
   spawns and refuses ASSIGN from specialists. No spawn tool ever appears in any agent tool set
   (AP-5/AP-14 stand unchanged); maximum delegation depth is 2; no recursion. The society cannot
   chain-react — structurally.
6. **One world feed: the society event log.** Existing mission/fan-out events are bridged into
   `society_events` (same `trace_id`) by a thin adapter, so the world, the ledger tab, and the cost
   HUD all read ONE stream (snapshot + delta over one WebSocket). The old three-source merge
   (`/api/sub-agents/tree` + `/api/missions` + `/api/outputs`) feeds the adapter, not the view.
7. **Position: backend owns the semantic place, client owns the pixels.** The roster persists only
   a checkpoint name (`desk | meeting | archive | gate | idle`). Continuous wandering/walking is
   client-side (Hermes roam constants: rest-biased beats, exponential dwell) and deliberately not
   synced between windows — two open windows agree on checkpoints, not on footsteps. Cosmetic, by
   design, documented.
8. **Idle is strictly LLM-free.** No polling, no "look alive" calls, no scheduled housekeeping
   LLM ticks. Curation runs event-driven (after RESULT events, batched), on a local model when one
   is installed, else within an explicit opt-in cloud budget. An idle society costs $0.
9. **Approvals: queue as data, chat cards as projection.** Unattended `ask`-tier actions land in
   one approvals table with expiry; the agent's chat card, the world's gate icon, the Jarvis bar
   badge, and voice ("Scout wartet auf deine Freigabe") are projections of that queue. Expiry
   NEVER silently drops work — the task parks as `blocked` and re-surfaces on the next app focus
   or voice turn.

## 3. Architecture

### 3.1 Substrate (extends the mission subsystem — not a rewrite)

Branch B's verified finding: ~80 % of the substrate exists. We add a durable layer on top of it.

- **`data/society.db`** (WAL, mirrors `missions_schema.sql` conventions):
  - `society_agents` — the roster / model-card data: `agent_id, name, role, tier, provider, model,
    avatar JSON (the figure recipe, character-pipeline.md §9.1), tool_grants JSON, permission_ceiling (safe|monitor|ask — never a
    block-bypass), daily_budget_usd, parent_agent_id, checkpoint, state, created_ms`.
  - `society_events` — append-only typed log (the blackboard): `seq, event_id, msg_type,
    from_agent, to_agent (NULL = broadcast), trace_id, parent_event_id, ts_ms, cost_usd,
    payload_json`; indexes on `(to_agent, seq)` (inbox) and `(trace_id, seq)`.
  - `knowledge` + `knowledge_fts` — derived, curated ~150-word summaries (reuses the
    awareness-episode compaction pattern), with **taint/provenance columns** (`source_agent,
    source_event, origin (user|tool|web|agent), reviewed`) — see §6.
  - `approvals` — the unattended ask-queue (§2.9).
- **Persist-before-publish** exactly as `jarvis/missions/event_store.py` does; the bus
  (`jarvis/core/bus.py`, AP-18 protections) fans out live; startup replays `events_since(seq)`.
- **Identity is free:** only `ToolExecutor.execute()` writes to the board (AP-3), so no agent can
  forge another's messages (the incident had to retrofit signing; we get it by chokepoint).
- **Scheduler:** trusted Python. Consumes `ASSIGN`, checks tier + budget (`assert_under_limit`
  pre-spawn) + society concurrency cap, spawns workers via the existing mission/fan-out machinery
  (worktree + Job-Object containment, AP-10), bridges their events back as society events.
- **Master kill switch:** one flag that halts all dispatch and settles running rooms — the control
  every studied swarm lacked. Surfaced in the UI and as a voice command.

### 3.2 Execution & runners

A working agent = a mission/fan-out worker with the roster row's provider/model/tool grants.
Per-agent model choice rides the existing `agent_chat` catalog (API families, CLI seats, local
brain) — capability-gated, never provider-name-gated (AP-21/22); any single key must yield a
working society. Voice stays Jarvis-only; society agents are reached via Jarvis or typed chat.

**Router contract (T3):** one new router-tier tool (evolution of `spawn-worker`, e.g.
`delegate-to-agent`) added via an ADR-0011 amendment + `test_routing.py`. It ACKs within the
5-second voice budget ("Scout ist dran — ich sage Bescheid") and never blocks on completion;
completions/blocks re-enter voice via the existing announcement path, passing regex-only
`scrub_for_voice` (AP-11). Voice status queries ("was macht Scout?") read the roster + last events,
no LLM required.

### 3.3 The world is a projection

The 3D view subscribes to the society stream and renders state; it can never desync from what
agents actually did, and it holds no truth of its own. Bursts outpacing walk animations are
coalesced client-side by a choreography queue (walk to the *latest* checkpoint, show a digest
bubble). Multi-window/dev-instance viewers are independent projections of the same log.

## 4. UI

### 4.1 World view (the section's face)

One viewport (one-viewer doctrine, overflow in drawers). Isometric pixel island rendered with the
**already-shipped stack**: three ^0.185 + @react-three/fiber v8 + drei, orthographic dimetric
camera, scene rendered into a ~320×180 nearest-filtered render target (`RenderPixelatedPass`) —
crisp retro pixels AND a ~16–36× fragment-cost reduction, which is the medicine for WebView2's
integrated-GPU ceiling. Both world and card canvases mount through `useWebglSurface` (AP-32
context-loss recovery, context budget); rAF pauses via IntersectionObserver (never
`document.hidden` — unreliable in this shell); `frameloop="demand"` when nothing moves;
devicePixelRatio capped at 1.

The world is an **open island** (maintainer decision 2026-09-01 — not a single room), with
checkpoints as places: a **workshop** with a desk row (agent at its desk + glowing screen =
mission running), a **meeting pavilion** (bounded rooms, visible round counter "Runde 2/3"), an
**archive house** (knowledge writes), a **harbor gate** (routines firing / outbound actions /
pending approvals), and **Jarvis' lighthouse** (the lead agent, mapped to the voice orb). Idle
agents wander the island client-side (rest-biased, exponential dwell).
Click a figure → model card. HUD: active count, today's cost (from the costs ledger, §5), "Active
now" face strip.

**Island layout (maintainer decisions 2026-09-01/02, binding detail in
[`world-art-direction.md`](world-art-direction.md)):** the island is a 10 × 10 grid of screen-sized
fields; the central 4 × 4 fields are the **market district** — a solarpunk village in a ring
around ONE open square (the meeting place, with the big tree and the long table), the lead's hub
at the head of the square, the workshop / archive / harbor gate / lighthouse in the four quarters
around it. Camera: steep bird's-eye (50° pitch, 45° yaw), fine pixel grain (2 screen px per
rendered px), three fixed zoom steps, drag / keys / minimap navigation. The meeting pavilion of
the first sketch became the square itself. A **Ledger tab** keeps the current DepartureBoard as the data-dense secondary view
— and is the *declared* fallback wherever WebGL is absent, reduced motion is requested, or the
box is headless.

**Section layout (maintainer sketches, 2026-09-01):** left = the app's existing section sidebar
(unchanged); center = the world viewport (the main stage; a simple live board until M3 lands);
right = a **fixed agents roster rail** — one row per agent (avatar, name, last-message preview,
timestamp, active dot), a "+" to create, search; a thin top strip above the stage whose content is
still open (candidates: active count, today's cost, kill-switch chip). Clicking a figure in the
world and clicking a row in the rail open the SAME model card (§4.2).

### 4.2 Agent model card

The card is a large **overlay window above the world** (maintainer sketch 2026-09-01): almost
full-screen, the world stays visible behind it through a dimmed, blurred scrim; Esc / ✕ close it.
Three columns, in this order: **Specs | 3D figure | Chat.**

- **Specs (left):** the spec sheet described below.
- **3D figure (center):** rotating figure — the agent's **low-poly character with pixel-art textures** (maintainer
decision 2026-09-01; GLB asset, nearest-filtered texture sheet), drag-to-orbit, idle animation.
- **Chat (right):** the agent's canonical chat rendered as the app's ordinary agent chat — the same
  timeline, reasoning trail ("Thought", tool-step groups) and composer the coding panes use for
  Claude Code / Codex sessions. Only ONE agent chat is open at a time (it lives inside the card),
  so the world stays the star. (Earlier "chat overlay over the stage" idea: superseded by this.)

Figure details:
Pipeline: ONE shared low-poly base rig (built once, e.g. in Blender), customization through
swappable part meshes (hair/headgear/outfit) plus palette/texture-sheet variants; AI generation
targets the flat texture sheet and runs through a validate-and-repair step. The full figure
standard — archetypes (`biped` / `quadruped` / `spirit`), the asset contract (+Z forward, origin
at the feet, bone and clip names, sheet layout), the locomotion rules that prevent backwards
walking and foot sliding, the headless Blender build, the four creation workflows and the CI
gate — is [`character-pipeline.md`](character-pipeline.md). Right: the spec sheet — provider/model pill,
effort default; tools & plugins (per-agent allowlist *under* the global tiers); permission badge;
memory scope; routines with next-fire times; lifetime stats (runs, cost, last active). Actions:
Chat, Assign task, Edit, Change avatar (preset parts + palettes / texture upload / AI-generate),
Pause. Creation = **three fields (name, role, description) + an Advanced disclosure**; the agent
introduces itself as its first chat message (Hermes' lesson: no wizard). One rig + one per-agent
texture/part set drives world walker AND card figure; a rendered face crop feeds chat avatars.

### 4.3 World branding (maintainer directive, 2026-09-01)

The world gets its **own, complete branding — it is a game inside the app, not another app
surface.** Direction: bright, beautiful, high-quality video-game art — warm light, saturated
friendly colors, real game feel. Explicitly NOT in scope for the world: the Cursor-derived design
doc (`PersonalJarvisDesignDesign.md.md` — cream/grey editorial canvas, Cursor Orange; that
document never styles the world), and NOT the app's Ink & Paper monochrome. The surrounding app
chrome (sidebar, drawers, ledger, the model card's spec sheet) stays Ink & Paper; the world
viewport and everything rendered inside it (tiles, light, sky mood, in-world labels, speech
bubbles) is its own branded space with its own palette and type. A dedicated art-direction pass
(palette, lighting, tile-set mood boards) is part of M3 before any environment assets are built.

### 4.4 Communication surfaces

Per-agent canonical chat (persistent `agent_chat` session; routine results labeled inline — Grok's
"Aktualisiert: Routine" pattern; approval cards). Society feed: inter-agent messages grouped by
thread/room, every message attributed ("Scout → Archivist"); clicking a world bubble opens the same
thread. Per-agent routines are namespaced `[agent:<name>]` entries in the existing Automations
scheduler and appear in BOTH the Automations view and the model card (finish-it-everywhere rule).

## 5. Cost model (consumer budgets are a hard constraint)

Event-driven, cache-heavy, compact-protocol, local-first. Numbers from Branch B (re-verify pricing
via the claude-api skill at implementation time):

- **Idle: $0.** Decision §2.8. Heartbeats are DB writes; wander is client-side.
- **Heavy task** (5 workers × 20 turns, Sonnet-class): ~$1.80–3.40 cloud; **$0.20–0.50** with
  local-bulk workers escalating hard steps only.
- **Levers:** tiered routing (cheap/local router & curator; strong models only on escalation),
  prompt caching on prefix-stable prompts (system + role card first), the typed protocol itself,
  queued background work in idle windows.
- **One authoritative ledger:** the existing `BudgetTracker` ($5/mission + $50/day token buckets,
  50/80 % warnings, hard abort, pre-spawn check) is the source of truth; per-agent
  `daily_budget_usd` composes as an additional bucket. `society_events.cost_usd`, registry nodes,
  and the world HUD are *projections* of it — meters must never disagree in front of the user.
- **Local-model concurrency:** N agents share one Ollama/native engine → per-instance non-blocking
  lock + queueing + `recover()` (AP-24); never assume free local throughput in UX copy.

## 6. Safety model (harmless by construction)

Hard rule, restated: **this system never attacks, spams, probes, or harms anyone or anything.**
The reported incident is studied ONLY for its coordination architecture; we adopt the substrate and
add the four controls it lacked: authenticated writes (chokepoint), bounded non-recursive hierarchy
(scheduler wall), a master kill switch, and tiered approval for unattended actions.

1. Every action through `ToolExecutor.execute()` (AP-3); blacklist > whitelist > default.
2. **Unattended ceiling = `monitor`.** Ask-tier actions queue for the human (§2.9); block is block.
3. **Anti-harm blacklist class** seeded in the risk tiers: mass outbound messaging, credential
   probing, repeated actions against non-consenting external endpoints — blocked by pattern, not
   by prompt. Voice/chat never accept secrets (AP-2); secrets only via `get_secret`; per-agent
   credentials are keyring-scoped (NEVER Hermes-style per-profile `.env` files — AP-12).
4. **No spawn tools in any agent tool set**; dispatch is the scheduler's privilege (§2.5).
5. Loop caps, iteration counters, spend meters, stall watchdogs (AP-19), `WorkerKilled`, and the
   society master kill switch.
6. **Knowledge taint:** curated rows carry provenance; web/agent-origin knowledge is quarantined
   from silently steering other agents' prompts until reviewed (confused-deputy defense), and
   NOTHING auto-promotes into the user's Obsidian wiki — promotion is a reviewed action.
7. **Self-improvement stays governed:** agents may draft skills (AP-15 `draft` status) into a
   per-agent namespace; a visible approval flow (model card → "Learned skills") is the only path to
   active. Memory curation is rate-limited. No self-modification of society code.

## 7. Hard repo constraints folded in (from the gap check)

- **Voice:** every society voice tool answers < 5 s (`VOICE_TOOL_BUDGET_S`); long work → dispatch +
  ACK; output through `scrub_for_voice`; awareness/wiki stay off the voice critical path (AP-9).
- **Language:** runtime output language comes from `turn_language.py` once per turn — agent chats,
  self-introductions, routine results included. All UI strings via the hand-formatted locale files
  (insert as text; en.json duplicate-key trap), German CI gate respected.
- **Headless / no-GPU (T3):** full REST + `jarvis` CLI parity for create/assign/inspect/kill; the
  Ledger tab is the official non-WebGL representation; `agent_screen` backends per-OS behind
  capability probes; `docs/os-parity.md` updated in the same change.
- **VRAM coexistence:** the world pauses/degrades while local inference is hot (known 1-FPS
  failure mode); FPS cap + frameloop-on-demand; a VRAM/FPS budget documented before M3 ships.
- **Boot & bundle (AP-26):** society DB init lazy, off the boot path; the world is a lazy route
  chunk (skinview3d + pixel pass are new bytes); run `check_boot_budget.py` after startup changes.
- **Parity (AP-4):** `tier`, `state`, `msg_type` cross Python↔SQL↔Pydantic↔TS↔UI → five-layer
  pattern + parity tests. `test_routing.py` gains the new router tool. World rendering gets a
  headless-Chrome measurement check (recipe exists in project memory).
- **App-closed semantics:** local-first inverts Grok's promise — a closed app freezes the society.
  Honest UX copy + missed-routine catch-up policy (run-once-on-next-boot per routine, opt-in).
- **Accessibility:** `prefers-reduced-motion` → static world or Ledger; keyboard/screen-reader path
  = Ledger + model cards (declared equivalent); no strobe/flash effects.
- **Notifications:** finished/blocked agents surface via the existing Jarvis bar + badge counts +
  voice announcements; an expired approval re-asks on next focus (§2.9).
- **Own implementation, no Hermes code (maintainer decision 2026-09-01, supersedes the earlier
  "port Hermes" idea):** Hermes Bot Mode and Grok Bot are REFERENCES for behavior and product
  shape only. No Hermes source is copied or adapted, no Hermes UI (JSX/CSS/components) is
  transcribed, no `third_party/hermes-agent` attribution tree is needed because nothing is taken.
  Rules and constants we adopted as ideas (bounded rooms 2–6 / ≤3 rounds / ≤10 messages, the
  one-canonical-chat invariant, three-field creation, avatar-as-status) are re-implemented from
  their described behavior in our own code. skinview3d MIT (dropped anyway, see below). Asset/skin uploads follow the existing report-then-delist
  precedent; the character pipeline is first-party in meshes, textures and runtime, on a CC0
  skeleton with CC0 clips (KayKit Character Pack: Adventurers, recorded with sha256 and license
  in `scripts/figures/sources/` and `src/assets/society/figures/SOURCES.md` — decision 2026-09-02;
  the earlier skinview3d/Minecraft-skin route was dropped with the avatar decision); no
  third-party game trademarks in product copy — "pixel retro" language only.
  World visual identity: see §4.3 — the world carries its own bright game branding and is exempt
  from both Ink & Paper and the Cursor-derived design doc.

## 8. Milestones (~1 month, sequential waves; nothing pushes until the maintainer says so)

- **M1 — Substrate (T3).** `society.db` (roster/events/knowledge/approvals), typed protocol enums
  (five-layer + parity tests), scheduler with tier wall + budgets + kill switch, mission-event
  bridge, REST + CLI parity, contract tests, os-parity doc. *Exit: two seeded agents exchange
  typed messages end-to-end on a headless box.*
- **M2 — Identity & chat (T2).** Creation flow (3 fields + Advanced), model cards (data, no 3D
  yet), per-agent model/provider/tools/permissions, canonical chats via `agent_chat`, approvals
  queue + cards, `[agent:*]` routines in Automations, i18n. *Exit: create "Scout", chat with it,
  give it a routine, approve one asked action.*
- **M3 — World V1 (T2).** Art-direction pass FIRST (§4.3 — island mood boards, palette, tile/
  building set), then: island terrain + checkpoint places + low-poly pixel-textured walkers (base
  rig + first part/palette set) + model-card figure viewer; snapshot+delta WS; wander;
  click-to-inspect; Ledger fallback wired; reduced-motion; AP-32; lazy chunk; VRAM policy. The
  open-island decision makes this the largest milestone — split into M3a (terrain, navigation,
  walkers) and M3b (buildings, polish) if it crowds the month. *Exit: watching a real mission
  play out on the island, on integrated graphics, with WebGL forced off falling back cleanly.*
- **M4 — Society dynamics (T2/T3 for voice).** Bounded group rooms (meeting table), curator +
  taint + wiki review gate, `delegate-to-agent` router tool + ADR amendment + voice status/ack
  paths, notifications. *Exit: the spoken request "Jarvis, lass Scout und Archivist das zusammen klären" (i18n-allow: quoted German voice example)
  works end to end, costs a visible bounded amount, and the meeting shows in the world.*
- **M5 — Hardening & migration.** Old JarvisAgentsView/AgentsView slots replaced (deep links
  redirected, history visible in Ledger), first-run seed (a starter coordinator + one specialist),
  cost-ledger unification, German gate, boot budget, accessibility pass, full guard suite.
- **M6 — Stretch.** Skill-learning approval flow UI, event triggers ("when a PR merges"),
  per-agent screens ("Bildschirm von X") via `agent_screen` leases, marketplace skin catalog
  (licensing terms first), Gigi as a true 3D lead figure.

## 9. Target folder structure

```
jarvis/society/                     # backend package (M1) — see its README
  __init__.py                       # lazy; nothing on the boot path
  roster.py                         # society_agents CRUD + validation
  events.py                         # typed envelopes (msg_type enums, five-layer home)
  store.py                          # society.db event store (persist-before-publish)
  scheduler.py                      # ASSIGN consumer, tier wall, budgets, kill switch
  bridge.py                         # mission/fan-out events -> society_events adapter
  rooms.py                          # bounded group-discussion policy (deterministic)
  curator.py                        # event-driven knowledge compaction (+ taint)
  approvals.py                      # unattended ask-queue
  society_schema.sql
jarvis/ui/web/society_routes.py     # REST (+ CLI coverage via generate-cli-command)
jarvis/ui/web/frontend/src/components/society/
  world/                            # R3F scene, checkpoints, walkers, choreography queue
  card/                             # model card (rotating GLB figure + spec sheet)
  ledger/                           # DepartureBoard successor tab / fallback
  feed/                             # society message feed & room transcripts
docs/agent-society/                 # this plan + research (already present)
tests/contract/ + tests/unit/society/
```

## 10. Maintainer decisions & open questions

Decided 2026-09-01:

1. **Name:** the section keeps the name **Jarvis Agents** (codename `society` stays internal).
2. **World V1 scope:** an **open island** (not a single room) — M3 sized accordingly.
3. **Avatar style:** **low-poly figures with pixel-art textures** (not Minecraft-skin voxels) —
   one shared base rig, swappable parts, texture/palette variants (§4.2).
4. **World branding:** its own bright video-game identity (§4.3).
5. **Section layout:** sidebar | world stage | fixed agents rail; thin top strip (content open)
   (§4.1).
6. **Model card:** near-full-screen overlay above the world, three columns Specs | 3D | Chat;
   the chat is the ordinary agent chat (Claude Code / Codex-style timeline); one open chat at a
   time (§4.2).
7. **No Hermes code or UI is copied.** Hermes and Grok Bot are orientation only; the whole
   society — backend and UI — is built in-house (§7).
8. **Build start:** frontend card work may proceed on clearly-labeled sample data while M1 lands
   (`components/society/data.ts` is the single swap point).
10. **World layout & art direction (2026-09-01/02):** colourful pixel island, solarpunk village in
    a comic-village ring around the central market square (4 × 4 of 10 × 10 fields), steep
    bird's-eye camera, fine pixel grain, three zoom steps + minimap — see
    [`world-art-direction.md`](world-art-direction.md) (binding for M3 world work). V1 of the
    island ships as the World face of the Jarvis Agents section with the board as its Ledger.
9. **Agent definition & ecosystem wiring:** see [`agent-definition.md`](agent-definition.md)
   (binding). Highlights: full customization per agent (brain, grant/focus/deny over ONE
   capability catalog of plugins + CLIs + MCPs + skills, approval rules, budget, workspace);
   a "Gmail agent" is `grant_mode=all` + `focus=[plugin:gmail]` derived from its description;
   the **Obsidian wiki is the society's shared memory** (namespaced `society/<agent>/` writes,
   reviewed promotion to `society/shared/`) — the `knowledge` table of §3.1 becomes the
   staging/provenance layer, not the truth. Grok Bot re-analyzed there as the product floor.

Still open:

5. Confirm the DeepMind paper: *From AGI to ASI* (arXiv 2606.12683, Hutter & Legg et al.) — the
   research identified it as the one meant; it is an inference, not a certainty.
6. First-run seed agents: ship a starter coordinator + one specialist, or start empty?
7. Content of the thin top strip above the world stage (§4.1).
8. ~~The three figure-pipeline decisions~~ — taken 2026-09-02, all as recommended
   ([`character-pipeline.md`](character-pipeline.md) §12): CC0 skeleton + clips as the biped
   base, the figure recipe as JSON on the roster row, the Tripo likeness route behind the keyring
   flow, off by default. The first figure ships (`biped-medium.glb`) and the Agents section
   renders the rail, the model card with the turnable figure and the creator on sample data.
