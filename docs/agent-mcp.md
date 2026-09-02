# The Agent MCP surface

Jarvis' agent ecosystem, offered outwards over the Model Context Protocol. Any
MCP client that holds the control key — Claude Desktop, Cursor, VS Code, Codex,
another Jarvis — can find the team, talk to it, give it work, watch the board
and answer its approval requests.

This document is the contract. It is pinned by
`tests/contract/test_agent_mcp_surface.py`; a change here that is not a change
there is a client somewhere breaking silently.

---

## 1. Two surfaces, one mount

| URL | What it offers | For whom |
|---|---|---|
| `/api/control/mcp` | Jarvis' **tools** — `open-app`, `google-calendar`, `wiki-recall`, every registered tool plugin | a session Jarvis itself spawned, reaching back in |
| `/api/control/mcp/agents` | the **ecosystem** — roster, board, rooms, assignments, approvals | any client holding the control key |

Both are Streamable HTTP, both authenticate with the Control API's bearer key,
and **loopback does not bypass it**: anything on the machine could otherwise
open a socket and start driving somebody's team.

Note the trailing slash: the tools surface is addressed as
`/api/control/mcp/` (what `jarvis_harness` has always sent). Without it
Starlette answers a redirect that MCP clients do not follow. The agents URL
needs no slash — `/api/control/mcp/agents` is a path *inside* the mount.

They share one Starlette mount and dispatch on the rest of the path. This is
not a stylistic choice. A `Mount` on `/api/control/mcp/agents` compiles to
`^/api/control/mcp/agents(?P<path>/.*)$` — it requires a segment *after* the
prefix, so a plain `POST /api/control/mcp/agents` never matches it and falls
through to the shorter mount. The client would then get the tools catalog on
the agents URL: same transport, same auth, wrong tools, no error anywhere.

## 2. Connecting a client

**Preferred — the stdio bridge.** The client launches a small process that
forwards to the HTTP surface:

```
python -m jarvis.mcp.agents.bridge
```

It needs no URL in the config, survives Jarvis changing port, works from
clients that cannot send an `Authorization` header, and finds the control key
the way the app does (keyring → env → file). When Jarvis is not running the
client still connects and is told what to start, rather than showing a red
"server failed" — which matters, because a desktop client launches the bridge
at *its* startup, long before anyone opens Jarvis.

**Direct — Streamable HTTP.** For a remote Jarvis or a client that cannot
launch a process:

```json
{ "type": "http",
  "url": "http://127.0.0.1:47821/api/control/mcp/agents",
  "headers": { "Authorization": "Bearer <control key>" } }
```

Fewer moving parts, but the key lives in a config file — and a key in a config
file is a key in a backup (AP-12).

**One click — `POST /api/agent-mcp/pair`.** The intended path. It mints this
client its OWN credential and returns the finished config in the same answer:

```
POST /api/agent-mcp/pair
{ "name": "Claude Desktop - MacBook", "scope": "work", "client": "claude-desktop" }
```

The response carries `config_text` — paste it, restart the client, done — and
the secret appears there once and never again, because only its hash is kept.
Add `"install": true` to write it into the client's config directly, or
`"base_url": "http://192.168.1.50:47821"` when pairing a client on another
machine.

**Other endpoints.** `GET /api/agent-mcp/clients` lists which clients this
machine has; `GET /api/agent-mcp/snippet?client=…` returns a block to paste;
`POST /api/agent-mcp/connect` writes it into the client's own config. Writing
merges — every other server in that file survives — and goes through a sibling
temp file, because a client reading a half-written config loses *all* its
servers, not just ours. Codex is never written to: a hand-formatted
`config.toml` is easy to corrupt and hard to restore, so it gets a snippet.

Environment overrides, for a Jarvis that is not on this box:

* `JARVIS_API_URL` — base URL of the Jarvis to drive
* `JARVIS_CONTROL_KEY` — the control key

## 3. Credentials: one per client

Two things open the agent surface.

**The control key** is the owner at their own machine: full scope, everything.
It is the master credential for the whole Control API, so it should not travel
into a config file — a key in a config file is a key in every backup (AP-12).

**An MCP token** is what a paired client gets. It is named, scoped,
individually revocable, and its last use is stamped, so "who has access?" and
"cut that one off" are each one call. Only the hash is stored; a stolen token
file yields nothing to replay, and a lost token is re-issued rather than
recovered.

| Scope | May do |
|---|---|
| `read` | look, never spend: roster, board, quests, capabilities, approvals |
| `work` | that, plus talk to agents, post quests, assign, speak in rooms, hire |
| `full` | that, plus governance: resolve approvals, the kill switch, import a team |

Scope is enforced by **not listing** a tool, not by refusing it afterwards: a
model shown a tool it may not call will call it, read the refusal, and try
again. Not offering it is the only honest way to say no. A call that slips
through anyway — a client that cached an old list — still gets a plain sentence
naming the scope it would need.

