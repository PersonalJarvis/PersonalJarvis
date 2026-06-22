# Jarvis CLI — Command Reference

_Generated from the curated command tree by `scripts/ci/gen_cli_reference.py` — do not edit by hand. Every mounted REST endpoint is additionally reachable via `jarvis api <tag> <op>`._

## auth

- `jarvis auth login --url --key` — Verify the key against the server and persist it for future calls.
- `jarvis auth logout` — Forget the saved credentials.
- `jarvis auth status --url --key` — Report whether the configured (or given) target is reachable.

## board

- `jarvis board achievements` — Show unlocked + locked achievements.
- `jarvis board bio` — Show the AI-generated bio.
- `jarvis board bio-regenerate --yes --dry-run` — Regenerate the AI bio.
- `jarvis board heatmap --days` — Show the activity heatmap cells.
- `jarvis board profile` — Show the profile (meta + people + review count).
- `jarvis board records` — Show personal records.
- `jarvis board summary --window-days` — Show personal totals + streaks over a window.

## brain

- `jarvis brain deep-model <model> --persist --yes --dry-run` — Set the sub-agent deep model.
- `jarvis brain list` — List configured brain providers (alias of status).
- `jarvis brain status` — Show configured providers and which one is active.
- `jarvis brain subagent-switch <provider> --persist --yes --dry-run` — Switch the sub-agent / worker provider (e.g. Codex -> OpenAI).
- `jarvis brain switch <provider> --persist --yes --dry-run` — Switch the ACTIVE main brain provider (e.g. `jarvis brain switch openai`).
- `jarvis brain test <provider> --dry-run` — Test connectivity + auth for a provider.

## conductor

- `jarvis conductor add --def --yes --dry-run` — Add a job from a Job JSON document.
- `jarvis conductor delete <job_id> --yes --dry-run` — Delete a job.
- `jarvis conductor list` — List Conductor jobs + a run summary.
- `jarvis conductor run <job_id> --yes --dry-run` — Manually trigger a job run.
- `jarvis conductor show <job_id>` — Show one job + recent runs.
- `jarvis conductor toggle <job_id> --enabled --yes --dry-run` — Enable or disable a job.

## config

- `jarvis config get <path>` — Get a config value by dotted path (control-key gated).
- `jarvis config language get` — Show the current reply language + the available options.
- `jarvis config language set <lang> --persist --yes --dry-run` — Set the reply language (hot-reloads, no restart).
- `jarvis config list` — List the mutable-settings allowlist (path, risk tier, restart needed).
- `jarvis config set <path> <value> --reason --yes --dry-run` — Set a mutable config value via the atomic write pipeline (destructive: --yes).

## contacts

- `jarvis contacts add --json-body --yes --dry-run` — Add a contact.
- `jarvis contacts delete <slug> --yes --dry-run` — Delete a contact.
- `jarvis contacts edit <slug> --json-body --yes --dry-run` — Edit a contact (partial).
- `jarvis contacts list` — List contacts.
- `jarvis contacts show <slug>` — Show one contact.

## docs

- `jarvis docs list` — List documentation pages.
- `jarvis docs search <query>` — Search the docs.
- `jarvis docs show <slug>` — Show one doc page's body.
- `jarvis docs tree` — Show the Diataxis-grouped doc tree.

## frontier

- `jarvis frontier ack --yes --dry-run` — Acknowledge (dismiss) the pending frontier proposals.
- `jarvis frontier pending` — List proposed model upgrades awaiting acknowledgement.

## marketplace

- `jarvis marketplace connect-pat <plugin_id> --token --yes --dry-run` — Connect a plugin with a personal access token.
- `jarvis marketplace connect-poll <plugin_id> <flow_id>` — Poll an in-progress OAuth connect flow.
- `jarvis marketplace connect-start <plugin_id> --yes --dry-run` — Begin an OAuth connect flow (prints the redirect URI + flow id).
- `jarvis marketplace disconnect <plugin_id> --yes --dry-run` — Disconnect a plugin.
- `jarvis marketplace list` — List marketplace plugins + their connection status.

## mcps

- `jarvis mcps check <name> --yes --dry-run` — Health-check an MCP server (lists its tools).
- `jarvis mcps delete <name> --yes --dry-run` — Remove an MCP server.
- `jarvis mcps disable <name> --yes --dry-run` — Disable an MCP server.
- `jarvis mcps enable <name> --yes --dry-run` — Enable an MCP server.
- `jarvis mcps import-claude-desktop --yes --dry-run` — Import MCP servers from the Claude Desktop config.
- `jarvis mcps list` — List MCP servers + a summary.

