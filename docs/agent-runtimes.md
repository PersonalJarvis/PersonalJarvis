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
  (the Agents-tier key wins, as for Jarvis' own agents), a local server with
  an address, or Vertex AI on a Google Cloud project without a key. The
  runtimes can use the providers listed in `model_map._ENDPOINTS`; a new
  provider plugin needs an entry there too. Claude subscription availability
  comes from the selected official CLI account, independently of HTTP Extra
  Usage settings. Actual native quota errors surface from user-started turns.
- **Keys and logins stay in Jarvis.** The runtime gets a per-agent token
  (`jrg_…`, process environment only); `SurfaceSecurity` accepts it on
  `/api/runtime-gateway/` and nowhere else. Hermes finds it again on session
  restore through `OPENAI_BASE_URL` = the gateway URL. A token answers only
  for the models its route registered, the gateway keeps at most 1024 grants
  (never dropping one whose turn is running), and `gateway.revoke_agent()`
  invalidates an agent's tokens when it is deleted or moved to another
  provider.
- **Translation.** A Chat Completions request becomes a `BrainRequest`
  (system text, messages with tool calls and results, function tools, max
  tokens, effort); the plugin's stream becomes Chat Completions chunks with
  tool calls and usage. Gemini's thought signature, which the OpenAI shape has
  no field for, is kept per tool-call id and sent back with the call; a call
  without one (a restart in between, the later calls of a parallel step)
  still replays as a native call with Google's validator sentinel. The
  plugin runs in a task of its own and hands deltas over a queue: its key
  override, cost caller and agent request profile are context variables, and
  a streamed response is read by another task. `prompt_tokens` counts the
  whole prompt, cache reads and writes included, because both runtimes
  compress from it. A provider's "cut off" stop (`max_tokens`, Gemini's
  `MAX_TOKENS`, Anthropic's `model_context_window_exceeded`) stays `length`
  even with tool calls, so a truncated call is retried, never run.
- **Agent request profile.** The plugins were tuned for voice; the gateway
  runs them with a profile the voice path never sets
  (`plugins/brain/_agent_profile.py`): a 300 s read timeout for hosted
  providers and 900 s for local servers (a reasoning model thinks for
  minutes before its first token), no temperature unless the runtime chose
  one, and Anthropic prompt-cache breakpoints on system, tools and the
  latest message. One provider brain, with its HTTP client, is kept per
  session, model and credential (LRU of 32) instead of one per call.
- **Failures.** A provider error before the first token becomes an HTTP
  status and code the runtime acts on; after streaming began it is an `error`
  chunk (`response.failed` on the subscription). The message is Jarvis' own,
  never the provider's body; the body goes to the log as one bounded,
  redacted WARNING line. `provider_errors.py` tells the cases apart: 402
  `billing` (also when OpenAI sends 429 for an empty balance), 400
  `context_length_exceeded` (worded "context length exceeded", which both
  runtimes read as "compress and resend"), 504 `timeout` ("took too long",
  not "not reachable"), 503 `provider_unreachable` / `provider_overloaded`,
  404 `model_not_found`, 400 `tools_unsupported` / `invalid_tool_schema` /
  `invalid_request`, 401 `provider_auth` / `claude_login_expired`, 429
  `rate_limited` with a cooldown, 502 `provider_error`. Only the codes no
  runtime can recover from end the Jarvis turn at once
  (`provider_errors.TERMINAL_CODES`: billing, Extra Usage, refused key or
  login, unknown model, tool refusals, rate limit); an overflow, a timeout or
  an overloaded provider goes back to the runtime's own retry and
  compression.
- **Costs.** Every gateway call is metered into the cost ledger (caller
  `agent-runtime`). From the first metered call on, the cost report bills
  Hermes / OpenClaw turns from those rows and skips the runtime's own turn
  report, so no call is counted twice.
- **Model budgets.** The route refreshes catalog metadata with a bounded wait
  before writing the runtime config. Hermes receives `model.context_length`;
  OpenClaw receives `contextWindow` and `maxTokens`. Gateway model discovery
  includes the selected local model and its limits. A request that names no
  output limit gets the declared output maximum, but never more than the
  context window leaves after a conservative prompt estimate (OpenRouter's
  upstreams and vLLM refuse `prompt + max_tokens > context`, and many models
  declare an output maximum as large as the window); an explicit request
  limit is kept, capped at the model's maximum. Ollama uses the `num_ctx`
  chosen on the model card (bounded by the native window) and otherwise
  65,536 tokens, never the whole native window by default: Ollama reserves
  the KV cache up front, and a 30B model at 256k needs about 45 GB. Its
  provider prepares an isolated model profile with that allocation before
  inference. An explicit positive `num_predict` remains effective. Unknown output capacity
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
  A runtime's thinking level is snapped to one the selected ChatGPT model
  offers (from the account's catalog, no inference), and a `system` or
  `developer` input message moves into the instructions, which ChatGPT's
  backend requires.
- **Claude subscription through the official CLI.** An explicitly selected
  subscription uses each runtime's native Claude Code adapter, even when an
  Anthropic API key is saved. Existing unpinned agents keep their API-key route
  when one exists. Jarvis probes `claude auth status --json` against the selected
  or active account and passes only its account directory to the runtime; the
  official CLI owns credentials and renewal. Native requests do not use the
  HTTP gateway, read OAuth bearers, or fall back to a paid API key.
  Hermes uses the official
  [Claude Subscription DirectSDK plugin](https://hermes-agent.nousresearch.com/docs/plugins/claude-subscription-directsdk),
  pinned to `4bc79c78031d1a042b5d8a7314ceea283db5c5e2` and installed inside the
  agent's profile on first use. It requires Hermes 0.21.4 or newer. The plugin
  keeps Hermes' tools, approvals and conversation history; it remains an
  experimental upstream transport. OpenClaw uses its bundled `claude-cli`
  backend on the canonical `anthropic/<model>` route. Both retain Jarvis' MCP
  tools and selected permissions. Restored Hermes sessions explicitly select
  the current model before the next prompt, so an older HTTP session does not
  continue with stale credentials.
  [Anthropic's plan guidance](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
  describes native SDK/CLI subscription usage. Model entitlement, quota and
  any account-enabled overage remain controlled by the account; a CLI cost
  estimate is not an invoice. A login copied into an API-key slot is never
  accepted as an API key.

`scripts/spikes/agent_runtimes_gateway_e2e.py <runtime> <provider> [model]`
runs two real turns through the gateway (route built by `route_for`, the real
guard in front); `--replay` answers the subscription from a stand-in in its
event format. Verified 2026-10-06: Hermes and OpenClaw on a local Ollama model
(`qwen3.5:9b`) and on the subscription with `--replay` — answer streamed,
history carried into the second turn, no error chunk. Against the live
subscription the account was at its usage limit; that error reached Hermes as
`response.failed`. API-key providers share the plugin path with Ollama but
were not called live (no paid test calls). Re-run 2026-10-07 with `--replay`
after the gateway hardening: Hermes and OpenClaw on Chat Completions and
Hermes on the subscription, two turns each, all passing.

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
  under `setsid` (Hermes does that for every command) is reaped with the turn: the tree is walked while the runtime lives, and every descendant inherits a per-turn `JARVIS_TURN_MARK`, so one that detached between two walks is still found by its environment.
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
- **Tool search:** Hermes agents keep MCP schemas behind Hermes' own
  `tool_search` (`tools.tool_search.enabled: on`, listing capped at 2000
  tokens): connected accounts can expose hundreds of tools, and offering them
  all cost about 69k input tokens per call. The profile's `SOUL.md` tells the
  model to search, describe and call through `tool_search` / `tool_describe` /
  `tool_call` and to answer greetings directly; a resumed session's pinned
  eager tool catalog is invalidated first (`tool_snapshot.py`) so the deferred
  configuration takes effect. OpenClaw still offers the tools directly
  (`tools.toolSearch: false`): a 9B local model never found deferred tools there.
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

## CI and release gates

A change the `agent_runtimes` lane covers (the runtimes, the chat and society
code that drives them, their fakes and spikes; the table lives in
`scripts/ci/agent_runtime_suite.py`) runs three jobs, and `CI gate` waits for
each of them:

| Job | Where | What it proves |
|---|---|---|
| `agent-runtimes-unit` | Linux, Windows, macOS on the portable base install | The runtime drivers, gateway, ACP runner, chat service, send queue, controls, society binding, roster, routines and society routes (`python scripts/ci/agent_runtime_suite.py`). Fakes only. |
| `agent-runtimes-headless` | `python:3.11-slim` | The runtime package imports and its tests pass on a server with no GPU, audio or desktop stack, and the app's Hermes setup without curl stops with "Setup needs curl". |
| `agent-runtimes-e2e` | Linux, Windows, macOS × Hermes, OpenClaw | `scripts/ci/agent_runtime_e2e.py`: the app's own setup job (`manager.wait_ready`) installs the release pinned in `jarvis/agent_runtimes/runtime-versions.json` with a desktop app's PATH, never npm or a manual installer. Then two turns per model-gateway protocol against scripted providers (`--replay`), the fake-model driver check with turn-1 budgets (Hermes 30 s, OpenClaw 90 s), `pytest tests/unit/agent_runtimes`, and proof that the Windows user PATH gained no `agent_runtimes` entry and OpenClaw edited no shell profile. No key and no network to a model. |

The impact selection of the Windows pull-request leg adds the same suite
whenever the lane is on, so a runtime change also runs the chat and society
tests, not only the tests that import the changed module.

`agent-runtimes-canary.yml` runs weekly and on dispatch. In its own checkout
it points the pin at upstream latest (`agent_runtime_suite.py
--point-at-latest`) and runs the same app-setup check on all three OSes. When a runtime passes on every OS, the
`raise-pin` job records its version (and Hermes' upstream commit from
`hermes --version`, never a local checkout's HEAD; the installers pin Hermes
by `--commit` / `-Commit`) and opens a pull request that changes
`runtime-versions.json`. That pull request is the admission of a new upstream
release: its CI run is the pinned check above. A failed scheduled canary opens
an issue and leaves the pin where it is.

## Code map

| Piece | Where |
|---|---|
| ACP turn client (transport-free) | `jarvis/agent_runtimes/acp.py` |
| Shared types, child environment, persona text | `jarvis/agent_runtimes/base.py` |
| Jarvis provider → runtime endpoint and key | `jarvis/agent_runtimes/model_map.py` |
| Hermes driver | `jarvis/agent_runtimes/hermes.py` |
| OpenClaw driver and Gateway supervisor | `jarvis/agent_runtimes/openclaw.py` |
| Pinned releases (minimum, tested, Hermes commit) and their reader | `jarvis/agent_runtimes/runtime-versions.json`, `jarvis/agent_runtimes/versions.py` |
| One-time cleanup of PATH entries older setups left behind | `jarvis/agent_runtimes/path_cleanup.py` |
| Every process a runtime starts, tracked for shutdown | `jarvis/core/process_tree.py` (`DescendantTracker`) |
| CI: lane table, pins for CI, canary pin raise; setup-through-the-app check | `scripts/ci/agent_runtime_suite.py`, `scripts/ci/agent_runtime_e2e.py` |
| Turn planning for the chat | `jarvis/agent_chat/runner_acp.py` |
| Process, pump, rollover, approvals (shared with every CLI seat) | `jarvis/agent_chat/runner_cli.py` |
| The agent's `runtime` field | `jarvis/society/roster.py`, `society_schema.sql` |

## Routines

A routine run keeps its owner's runtime when the run's model can drive it
(a pinned subscription seat runs on Jarvis' own runtime instead). On OpenClaw
each run gets its own Gateway session key, so a run never resets the agent's
main conversation.
