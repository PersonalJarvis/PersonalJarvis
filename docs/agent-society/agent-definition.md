# Agent Definition — what a society agent IS and how it plugs into Jarvis

Status: **binding design, 2026-09-01.** Subordinate to [`MASTERPLAN.md`](MASTERPLAN.md); it
refines §3.1 (roster), §3.2 (runners), §4.2 (model card), §6 (safety) and answers the questions
the build plan left open: how an agent gets its hands (plugins, CLIs, MCPs, skills), how it knows
the Jarvis ecosystem, and how the Obsidian wiki becomes the society's shared memory.

The reference product is Grok Bot (§1). The whole thing is built in-house (MASTERPLAN §10.7).

---

## 1. Grok Bot, analyzed (docs.x.ai, 2026-09-01)

What xAI ships, in one table, with our verdict per row.

| Grok Bot | What it means | Ours |
|---|---|---|
| A Bot = one persistent, named teammate with **name, title, description**; the description holds *durable operating rules*, the chat holds one-off instructions | Three fields, no wizard. "Focused Bots build more useful context than one catch-all Bot." | **Adopt** exactly: `name`, `title` (job line), `description` (standing instructions = the agent's own AGENTS.md). |
| Bots **message each other**, share context in threads / group chats, `@mention`, **pass ownership**; xAI recommends "message one owner Bot" rather than parallel threads | Coordinator-over-specialists; noisy group chats are a known failure ("repeat each other, start unnecessary loops") | **Adopt the shape, harden the mechanism:** owner = our `orchestrator` tier; typed envelopes instead of prose; rooms capped 2–6 / ≤3 rounds / ≤10 messages. |
| A **handoff** records: output path, what was completed, evidence used, what remains unresolved, which Bot owns the next step | The one durable coordination artifact | **Adopt as the `RESULT` payload schema** (§4.3). |
| **Skills** = reusable procedure (steps, decision rules, expected output, safety boundaries), invoked with `/`, created from chat, manually, or by *teaching* (recorded browser demo → draft skill) | Their self-improvement loop | We already have `SKILL.md` skills with `draft` lifecycle (AP-15), `/` typeahead, `create-skill` + `run-skill`. **Adopt** per-agent skill enablement; teach-by-demo = M6 stretch. |
| **Routines** = one Bot + schedule or event trigger; results post into the Bot's chat; 50 per Bot, 20 run records kept; "test the skill on a real one-time task first" | Automations | Our Automations scheduler already does schedules, event triggers, approvals. **Adopt** `[agent:<name>]` routines with results into the canonical chat. |
| **Connectors / Plugins / MCP servers** are account-wide ("not isolated to one Bot"); prefer a connector over clicking through a website | Capability surface is shared, bots differ by role | **Adopt with a twist:** the capability catalog is global, but each agent has a *grant* (all / allow-list) and a *focus* (§3.2). |
| One **shared cloud computer** per account; every Bot has its own screen; "do not use separate Bots as a security boundary" | Cheap handoff via shared files/logins | **Do differently:** local-first. Files = the agent's workspace dir; screens = `agent_screen` leases (M6); security boundary = the risk tiers + per-agent grant, not the bot. |
| **Memory**: stable preferences, role context, summaries of prior work; "context and memory are not the same as a database"; durable work goes to `/workspace` or external systems | Memory is per bot, sharing is by files/messages | **Do differently:** the Obsidian wiki is the shared memory (§5); per-agent memory is a wiki page, not a hidden store. |
| **Approvals**: Auto-review rules `Require Approval` (always stop) vs `Always Allow` (only if nothing else objects); Require wins; high-risk classes: sending, publishing, purchases, deleting, permissions, production changes, legal terms; "Allow once / Deny"; approval binds to the exact action | Model-based review complementing least privilege | We have `safe / monitor / ask / block` with blacklist > whitelist. **Adopt** per-agent *Require-approval rules* on top of the ceiling (§3.4) and the "binds to the exact action" wording. |
| **No model picker**, no local models, cloud only, Linux desktop unsupported | Their biggest gap | **Our edge:** provider + model + effort per agent from the existing catalog, local models included, every OS. |
| 50 Bots + group chats per account; duplicate a Bot copies profile/skills/routines but not memory; hide ≠ pause; share = config only, "strip secrets" | Housekeeping rules | **Adopt** duplicate/hide/archive semantics; sharing = M6 marketplace item with the same "no secrets" rule. |
| First run: asks which tools you use, then suggests teammates ("Piper — product performance investigator") | Onboarding by tool inventory | **Adopt:** seed agents proposed from *connected* capabilities (a Gmail agent only if the Gmail plugin is connected). |

Net: Grok Bot's product shape is the right floor. What we add is per-agent models, local-first,
structural safety, the wiki as shared memory, and the world.

---

## 2. The agent, field by field (roster row = model card)

`society_agents` grows from the MASTERPLAN sketch to this. Every field is user-visible on the
card; nothing hidden drives behavior.

**Identity**
- `agent_id` (slug, stable), `name` (UNIQUE, the display name), `title` (one job line,
  "Gmail assistant"), `description` (standing instructions, Markdown; the agent's own AGENTS.md:
  goal, ownership, working style, approval boundary, durable rules). `tier`
  (`lead | orchestrator | specialist`), `parent_agent_id`, `state` (`active | paused | archived`).
- `avatar` (parts + palette now; GLB/texture uri in M3), `checkpoint` (world place).

**Brain (revised 2026-09-02 — subscriptions)**
- `provider`, `model`, `effort` — from `agent_chat.catalog.rows_for("society")`: every API row
  (key-based: `openai`, `gemini`, `grok`, `openrouter`, `nvidia`, `vertex`), the keyless local
  rows (`ollama`, `local-openai`) AND the **subscription seats** — the vendor CLIs the app
  already drives on a plan: Claude Code (Claude Max), Codex (ChatGPT), Grok Build (SuperGrok),
  Antigravity (Google), OpenCode, Kimi, GLM, Cursor. `resolve_runner(provider, "society")`
  decides per box: a seat's CLI when installed (`claude-api` is dual — Claude Code when
  present, else the Anthropic API), Jarvis' brain runner for API rows.