## missions

- `jarvis missions cancel <mission_id> --yes --dry-run` — Cancel a running mission (kills its worker).
- `jarvis missions dispatch <prompt> --language --confirmed --yes --dry-run` — Dispatch a new self-healing mission — spawns a worker (destructive: --yes).
- `jarvis missions kill <worker_id> --yes --dry-run` — Hard-kill a worker process by id.
- `jarvis missions list --state --limit` — List missions (optionally filtered by state).
- `jarvis missions rerun <mission_id> --confirmed --yes --dry-run` — Re-dispatch a terminal mission's prompt as a new linked mission.
- `jarvis missions show <mission_id>` — Show one mission with its events + verdicts.

## outputs

- `jarvis outputs files <slug>` — List the artifacts a mission produced.
- `jarvis outputs list` — List output sessions (a mission's deliverable folders).
- `jarvis outputs open-with <slug> <path> --opener --yes --dry-run` — Open an artifact with a chosen editor (desktop only).
- `jarvis outputs openers` — List installed editors/apps that can open an artifact.
- `jarvis outputs plan <slug>` — Show a session's plan + steps.
- `jarvis outputs preferred-opener <opener> --yes --dry-run` — Get or set the default artifact opener.

## refresh

- `jarvis refresh` — Clear the cached API schema (next call re-fetches it).

## sessions

- `jarvis sessions delete <session_id> --yes --dry-run` — Delete a text conversation thread.
- `jarvis sessions list --days --limit` — List text + voice sessions, newest first.
- `jarvis sessions resume <kind> <session_id> --yes --dry-run` — Seed the brain from a past conversation to continue it in text.
- `jarvis sessions show <kind> <session_id>` — Show one conversation with its messages.
- `jarvis sessions speak <kind> <session_id> --yes --dry-run` — Start a voice session seeded from a past conversation (503 on headless).

## skills

- `jarvis skills catalog-install <name> --source-url --title --raw-url --yes --dry-run` — Install a skill from the catalog (lands as a draft).
- `jarvis skills catalog-search <query>` — Search the installable skill catalog.
- `jarvis skills commit --draft --yes --dry-run` — Commit a generated draft to disk (still state=draft until enabled).
- `jarvis skills disable <name> --yes --dry-run` — Deactivate a skill.
- `jarvis skills draft <intent> --name-hint --category --yes --dry-run` — Generate a skill draft from an intent (AI author; lands as state=draft).
- `jarvis skills enable <name> --yes --dry-run` — Activate a skill.
- `jarvis skills list` — List all discovered skills.
- `jarvis skills reload --yes --dry-run` — Re-scan the skills directory.
- `jarvis skills show <name>` — Show one skill's detail.

## system

- `jarvis system restart --force --yes --dry-run` — Cleanly restart the desktop app (POST /api/settings/restart-app).
- `jarvis system status` — Report server reachability + version (GET /api/control/auth/probe).

## tasks

- `jarvis tasks cancel <task_id> --yes --dry-run` — Soft-cancel a scheduled/running task.
- `jarvis tasks create --json-body --yes --dry-run` — Create + schedule a task from a TaskSpec JSON document.
- `jarvis tasks delete <task_id> --yes --dry-run` — Hard-delete a task (terminal states only, server-enforced).
- `jarvis tasks get <task_id>` — Show one task with its step timeline.
- `jarvis tasks list --state --limit` — List tasks (optionally filtered by state).

## telephony

- `jarvis telephony config` — Show the telephony config.
- `jarvis telephony outbound <to> --message --yes --dry-run` — Place a real outbound call (destructive: costs money; needs --yes).
- `jarvis telephony status` — Report telephony availability/status.

## version

- `jarvis version` — Print the jarvisctl version.

## wiki

- `jarvis wiki page <slug>` — Read a wiki page by vault path / slug.
- `jarvis wiki recall <query>` — Full-text search the wiki.
- `jarvis wiki tree` — Show the vault folder tree + stats.

## workflows

- `jarvis workflows create --def --yes --dry-run` — Create a workflow from a WorkflowDef JSON document.
- `jarvis workflows delete <workflow_id> --yes --dry-run` — Delete a workflow.
- `jarvis workflows list` — List workflows + a run summary.
- `jarvis workflows run <workflow_id> --yes --dry-run` — Trigger a workflow run.
- `jarvis workflows run-history --workflow-id` — List workflow runs (optionally for one workflow).
- `jarvis workflows show <workflow_id>` — Show one workflow + recent runs.

