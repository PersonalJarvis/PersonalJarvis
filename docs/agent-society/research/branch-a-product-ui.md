<!-- Generated 2026-09-01 by the agent-society research workflow (two lead deep-dive agents, five sub-agents each, plus a completeness critic). Facts about post-cutoff products/events were web-verified by the agents; re-verify load-bearing numbers (pricing, paper identity) before implementation. -->

# Branch A — Product & UI Research: What to Build the Jarvis Agent Society From

This report covers five investigations: the Hermes Agent codebase (cloned and read), the Hermes "Bot Mode" product surface, xAI's Grok Bot (including a hands-on screenshot of the desktop app), the technology choice for the 3D retro world, and a read-only map of the existing Jarvis codebase with reuse/extend/replace verdicts.

---

## 1. What Hermes and Grok actually built

### 1.1 Hermes Agent (Nous Research) — open source, MIT, directly minable

Repo: https://github.com/NousResearch/hermes-agent — **MIT license, © 2025 Nous Research**, so we may legally borrow both ideas and code (keeping the copyright notice when copying substantial portions). Cloned to scratchpad and analyzed; the repo is large and production-grade: `agent/` (~200 modules: conversation loop, context compression, prompt caching, iteration budgets, repetition guard), `tools/` (~150 tool modules plus `toolsets.py` composable named toolsets), `gateway/` (one process bridging Telegram/Discord/Slack/WhatsApp/Signal/CLI), `apps/desktop` (Electron + React), `skills/` (SKILL.md standard, agentskills.io-compatible), `cron/`.

**Agents are directories.** A Hermes "profile" — which Bot Mode renames to a Bot — is a full `~/.hermes/profiles/<name>/` directory containing `config.yaml` (model/provider pin, MCP servers, enabled skills), a `skills/` folder, `.env` secrets, and `SOUL.md` / `USER.md` identity files (`docs/design/profile-builder.md`). Per-agent scoping of model, tools, and memory is therefore *native to the storage layout*, not a bolted-on database schema. Their profile-builder design doc is candid about the two traps they hit: module-level path globals that ignore a profile override, and async installs that break create-flow atomicity — both worth reading before we design our own creation flow.

**Inter-agent communication is deliberately narrow.** One tool, `message_agent(target, message)`, is the *only* send path ("the peers-vs-connections split was itself the bug" — `tools/bot_relay.py` header). It validates the target against a live roster, auto-prefixes `Message from 🤖 <sender>`, and delivers into the target's one canonical Bot Chat. Cross-machine delivery is file-based plumbing (`roster.json`, `outbox/`, `replies/`) relayed by the desktop app, with typed failure reason codes end-to-end (`provider_quota_limit`, `context_overflow`, `target_busy`, `runtime_offline`, …) so a calling agent can branch on failure.

**Group chats are hard-bounded.** `gateway/hosted_room_discussion.py` is a *pure, deterministic* policy over an append-only SQLite room log (`gateway/hosted_rooms.py`, protocol v2): 2–6 members, **max 3 serial rounds, max 10 messages per exchange**, 64 KB reply cap, and "not every Bot replies to every message — speaking is each member's own choice." A durable driver (`gateway/hosted_room_driver.py`) runs the room as a leased task state machine (queued/running/settled/failed/…), so a restart reconstructs a discussion instead of orphaning it. This is the single best token-budget idea in either product: multi-agent conversation with a constitutional cap, not an open loop.

**Subagents** (`tools/delegate_tool.py`): child agent instances get a fresh conversation, their own terminal session, the parent's toolsets minus child-blocked tools, and the parent sees *only the delegation call and the summary* — never intermediate steps. Batch/parallel mode plus worktree isolation (`tools/subagent_worktree.py`) mirrors what Jarvis' missions already do.

**The learning loop** (their headline claim of self-improvement): an agent-curated memory with periodic nudges (`agent/curator.py`, `memory_manager.py`), autonomous skill creation after complex tasks, skills that self-improve during use, and FTS5 search over past sessions with LLM summarization for recall. Self-improvement here means *artifact accumulation* (skills + memory + a user model), not weight updates — a realistic template for our "long-term self-improving" goal.