- **On a seat the CLI runs AS the agent**: its briefing (§3.3) is the identity the CLI is handed
  (`jarvis_harness.identity_prompt(prompt_override=…)`, Jarvis' own layers stay out), Jarvis'
  tools reach it over MCP (`mcp__jarvis__*`), and it brings its own hands (Claude Code's shell,
  editor, browser). The society-only tools (`society_message_agent`, `society_wiki_note`,
  `society_shell`, `society_browser`, `society_run_skill`) are available on the brain runner
  today and are NOT yet offered over MCP to a seated CLI (tracked gap).
- `account_id` — which stored subscription of that CLI the agent uses (`jarvis.agent_accounts`;
  empty = the platform's active account). Pinned per turn through
  `runner_cli.ACCOUNT_OVERRIDE`, so two agents can sit on two Claude Max logins.
- **Switching later**: `POST /api/society/agents/{id}/model` (or PATCH) changes provider /
  model / effort / account; the canonical chat is re-seated at once, transcript kept
  (`chat_binding.ensure_session`). `GET /api/society/providers` lists every row with the
  runner it resolves to on this box (`subscription: true|false`) and the accounts stored for
  its CLI; models, efforts and permission ladders come from
  `GET /api/agent-chat/catalog?surface=society`.
- **The creator lists only connected seats** (maintainer, 2026-09-02, after Grok Bot's "New
  Bot" sheet): the catalog joined with the Agents tab's credential truth (the same
  `joinProviderOptions` join the chat's composer uses), grouped subscription → API key → local,
  with a login picker only when a CLI has more than one signed-in account, the model (a keyed
  row's live list, Ollama's installed models) and the effort. A provider that is not connected
  is not shown — a keyless local row counts as connected only when it answers with at least one
  model — and one sentence says where to connect it. Name, title, description and "Runs on"
  are the whole sheet; ceiling and budget sit under "More". No tool picking at creation:
  `grant_mode = all`, and what the agent reaches for first is settled in its own chat
  afterwards (`components/society/create/CreateAgentDialog.tsx`, `brainPicker.ts`).
- Reference check (2026-09-02): Hermes Agent switches with `hermes model` / `/model provider:name`
  and offers Codex/ChatGPT device-code OAuth, xAI SuperGrok OAuth and Claude OAuth (Max only);
  OpenClaw selects `agents.entries.*.model` as `provider/model`, reuses `claude -p` for the
  Claude plan, ChatGPT OAuth for OpenAI, and keeps an ordered fallback chain. We match the
  shape — per-agent provider/model/account, switch any time — through the seats the app
  already runs, and keep fallbacks global (AP-21/22).

**Capabilities** (§3)
- `grant_mode`: `all` (default — everything the global tiers allow) or `allowlist`.
- `grants` JSON: capability ids from the catalog (only read when `grant_mode = allowlist`).
- `focus` JSON: capability ids the agent should reach for FIRST (derived from `description`,
  editable). A "Gmail agent" gets `focus = ["plugin:gmail"]` without losing everything else.
- `denies` JSON: capability ids never offered to this agent, even in `all` mode.
- `skills` JSON: enabled skill slugs (default: all `active` skills; `draft` never).
- `workspace_dir`: the agent's folder (default `DATA_DIR/society/<agent_id>/workspace`).

**Knowledge & memory** (§5)
- `wiki_namespace`: `society/<agent_id>/` in the vault (created lazily).
- `memory_page`: `society/<agent_id>/memory.md` — the agent's own durable notes.
- `knowledge_scope`: `shared` (reads the whole vault) or `own` (only its namespace + the
  society hub pages). Default `shared`.

**Safety & economy** (§3.4)
- `permission_ceiling` (`safe | monitor | ask`; block is block, never a bypass).
- `approval_rules` JSON: Grok-style rules — `require_approval: [patterns]`,
  `always_allow: [patterns]`; require wins; patterns are capability ids or `capability:verb`
  (`plugin:gmail:send`). Global blacklist and `always_confirm_tiers` sit above all of it.
- `daily_budget_usd`, `max_concurrent_runs` (default 1 for specialists, 3 for orchestrators).

**Automation & stats**
- routines live in the Automations scheduler tagged `agent:<agent_id>` (no column);
- `stats` are derived from `society_events` (runs, cost, last active) — never stored twice.

---

## 3. Hands: how an agent gets tools from the Jarvis ecosystem

### 3.1 One capability catalog

Everything a Jarvis brain can call already exists in four registries. The society adds ONE
read-only view over them — `jarvis/society/capabilities.py` — that returns typed rows
`CapabilityRow(id, kind, label, one_liner, risk_tier, connected: bool, source)`:

| kind | id form | comes from | connected means |
|---|---|---|---|
| `plugin` | `plugin:<entry-point name>` (`plugin:gmail`, `plugin:google-calendar`, `plugin:spotify`) | `jarvis.tool` entry points (`jarvis/core/registry.py`), marketplace-installed plugins included | its credential probe is green (`brain/app_control.is_credential_present`) |
| `cli` | `cli:<name>` (`cli:gcloud`, `cli:gh`) | `jarvis.clis` registry — the `cli_<name>` tools the CLI Test Hub already drives | binary found + connected in the CLIs section |
| `mcp` | `mcp:<server>` (whole server) and `mcp:<server>/<tool>` | `jarvis/mcp/adapter.py` (`MCPToolAdapter` rows in the tool registry) | server verified (`mcp/bootstrap.verify_server`) |
| `skill` | `skill:<slug>` | `jarvis/skills/registry.py`, `active` lifecycle only | always (skills are text) |
| `core` | `core:<tool>` (`core:search-web`, `core:wiki-recall`, `core:run-shell`, `core:computer-use`, `core:read/write/edit` folder tools) | the built-in tool set minus the router-only dispatch tools | always |

**The agent's own hands (built 2026-09-02, all in `jarvis/society/`):**

| tool | what | gate |
|---|---|---|
| `society_shell` | commands in the agent's OWN workspace folder (`data/society/<id>/workspace`), path containment, output cap, timeout — local by decision (Hermes' default backend is local, OpenClaw's sandbox is off by default); `ShellBackend` is the seam for a later Docker backend | destructive class → `ask`; `approvals.decide` on `core:shell` |
| `Read/Write/Edit/Ls/Glob/Grep` | the chat's folder tools, wrapped so every path stays inside the workspace | folder tiers |
| `society_browser` | one browser-use run in the agent's persistent Chromium profile (or its attached Chrome), out of process in a managed venv (`jarvis/society/browser/`), capped steps and time, results and cost on the board | send/buy/delete/publish wording → `ask`; `approvals.decide` on `core:browser`; `browser_allowed_domains` |
| `society_run_skill` | loads one of the agent's learned skills as instructions | monitor |
| `society_message_agent`, `society_wiki_note` | see §4 and §5 | monitor |

**Learning (automatic, maintainer decision 2026-09-02):** after a finished chat-run task the
turn is digested and, when a procedure emerged, authored into `data/society/<id>/skills/<slug>/`
— active for that agent immediately, listed in its briefing, promotable to the global user
skills as a draft (`POST /agents/{id}/skills/{slug}/promote`). Daily cap per agent; a memory
line on the agent's wiki page; a notice in its chat.

Never in any society tool set, structurally (AP-5/AP-14): `spawn-worker`, `spawn-subagents`,
`multi-spawn`, `dispatch-*`, `create-artifact`, `navigate`, `switch-provider`,
`manage-mcp-server`, `app-command`. Dispatch is the scheduler's privilege; app control stays
with Jarvis.

The catalog is what the model card's "Tools" tab lists (grouped by kind, connected first, with
the same brand marks the Plugins / CLIs / MCPs sections use), what the Create dialog's Advanced
disclosure offers, and what `capability_epoch` fingerprints so an agent notices when its hands
change.

### 3.2 Grant, focus, deny — and "it just knows it is the Gmail agent"

Per turn, the society surface kit builds the agent's tool dict in this order:

1. **Start** from the full brain tool set the global safety tiers allow (what the `jarvis`
   surface gets today) — that is `grant_mode = all`. With `allowlist`, keep only `grants`.
2. **Remove** `denies`, everything unconnected (a disconnected plugin is not offered — no
   "tool not available" refusals), every dispatch tool, and every skill not in `skills`.
3. **Order** the schema list: `focus` first, then the rest alphabetically (models attend to the
   head of the tool list; prompt caching is safe because the order is deterministic per agent).
4. **Brief** the model (system_extra, §3.3): a "Your hands" section listing focus tools with
   their one-liners, then a compact "Also available" line of names by kind.

`focus` is derived ONCE at creation (and on every description edit) by
`jarvis/society/focus.py`: deterministic keyword matching of the `title` + `description`
against catalog labels, one-liners and plugin aliases ("Gmail", "mail", "inbox" → `plugin:gmail`;
"calendar", "Termin" → `plugin:google-calendar` <!-- i18n-allow: alias example -->; "GitHub", "PR" → `cli:gh` / `mcp:github`).
No LLM call for this — it must work with any single key and cost nothing. The card shows the
derived focus as chips the user can add to / remove; a wrong guess is a one-click fix.

A "Gmail agent" therefore: keeps every tool (so it can still look something up in the wiki or
the calendar), gets Gmail listed first and briefed as its primary hand, and its description
tells it what it owns. That is the Grok behavior ("the description stores durable rules") on top
of our tiers.

### 3.3 Prompt assembly — how the agent knows the ecosystem

The society kit's `system_extra` is assembled per turn from cached, byte-stable parts (prompt-
cache friendly, prefix-stable), in this order:

1. **Society persona frame** (constant): you are `<name>`, `<title>`, tier `<tier>`, in a team
   led by Jarvis; how to hand off (§4.3); what you never do (dispatch, secrets, outbound at
   scale).
2. **The agent's `description`** verbatim (its standing instructions).
3. **Capability brief** (§3.2) — focus tools with one-liners; "also available" line; the
   capability epoch fingerprint.
4. **Ecosystem card** (constant, generated from code, ~40 lines): what Jarvis is (voice lead,
   sections, Automations, wiki, missions, artifacts), which of those the agent reaches how
   (routines via the scheduler tag; wiki via `wiki-recall` / `wiki-page-read` / `wiki-ingest`;
   files via its workspace; other agents via `message_agent`), and the house rules (turn
   language, no secrets in chat, ask-tier actions queue for the human).
5. **Identity card** of the user (`brain/identity_card.py`, already cached) and the user's
   agent-instructions file — the agent knows whom it works for.
6. **Wiki context** for this turn (`brain/wiki_context`) + the agent's `memory_page` head.
7. **Roster line**: the other active agents with their titles, so the agent knows whom to
   message ("Archivist — knowledge curator").

Turn language is decided once by `turn_language.py` and passed through; agents never re-derive
it.

### 3.4 Safety composition (unchanged chokepoint, three new layers)

`ToolExecutor.execute()` stays the only executor. Per call, in order:

1. Global **blacklist** (config) — raises before anything else, every stance.
2. Agent **denies** and grant (§3.2) — a tool not in the dict cannot be called at all.
3. Agent **approval rules**: `require_approval` match → queue (unattended) or card (attended);
   else `always_allow` match → run if the tool's own tier ≤ `monitor`; else fall through.
4. Tool **risk tier** vs the agent's `permission_ceiling` (`ask` ceiling: ask-tier queues;
   `monitor` ceiling: ask-tier is refused with a typed reason, never silently skipped).
