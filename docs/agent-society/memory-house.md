# The Memory House — one shared memory for every agent

Status: **binding for the memory wave, written 2026-09-02.** Subordinate to
[`MASTERPLAN.md`](MASTERPLAN.md) §2/§6 and [`agent-definition.md`](agent-definition.md) §5
(the Obsidian wiki is the society's shared memory). This document records the deep dive the
maintainer asked for, the decisions, and the checklist the build follows.

Maintainer's ask (2026-09-02): *"all agents share one memory system … analyze how it works today,
say how professionals would do it, make concrete proposals, build a spectacular modern Memory
House on the island, and when an agent uses the memory it walks there."*

---

## 1. How memory works today (the deep dive)

Jarvis itself has four memory layers; the society uses one of them, thinly.

| Layer | Where | Who writes | Who reads | Society agents today |
|---|---|---|---|---|
| **Core memory** (persona, user facts, ~1.5 k tokens, always in the prompt) | `data/core_memory.json` (`jarvis/memory/core_memory.py`) | the `remember` tool, the wizard | every brain call of Jarvis | not in their prompt; `remember` is a core tool they *can* call, which would write Jarvis' facts, not theirs |
| **Recall** (conversation log + awareness episodes, FTS5/BM25) | `data/jarvis.db` (`jarvis/memory/recall.py`) | the message recorder, awareness | `awareness-recall`, `awareness-snapshot` | Jarvis' episodes only; agent chats are not recorded there |
| **The wiki** (the long-term store: entities, concepts, projects, sessions; two-stage LLM curator; FTS5 index; a per-turn context injector with relevance gates) | `wiki/obsidian-vault/` (`jarvis/memory/wiki/*`, `jarvis/brain/wiki_context.py`) | the curator, `wiki-ingest`, the user in Obsidian | `wiki-recall`, `wiki-page-read`, `wiki-list`, the injector | **read**: the three tools when granted. **write**: only `society_wiki_note` into `society/<id>/` (a memory page + dated notes), with a `knowledge` staging row per write. `wiki-ingest` is denied |
| **Skills** (procedural memory) | `skills/`, `data/society/<id>/skills/` (`jarvis/society/learning.py`) | the learning pass | the briefing, `society_run_skill` | built in the last wave; private per agent, promotion as draft |

What is missing, found by reading the code, not the plan:

1. **No ambient memory.** `agent-definition.md` §3.3 step 6 promised "wiki context + the agent's
   memory page head" in every turn. Neither is wired: `society_system_extra` builds the briefing
   from roster + catalog + browser + skills and nothing else. An agent that wrote a memory
   yesterday does not see it today unless it thinks of searching.
2. **Agent notes are invisible to search.** `VaultIndex` loads only the typed directories
   (`entities/`, `concepts/`, …), and the FTS index skips nothing but `_archive`, but the pages
   `society_wiki_note` writes carry no `type:` frontmatter, so they are schema-invalid and the
   vault index drops them. `wiki-recall` therefore never returns another agent's note, and the
   "unreviewed marker" that §5 promises has no code behind it.
3. **No shared tier.** `society/shared/` exists on paper only. There is no promotion path, no
   curator action, no approval item "promote to shared knowledge"; `knowledge.reviewed` is never
   set by anything but a store method nobody calls.
4. **No provenance in retrieval.** The `knowledge` table records origin and taint, but no reader
   consults it: a web-origin note reads exactly like a user-written page.
5. **Nothing moves anyone.** `Checkpoint.ARCHIVE` exists in every layer, the walkers already
   walk to the `archive` place when a row says so, but no code ever derives a checkpoint
   (`world-behaviour-manual.md` §3 rule 6 is unimplemented). The roster column is `idle` forever.
6. **Two write shapes, one hidden.** A memory line lands in `memory.md`; a note lands as a page;
   both are recorded as staging rows; the board never sees a memory event, so the ledger and the
   HUD cannot show "Scout remembered X".

## 2. How the field does shared agent memory (what "professional" means here)

The patterns that have held up across MemGPT/Letta, LangGraph's memory store, Mem0, CrewAI's
shared memory and Anthropic's memory tool, reduced to what applies to a local single-user app:

| Principle | Meaning | Our reading |
|---|---|---|
| **One store of truth** | never two memories that can disagree | the vault stays the truth (agent-definition §5); every database row is derived and rebuildable |
| **Layers with different lifetimes** | working (this turn) · episodic (what happened) · semantic (what is true) · procedural (how to) | briefing head · dated notes · `memory.md` + `shared/` pages · learned skills |
| **Explicit scopes** | private · team · user; a reader always knows which | `society/<id>/` (own) · `society/shared/` (team, reviewed) · the rest of the vault (the user's) |
| **Provenance and trust travel with the fact** | origin, author, trace, reviewed state are part of the record and of every retrieval result | frontmatter + `knowledge` rows; the recall result prints `[unreviewed, web]` on the line |
| **A write gate, not a write ban** | agents write freely into their own scope; anything shared is reviewed | own scope free; `shared` = approval item; user pages never |
| **Retrieval is ranked, bounded and labelled** | relevance + recency + scope boost, a small k, and a frame that says "may be irrelevant" | the recall tool ranks own > shared > user > others' unreviewed; the briefing head is ≤ 1 200 chars |
| **Ambient + explicit** | a small always-on head plus a tool for deliberate lookups; never the whole memory in the prompt | "## Your memory" in the briefing + `society_memory_recall` |
| **Consolidate, do not accumulate** | dated notes are summarised into durable facts; duplicates supersede | the learning pass already writes one memory line per RESULT; the promote step copies, the dismiss step marks reviewed |
| **Every access is an event** | audit, UI, and — here — the world | a `DIGEST` envelope with `kind: memory` per read/write; the checkpoint engine reads it |
| **Quarantine untrusted text** | web-derived content never enters another agent's prompt ambiently | unreviewed web/agent pages are available on request only, never in the head |

## 3. The design — the Memory House system

**One rule:** the vault is the memory, the Memory House is its face, and every touch of memory
is an event that the island shows.

### 3.1 Layout in the vault (unchanged shape, now indexed)

```
society/
  <agent_id>/memory.md                 durable facts of this agent (semantic, own)
  <agent_id>/YYYY-MM-DD-<slug>.md      work notes (episodic, own)
  shared/<slug>.md                     team knowledge (semantic, reviewed: true)
```
Every agent-written page carries `type: society`, `title`, `author: agent:<id>`, `origin`,
`trace`, `reviewed`, and shared pages add `promoted_from` and `promoted_ms`. `type: society`
makes them schema-valid for the vault index without touching the schema of the user's pages.

### 3.2 `jarvis/society/memory.py` — the one memory service

```
SocietyMemory(store, vault_root, on_activity)
  head(agent)                    -> str    the briefing section (memory.md head + shared titles)
  recall(agent, query, k=5)      -> hits   ranked: own > shared > user vault > others' unreviewed
  remember(agent, text, origin)  -> path   append to memory.md            (own, free)
  note(agent, title, text, …)    -> path   a dated page                   (own, free)
  propose_shared(agent, title, text) -> approval   a dated page + approval item core:memory:share
  promote(knowledge_id | path)   -> path   copy into shared/, reviewed: true, row reviewed
  dismiss(knowledge_id)          -> None   row reviewed without promotion (stays own)
  overview()                     -> dict   shared topics, per-agent heads, unreviewed queue
```
Ranking is deterministic and dependency-free (token overlap over title/body, a scope boost, a
recency tie-break); the user's vault pages come through `VaultSearch` when the FTS index is
available and are skipped quietly when it is not. Every call emits a `DIGEST` envelope
`{kind: "memory", op, path|query, hits, scope}` from the agent and calls `on_activity(agent_id)`.

### 3.3 Hands and prompt

- `society_memory_recall` (safe): the deliberate lookup. Result lines carry the scope and the
  trust marker: `[own]`, `[shared]`, `[user]`, `[unreviewed · web · scout]`.
- `society_wiki_note` keeps `note` and `memory` and gains `kind: shared` — a proposal that lands
  in the approvals queue as "promote to shared knowledge". Nothing an agent does writes into
  `shared/` directly.
- The briefing gains **`## Your memory`**: the first ≤ 1 200 characters of `memory.md`, the
  shared topic titles, and one line on how to use the two tools. Byte-stable between memory
  writes, so the provider cache stays warm.

### 3.4 The world reads memory — the checkpoint engine

`jarvis/society/checkpoints.py`, trusted Python, pure rules over facts the runtime already has
(the vocabulary that exists in every layer today — `desk | meeting | archive | gate | idle`):

1. `state == paused` → `idle`
2. an open approval for the agent → `gate`
3. member of a running room → `meeting`
4. memory activity within the last **20 s** (hold, so a walk of ~10 s is visible) → `archive`
5. a run is in flight for the agent → `desk`
6. otherwise → `idle`

The runtime persists a change through `roster.update`, publishes `SocietyCheckpointChanged`
on the app bus (the WebSocket forwards every bus event), and the World stage invalidates its
roster query on that event — the figure walks within a second, and the ledger shows the same row.
A timer re-evaluates when the hold expires. The `hub:*` superset of the behaviour manual is
unchanged and lands with the five-layer change it needs.

### 3.5 The Memory House on the island

- Replaces the primitive Archive tower and takes two house slots on the north-east of the ring
  (mirror of the Plugin Docks in the north-west); the `archive` place keeps its id, so the
  walkers, the labels and the checkpoint enum need no new value.
- Built by `scripts/world/build_world_kit.py --target memory-house` (the Blender MCP drives the
  inner loop; the committed script is the truth): a dark plinth with a glowing reflecting ring,
  a translucent glass monolith with a luminous core sphere and stacked "memory layers" inside, a
  cantilevered roof slab with a light edge, a tilted halo ring with light nodes, two data pylons
  and a floating MEMORY sign.
- Faces (behaviour manual §6): **agent** — stand point in front of the door, `work` clip while
  there; **viewer** — click opens the Memory drawer: shared topics, each agent's memory head, the
  unreviewed queue with *Promote* / *Mark reviewed*, a recall search box, a link to the Wiki
  section; **live signal** — the core and the halo nodes brighten while an agent stands at the
  house (derived on the client from the roster's checkpoints — no second truth).

### 3.6 Safety (unchanged chokepoint)

Agents write only under `society/<own id>/`; `shared/` is written by the promote action only, from
the approvals route; user pages are never edited (path containment in the service). Web- and
agent-origin pages are never in any agent's ambient head. Keys never enter memory (AP-2/AP-12):
the service refuses a `remember`/`note` body that matches the secret guard patterns.

## 4. Checklist

| # | Item | Where | Done when |
|---|---|---|---|
| 1 | `SocietyMemory` service: head, recall, remember, note, propose_shared, promote, dismiss, overview; `DIGEST kind=memory` per op; `type: society` frontmatter; secret guard | `jarvis/society/memory.py` | `tests/unit/society/test_memory.py` green |
| 2 | Tools: `society_memory_recall`; `society_wiki_note` delegates to the service and accepts `kind: shared` | `agent_tools.py`, `surface.py` | tool tests green, briefing shows `## Your memory` |
| 3 | Checkpoint engine + `SocietyCheckpointChanged` + runtime hooks (run start/end, memory activity, approval enqueue/resolve, room open/settle) | `checkpoints.py`, `runtime.py`, `jarvis/core/events.py` | `test_checkpoints.py` green; the roster row flips to `archive` after a memory op |
| 4 | REST: `GET /api/society/memory`, `POST /api/society/memory/recall`, `POST /api/society/memory/{id}/promote`, `POST …/dismiss`; approvals resolve promotes `core:memory:share` items | `society_routes.py` | route tests green; CLI coverage gate green |
| 5 | Blender: `build_memory_house()`; GLB exported; preview checked through the MCP | `scripts/world/build_world_kit.py`, `assets/society/world/kit/memory-house.glb` | GLB in the tree, ≤ 3 000 triangles |
| 6 | Island: `archive` becomes a ring kit place (slots 2–3), old tower removed, stand tile in front, labels renamed, house count test updated | `islandLayout.ts`, `KitBuilding.tsx`, `Landmarks.tsx`, `PlaceLabels.tsx`, locales | `islandLayout.test.ts` green |
| 7 | Memory drawer + live core signal + WS invalidation on `SocietyCheckpointChanged` | `MemoryHouseDrawer.tsx`, `KitBuilding.tsx`, `WorldStage.tsx`, `useWebSocket.ts` | drawer opens on click; core glows when an agent is at the house |
| 8 | Docs: this file, `jarvis/society/README.md`, `world-behaviour-manual.md` §3 note, `agent-definition.md` §5 pointer | docs | privacy review of touched docs |
| 9 | Bundle rebuilt (`npm run build`), commits per wave by pathspec, no push | — | the running app shows the house after its self-reload |

## 5. Tests

- `test_memory.py`: frontmatter is schema-valid; recall ranks own before shared before others;
  unreviewed web notes carry the marker and never appear in `head()`; `propose_shared` queues an
  approval and writes nothing under `shared/`; `promote` copies with `reviewed: true` and marks
  the row; secrets are refused; every op appends one `DIGEST` with `kind: memory`.
- `test_checkpoints.py`: rule order, the memory hold, the timer re-evaluation.
- `test_society_routes.py`: overview, recall, promote via the approvals route.
- `islandLayout.test.ts`: archive pose on the ring, stand reachable, footprint blocked, 8 houses.

## 6. Risks and the honest edges

- The user's vault FTS index is refreshed by the wiki watcher; a note written a second ago may not
  be found through `VaultSearch` yet. The service's own scan of `society/` is always current.
- The world reads the roster; a window that lost its WebSocket falls back to the 30 s poll and
  still ends up with the right place, just later.
- The Memory House is the first building whose live signal depends on other agents' rows; it is
  derived client-side from the same roster the walkers use, so it can never disagree with them.