**The pet system is a working prototype of "agents that visibly live."** Hermes ships a floating pixel pet: spritesheets from a public catalog (petdex.dev manifest, ~2,926 community pets, `agent/pet/manifest.py`), 192×208 px frames, 6-frame walk loop at 1100 ms, and — most importantly — an ambient-AI wander model (`apps/desktop/src/components/pet/roam-behavior.ts`, citing GameAIPro ch. 36): **62 % of decision beats the pet just rests; dwell times are exponential (mean 4.2 s, clamped 1.5–13 s)** so it reads as calm and alive, never metronomic. These exact constants should seed our world's idle behavior.

### 1.2 Hermes Bot Mode — the product surface (launched ~Aug 17, 2026, default-on in Desktop v0.20.3)

From the official docs (https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode) and the launch posts: "Your agent profiles become a series of named Bots. Each Bot has its own role, model, memory, skills and profile picture; Bots can use any model and even communicate with each other. Build a specialist Bot once to use it forever."

UI anatomy, concretely:
- A **Bots tab** in the left sidebar: a **roster** ("one row per agent profile: avatar, latest-message preview, and timestamp"), an **Active now** presence strip, live search, and right-click **Hide Bot** (hidden bots keep working silently).
- Each bot has exactly **one canonical, persistent Bot Chat**; the composer reroutes `/new` to `/compact` so the relationship is continuous — you never "start over" with a colleague.
- **Creation**: three required fields (Name, Title, Description) and the bot introduces itself as the first message. An **Advanced** disclosure adds model/provider pin, custom SOUL.md, per-skill/per-toolset/per-MCP enablement, clone-from source, and a "Create on" machine picker.
- **Avatars**: deterministic **blob faces** drawn from the name (same name → same face, forever), geometric faces whose eyes *scan while the bot works*, uploaded images, AI-generated portraits, or **pixel pets that bounce during activity** — avatar-as-status is a core trick.
- **Routines pane** per bot: a structured schedule picker; routines are plain cron jobs namespaced `[bot:<name>] <routine>`, results land in the bot's chat.
- **@mentions** in any chat hand work to a bot ("your text is never forwarded verbatim, and the reply comes back attributed to that agent"); right-click → **Manage groups** for group chats.
- Bot Mode is itself a **plugin** (Settings → Plugins → Bots) that unregisters live — roster, Routines pane and composer middleware — without touching profiles or cron jobs.

### 1.3 Grok Bot (xAI) — closed, cloud-first, launched in beta August 11, 2026

From https://x.ai/news/introducing-grok-bot, https://docs.x.ai/grok-bot/overview and /faq: Grok Bot is "a team of AI teammates you can give real work to." Each Bot is a persistent named agent; **all Bots on an account share one persistent cloud Linux computer** (browser, filesystem, terminal, app logins), so bots hand work to each other by sharing files and sessions, and "closing the app, laptop, or iPhone does not stop a background turn or routine." Bots "message each other, share context in threads or group chats, and pass ownership so you are not the router between tools"; xAI explicitly recommends **focused bots over one catch-all** and describes **coordinator bots overseeing specialists**. Consequential actions stop for approval (risk-based Auto-review rules; passwords/2FA/CAPTCHAs always human). **Routines** attach a workflow to one bot "on a schedule or, where supported, after an event." Eligibility is subscription-gated (SuperGrok Plus/Heavy and Cursor Pro+/Ultra/Teams per the FAQ) with weekly usage plus paid add-ons; platforms are macOS, Windows x64/Arm64, iOS 18+ — **no Linux, Android or iPad at launch**.

**A hands-on screenshot of the desktop app** (Grok Bot on Windows, German locale; local reference `.jarvis/drops/20260901-151620-Grok_Bot_HULtGHXhnW.png`, gitignored) shows the exact three-column anatomy we should learn from:
- **Left — roster**: search field, "+" create button, a *pinned featured tile* ("Chef Bot" — a coordinator, "Chef" = boss) with a large avatar, then rows of specialist bots ("X marketing", "Morning Meeting", "Discord Bot", "YouTube") — each with a colorful fluffy blob avatar, last-message preview, and a day stamp; bottom: a **Plugins** entry and an account switcher.
- **Middle — the canonical chat** with the selected bot: user bubbles right, bot bubbles left, hover actions (react/redo/menu), and an inline system line ("Aktualisiert: Routine …") labeling a routine-triggered update inside the same thread; composer "Nachricht an X marketing" with attach + microphone. (i18n-allow: quoted German UI labels from the screenshot.)
- **Right — the bot's world**: a live **"Bildschirm von X marketing"** panel (the bot's cloud-computer screen you can watch), and a **Routinen** list mixing schedules ("Every 15 minutes", "Every day at 8:00 AM") with **event triggers** ("When a PR merges…").

