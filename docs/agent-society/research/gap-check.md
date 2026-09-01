<!-- Generated 2026-09-01 by the agent-society research workflow (two lead deep-dive agents, five sub-agents each, plus a completeness critic). Facts about post-cutoff products/events were web-verified by the agents; re-verify load-bearing numbers (pricing, paper identity) before implementation. -->

# Gap Check — Completeness Critic over Both Branch Reports

## Verdict

NOT READY FOR PLANNING — one reconciliation pass required first. The reports are individually strong and complementary (A: product surfaces, 3D stack, reuse map; B: substrate, cost, safety), and their repo anchors check out on disk (budget.py $5/$50 defaults, ROUTER_TOOLS at brain/factory.py:61, three ^0.185.1 / R3F ^8.18 / drei ^9.122 pins, useWebglSurface.ts, turn_language.py, agent_chat/, agents/registry.py, subagent_fanout.py all verified). But they contradict each other on the society's spine — prose chat vs typed blackboard, agent-as-persistent-session vs roster-row-plus-ephemeral-workers, config-directory vs society.db identity, flat vs three-tier delegation rights, which store feeds the world, and whether idle is LLM-free — and both are silent on constraints this repo treats as hard: the 5-second voice tool budget, turn-language/i18n, headless/no-GPU degradation (T3 by the repo's own tiering), VRAM coexistence with the voice stack, a single cost ledger, boot budget, and AP-4 five-layer parity. Recommended: write a short decision memo resolving the nine contradictions (the natural synthesis is B's typed envelope and durable roster as the substrate with A's surfaces and bounded group rooms as projections on top), fold the voice/i18n/headless/VRAM/cost-ledger gaps into the master plan as first-class sections, and carry the remaining gaps as named workstreams with owners. Planning can start immediately after that memo; starting before it will bake in two incompatible architectures.

## Contradictions between the branches (9)

1. Inter-agent protocol: Report A adopts Hermes' single prose send path (message_agent-equivalent, natural-language messages with sender prefix, rendered as chat threads); Report B calls the opposite 'the single most important design decision' — typed enum events (ASSIGN/CLAIM/RESULT/QUERY/ANSWER/HOLD/VETO/DIGEST) on a blackboard, explicitly 'not a chat'. The plan must pick one, or explicitly define typed-envelope-with-prose-payload and say which surface renders what.
2. Group discussions: A adopts hard-bounded natural-language group rooms (2–6 members, <=3 rounds, <=10 messages) as THE consumer-token-budget mechanism and builds the world's meeting-table moment on them; B rejects conversation-centric coordination as the expensive anti-pattern ('not a free-form debate club') and ships no group mechanism at all.
3. What an agent IS at runtime: A = a persistent agent_chat session per agent (a long-lived conversation engine reusing service.py/runners); B = a durable roster row whose work is done by ephemeral SubagentFanoutRunner workers that are TTL-reaped after 60 s. These imply different RAM footprints, token profiles, and persistence stories — both cannot be the primary definition.
4. Where agent identity/config persists: A extends jarvis/core/config.py with an [agents.*] schema plus Hermes-style agent-account directories (jarvis/agent_accounts.py); B creates a society_agents table in a new data/society.db. Two sources of truth for the same model-card data (model/provider pin, tool grants, permission ceiling, avatar).
5. Hierarchy and the AP-5/AP-14 no-spawn wall: A says the society is flat specialists under Jarvis and 'the society cannot chain-react' because no society agent can spawn; B's middle tier (orchestrator/kontrollierer agents) explicitly delegates to sub-agents, i.e. some society members DO dispatch workers. The plan must state which tiers may dispatch and where the structural wall sits, because the safety claim differs between the reports.
6. World data feed: A says the world's data spine is the existing three-source merge (/api/sub-agents/tree + /api/missions + /api/outputs); B says the world is a pure projection of the new society_events log. Two competing single-sources-of-truth for the same view.
7. Spatial truth: B persists world_x/world_y in the backend roster and claims the world 'can never desync' because it is a projection; A runs idle wander purely client-side for zero cost. Unresolved: who owns position, whether it survives restart, and whether two open app windows (live + dev instance exist today) show the same world.
8. Idle cost promise: A states idle costs zero tokens — an LLM call happens only on a message, routine, or mission, 'never to look alive' — and calls this the most important cheap-to-run decision; B's idle model includes ~150 LLM housekeeping/digest/compaction calls per day (~$0.45–0.68/day cloud-assisted). Decide: strictly LLM-free idle, or curation-at-idle with a budget.
9. Approval surfacing: A routes approvals as cards into each agent's canonical chat via agent_chat/approval_bridge.py (Grok pattern); B queues unattended ask-tier actions in a central review surface with expiry. One UX must win, or the plan must define queue-as-data with chat-cards-as-projection.