5. Society **budget** (`daily_budget_usd`, global `BudgetTracker`) and the kill switch.

The anti-harm blacklist class (mass outbound, credential probing, repeated actions against
non-consenting endpoints) is seeded at tier `block` and is not overridable by any agent field.

---

## 4. Talking: agents, Jarvis and the user

### 4.1 Surfaces
- **User ↔ agent:** the canonical chat (card's right column), `society:<agent_id>` session on
  the agent_chat store; the composer's `/` lists the agent's enabled skills, `@` the roster.
- **Jarvis ↔ agent (voice):** the `delegate-to-agent` router tool (M4): ACK < 5 s, work goes
  through the scheduler as `ASSIGN`, completion re-enters voice through `scrub_for_voice`.
- **Agent ↔ agent:** exactly one tool, `message_agent(target, text, kind)` → one `SAY` /
  `QUERY` / `PROPOSE` envelope to ONE teammate; the scheduler wakes the target's chat.
  `@name` in a canonical chat by the USER also produces a `SAY` from the user's identity.
- **Group:** a room (`ROOM_OPEN → SAY* → ROOM_SETTLE`), opened by Jarvis or an orchestrator,
  rendered as one thread in the society feed and as the meeting pavilion in the world.

### 4.2 Envelope payload (typed, compact)
`{ "type": "SAY", "text": "...", "refs": ["wiki:society/scout/2026-09-01-brief.md", "file:..."],
"lang": "de" }` — text is for humans, `refs` are what the receiver opens. Every envelope carries
`trace_id`; a reply carries `parent_event_id`.

### 4.3 Handoff = `RESULT` payload (Grok's record, made mandatory)
```
{ "type": "RESULT", "status": "done | partial | blocked",
  "output": ["file:...", "wiki:..."],          # where the work is
  "done": "one paragraph",                      # what was completed
  "evidence": ["url", "file:...", "event:seq"], # what it was based on
  "open": ["..."],                              # what remains unresolved
  "next_owner": "archivist" | null,             # who owns the next step
  "cost_usd": 0.12 }
```
The scheduler refuses a `RESULT` without `done` and `output`/`open`; the world plays "walks to
the archive" on it; the ledger shows the five fields.

### 4.4 Delegation rules (scheduler-enforced)
- `ASSIGN` only from `lead` / `orchestrator`; depth ≤ 2; no recursion; specialist `ASSIGN` →
  typed refusal `tier_not_allowed`.
- One `ASSIGN` = one worker under the target's identity, model, grants and ceiling; the
  worker's tool set is the target's §3.2 dict minus every dispatch tool (already absent).
- Per-trace message cap (default 24) and room caps end every conversation deterministically.

---

## 5. Memory: the Obsidian wiki is the society's shared store

> Built 2026-09-02 as the **Memory House** — service, hands, prompt section, review gate, REST and
> the island building: [`memory-house.md`](memory-house.md) is the binding description; this
> section keeps the original direction.

Maintainer direction (2026-09-01): the agents continue and use the wiki we built as their
common memory. This refines MASTERPLAN §3.1 / §6.6 — the `knowledge` table becomes a staging
and provenance layer; the vault is where knowledge lives.

**Layout in the vault**
```
society/
  README.md                 # hub: what this folder is, who writes here
  <agent_id>/
    memory.md               # the agent's durable notes (its "memory page")
    YYYY-MM-DD-<slug>.md    # work notes, one per RESULT that produced knowledge
  shared/
    <topic>.md              # curated cross-agent knowledge (reviewed)
```
Every page written by an agent carries frontmatter `author: agent:<id>`, `origin:
user|tool|web|agent`, `trace: <trace_id>`, `reviewed: false`.

**Read path.** Agents use the existing `wiki-recall`, `wiki-page-read`, `wiki-list` tools
(safe tier). `knowledge_scope = shared` reads the whole vault; per-turn `wiki_context` also
serves them. Pages with `reviewed: false` and `origin: web|agent` are surfaced to agents with a
visible "unreviewed" marker in the tool result, and never enter another agent's *system prompt*
(confused-deputy defense) — they are available on request, not ambiently.

**Write path.** `wiki-ingest` (monitor tier) is namespaced for society sessions: an agent can
write only under `society/<own id>/`. Writing to `society/shared/` or anywhere else is a
curator action (M4, event-driven after `RESULT`, local model preferred) and lands in the
approvals queue as "promote to shared knowledge" when the source is web/agent-origin. The user's
own pages are never edited by an agent.

**Why not a separate knowledge DB as the truth:** the user already reads, edits and searches the
vault; an invisible second store would split memory. The `knowledge` table keeps only what the
vault cannot: taint state, curation cursors, FTS over agent notes — derived, rebuildable.

---

## 6. Lifecycle & housekeeping (Grok parity)

- **Create**: three fields + Advanced (brain, grants, ceiling, budget, avatar). The agent
  introduces itself as the first chat message, in the turn language, naming its focus tools.
- **Seed on first run**: propose teammates from CONNECTED capabilities (Gmail plugin connected →
  "Mail agent"; `gh` connected → "Repo agent"); ship a starter coordinator once the maintainer
  decides §10.6.
- **Duplicate**: copies profile, grants, rules, skills, routines, avatar; not the chat, not the
  memory page, not the workspace.
- **Pause**: no routine fires, no `ASSIGN` accepted, chat still answers. **Hide**: roster only.
  **Archive** (delete): profile + routines gone; wiki pages stay (they are the user's vault);
  workspace dir is kept for 30 days.
- **Edit description** → focus re-derived → card shows the diff of focus chips → the canonical
  session is re-seated (transcript kept).
- **Capability epoch**: the brief's fingerprint changes when plugins/CLIs/MCPs connect or drop;
  the next turn re-briefs, the card shows "hands changed".

---

## 7. What was missing, now defined

| Gap (from the brainstorm) | Answer here |
|---|---|
| "Default connected to all plugins, but knows it is the Gmail agent" | `grant_mode=all` + `focus` derived from the description (§3.2) |
| Explicit "use only X" | `grant_mode=allowlist` + `denies` (§2, §3.2) |
| Skills / MCPs / CLIs / CLI Test Hub tools reachable | one capability catalog over the four registries (§3.1) |
| Agent knows the whole Jarvis ecosystem | ecosystem card + roster line in the prompt (§3.3) |
| Wiki as shared memory | §5, namespaced writes, reviewed promotion |
| Context exchange between agents | `refs` in envelopes, `RESULT` handoff record (§4.2–4.3) |
| Per-agent approval rules like Grok | `approval_rules` under the ceiling (§3.4) |
| Onboarding | seed proposals from connected capabilities (§6) |

---

## 8. Roadmap — where this lands in the build plan

Maps onto [`build-plan-m1-m2.md`](build-plan-m1-m2.md); new items are marked **+**.

| Wave | Adds from this document |
|---|---|
| 2 | roster schema with §2 fields (`title`, `description`, `grant_mode`, `grants`, `focus`, `denies`, `skills`, `approval_rules`, `workspace_dir`, `wiki_namespace`, `knowledge_scope`, `max_concurrent_runs`) + five-layer parity for the new enums |
| **2b +** | `jarvis/society/capabilities.py` (catalog over plugins / CLIs / MCPs / skills / core) + `focus.py` (deterministic derivation) + tests with fakes for every registry |
| 4 | scheduler enforces §4.3 `RESULT` schema and §4.4 rules; per-trace cap |
| 5 | REST: `GET /api/society/capabilities`, `POST /agents` derives focus, `PATCH` re-derives on description change; CLI `jarvis society capabilities` |
| 6 | society surface kit implements §3.2 tool dict + §3.3 prompt assembly; `message_agent` with `refs`; wiki tools namespaced (§5 write path) |
| **6b +** | ecosystem card generator (from code, cached, byte-stable) + `capability_epoch` wiring |
| 7 | card "Tools" tab reads the catalog with brand marks; focus chips; approval-rules editor |
| 9 | approvals queue applies §3.4 order; routines tagged `agent:<id>`; seed proposals from connected capabilities |
| **9b +** | vault `society/` layout, `memory.md` per agent, unreviewed marker in wiki tool results, "promote to shared" approval item |
| M4 | `delegate-to-agent` voice tool; curator writes `society/shared/`; rooms live |
| M6 | teach-by-demonstration → draft skill; agent screens; sharing without secrets |

Exit test for this document (part of the M2 contract test): create "Mailbox" with a German
sample description meaning "You are my Gmail agent. You read, sort and answer my mail; external
mail only after approval" — literally: <!-- i18n-allow: German sample description -->
"Du bist mein Gmail-Agent. Du liest, sortierst und beantwortest meine Mails; externe Mails nur nach Freigabe." <!-- i18n-allow -->
Assert `focus == ["plugin:gmail"]`, `approval_rules.require_approval` contains
`plugin:gmail:send` (derived from the "only after approval" clause), the first chat message
names Gmail as its hand, a `wiki-ingest` outside
`society/mailbox/` is refused with a typed reason, and a `send` call lands in the approvals
queue while the card shows the pending item.