One observed exchange is instructive: asked about suppressing X's "Made with AI" label, the bot explained how the label works (client detection, API vs. official app), declined to disguise the posting client, and offered an honest alternative instead — post with the badge, or the human posts in the app and the bot only drafts the text. A bot that declines evasion and offers an honest alternative is precisely the safety posture our hard rule requires.

---

## 2. Adopt vs. deliberately do differently

**Adopt (proven in both products):**
1. **Named, persistent agents with one canonical chat each** — never session-per-conversation for society members (Hermes `/new`→`/compact`; Grok "like bringing on a coworker").
2. **Agent = a directory/config bundle**: model+provider pin, tool/plugin/skill enablement, persona file, avatar — mapped onto our existing config and account machinery (§4).
3. **One inter-agent send path** (`message_agent`-equivalent) with roster validation, automatic sender attribution, and **typed failure reason codes**.
4. **Hard-bounded group discussions**: 2–6 members, ≤3 rounds, ≤10 messages, silence allowed. This is the consumer-token-budget mechanism.
5. **Routines namespaced into the existing scheduler** (`[agent:<name>] <routine>`, results into the agent's chat) — our Automations section already matches the Cowork-quality bar the maintainer set.
6. **Avatar-as-status** (deterministic default face per name; motion while working; "Active now" presence).
7. **Coordinator-over-specialists** shape: Jarvis is the coordinator by construction; the maintainer already builds a "Chef Bot" by hand in Grok — give him that natively.
8. **Learning loop as artifact accumulation**: agents write skills and curated memory, searchable across sessions — our `draft`-gated skill generation (AP-15) is the safe version of this.

**Do differently (deliberately):**
1. **Local-first, every OS.** Grok's bots live on one cloud VM and skip Linux entirely; Hermes needs a gateway process. Our society runs inside the app the user already has, on Windows/macOS/headless Linux (repo baseline rule) — and our `jarvis/agent_screen/` (Windows Sandbox / virtual X display per agent) is the *local* answer to Grok's "bot's computer," with the same watchable-screen panel.
2. **The world is the roster.** Both competitors ship a flat list + chat. Our signature surface is the 3D pixel world where the roster *is visible life* — nobody in this market has that. The flat board stays as a secondary "ledger" tab, not the face.
3. **Idle costs zero tokens.** Agents wander, rest and animate from pure client-side state (Hermes roam constants); an LLM call happens only on a message, a routine firing, or a mission — never to "look alive." This is the single most important cheap-to-run decision.
4. **Safety is structural, not prompted.** Every agent tool call goes through `ToolExecutor.execute()` and the safe/monitor/ask/block tiers; outbound communication to external humans/services is ask-gated in unattended contexts (the approval-surface machinery already distinguishes CONVERSATIONAL vs UNATTENDED). No agent can spawn agents from a worker tool set (AP-5/AP-14) — the society cannot chain-react. Spam/attack capability is absent by construction, matching the hard rule.
5. **Voice stays Jarvis-only.** The user steers the lead agent by voice; society agents are steered by Jarvis and by typed chat — keeping the voice critical path clean (AP-9, 5-second voice tool budget).
6. **Any single key must work** (AP-21/22): per-agent model choice enumerates from code, degrades honestly, and never bricks on a one-provider household — Hermes' "any provider/model pair, different Bots on different models side by side" matched to our capability-gating discipline.

---

## 3. The 3D stack: Three.js + react-three-fiber v8 (already in the app) with a low-res pixelated render target

**Recommended (and the only sensible) stack: Three.js ^0.185 + @react-three/fiber ^8.18 + @react-three/drei ^9.122 — all three are already dependencies of `jarvis/ui/web/frontend/package.json`, already shipping, already proven inside this exact WebView2.** The mission deck already renders a real 3D room in R3F: `src/components/deck/DeckStage3D.tsx`, `src/components/deck/room/GigiFigure.tsx` (an extruded, breathing, pointer-tracking, clickable mascot — R3F raycast events proven), `src/lib/deckRoom.ts` (world-unit layout, fixed camera with pointer sway), and `src/hooks/useWebglSurface.ts` — the AP-32 implementation that releases contexts on unmount, handles `webglcontextlost` with `preventDefault()`, rebuilds twice with a 250 ms beat, and degrades to a flat fallback (Chromium's hard limit is 16 live WebGL contexts per page; the hook exists because the app already hit it).

Why not the alternatives: **Babylon.js** is a full engine (physics, GUI, inspector) — capabilities we don't need at several times three.js' ~168 kB gzipped core, and it would put a second 3D stack beside the shipped one. **PlayCanvas** is editor-centric ("Figma for 3D"), built around a proprietary cloud editor and team pipeline — wrong shape for a git-versioned open-source app. **A 2.5D DOM/canvas sprite approach** would be cheapest but cannot produce the rotating 3D figure in the model card or a true walkable world, and it duplicates none of the existing deck-room investment; it survives only as the AP-32 degraded fallback (static sprite portraits when contexts are lost), matching the flat-map pattern `useWebglSurface` already implements. React 18.3.1 pins us to R3F v8 (v9 requires React 19) — fine, v8 is the stable line for React 18.

**The retro rendering recipe** (bird's-eye/isometric, pixelated-Minecraft look):
- **Orthographic camera** at a classic dimetric angle (rotate X ≈ −30–35°, Y = 45°) — the standard three.js retro setup.
- **Render the scene into a low-resolution `WebGLRenderTarget`** (target ~320×180 to 480×270, i.e. a 4–6× downscale) **with `NearestFilter`**, then blit it to the canvas — or simply use three's own `RenderPixelatedPass` addon (`three/addons/postprocessing/RenderPixelatedPass.js`), which packages exactly this with optional depth/normal edge lines. Crisp pixels, no shimmer, and — critically for WebView2 — **fragment cost drops by the downscale factor squared**, which is the right medicine for the platform's known weaknesses: WebView2 is hardware-accelerated for WebGL2 but pins to the integrated GPU on dual-GPU machines (Chromium can't composite across GPUs) and has measured ~50 % frame-rate deficits vs. a full Edge window in heavy scenes. A 320×180 world sails on integrated graphics.
- Optional flavor: an 8–16 color palette-quantization + ordered-dither shader on the blit for the full retro grade; `SRGBColorSpace` and flat lighting to keep colors chunky. Cap `devicePixelRatio` at 1 for the world canvas.
- **Characters: the Minecraft skin format is the asset pipeline.** A 64×64 PNG in the standard skin layout drives a ~600-triangle box rig (head/torso/arms/legs, optional overlay layer). Use **skinview3d** (MIT, bs-community, three.js-based, ships `WalkingAnimation`, handles classic + slim arms) directly for the **rotating figure in the model card**, and reuse its texture-UV conventions for lightweight instanced world figures (one shared geometry, per-agent texture). One PNG per agent = trivially customizable: preset palette, user upload, or AI-generated via the existing image routes (xAI image works today per repo memory) — same texture renders the world walker, the card figure, and a 2D face crop for chat avatars. A community catalog via our Marketplace later mirrors petdex.dev.
- **Movement: waypoint graph, not navmesh.** The world is tiled; checkpoints (desk, archive, gate, meeting table…) are named nodes; A* on the tile grid between them (a ~80-line function — no library needed). Wander behavior copies Hermes' roam constants (rest-biased beats, exponential dwell) so idle agents read as alive at zero token cost; state changes (mission started, message sent) override wander with purposeful walks to the relevant checkpoint.
- **Interaction:** R3F pointer events on instanced meshes give click-to-inspect (GigiFigure's `onPress` already demonstrates the pattern); clicking an agent opens the model card; speech bubbles are drei `Html` overlays anchored to figures.
- **Discipline:** the world canvas and the model-card canvas both mount through `useWebglSurface` (context budget + loss recovery, AP-32); the world pauses its rAF loop when its section is hidden — but gated on IntersectionObserver/element visibility, *not* `document.hidden` (repo memory: WebView2 visibility signals are unreliable).

---

## 4. How the society hooks into the existing codebase

**Frontend (all under `jarvis/ui/web/frontend/src/`):**
- `views/JarvisAgentsView.tsx` + `views/sub-agents/` (DepartureBoard, AgentInsight, missionRows) — **REPLACE as the section's face, REUSE its data spine.** Its three-source merge (`/api/sub-agents/tree` live registry + `/api/missions` durable record + `/api/outputs` archive) is exactly the feed the world needs to know who is working on what; the board survives as a "Ledger" tab, AgentInsight as the run drill-down behind a world click.
- `views/AgentsView.tsx` ("Agent-Team", currently an empty-state placeholder) — **REPLACE**: this is the slot the society fills.
- `components/deck/room/*`, `lib/deckRoom.ts`, `hooks/useWebglSurface.ts` — **EXTEND** into the world renderer (camera/layout math, mascot techniques, context-loss discipline).
- One-viewer layout doctrine applies (whole section in one viewport, overflow into drawers).

**Backend:**
- `jarvis/agent_chat/` (service.py: one running turn per session, WebSocket fan-out, approval futures; catalog.py: provider rows = coding CLIs from `jarvis/workspace/agents.py` + API families + local models; runner_api / runner_cli / runner_brain; permissions.py; approval_bridge.py; typeahead.py) — **REUSE wholesale.** This is already a per-agent conversation engine with per-turn provider/model/effort choice across API keys, coding-CLI seats and the local brain. A society agent = a persistent agent-chat session bound to an identity; a new `SurfaceKit` gives the society its surface.
- `jarvis/agent_accounts.py` + `jarvis/workspace/agents.py` (`AccountSpec`, one-account-one-directory, per-spawn `CLAUDE_CONFIG_DIR`/`CODEX_HOME` env) — **REUSE**: agents backed by CLI seats get exactly the isolation Hermes gets from profile directories.
- `jarvis/missions/` (manager.py persist-before-publish event log, state_machine, recovery, `isolation/` git-worktree + Windows Job Object + env allowlist, kontrollierer/, critic/, subagent_fanout.py, tool_approvals.py) — **REUSE as the execution substrate**: an agent "working" in the world is a mission dispatched under that agent's identity; AP-10 containment stays untouched.
- `jarvis/core/bus.py` + `core/events.py` + `core/protocols.py` — **EXTEND**: new frozen events (`AgentSocietyMessage`, `AgentStateChanged`, `AgentRoutineFired`…) carrying `trace_id`; the wildcard-subscriber timeout (AP-18) already protects the bus from a stalled world-view WebSocket.
- **Router constraint respected**: `ROUTER_TOOLS` (`jarvis/brain/factory.py:61`) stays a pure dispatcher; the society is reached through *one* router-tier tool (an evolution of `spawn-worker`, e.g. `delegate-to-agent`), added via an ADR-0011 amendment + `test_routing.py` — and **never** appears in any worker tool set (AP-5/AP-14). `jarvis/sub_jarvis/` is an empty tombstone; no SUB_TOOLS resurrection.
- `jarvis/safety/tool_executor.py` — **REUSE untouched**: "the only authorized entry point for tool calls." Per-agent permissions = per-agent tool allowlists layered *under* the safe/monitor/ask/block tiers (blacklist > whitelist > default), approvals surfacing through `agent_chat/approval_bridge.py` into the agent's chat, exactly like Grok's approval cards.
- `jarvis/core/config.py` (+ `config_writer.py` locked writes only) — **EXTEND** with an `[agents.*]` schema (respect AP-16 `extra="allow"` gotchas, AP-31 no dead fields); provider enumeration from code, never from this box's `jarvis.toml`.
- `jarvis/agent_screen/` (manager.py leases, port.py seam, same-named tools) — **EXTEND**: per-agent isolated screens are our "Bildschirm von X" panel; a lease per society agent, streamed into the model card.
- `jarvis/memory/` (`wiki/` curator + FTS index + consolidator = the Obsidian wiki; `soul.py`; `user_profile.py`; `people.py`) — **REUSE as the shared knowledge store** all agents read/write through the existing recall tools; **EXTEND** `soul.py`'s pattern to per-agent souls (Hermes SOUL.md ≙ our persona file). Off the voice critical path (AP-9).
- Automations/scheduler + `views/AutomationsView.tsx` — **EXTEND**: per-agent routines as namespaced entries (`[agent:<name>]`), surfaced both in the global Automations section and on the agent's model card, with event triggers later.
- `sessions.db` + `jarvis/agentic_ide/` — **REUSE** transcript/recording patterns; the IDE stays its own surface (a society agent may *own* IDE panes later, not now).

---

## 5. The new UI surfaces (sketch)

**World view (the section's face).** One viewport: the isometric pixel world. Checkpoints with meaning — a **desk row** (one desk per agent; an agent at its desk with a glowing screen = mission running, feed: sub-agents tree + missions store), a **meeting table** (agents gather during a group discussion; bubbles show the bounded rounds), an **archive** (walks there when writing to the wiki), a **gate** (walks there when a routine fires or an external channel is touched), and **Jarvis' podium** — Gigi, the existing mascot, as the lead agent the voice orb maps onto. Idle agents wander with the rest-biased roam model. Click a figure → model card. A thin HUD strip: active count, tokens/cost today (from the existing costs section's data), and an "Active now" row of face crops. Ledger tab preserves today's DepartureBoard.

**Agent model card (click-to-inspect).** Left: the **rotating skinview3d figure** (same 64×64 skin as the world, `WalkingAnimation` idle, drag-to-orbit) over name + role line. Right, the spec sheet: provider/model pill (any connected provider incl. local — catalog rows), effort default; tools & plugins (per-agent allowlist under the global tiers); permission tier badge (safe/monitor/ask/block posture); memory scope (shared wiki + private notes); routines list with next-fire times; lifetime stats (runs, cost, last active — from missions/outputs). Actions: Chat, Assign task, Edit, Change avatar (preset / upload / generate), Pause. Creation flow copies Hermes' lesson: **three fields (name, role, description) + an Advanced disclosure**, agent introduces itself as its first message — not a 10-step wizard.

**Communication view.** Per-agent: the canonical chat (agent_chat session) with routine results labeled inline (Grok's "Aktualisiert: Routine" pattern) and approval cards. Society-wide: a feed of inter-agent messages (bus events), grouped by thread/room; a group discussion renders as a room transcript with its round counter visible ("round 2 of 3") so the cost bound is *legible*, not hidden. Every inter-agent message is attributed ("from Scout → Archivist") and clicking a bubble in the world opens the same thread — one source of truth, two projections.

**Safety, visible.** The society ships with the block tier enforced at `ToolExecutor`, ask-gated outbound actions in unattended contexts, no spawn tools in worker sets, and bounded discussions — and the UI *shows* the bounds (round counters, approval cards, a per-agent permissions badge), because a harmless assistant should also look accountably harmless.

---

## Sub-agent trail

### A1 — Hermes Agent repository analysis (run inline; Agent tool unavailable)

Cloned NousResearch/hermes-agent (shallow, into the scratchpad; needed core.longpaths on Windows) and read the architecture: MIT license, agents-as-profile-directories (config.yaml + skills/ + .env + SOUL.md/USER.md), a single message_agent send path with typed failure codes, SQLite hosted rooms with a deterministic bounded discussion policy (2–6 members, max 3 rounds, 10 messages), delegate-tool subagents with summary-only reporting, an artifact-based learning loop (curator, skill creation, FTS5 recall), and a desktop pixel-pet system (petdex catalog, rest-biased roam AI with exponential dwell) plus a subagent-tree Agents panel in the Electron app.

### A2 — Hermes Bot Mode product surface (web research)

Bot Mode launched ~Aug 17, 2026, default-on in Hermes Desktop v0.20.3 as a plugin. The official docs detail the full UI: a Bots roster tab (avatar, preview, timestamp, Active-now strip, hide via right-click), one canonical Bot Chat per bot (/new reroutes to /compact), a 3-field create dialog with an Advanced disclosure (model pin, SOUL.md, per-skill/MCP enablement, create-on-machine picker), avatar system with deterministic blob faces and activity-animated pixel pets, per-bot Routines as namespaced cron jobs, @mention handoffs, and cross-machine relay with self-propagating rosters.

### A3 — Grok Bots: screenshot + web research

Described a hands-on screenshot of Grok Bot on Windows: three columns — bot roster with blob avatars and a pinned 'Chef Bot' coordinator, the canonical chat with inline routine-update labels, and a right panel showing the bot's live screen ('Bildschirm von X marketing') plus a Routinen list mixing schedules with event triggers ('When a PR merges…'). Web research confirmed Grok Bot launched in beta Aug 11, 2026 for SuperGrok/Cursor plans: persistent named agents sharing one cloud Linux computer per account, bot-to-bot messaging and group chats, learned routines, risk-based approvals, and no Linux desktop support.

### A4 — 3D retro world technology evaluation

Recommended exactly one stack: Three.js ^0.185 + @react-three/fiber v8 + drei — all already dependencies of the Jarvis frontend and already shipping in the deck room (DeckStage3D, GigiFigure, useWebglSurface with AP-32 context-loss recovery). The retro look comes from an orthographic dimetric camera rendering into a ~320×180 nearest-filtered render target (or three's RenderPixelatedPass), which also solves WebView2's integrated-GPU performance ceiling; characters use the 64×64 Minecraft skin format with skinview3d for the rotating model-card figure, grid A* over named checkpoints for walking, and Hermes' roam constants for zero-token idle life. Babylon.js (size, duplicate stack), PlayCanvas (editor-centric) and pure 2.5D sprites (no 3D card figure) were rejected.

### A5 — Existing Jarvis codebase map (read-only exploration)

Mapped the substrate with verdicts: REPLACE the JarvisAgentsView board face and the empty AgentsView placeholder while reusing their three-source data merge; REUSE jarvis/agent_chat/ (per-turn provider/model choice across API, CLI-seat and brain runners), agent accounts (one-directory-per-seat), jarvis/missions/ (event-sourced lifecycle, worktree+job-object isolation), ToolExecutor + risk tiers, EventBus/protocols, and the wiki memory as the shared knowledge store; EXTEND config.py schema, agent_screen (per-agent isolated screens), soul.py to per-agent souls, the Automations scheduler for per-agent routines, and the deck-room 3D components. The router stays a pure dispatcher over ROUTER_TOOLS (jarvis/brain/factory.py:61) reached via one new ADR-0011-amended tool, with no spawn tools in worker sets and jarvis/sub_jarvis/ left dead.

---

## Sources

- [Hermes Agent repository (Nous Research, MIT)](https://github.com/NousResearch/hermes-agent)
- [Bot Mode — Hermes Agent official docs](https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode)
- [Nous Research: Introducing Bot Mode for Hermes Desktop (X announcement)](https://x.com/NousResearch/status/2089429432612147572)
- [Nous Research: Bots are coming to Hermes Desktop (X teaser)](https://x.com/NousResearch/status/2088802450727637022)
- [MarkTechPost: Nous Research Ships Bot Mode for Hermes Agent (2026-08-17)](https://www.marktechpost.com/2026/08/17/nous-research-hermes-bot-mode/)
- [daily.dev: Nous Research adds multi-agent Bot Mode to Hermes Desktop](https://daily.dev/posts/nous-research-adds-multi-agent-bot-mode-to-hermes-desktop-inwnnuqrq)
- [xAI: Introducing Grok Bot (launch announcement, beta 2026-08-11)](https://x.ai/news/introducing-grok-bot)
- [Grok Bot overview — xAI docs](https://docs.x.ai/grok-bot/overview)
- [Grok Bot FAQ — xAI docs (plans, shared cloud computer, approvals, platforms)](https://docs.x.ai/grok-bot/faq)
- [Helio: What is Grok Bot? xAI's named agents, explained](https://www.helio.im/blog/what-is-grok-bot/)
- [skinview3d — Three.js powered Minecraft skin viewer (MIT)](https://github.com/bs-community/skinview3d)
- [RenderPixelatedPass — three.js docs](https://threejs.org/docs/pages/RenderPixelatedPass.html)
- [Dimetric (orthographic) camera angle for retro pixel look — three.js forum](https://discourse.threejs.org/t/dimetric-orthographic-camera-angle-for-retro-pixel-look/24455)
- [Three.js vs Babylon.js vs PlayCanvas comparison (2026)](https://www.utsubo.com/blog/threejs-vs-babylonjs-vs-playcanvas-comparison)
- [react-three-fiber releases (v8 for React 18, v9 for React 19)](https://github.com/pmndrs/react-three-fiber/releases)
- [WebView2 performance best practices — Microsoft Learn](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/performance)
- [WebView2Feedback #1919: WebGL performance discrepancy vs Edge](https://github.com/MicrosoftEdge/WebView2Feedback/issues/1919)
- [WebView2Feedback #5072: Chromium pins WebGL to the integrated GPU](https://github.com/MicrosoftEdge/WebView2Feedback/issues/5072)
- [petdex.dev — public pixel-pet catalog used by Hermes Desktop](https://petdex.dev/)