## Gaps (what both reports missed)

### Voice-loop round trip within the 5-second tool budget

VOICE_TOOL_BUDGET is real code (jarvis/core/tool_budget.py, enforced in jarvis/realtime/session.py), yet neither report designs how the router-tier delegate-to-agent tool acks in under 5 s, how voice status queries ('what is Scout doing?') are answered, how agent completions or blocked approvals re-enter the voice loop, or that agent output reaching voice must pass regex-only scrub_for_voice (AP-11). This is a voice-first product; the society's voice contract is its front door and it is undesigned.

### Turn-language and i18n

jarvis/core/turn_language.py decides output language once per turn and no layer may re-derive it; neither report says what language agent chats, inter-agent traffic, routine results, agent self-introductions, or the world HUD use, nor that all new UI strings must go through the hand-formatted locale files and the German CI gate (en.json duplicate-key trap, entry-chunk verification). Agents answering in the wrong language is a shipped-looking-broken bug on the closed product surface.

### Headless / no-GPU / no-screen degradation

The repo baseline requires base install + boot on a headless python:3.11-slim box. Missing: the non-world steering surface (REST + jarvis CLI parity for create/assign/inspect/kill), an official non-WebGL representation (is the Ledger tab the declared fallback?), and per-OS capability probes for agent_screen (Windows Sandbox vs Xvfb vs macOS answer) recorded in docs/os-parity.md. A's fallback covers WebGL context LOSS, not WebGL absence.

### GPU/VRAM coexistence with the voice stack

