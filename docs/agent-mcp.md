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

**From inside the app.** `GET /api/agent-mcp/clients` lists which clients this
machine has; `GET /api/agent-mcp/snippet?client=…` returns the block to paste;
`POST /api/agent-mcp/connect` writes it into the client's own config. Writing
merges — every other server in that file survives — and goes through a sibling
temp file, because a client reading a half-written config loses *all* its
servers, not just ours. Codex is never written to: a hand-formatted
`config.toml` is easy to corrupt and hard to restore, so it gets a snippet.

Environment overrides, for a Jarvis that is not on this box:

* `JARVIS_API_URL` — base URL of the Jarvis to drive
* `JARVIS_CONTROL_KEY` — the control key

## 3. The tools

Sixteen, grouped by intent. Everything marked **$** starts spend, work, or
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

### The board

| Tool | What it reads |
|---|---|
| `agent_inbox` | everything addressed to one agent; page with `after_seq` |
| `board_events` | the whole ecosystem stream; filter by agent or `trace_id` to follow one task end to end |

### Roster, rooms, governance

`agent_create` · `rooms_list` · `room_open` · `room_say` **$** ·
`room_settle` **$** · `approvals_list` · `approval_resolve` **$** ·
`kill_switch` **$**

## 4. Resources and prompts

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

## 5. The design rules

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

## 6. What the surface cannot do

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

## 7. Verified end to end

Against a running instance (headless, port 47941), a real MCP client saw:

* `/api/control/mcp/agents` → server `jarvis-agents`, 16 tools, the three
  fixed resources plus one per agent on the roster, 3 prompts, and live
  `ecosystem_status` data;
* `/api/control/mcp/` → server `jarvis`, 89 tools, **no** `agent_chat` — the
  surfaces do not leak into each other;
* the stdio bridge (`python -m jarvis.mcp.agents.bridge`) → the same catalog
  and the same live answers, which is the path a desktop client takes;
* an unauthenticated POST → rejected before it reaches either surface.

## 8. Where the code is

| File | Role |
|---|---|
| `jarvis/mcp/agents/tools.py` | the tool set: schemas, handlers, the refusal vocabulary |
| `jarvis/mcp/agents/server.py` | the MCP server: tools, resources, prompts |
| `jarvis/mcp/agents/context.py` | lazy access to the live runtime (AP-26: nothing on the boot path) |
| `jarvis/mcp/agents/bridge.py` | the stdio ↔ HTTP bridge for outside clients |
| `jarvis/mcp/agents/export.py` | per-client config paths, snippets, and the merge-safe write |
| `jarvis/ui/web/mcp_server_routes.py` | the mount and the surface dispatch |
| `jarvis/ui/web/agent_mcp_routes.py` | `/api/agent-mcp` — status, clients, snippet, connect |

Tests: `tests/contract/test_agent_mcp_surface.py` (the contract),
`tests/unit/mcp/test_agent_mcp_chat.py`,
`tests/unit/mcp/test_agent_mcp_routes.py`,
`tests/unit/mcp/test_agent_mcp_export.py`.
