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
  picked; creating the agent sends it again. `ensure` starts whichever job the
  runtime needs: an install when it is missing, an update when it is older
  than the minimum, and for Hermes a one-time *prepare* of the data root its
  agents share (below). The agent can be created while that runs.
- **Pinned releases.** `jarvis/agent_runtimes/runtime-versions.json` names per
  runtime a `minimum` (older installs are not ready) and a `tested` release
  (Hermes also its source `commit` and the `config_version` it writes). Every
  install and update goes to `tested`, never to upstream latest; `tested` is
  bumped only from a green agent-runtimes canary run on all three OSes. A
  newer install the person made themselves keeps running and is reported as
  `untested` in `GET /api/agent-runtimes`.
- **Installers.** Hermes: its official `install.ps1` / `install.sh` at the
  pinned commit, without browser tools or desktop control
  (`-SkipBrowser -SkipComputerUse` / `--skip-browser --skip-computer-use`).
  OpenClaw: Jarvis installs its own copy into `<runtimes_root>/openclaw-cli`
  with a private Node.js — `install-cli.sh --prefix … --version <tested>` on
  macOS and Linux (no sudo, no shell-profile change), `install.ps1 -NodeOnly`
  (a checksum-verified private Node.js, no `winget`/UAC prompt) plus that
  Node's npm on Windows. Jarvis never updates the person's own OpenClaw; a
  ready one they installed is used until Jarvis' copy exists. On macOS and
  Linux the installer script is downloaded first and run under
  `set -euo pipefail`, so a missing `curl`/`git`, an offline machine or a 404
  fails the job with a plain reason (exit 90 names the missing tool); the
  installer's last log line is the reason a waiting turn shows.
- **Setup holds the runtime alone.** A job waits until no turn of its runtime
  runs, and turns that arrive meanwhile wait for the job (`base.RuntimeGate`).
  An update replaces files a running turn holds open; on Windows it cannot.
- **A turn waits for the setup.** A turn on a runtime that needs a job posts a
  `runtime_setup` notice in the chat and waits for it; a setup that fails ends
  the turn with its reason.
- **Daily round.** While any agent uses a runtime, the society runtime checks
  it once a day (first round 10 minutes after start) and, only when no turn of
  it runs, lifts an install older than `tested` up to it. An update that
  leaves the runtime unable to start is repaired by a fresh install; when
  that fails too, the last release that finished a setup ready
  (`<runtimes_root>/<runtime>-last-good.json`) is reinstalled.
- **Found without a restart.** Binary discovery re-checks the well-known
  install folders on every call (`base.which`), searches the installers'
  private Node.js and command folders, and merges the persistent PATH the
  installer may have changed (Windows registry) after each job.
- **No version numbers in the UI.** The dialog says "Ready", "Setting up…" or
  "Sets itself up"; versions stay internal.

None of this calls a model, so setup and updates never spend a key.

Each app instance keeps its own runtime folders (`agent_runtimes` for the
default app, `agent_runtimes-dev` for the dev instance), and a turn holds a
cross-process lock on its folder.

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
- **Model budgets.** The route refreshes catalog metadata with a bounded wait
  before writing the runtime config. Hermes receives `model.context_length`;
  OpenClaw receives `contextWindow` and `maxTokens`. Gateway model discovery
  includes the selected local model and its limits. Both Chat Completions
  modes use the declared output maximum when a request omits its limit and
  preserve smaller explicit request limits. There is no extra 128k output cap
  or fixed percentage reserved by Jarvis. Ollama uses the full native context
  unless the user selected a smaller `num_ctx`; its provider prepares an
  isolated model profile with that actual allocation before inference. An
  explicit positive `num_predict` remains effective. Unknown output capacity
  is omitted from runtime metadata rather than invented as an 8k limit; a
  request with neither metadata nor an explicit budget uses the brain's normal
  request default. Unknown context still uses plugin metadata (32k otherwise)
  until the catalog can establish the real capacity.
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
- **Stop is a real stop.** Stop, the turn deadline, a provider failure and the
  stall watchdog first send ACP `session/cancel` and give the runtime five
  seconds to end its turn, then kill it. An OpenClaw run lives in its Gateway,
  so killing the bridge alone would leave it calling the model and running
  tools.
- **Stall watchdog.** The runtime must open its session within 7 minutes (it
  may finish its own setup first); afterwards the model must say something
  within 5 minutes. The counter resets on every ACP frame and pauses while a
  tool runs or an approval card is open. A stalled turn ends with a plain
  reason instead of holding the agent's folder for the full hour.
- **Incomplete answers say so.** `stopReason` `max_tokens`,
  `max_turn_requests` or `cancelled` without any text is the turn's error;
  with text the answer stands and a `stop_reason` notice marks it incomplete.