A documented failure mode on this machine is the local voice stack filling VRAM and dropping the app to 1 FPS; a persistent 3D scene plus per-agent local models multiplies that pressure. No VRAM/FPS budget, no frameloop-on-demand or FPS-cap policy, no world-pause rule while local inference is hot, and no concurrency model for N agents sharing one Ollama server (single native engine — AP-24-style non-blocking lock + queueing, and B's $0-local cost math assumes throughput nobody sized).

### Idle runtime model: processes or rows?

B says an idle agent 'exists as a row and a live node with a heartbeat' but also that agents 'sleep until an event arrives'. Whether 15 idle agents are resident runner processes (RAM/CPU idle diet is a maintainer sore point) or dormant rows woken by the bus determines heartbeat ownership, registry TTL semantics, crash recovery, and the honesty of the near-zero-idle claim.

### Two event stores, one trace

B adds society.db beside missions.db while both reports route execution through missions. Cross-DB trace_id joins, replay ordering after a crash, WAL/locking with the dev instance, and whether 'working' derives from mission events or society events are all undefined — without a reconciliation rule the world and the ledger will tell different stories.

### One authoritative cost ledger

Spend would now live in missions cost_usd, society_events.cost_usd, registry AgentNode, BudgetTracker, and the Costs view. Which is authoritative, how per-agent daily caps compose with the existing $5/mission + $50/day buckets (verified in jarvis/missions/budget.py), and what the world HUD reads must be defined once or the meters will disagree in front of the user.

### Workspace model for non-coding agents

Missions/worktrees are shaped for coding on this repo, but society agents will do research, email, and home tasks. Undefined: what filesystem scope such agents get, isolation and cleanup, disk caps for N concurrent worktrees, a society-wide concurrency cap (fanout's max-10 is per-run, not global), and how agent worktrees coexist with the shared-working-tree discipline other sessions already fight over.

### Cross-agent prompt-injection and knowledge poisoning

One agent reads a hostile web page or email; its RESULT is curated into shared knowledge and silently steers every other agent — a confused-deputy chain across the society. B's authenticated writes cover identity, not content trust. Needs taint/provenance on knowledge rows, a review gate before the curator promotes anything into the user's Obsidian wiki (currently proposed as automatic), and contradiction/decay handling.

### Self-improvement guardrail detail

'Long-term self-improving' is a headline goal, but A only name-drops AP-15 (draft skills) without an approval flow, owner, per-agent vs global skill namespace, or firing doctrine on the weak router; B omits skills and memory-curation rate limits entirely. Without this, self-improvement is either inert (drafts nobody reviews) or ungoverned.

### Migration path and information architecture for the existing views

A marks both AgentsView ('Agent-Team' placeholder) and JarvisAgentsView as REPLACE without deciding which sidebar slot the society occupies, what happens to deep links, whether rollout is flagged/staged, how existing missions/outputs history appears in the new section, and whether first-run seeds starter agents (Grok ships a featured coordinator). The finish-it-everywhere rule also demands [agent:*] routines surface in the Automations view — named by A but not planned.

### World/environment asset pipeline effort

Characters are solved (skin format + skinview3d), the environment is not: nobody owns building tiles, desks, meeting table, archive, gate, and podium, in what tool, at what effort; animations beyond walking (sit/type/talk) don't exist in skinview3d; 2D Gigi must become a 3D lead; AI-generated skins need a validate/repair step (image models are unreliable at exact 64x64 pixel layouts); and no one addresses how a colourful retro world sits inside the Ink & Paper monochrome rebrand.

### Avatar/asset licensing and content moderation

A covers code licenses (Hermes MIT, skinview3d MIT) but not asset licenses: user-uploaded and community-catalog skins are routinely copyrighted characters (community 'Minecraft skins', petdex-style catalogs are unvetted), so the Marketplace plan needs license terms, IP screening or the existing report-then-delist precedent, and caution about marketing a 'Minecraft look' (trademark). Hermes attribution mechanics (NOTICE file when copying substantial code) also need a defined home.

### State-sync protocol specification

Neither report specifies the transport contract: which WebSocket carries society state, snapshot+delta shape, resume-from-seq after reconnect, event coalescing when bursts outpace multi-second walk animations (a choreography queue), and the multi-viewer policy — several app windows plus the dev instance can render the world simultaneously today.

### App-closed semantics and availability expectations

Local-first inverts Grok's headline promise ('closing the app does not stop work'): here a closed app or sleeping laptop freezes the society and its routines. Missed-schedule catch-up policy and honest UX copy setting that expectation are product decisions neither report makes.

### Boot budget and bundle discipline

AP-26 forbids initialization on the boot critical path, and check_boot_budget.py is the one gate that must be run by hand. Society DB init, roster load, and the 3D bundle (skinview3d + pixel pass + world assets are new bytes even though three.js ships already) must be lazy route chunks; neither report mentions boot or bundle cost.

### Five-layer parity tests and guard/CI strategy

Agent state, tier, and msg_type enums will cross Python <-> SQL <-> Pydantic <-> TS <-> UI, which AP-4 says requires the five-layer pattern plus a parity test; test_routing.py must gain the new router tool; and there is no plan for testing a canvas world in CI (a headless-Chrome measurement recipe already exists in project memory). B's 'steps 3–6 are T2' understates the world step.

### Maintainer confirmation of external anchors

B asserts arXiv 2606.12683 is 'the paper the maintainer means' (an inference) and builds design rationale on a reported incident's specifics (>70k messages, ~$400K analysis). Both are post-training-cutoff claims the master plan would load-bear on; confirm the paper with the maintainer and re-verify the 2026 pricing table (claude-api skill) at plan time instead of baking numbers in.

### Per-agent secrets

Hermes profiles carry a per-profile .env; copying that pattern violates AP-2/AP-12 (secrets only via get_secret/keyring, never in files or accepted via voice/chat). Per-agent credentials and provider keys need an explicit keyring-scoped design nobody wrote down.

### Notifications, attention routing, and approval expiry

How the user learns an agent finished or is blocked while they are in another section, on voice, or away (Jarvis bar, toast, badge counts) is unspecified, and B's approval-queue 'expires' has no defined consequence — an expired ask silently dropping a task is a trust-destroying failure mode for background agents.

### Accessibility and reduced motion

A constantly-animating pixel world needs a prefers-reduced-motion mode, photosensitivity care, and a keyboard/screen-reader path; the honest answer is probably 'the Ledger tab is the accessible equivalent', but no report declares it, and canvas-only UI with no fallback is an exclusion bug.

