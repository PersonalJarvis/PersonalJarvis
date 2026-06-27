---
name: drive-jarvis-cli
description: >-
  Use when you (a Claude Code / Codex session, or any terminal agent) need to
  control a running Personal Jarvis instance — switch the brain provider, dispatch
  or inspect missions, read/write config, search the wiki, manage skills, browse
  outputs, etc. Teaches the `jarvis` CLI: zero-config local auth, machine-readable
  output, and the safety flags. Trigger phrases: "control Jarvis", "drive Jarvis
  from the CLI", "switch Jarvis's brain provider", "dispatch a Jarvis mission".
---

# Drive Jarvis from the CLI

Jarvis exposes its entire WebUI action surface as a command-line tool, the same
way `gcloud` / `aws` exposes a cloud platform. You drive a **running** Jarvis over
HTTP — no need to be inside the app.

## Setup (one time)

The CLI ships with the package, so after `pip install -e .` the `jarvis` binary is
on PATH (aliases: `jarvisctl`, `jctl`). Check it:

```bash
jarvis version
```

**Auth is zero-config on the same machine as a running Jarvis.** The desktop app
writes its live port + control token to a single-instance session file; the CLI
discovers it automatically, and falls back to the machine's control key. You do
not need to log in. Confirm reachability:

```bash
jarvis system status        # {"reachable": true} when Jarvis is running
```

For a **remote** Jarvis (e.g. a VPS), tunnel it to loopback and point the CLI at it:

```bash
ssh -L 47821:127.0.0.1:47821 <host>          # then it behaves like local
# or, explicitly, per call:
jarvis --url http://127.0.0.1:47821 --key "$JCTL" missions list
```

## How to use it (agent ergonomics)

- **Discover commands**: `jarvis --help`, `jarvis <group> --help`. Every mounted
  REST endpoint is also reachable generically via `jarvis api <tag> <op> --help`.
- **Machine-readable output**: put `--json` *before* the group, e.g.
  `jarvis --json missions list`. Parse that, not the human table.
- **Preview before acting**: `--dry-run` on any command prints the exact request
  (method, path, body, whether auth is attached) and sends nothing.
- **Authorize destructive actions**: reversible actions (switch provider, set
  language, enable a skill) just run. Destructive ones (any `DELETE`, `config
  set`, `missions dispatch`, `telephony outbound`) refuse unless you pass `--yes`
  (or set `JARVIS_CLI_ASSUME_YES=1`). This is deliberate — only add `--yes` when
  you mean it.
- **Never** pass a secret as an inline argument; `auth login` and PAT connects
  read it from a hidden prompt or stdin (`--key -`).

## Common tasks

```bash
# Switch the active brain provider (the flagship; reversible)
jarvis brain switch openai
jarvis brain subagent-switch openai          # switch the worker/sub-agent provider
jarvis --json brain status                   # which provider is active

# Config (atomic write pipeline; `set` is destructive)
jarvis --json config get brain.primary
jarvis config set ui.theme dark --yes
jarvis config language set en

# Missions (dispatch is destructive — spawns a worker)
jarvis --json missions list --state RUNNING
jarvis missions dispatch "summarize today's PRs" --yes
jarvis --json missions show <id>

# Knowledge + history + outputs
jarvis --json wiki recall "melbourne visa"
jarvis --json sessions list
jarvis --json outputs list

# Skills, board, workflows, mcps, docs, … (every domain has a group)
jarvis --json skills list
jarvis --json board summary
jarvis api --help                            # the full auto-generated surface
```

## Rules

- Read with `--json` and parse; act with explicit intent.
- `--dry-run` first when unsure what a destructive command will send.
- If `jarvis system status` reports unreachable, Jarvis is not running — start it
  (`jarvis serve` for headless) rather than retrying blindly.