- **Nothing outlives the turn.** The runtime runs in the turn's process
  container (Job Object on Windows, process group on POSIX). On POSIX its
  descendants are also recorded while it runs, so a shell command it started
  under `setsid` (Hermes does that for every command) is reaped with the turn.
  A frame larger than the 16 MB read limit ends the turn with a plain error.

## Hermes

- **Process:** `hermes acp`, one process per turn, stdin EOF ends it (exit 0).
- **Profiles of one Jarvis data root:** every agent folder is a Hermes profile,
  `HERMES_HOME=<runtimes_root>/hermes-home/profiles/<agent>`. Hermes keeps its
  Python dependency environment (about 700 MB) per *data root*, and a profile
  shares its root's. With a root per agent, every agent's first turn — and the
  first after every Hermes update — spent about two minutes building its own
  (measured 84–136 s); now the setup job prepares the shared root once per
  Hermes build (`hermes profile list` under a setup profile, 37–45 s measured
  on Windows) and a first turn starts in seconds (6.8–8.3 s measured, replay
  model). The root is Jarvis' own, not the person's Hermes home: a profile of
  their home would read, and rotate, their own Hermes logins through Hermes'
  global-root credential fallback.
- **Never on the user's PATH:** Hermes' home maintenance publishes launchers
  into `<data root>/bin` and, on Windows, registers that folder in the user's
  PATH. The shared root keeps a *file* named `bin`, so that step fails before
  it touches the registry; on macOS and Linux the config sets
  `cli.expose_on_path: false`. Older builds left one PATH entry per agent
  folder; `path_cleanup` removes exactly those (entries inside an
  `agent_runtimes` folder) once per app run. An agent's old folder is moved:
  its session store is copied into the profile, then the old folder and its
  private environment are removed.
- **Config Jarvis writes** (`config.yaml`): `_config_version` from the pins (so
  Hermes migrates the file forward), `model.provider` / `model.default` /
  `model.base_url`, `terminal.cwd` = the agent workspace, Hermes' own memory
  and skill nudges off (Jarvis owns memory and skills), the `browser`,
  `computer_use` and `cronjob` toolsets always disabled (Jarvis has a visible
  browser and its own routines; Hermes' browser is a headless Chromium), and
  `SOUL.md` = the agent's Jarvis briefing. The only key is the gateway token,
  in the process environment.
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
- **Not yet covered:** Hermes discovers ambient credentials (for example the
  GitHub CLI's `gh auth token` for Copilot) into each profile's credential
  pool. Jarvis' config never routes a model call to them; switching the
  discovery off needs an upstream setting.

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
  trusting its file watcher to have reloaded in time. Start-up took 17–26 s
  on Windows with the plugins below denied (46 s before). The user's own
  `~/.openclaw` and their Gateway service are never touched.
- **Supervision:** the Gateway counts as ready when `GET /healthz` answers 200
  from a live child — a bare TCP accept could be another program that took
  the port, which is retried once on a fresh port. Each folder's Gateway holds
  a cross-process lock and records `gateway.pid` (pid, port, creation time); a
  Gateway an earlier app left running after a crash or force-quit (POSIX,
  where no Job Object reaps it) is stopped before a new one starts — only the
  recorded process, never a recycled pid. Descendants that left its process
  group are reaped with it.
- **Config Jarvis writes** (`openclaw.json`, only long-stable keys):
  `gateway.{mode,port,bind,auth}`, `models.providers.<id>` for the agent's
  model, `agents.defaults.{model.primary,workspace,heartbeat.every:"0m"}`,
  `session.reset.mode:"none"`, `mcp.servers.jarvis`, `tools.deny` for native
  tools that duplicate Jarvis features (automations, messaging, the browser),
  `plugins.deny` for the bundled browser, canvas, desktop-control
  (`cua-computer`), device-pairing, file-transfer, location, voice, GitHub and
  Linux-node plugins, and `logging.file` inside the agent's state folder
  (OpenClaw otherwise appends every Gateway's log to one shared temp-folder
  file). A Gateway exit code 78 (invalid config) runs
  `openclaw doctor --fix --yes --non-interactive` once (contained, killed
  after two minutes), then retries.
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
  Its native cron scheduler and memory-core plugin/slot are also disabled:
  disabling heartbeat alone still allowed a managed dreaming job at startup.
- **Node:** OpenClaw 2026.9 requires Node `>=24.16 <25 || >=26.1`. Jarvis' own
  copy brings Node 24.21; a person's own install on an unsuitable Node is
  reported (`problem_kind: node`) and replaced by Jarvis' copy.
- **Not yet covered:** on Windows a Gateway is stopped with
  `TerminateProcess`, skipping its own shutdown drain; a graceful stop needs a
  console-less signal path upstream.

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
