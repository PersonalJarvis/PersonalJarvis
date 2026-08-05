# The Jarvis CLI

`jarvis` is a command-line interface to a **running** Personal Jarvis instance. It
exposes the same action surface as the desktop WebUI, so anything you can click in
the UI you can also do from a terminal — and, more importantly, so can an external
coding agent (Claude Code, Codex) or a script.

This is the same relationship `gcloud` / `aws` / `az` have with their cloud
platforms: a single CLI that surfaces the full capability plane of a service. You
point it at a running Jarvis and drive it.

> Full command list: [`jarvis-cli-reference.md`](jarvis-cli-reference.md)
> (auto-generated). Design rationale: [`superpowers/specs/2026-06-21-jarvis-cli-design.md`](superpowers/specs/2026-06-21-jarvis-cli-design.md).

## How it works

The CLI is a **thin HTTP client over the REST API**, in two layers:

1. **Dynamic layer (`jarvis api <tag> <op>`)** — every mounted REST endpoint is
   surfaced automatically from the server's OpenAPI spec. New features become
   commands with no extra code. This is what guarantees *every* WebUI action is
   reachable.
2. **Curated layer (`jarvis <group> <command>`)** — hand-written, ergonomic
   commands for the common domains: `brain`, `config`, `missions`, `wiki`,
   `sessions`, `skills`, `outputs`, `board`, `workflows`, `conductor`, `contacts`,
   `telephony`, `marketplace`, `mcps`, `docs`, `frontier`, plus `auth`, `system`,
   `tasks`.

Because the CLI only ever calls the same routes the WebUI calls, it inherits all
of Jarvis's safety machinery (risk tiers, the atomic config-write pipeline, the
event bus, audit) for free — it cannot bypass a guardrail it never re-implements.

It runs on Linux, macOS, and Windows (pure-Python: `typer`, `httpx`,
`platformdirs`, `rich` — all base dependencies), including a headless VPS.

## Install

The CLI ships with the package, so after the normal install it is on your PATH:

```bash
pip install -e .            # provides the `jarvis` binary (aliases: jarvisctl, jctl)
jarvis version
```

Bare `jarvis` (no subcommand) still launches the app/tray as before; `jarvis serve`
still starts the headless server. Only the control subcommands (`jarvis missions
…`, `jarvis brain …`, etc.) route into the CLI.

## Auth & discovery

**Local is zero-config.** When Jarvis is running on the same machine, the desktop
app writes its live port + control token to a single-instance session file; the
CLI discovers it automatically (and falls back to the machine's control key). Just
run commands:

```bash
jarvis system status        # {"reachable": true} when Jarvis is up
jarvis --json brain status
```

Resolution order (highest first): explicit `--url` / `--key` → `JARVISCTL_BASE_URL`
/ `JARVISCTL_CONTROL_KEY` env → saved profile (`jarvis auth login`) → the live
session file → the local control key + default `127.0.0.1:47821`.

**Remote / VPS.** Tunnel the loopback port and the CLI treats it as local:

```bash
ssh -L 47821:127.0.0.1:47821 <host>
```

Or save a remote profile (the key is read from a hidden prompt — never an inline
argument that would land in shell history):

```bash
jarvis auth login --url https://jarvis.example:47821
# (prompts for the control key; or: echo "$JCTL" | jarvis auth login --url … --key -)
```

## Output

Put `--json` **before** the group for machine-readable output; omit it for human
tables:

```bash
jarvis --json missions list          # JSON, for scripts/agents
jarvis missions list                 # rich table, for humans
```

## Safety model

The CLI is agent-first, so it stays out of your way for safe work and only gates
the genuinely consequential:

- **Reads** (`GET`) and **reversible mutations** (switch provider, set language,
  enable a skill) just run.
- **Destructive** actions — every `DELETE`, plus `config set`, `missions dispatch`,
  `telephony outbound`, `system restart` — refuse unless you pass `--yes` (or set
  `JARVIS_CLI_ASSUME_YES=1`). A prompt is not an accepted substitute.
- `--dry-run` on any command prints the exact request (method, path, body, whether
  auth is attached) and sends nothing — the safe way to inspect before acting.
- Secrets are never accepted as inline arguments; they are read from a hidden
  prompt or stdin and stored in the OS credential store.

## Driving Jarvis from a Claude Code / Codex session

This is a first-class use case. An external coding agent can control your running
Jarvis end-to-end through its shell:

1. **It's already installed and authenticated.** After `pip install -e .` the
   `jarvis` binary is on PATH, and local auth is zero-config (session-file
   discovery). The agent runs `jarvis system status` to confirm Jarvis is up.
2. **It discovers the surface** with `jarvis --help`, `jarvis <group> --help`, and
   `jarvis api --help` for the full generated list.
3. **It reads with `--json`** and parses, and **acts with explicit intent**, using
   `--dry-run` to preview and `--yes` to authorize destructive actions.

Worked example — switch the brain provider, then dispatch a mission:

```bash
jarvis --json brain status                       # see the active provider
jarvis brain switch openai                        # reversible → runs immediately
jarvis brain subagent-switch openai               # switch the worker provider too
jarvis missions dispatch "summarize today's PRs" --yes   # destructive → needs --yes
jarvis --json missions list --state RUNNING       # watch it
```

The `drive-jarvis-cli` skill (in `.claude/skills/`) packages these conventions so
an agent can pick them up automatically.

## Extending the CLI

When you add a REST route for a new feature, it becomes CLI-reachable
automatically via the dynamic layer — provided the router is **mounted**. The
`generate-cli-command` skill is the "definition of done" checklist; the
`scripts/ci/check_cli_coverage.py` gate fails CI/pre-push if a route module is
defined but never mounted (an unreachable feature). Run
`scripts/ci/gen_cli_reference.py` to refresh the reference after adding commands.