Manage them at `GET /api/agent-mcp/tokens` and
`DELETE /api/agent-mcp/tokens/{id}`.

### Why a token passes the CSRF guard

Worth knowing, because it looks like a hole and is not. The app's surface guard
treats cookie- and open-access requests as browser-shaped and demands an
`Origin` header on unsafe methods; Bearer credentials are exempt, because a
browser cannot silently mint one. An MCP token is such a credential, so it is
exempt too — but **only on `/api/control/mcp/agents`**. Everywhere else it is
an unknown Bearer and authenticates nothing. That narrowness is deliberate: the
desktop UI routes (`/api/settings/*`) are not key-gated and rely on this guard,
so a token accepted app-wide would have opened them.

## 4. The tools

Twenty-four, grouped by intent. Everything marked **$** starts spend, work, or
stops the house; a client should gate those and the description says so.

### Discovery

| Tool | What it answers |
|---|---|
| `ecosystem_status` | the whole house at a glance: agents by run state, active runs, running rooms, pending approvals, connected capabilities, kill switch |
| `agents_list` | the roster with `run_state` (idle / working / paused) |
| `agent_get` | one agent: roster row, recent board events, active runs, learned skills |

### Conversation

| Tool | What it does |
|---|---|
| `agent_chat` **$** | write to an agent and **get its answer back** — one turn on that agent's own session, returning the reply text and the tools it used |
| `agent_message` **$** | leave a note on the board — delivered, but starts no turn and has no reply |
| `agent_assign` **$** | give a task; the **scheduler** decides whether it runs |

`agent_chat` is the centre of the surface — it is what makes an MCP client a
keyboard. It waits up to `timeout_s` (default 180 s, max 600 s); a turn still
running when that expires comes back as `status: "still_running"` with the turn
id, and the answer lands on the board where `agent_inbox` finds it.

### Quests — the strongest verb here

| Tool | What it does |
|---|---|
| `quest_post` **$** | post a job WITHOUT naming who does it |
| `quests_list` · `quest_get` | the Quest Board, and one job's whole story |
| `quest_cancel` **$** · `quest_retry` **$** | stop it, or route it again |

`quest_post` is the tool to reach for when you know what you want done but not
who should do it. Deterministic Python — never a model — scores every agent on
focus overlap, whether the quest names them, and current load; if nobody fits,
it **forges a new teammate** for the job. The answer names the taker and why,
so the choice is reviewable.

### The board

| Tool | What it reads |
|---|---|
| `agent_inbox` | everything addressed to one agent; page with `after_seq` |
| `board_events` | the whole ecosystem stream; filter by agent or `trace_id` to follow one task end to end |

### Roster, rooms, governance

`agent_create` · `capabilities_list` · `rooms_list` · `room_open` ·
`room_say` **$** · `room_settle` **$** · `approvals_list` ·
`approval_resolve` **$** · `kill_switch` **$**

### Moving a team to another machine

| Tool | What it does |
|---|---|
| `ecosystem_export` | the team as a portable bundle |
| `ecosystem_import` **$** | apply a bundle here; `full` scope only |

A bundle carries the **design** — every agent with its role, focus, model,
permissions and budget — and never the operation: no secrets, no chat history,
no board, no workspace paths, no subscription seats. The safe subset is an
allowlist, so a roster field added later cannot leak by accident; a test fails
instead.

Import matches **by name**: agents already on the target are updated, the rest
are created. Run it with `dry_run` first to see the plan. Importing twice
changes nothing the second time, which is what makes a retry harmless.

## 5. Resources and prompts

A standard that ships only tools makes every client rediscover the same state
by hand, so the surface also serves:

**Resources** (read-only, attachable as context without spending a tool call):

* `jarvis://ecosystem` — the house at a glance
* `jarvis://agents` — the roster
* `jarvis://capabilities` — plugins, CLIs, MCP servers and skills the agents can use
* `jarvis://agent/<id>` — one teammate, listed per agent

**Prompts** (openings worth having ready):

* `standup` — what is everyone working on, what is waiting on me
* `brief_agent` — check an agent has what it needs, then hand the task over
* `settle_question` — put a question to a room and report the conclusion

## 6. The design rules

Held deliberately, because a standard other clients rely on cannot drift:

1. **One tool per intent, not per endpoint.** `agent_chat` seats a session,
   subscribes, sends and waits — the caller says one thing. MCP clients have
   tool budgets; a REST mirror would spend them on plumbing.
2. **Answers use the field names the REST layer already uses** — snake_case,
   `agent_id`, `run_state`, `seq`. One vocabulary, not two.
3. **A refusal is typed and says what to do next.** An unknown agent lists the
   known ones; a busy agent says it is mid-turn; a stopped ecosystem names the
   kill switch. Every failure leaves as readable text, never as an MCP error —
   an error is a dead end for the model, a sentence is not.
