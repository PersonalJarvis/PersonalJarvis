# Agent runtimes: Hermes and OpenClaw

A society agent runs on one of three runtimes:

| Runtime | What executes a turn | Default |
|---|---|---|
| `jarvis` | Jarvis' own brain or the CLI seat the agent's model picks (Claude Code, Codex, …) | yes |
| `hermes` | [Hermes Agent](https://github.com/NousResearch/hermes-agent) (NousResearch, MIT) | |
| `openclaw` | [OpenClaw](https://github.com/openclaw/openclaw) (MIT) | |

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

## Hermes

- **Process:** `hermes acp`, one process per turn, stdin EOF ends it (exit 0).
- **Isolation:** one Hermes profile per agent, `HERMES_HOME` under Jarvis' data
  directory. The user's own `~/.hermes` / default profile is never touched.
- **Config Jarvis writes** (`config.yaml`): `model.provider` / `model.default` /
  `model.base_url`, `terminal.cwd` = the agent workspace, Hermes' own memory
  and skill nudges off (Jarvis owns memory and skills), and `SOUL.md` = the
  agent's Jarvis briefing. Provider keys go into the process environment only.
- **Jarvis tools:** passed per session in `session/new` / `session/load`
  `mcpServers` (HTTP with headers); `HERMES_ACP_SKIP_CONFIGURED_MCP=1` keeps
  any configured servers out. Hermes names them `mcp__jarvis__<tool>`.
- **Sessions:** persisted in the profile's `state.db`; `session/load` restores
  a conversation in a new process and replays it. An unknown id answers
  `session/load` with an empty result, which Jarvis treats as "resume lost" and
  retries fresh with the transcript in front.
- **Tool search:** Hermes defers MCP schemas behind its own `tool_search` by
  default. Jarvis keeps that native behaviour; tests switch it off
  (`tools.tool_search: false`) so a scripted model sees the tools.

## OpenClaw

- **Process model:** the documented embedding path (`docs/gateway/embedding.md`
  upstream): Jarvis supervises an OpenClaw Gateway child with the embedding
  preset (`OPENCLAW_SKIP_CHANNELS=1`, `OPENCLAW_NO_RESPAWN=1`,
  `OPENCLAW_DISABLE_BONJOUR=1`, `OPENCLAW_EXEC_SHELL_SNAPSHOT=0`) and drives
  each turn through the version-matched `openclaw acp` bridge
  (`--url ws://127.0.0.1:<port> --session agent:main:main`). No hand-written
  Gateway WebSocket client: its wire protocol requires an exact version match.
- **Isolation:** one state directory (`OPENCLAW_STATE_DIR`,
  `OPENCLAW_CONFIG_PATH`) and one Gateway per agent, on its own loopback port,
  started on the first turn and stopped after an idle period. Start-up is
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
