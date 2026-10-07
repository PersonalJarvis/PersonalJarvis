# Agent runtimes: Hermes and OpenClaw

A society agent runs on one of three runtimes:

| Runtime | What executes a turn | Default |
|---|---|---|
| `jarvis` | Jarvis' own brain or the CLI seat the agent's model picks (Claude Code, Codex, …) | yes |
| `hermes` | [Hermes Agent](https://github.com/NousResearch/hermes-agent) (NousResearch, MIT) | |
| `openclaw` | [OpenClaw](https://github.com/openclaw/openclaw) (MIT) | |

The runtime is chosen once, in the "new agent" dialog every plus in the
society opens (name, runtime, the companion bot that follows the agent), and
is fixed for the agent's life: the roster refuses a change, because the
agent's sessions, runtime folder and tools belong to it. The model menu of
the agent's chat shows the runtime.

## Setup and updates

Nobody installs Hermes or OpenClaw by hand (`jarvis/agent_runtimes/manager.py`):

- **Picking a runtime sets it up.** The dialog sends
  `POST /api/agent-runtimes/{runtime}/ensure` as soon as Hermes or OpenClaw is
  picked; creating the agent sends it again. `ensure` runs the project's own
  installer when the runtime is missing (OpenClaw's also adds the Node.js it
  needs) and its own updater when it is too old. The agent can be created
  while that runs.
- **A turn waits for the setup.** A turn on a runtime that is not ready posts
  a `runtime_setup` notice in the chat and waits for the job; a setup that
  fails ends the turn with its reason.
- **Daily updates.** While any agent uses a runtime, the society runtime runs
  its updater once a day (first round 10 minutes after start), only when no
  turn of it is running. An update that leaves the runtime unable to start is
  repaired by a fresh install with the official installer.
- **No version numbers in the UI.** The dialog says "Ready", "Setting up…" or
  "Sets itself up"; versions stay internal (the minimum versions gate
  readiness).

None of this calls a model, so setup and updates never spend a key.

## Models: Jarvis' model gateway

An agent on Hermes or OpenClaw never talks to a model vendor. Its runtime is
configured with exactly one provider — Jarvis — and Jarvis answers every model
call with its own provider plugins (`gateway.py`,
`ui/web/runtime_gateway_routes.py`, routes in `model_map.py`):

- **One endpoint, two long-stable shapes.** `/api/runtime-gateway/v1` on
  Jarvis' loopback server: `POST /chat/completions` for every API-key and
  local provider (Hermes `transport: chat_completions`, OpenClaw
  `api: openai-completions`), `POST /responses` for the ChatGPT subscription
  (Hermes `transport: responses`, OpenClaw `api: openai-responses`),
  `GET /models` from Jarvis' catalog (the subscription asks its account). The
  runtime config is the same for every provider, which is what keeps it
  working across Hermes and OpenClaw updates.
- **Every connected provider works.** The dialog lists what can answer right
  now — a signed-in subscription first, then each provider with a saved key
  (the Agents-tier key wins, as for Jarvis' own agents) or a local server
  with an address. A new provider plugin in Jarvis is new to the runtimes too.
- **Keys and logins stay in Jarvis.** The runtime gets a per-agent token
  (`jrg_…`, process environment only); `SurfaceSecurity` accepts it on
  `/api/runtime-gateway/` and nowhere else. Hermes finds it again on session
  restore through `OPENAI_BASE_URL` = the gateway URL.
- **Translation.** A Chat Completions request becomes a `BrainRequest`
  (system text, messages with tool calls and results, function tools, max
  tokens, effort); the plugin's stream becomes Chat Completions chunks with
  tool calls and usage. Gemini's thought signature, which the OpenAI shape has
  no field for, is kept per tool-call id and sent back with the call. The
  plugin runs in a task of its own and hands deltas over a queue: its key
  override and cost caller are context variables, and a streamed response is
  read by another task.
- **Failures.** A provider error before the first token becomes an HTTP
  status the runtime backs off on (429 rate limit, 401 refused key, 502
  otherwise); after streaming began it is an `error` chunk. Either way the
  message is Jarvis' own, never the provider's body. Refusals no retry can
  fix are told apart first (`provider_errors.py`): an empty balance or zero
  quota is 402 `billing` even when the provider sent 429 (OpenAI does), a
  provider that cannot be reached (a stopped local server) is 503
  `provider_unreachable`. Hermes treats 402 as billing and stops instead of
  retrying.
- **Costs.** Every call goes through the plugin, so it lands in the cost
  ledger (caller `agent-runtime`).
- **ChatGPT subscription.** Handing the login to the runtime would make a
  second program refresh it, and OAuth refresh tokens are single-use:
  whichever refreshed first would break the other, including the person's
  own Codex login. The gateway answers with Jarvis' subscription client on the
  agent's Codex account, whose refresh is coordinated in one place
  (`live/subscription_auth.py`); a failure mid-stream is `response.failed`.
- **Claude subscription: billed as extra usage.** Claude runs on an
  Anthropic API key when one is saved. Without one, the gateway answers on
  the person's live Claude Code login (read-only: only the Claude CLI renews
  it, so an expired login waits until Claude Code runs again). Anthropic bills
  a subscription used outside Claude Code as extra usage, not from the plan's
  limits, so the model picker labels that seat "billed as extra usage"
  (`login_providers` on `/api/agent-runtimes`). A Claude login (`sk-ant-oat…`)
  saved in the Anthropic API-key slot is never sent as an API key.
  Measured 2026-10-07: Anthropic serves a subscription to Hermes and
  OpenClaw only from the account's Extra Usage ("Third-party apps now draw
  from your extra usage, not your plan limits", HTTP 400; Sonnet, Opus and
  Fable sometimes answer a bare 429 instead), for every model including
  Haiku. Before the runtime starts, `route_for` reads the account's usage
  report (`/api/oauth/usage`, no inference, cached two minutes) and refuses
  the turn at once when Extra Usage is off or its monthly limit is spent;
  a refusal that still arrives is reported as 402 with the same reason. The
  gateway sends Anthropic the agent's own request; it never presents itself
  as Claude Code.

`scripts/spikes/agent_runtimes_gateway_e2e.py <runtime> <provider> [model]`
runs two real turns through the gateway (route built by `route_for`, the real
guard in front); `--replay` answers the subscription from a stand-in in its
event format. Verified 2026-10-06: Hermes and OpenClaw on a local Ollama model
(`qwen3.5:9b`) and on the subscription with `--replay` — answer streamed,
history carried into the second turn, no error chunk. Against the live
subscription the account was at its usage limit; that error reached Hermes as
`response.failed`. API-key providers share the plugin path with Ollama but
were not called live (no paid test calls).

The runtime decides which agent loop, native tools and session store a turn
uses. Everything that makes a Jarvis agent stays in Jarvis and is identical
on every runtime: the one endless chat, the briefing (identity, standing
instructions, memory head, learned skills, teammates), the memory notebooks,
the learning loop, routines, delegation, quests, island checkpoints and
approvals. The runtime reaches all of that through the Jarvis MCP tools.

## Why ACP

Both projects ship an [Agent Client Protocol](https://agentclientprotocol.com)
server (JSON-RPC 2.0 over stdio) and document it as the interface for
embedding hosts. Jarvis drives both through it, plus a handful of documented
CLI commands, and never imports their internals or speaks their private wire
formats. An upstream update keeps working as long as the protocol does;
capabilities are read from the `initialize` answer, not from version strings.

`jarvis/agent_runtimes/acp.py` is the transport-free client for one turn:
`initialize` → `session/load` or `session/new` → `session/prompt`, translating
`session/update` notifications into agent-chat events (`text_delta`,
`assistant_text`, `reasoning`, `tool_call`, `tool_result`, `usage_delta`) and
answering `session/request_permission` from the chat's approval card. History
replayed during `session/load` is swallowed because the chat already shows it.

## Turns, folders and approvals (both runtimes)

- **One turn at a time per folder.** A runtime's config is written per turn,
  so the turns of one folder run one after another (`base.TurnSlots`); a turn
  that waits more than 15 minutes stops with a plain message.
- **Routine runs get their own folder** (`base.home_key`: `<agent>` for the
  agent's chat, `<agent>~runs` for every other session). A scheduled run never
  rewrites the model, tools or session store a chat turn is using, and on
  OpenClaw it runs on its own Gateway.
- **Approvals never live in the runtime's config.** Hermes runs with
  `approvals.mode: manual` and OpenClaw with `tools.exec.mode: ask`; every
  request reaches Jarvis over ACP and is answered from the chat's stance —
  Bypass allows without a card, Ask shows the card, Plan refuses.
- **A finished turn ends.** After the prompt's answer the runtime gets ten
  seconds to exit on its own, then it is ended; the answer already stands.

## Hermes

- **Process:** `hermes acp`, one process per turn, stdin EOF ends it (exit 0).
- **Isolation:** one Hermes profile per agent, `HERMES_HOME` under Jarvis' data
  directory. The user's own `~/.hermes` / default profile is never touched.
- **Config Jarvis writes** (`config.yaml`): `model.provider` / `model.default` /
  `model.base_url`, `terminal.cwd` = the agent workspace, Hermes' own memory
  and skill nudges off (Jarvis owns memory and skills), and `SOUL.md` = the
  agent's Jarvis briefing. The only key is the gateway token, in the process
  environment.
- **Jarvis tools:** passed per session in `session/new` / `session/load`
  `mcpServers` (HTTP with headers); `HERMES_ACP_SKIP_CONFIGURED_MCP=1` keeps
  any configured servers out. Hermes names them `mcp__jarvis__<tool>`.
- **Sessions:** persisted in the profile's `state.db`; `session/load` restores
  a conversation in a new process and replays it. An unknown id answers
  `session/load` with an empty result, which Jarvis treats as "resume lost" and
  retries fresh with the transcript in front.
- **Session restore and the key:** Hermes rebuilds a reopened session from the
  stored `custom` provider and base URL, without the named provider's
  `key_env`. Jarvis therefore also writes `model.base_url` and passes the
  gateway token as `OPENAI_API_KEY` with `OPENAI_BASE_URL` = the gateway URL,
  the pair that path reads, in that one process' environment only.
- **Tool search:** Hermes defers MCP schemas behind its own `tool_search` by
  default; OpenClaw does the same for local models. Both are switched off for
  agent profiles (`tools.tool_search: false`, `tools.toolSearch: false`): in a
  live turn a 9B local model never found the deferred Jarvis tools, and with
  them offered directly both runtimes called `society_wiki_note` correctly.
  The price is prompt size — about 69k input tokens per model call instead of
  about 27k on a fresh Hermes session (prompt caching covers roughly half of
  it from the second call on).

## OpenClaw

- **Process model:** the documented embedding path (`docs/gateway/embedding.md`
  upstream): Jarvis supervises an OpenClaw Gateway child with the embedding
  preset (`OPENCLAW_SKIP_CHANNELS=1`, `OPENCLAW_NO_RESPAWN=1`,
  `OPENCLAW_DISABLE_BONJOUR=1`, `OPENCLAW_EXEC_SHELL_SNAPSHOT=0`) and drives
  each turn through the version-matched `openclaw acp` bridge
  (`--url ws://127.0.0.1:<port> --session agent:main:main`). No hand-written
  Gateway WebSocket client: its wire protocol requires an exact version match.
- **Isolation:** one state directory (`OPENCLAW_STATE_DIR`,
  `OPENCLAW_CONFIG_PATH`) and one Gateway per agent folder, on its own
  loopback port, started on the first turn and stopped after 15 idle minutes.
  A changed config restarts the Gateway (with no turn in flight) instead of
  trusting its file watcher to have reloaded in time. Start-up is
  about 16 s on Windows with the preset (measured with 2026.9.8). The user's
  own `~/.openclaw` and their Gateway service are never touched.
- **Config Jarvis writes** (`openclaw.json`, only long-stable keys):
  `gateway.{mode,port,bind,auth}`, `models.providers.<id>` for the agent's
  model, `agents.defaults.{model.primary,workspace,heartbeat.every:"0m"}`,
  `session.reset.mode:"none"`, `mcp.servers.jarvis`, and `tools.deny` for
  native tools that duplicate Jarvis features (automations, messaging).
  A Gateway exit code 78 (invalid config) runs
  `openclaw doctor --fix --yes --non-interactive` once, then retries.
- **Jarvis tools:** OpenClaw rejects per-session `mcpServers`, so the Jarvis
  server is configured in the agent's own state directory. Values such as
  `${JARVIS_CONTROL_API_KEY}` are substituted from the Gateway's environment,
  so no key is written to disk. OpenClaw names them `jarvis__<tool>`; Jarvis
  normalises that to `mcp__jarvis__<tool>`.
- **Sessions:** the fixed session key keeps one conversation across bridge
  processes; the ACP session id changes per process and is not stored.
- **Heartbeat:** OpenClaw runs a background model turn every 30 minutes by
  default. Jarvis sets `heartbeat.every: "0m"` for every agent: nothing the
  person did not start may bill a key.
- **Node:** OpenClaw 2026.9 requires Node `>=24.16 <25 || >=26.1`. The runtime
  manager reports an unsuitable Node instead of failing a turn.

## Spike

`scripts/spikes/agent_runtimes_probe.py hermes|openclaw` drives a real
runtime against `tests/fakes/fake_openai_server.py` (a scripted
OpenAI-compatible model), so no paid key is involved. Verified 2026-10-06 with
Hermes 0.20.6 and OpenClaw 2026.9.8 on Windows: new session, an MCP tool call
with the right session header, resume in a new process, unknown-session
handling.

Live, 2026-10-06, on a headless dev instance with a local Ollama model
(`qwen3.5:9b`, no cost): a Hermes and an OpenClaw agent answered with the
agent's Jarvis identity, read a fact from its Jarvis memory after switching
runtimes, and wrote a new memory entry through `society_wiki_note`; killing
the app reaped the OpenClaw Gateway.

`scripts/spikes/agent_runtimes_e2e.py hermes|openclaw` runs the same fake
model through Jarvis' own drivers (profile and config writing, process
environment, the OpenClaw Gateway supervisor): two turns in one conversation,
no key written into the runtime's folder, no process left behind.

## Code map

| Piece | Where |
|---|---|
| ACP turn client (transport-free) | `jarvis/agent_runtimes/acp.py` |
| Shared types, child environment, persona text | `jarvis/agent_runtimes/base.py` |
| Jarvis provider → runtime endpoint and key | `jarvis/agent_runtimes/model_map.py` |
| Hermes driver | `jarvis/agent_runtimes/hermes.py` |
| OpenClaw driver and Gateway supervisor | `jarvis/agent_runtimes/openclaw.py` |
| Turn planning for the chat | `jarvis/agent_chat/runner_acp.py` |
| Process, pump, rollover, approvals (shared with every CLI seat) | `jarvis/agent_chat/runner_cli.py` |
| The agent's `runtime` field | `jarvis/society/roster.py`, `society_schema.sql` |

## Routines

A routine run keeps its owner's runtime when the run's model can drive it
(a pinned subscription seat runs on Jarvis' own runtime instead). On OpenClaw
each run gets its own Gateway session key, so a run never resets the agent's
main conversation.