4. **Anything that spends is marked** `dangerous` and says so in its own
   description, so a client can gate it and the model knows before it calls.

## 7. What the surface cannot do

The safety seam is the house's, not a new one:

* **No spawn vehicle.** No tool here starts a background worker that starts
  another — the recursion the router tiers exist to prevent (AP-5/AP-14). A
  client is a person with a keyboard, not a second router.
* **Work goes through the scheduler.** `agent_assign` is subject to the kill
  switch, the tier wall, depth ≤ 2, budgets and caps, and a refusal comes back
  as a typed `VETO` with a reason. Trusted Python decides, never a model.
* **The kill switch is real.** Engaged, `agent_chat` refuses; nothing runs
  until it is released.
* **Approvals are not bypassed.** A remote client is not one of Jarvis' own
  spawned sessions, so there is no chat card to route an ask to; parked actions
  surface through `approvals_list` and are answered with `approval_resolve` —
  the same path a person at the keyboard uses.

## 8. Keeping new features in harmony

The drift this surface is most likely to die of is nobody noticing. Somebody
adds a society route, the app grows a button, and every MCP client stays a
version behind — nothing breaks, so nothing gets fixed.

`tests/contract/test_agent_mcp_harmony.py` makes that impossible to ignore. It
enumerates the society's REST surface and requires every route to be either
COVERED (naming the tool that exposes it) or WITHHELD (with one line saying why
a remote client does not get it). A new route fails the build until somebody
decides. Answering takes a minute; the point is that the decision is made.

It earned its keep the day it was written: it immediately caught the Quest
Board and the chat-binding route, both added hours earlier by other work, and
`quest_post` exists because of it.

Two more floors hold the surface up:

* `tests/contract/test_agent_mcp_every_tool.py` runs EVERY published tool
  against a live society and then asserts that the set it ran equals the set
  the surface publishes — so a tool cannot ship untested.
* `tests/contract/test_agent_mcp_surface.py` pins the tool set and the
  dangerous flags. Adding a tool is a feature; renaming or removing one breaks
  every connected client, so it fails here first.

## 9. Verified end to end

Against a running instance (headless), a real MCP client saw:

* `/api/control/mcp/agents` → server `jarvis-agents`, the full tool set, the
  three fixed resources plus one per agent, 3 prompts, live `ecosystem_status`;
* `/api/control/mcp/` → server `jarvis`, its own 89 tools, **no** `agent_chat` —
  the surfaces do not leak into each other;
* the stdio bridge (`python -m jarvis.mcp.agents.bridge`) → the same catalog and
  the same live answers, which is the path a desktop client takes;
* an unauthenticated POST → rejected before it reaches either surface.

Pairing and scopes, over the wire with real tokens:

* a `work` token saw 21 tools, a `read` token 13, the control key all 24;
* the `read` token was offered no spending tool at all, and calling
  `kill_switch` anyway got a sentence naming the scope it would need;
* the `work` token could `quest_post` but never saw `kill_switch`;
* `ecosystem_export` returned the team with no secret-shaped string anywhere in
  it, and `ecosystem_import` was correctly refused to the `work` token;
* revoking one token killed it immediately while the other client kept working.

That live run is also what caught the CSRF-guard bug described in §3 — every
unit test was green while the feature did not work at all, because unit tests
mount the ASGI app directly and never meet the guard.
`tests/unit/mcp/test_agent_mcp_origin_guard.py` now covers it.

## 10. Where the code is

| File | Role |
|---|---|
| `jarvis/mcp/agents/tools.py` | the tool set: schemas, handlers, the refusal vocabulary |
| `jarvis/mcp/agents/server.py` | the MCP server: tools, resources, prompts |
| `jarvis/mcp/agents/context.py` | lazy access to the live runtime (AP-26: nothing on the boot path) |
| `jarvis/mcp/agents/bridge.py` | the stdio ↔ HTTP bridge for outside clients |
| `jarvis/mcp/agents/export.py` | per-client config paths, snippets, and the merge-safe write |
| `jarvis/mcp/agents/tokens.py` | per-client credentials: issue, scope, verify, revoke |
| `jarvis/mcp/agents/portable.py` | the export/import bundle and its allowlist |
| `jarvis/ui/web/mcp_server_routes.py` | the mount and the surface dispatch |
| `jarvis/ui/web/agent_mcp_routes.py` | `/api/agent-mcp` — status, clients, snippet, connect |

Tests: `tests/contract/` holds the contract (`_surface`), the coverage floor
(`_every_tool`), the drift gate (`_harmony`) and portability (`_portable`);
`tests/unit/mcp/` holds the chat path (`_chat`), the mount and pairing flow
(`_routes`), credentials (`_tokens`), the CSRF-guard regression
(`_origin_guard`) and client configs (`_export`).
