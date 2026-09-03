# World Behaviour Manual — how agents live on the island

Status: **binding for M3c/M3d, written 2026-09-02.** Subordinate to [`MASTERPLAN.md`](MASTERPLAN.md)
§2 (the world is a projection; idle is LLM-free; dispatch is the scheduler's privilege) and to
[`world-masterplan-v2.md`](world-masterplan-v2.md) §5–6, which this manual turns into rules a
programmer, an agent prompt and a tester can each check. The maintainer's ask: *"the agents must
know the world — when one uses a plugin it goes to the Plugin Store; the world has to work."*

## 1. Two truths, one picture

The island shows two things and never invents a third:

1. **What the backend says an agent IS doing** — its `checkpoint` (the semantic place) and its
   `state`. This comes from the society event log through the bridge; the world only reads it.
2. **How the island shows it** — the walk, the pose, the bubble. This is client-side and cosmetic.

Nothing an agent *says* moves it. Nothing the *viewer* does moves it directly either (§5): every
gesture becomes a typed event, and the figure follows because the truth changed.

## 2. Places — the vocabulary

| checkpoint | island place | the agent is… |
|---|---|---|
| `home` | its own house on one of the town's blocks | off duty: paused, or between runs at night |
| `meeting` | **Town Hall** | in a bounded room (`ROOM_OPEN … ROOM_SETTLE`) |
| `hub:plugins` | **Plugin Docks** | running a task whose dominant hands are **plugin tools** (Drive, calendar, files via a plugin…) |
| `hub:skills` | Skill Forge | running a task whose dominant hands are skills |
| `hub:mcp` | Relay Tower | calling MCP servers |
| `hub:cli` | Terminal Cantina | working through a CLI seat (Claude Code, Codex, …) |
| `hub:comms` | **Signal Office** | writing to someone: mail, chat, contacts, a call, a message to a teammate |
| `hub:desktop` | **Control Room** | driving the desktop: clicks, keys, windows, what is on screen |
| `hub:web` | **The Lookout** | reading the world outside: web search, the agent's own browser |
| `hub:models` | **Boiler House** | its brain is a local (keyless) model and a turn is running |
| `hub:workshop` | Workshop | core file/shell work, no dominant family |
| `archive` | **Memory House** | touching the shared memory: a recall, a memory line, a note, a share proposal (60 s hold — `memory-house.md` §3.4) |
| `gallery` | **Gallery** | delivering a `RESULT` (carries the crate, 6 s hold) |
| `gate` | Harbor Gate | waiting for an `ask`-tier approval |
| `foundry` | Agent Foundry | being created (3 s), or changing its avatar |
| `wander` | the square and its own street | idle; the rest-biased model picks the beats |

Shipped vocabulary (2026-09-03): `desk | meeting | archive | gate | idle | gallery | hub:plugins |
hub:skills | hub:mcp | hub:cli | hub:comms | hub:desktop | hub:web | hub:models` — in all five
layers (Python enum ↔ SQL CHECK ↔ Pydantic ↔ TS ↔ UI, AP-4; an older `society.db` is rebuilt to
the wider CHECK on open). `home` and `hub:workshop` are still this table's superset and join the
same way — never as free strings.

**Two kinds of family share one vocabulary.** A CAPABILITY family (`plugin | skill | mcp | cli |
core`) says where the hand came from; a WORK family (`comms | desktop | web`) says what the agent
is doing. Work wins, decided by the tool name before its registry kind, because the island shows
what an agent DOES and not how it is plumbed: a mail is a trip to the Signal Office whether it
leaves through a plugin, an MCP server or a CLI seat. `jarvis/society/checkpoints.py` holds the
one table (`_WORK_FAMILY`), and `HubDrawer.tsx` lists the same tools in each hall's drawer.

## 3. Derivation — who decides the place (trusted Python, no LLM)

The **society bridge** (`jarvis/society/bridge.py`, M1) derives `checkpoint` from events it already
sees. Rules, in priority order; the first that matches wins:

1. `state == paused` → `home`.
2. An open approval for the agent → `gate`.
3. The agent is a member of an open room → `square`.
4. A `RESULT` was emitted in the last 6 s → `gallery` (then rule 6 or 8 applies).
5. A worker runs under the agent's identity → `hub:<family>` where family is the **dominant
   family of its last 8 tool calls** (`comms | desktop | web` first, else `plugin | skill | mcp |
   cli | core` from the ONE capability catalog of `agent-definition.md` §3.1). The place changes
   only when a different family has dominated for **≥ 20 s** — hysteresis, so a figure never
   ping-pongs between shops. An agent on a keyless (local) provider that has not called a tool
   yet stands at the Boiler House: its own model thinking IS the work.
6. A memory touch in the last 60 s → `archive` (the Memory House). **Built:** `jarvis/society/checkpoints.py` derives `paused → idle`, `gate`, `meeting`, `gallery`, `archive`, `hub:cli`, `hub:models`, `hub:plugins | hub:skills | hub:mcp | hub:comms | hub:desktop | hub:web` (dominant family of the last 8 tool calls, 20 s hysteresis), `desk`, `idle` and publishes `SocietyCheckpointChanged`. "A worker runs under the agent's identity" means EITHER a scheduler run (`ASSIGN`) OR a turn typed into the agent's card: the society surface reports every turn start (`note_turn_started`), and the engine watches that turn's `tool_call` / `turn_finished` events. `hub:workshop` is still open — core file and shell work sends the figure to the Workshop through `desk`.
7. Being created / avatar change → `foundry`.
8. Otherwise → `wander`.

The rule set is pure and unit-tested (`tests/unit/society/test_checkpoint_rules.py`, M3d): given a
list of events with timestamps, it returns one checkpoint. The world never runs it.

## 4. What the agent itself knows — the "world card"

Agents do not need the map to work, but they should be able to **talk about where they are**
("I'm at the Plugin Docks, sending the mail through the Gmail plugin") and to reason about the
society's places when the user asks. The prompt assembly of `agent-definition.md` §3.3 gains one
short, cached section — the world card — and nothing else:

```
You live in a small island village with your fellow agents. Places: your house (rest),
the market square (strolling), the Town Hall (rooms with the others), the Plugin Docks
(plugin tools), the Skill Forge (skills), the Relay Tower (MCP servers), the Terminal
Cantina (coding CLIs), the Signal Office (mail, chat, contacts, calls), the Control Room
(driving the desktop), the Lookout (web search and your browser), the Boiler House (a local
model thinking), the Workshop (files and shell), the Memory House (the shared memory), the
Gallery (work you delivered), the Harbor Gate (waiting for approval), the Agent Foundry
(where agents are created). You are placed by what you actually do; you cannot move
yourself. When you mention your location, use these names.
```

Rules: prefix-stable (cached), ≤ 120 tokens, identical for every agent, never carries live state
(the place is a fact the runtime knows; the agent narrates, it does not decide). Runtime output
language stays with `turn_language.py` — the card is English in the prompt; the agent answers
in the turn's language.

## 5. Steering — how a viewer moves an agent

| Gesture in the world | Event | Guard |
|---|---|---|
| card → "Assign task" | `ASSIGN` through the scheduler | tier wall (§2.5), budget pre-check |
| drag a figure onto a hub | `ASSIGN` with the hub's capability as `focus`; the card opens pre-filled, the user confirms | user confirms every time |
| right-click → "Pause" / "Go home" | `state = paused` / `active` | — |
| voice via Jarvis: "send Scout to the Plugin Docks and …" | router's `delegate-to-agent` (M4) → `ASSIGN` | voice ACK ≤ 5 s |
| click a building → "Send someone here" | pick an agent → as drag | — |
| follow-cam, select, hover | none — camera only | — |

The figure walks when, and only when, the derived checkpoint changes. A rejected `ASSIGN` (tier,
budget, kill switch) never moves anyone; the card shows the typed failure reason.

## 6. What the buildings do

Every hub has three faces, and all three are required before a building ships (finish-it-everywhere):

| Face | Plugin Docks (the first, shipped in M3b/M3c) | Every other hub |
|---|---|---|
| **for the agent** | stand point in front of the bays; `work` clip while there | stand point + clip from the kit contract |
| **for the viewer** | click → drawer listing every installed plugin by family (`/api/plugins`), link to the Plugins section | click → drawer for that section, deep link |
| **live signal** | (M3d) bay stripe of the active family glows while an agent works there | per §5 of the v2 plan |

Bay colours are the family colours everywhere — the building, the drawer swatches, later the
bubbles: brains violet, tools teal, speech-to-text coral, text-to-speech amber, channels mint,
realtime sky.

## 7. Zero-cost guarantees (unchanged)

- Idle costs nothing: wander is a timer and a random tile; no LLM is ever called to "look alive".
- A closed app freezes the society; the island shows the last derived places on reopen.
- Two windows show the same places; footsteps differ by design.
- Reduced motion: figures stand at their places; the Ledger stays the declared equivalent.

## 8. Test plan (M3d exit)

1. Unit: `checkpoint_rules` — the eight rules, the hysteresis, the priority order.
2. Contract: a fake worker run with 10 plugin tool calls → the agent's row reads `hub:plugins`
   within one bridge tick; a `RESULT` → `gallery` then `wander`.
3. Screenshot (headless Chrome, recipe in project memory): the walker stands in front of the
   Plugin Docks' bays, facing north, nameplate visible.
4. Steering: drag Scout onto the Docks → an `ASSIGN` with `focus=[plugin:*]` appears in the log;
   a specialist dragging (via API) is refused with the typed reason.
