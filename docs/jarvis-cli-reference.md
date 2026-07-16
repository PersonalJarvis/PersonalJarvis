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

## clis

- `jarvis clis check <name> --yes --dry-run` — Probe a CLI's binary + auth (refreshes its status).
- `jarvis clis connect <name> --json-body --yes --dry-run` — Connect a CLI's auth (oauth_cli flow or api_key).
- `jarvis clis disconnect <name> --yes --dry-run` — Remove a CLI's stored auth credentials.
- `jarvis clis install <name> --method --yes --dry-run` — Start an install job for a CLI (output streams in the desktop view).
- `jarvis clis list` — List all CLIs with status (connected, installed, version, 7-day usage).
- `jarvis clis show <name>` — Show one CLI (homepage, install methods, auth mode, secrets set).
- `jarvis clis usage <name>` — Show a CLI's recent usage history.
- `jarvis clis usage-stats <name>` — Show a CLI's aggregated usage stats (success rate, avg duration, top commands).

## commands

- `jarvis commands list` — List every registry command (id, endpoint, params, danger, UI section).
- `jarvis commands show <command_id>` — Show one command's full definition (params schema, voice aliases).

## computer-use

- `jarvis computer-use cancel <mission_id> --yes --dry-run` — Cancel one active run.
- `jarvis computer-use cancel-all --yes --dry-run` — Cancel every active run (queued and running).
- `jarvis computer-use list --limit` — List active and recent Computer-Use runs.
- `jarvis computer-use show <mission_id>` — Show one run: status, goal, exit code, final output.
- `jarvis computer-use start <goal> --timeout-s --yes --dry-run` — Start a desktop goal in the background; prints the mission id.

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

## friends

- `jarvis friends add --json-body --yes --dry-run` — Add a friend.
- `jarvis friends delete <friend_id> --yes --dry-run` — Delete a friend and their channels.
- `jarvis friends edit <friend_id> --json-body --yes --dry-run` — Edit a friend (partial).
- `jarvis friends list` — List friends with their channels.
- `jarvis friends message <friend_id> --text --yes --dry-run` — Send an outbound message to a friend (consequential — needs --yes).
- `jarvis friends messages <friend_id>` — Show the message thread with a friend.
- `jarvis friends show <friend_id>` — Show one friend (detail + channels + permission profile).

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

- `jarvis missions approve-tool <mission_id> <trace_id> --yes --dry-run` — Approve one paused mission tool call and resume it.
- `jarvis missions cancel <mission_id> --yes --dry-run` — Cancel a running mission (kills its worker).
- `jarvis missions deny-tool <mission_id> <trace_id> --reason --dry-run` — Deny one paused mission tool call without executing it.
- `jarvis missions dispatch <prompt> --language --confirmed --yes --dry-run` — Dispatch a new self-healing mission — spawns a worker (destructive: --yes).
- `jarvis missions kill <worker_id> --yes --dry-run` — Hard-kill a worker process by id.
- `jarvis missions list --state --limit` — List missions (optionally filtered by state).
- `jarvis missions rerun <mission_id> --confirmed --yes --dry-run` — Re-dispatch a terminal mission's prompt as a new linked mission.
- `jarvis missions result <mission_id>` — Read a mission's signed outcome and actual deliverable contents.
- `jarvis missions show <mission_id>` — Show one mission with its events + verdicts.
- `jarvis missions tool-approvals <mission_id>` — List supervisor tool calls waiting for approval in a mission.

## outputs

- `jarvis outputs files <slug>` — List the artifacts a mission produced.
- `jarvis outputs list` — List output sessions (a mission's deliverable folders).
- `jarvis outputs open-with <slug> <path> --opener --yes --dry-run` — Open an artifact with a chosen editor (desktop only).
- `jarvis outputs openers` — List installed editors/apps that can open an artifact.
- `jarvis outputs plan <slug>` — Show a session's plan + steps.
- `jarvis outputs preferred-opener <opener> --yes --dry-run` — Get or set the default artifact opener.

## permissions

- `jarvis permissions open-settings <permission_id> --yes --dry-run` — Open the matching macOS privacy pane through LaunchServices.
- `jarvis permissions request <permission_id> --yes --dry-run` — Show the native macOS prompt for one permission.
- `jarvis permissions status` — Show permission and feature readiness without caching native state.

## refresh

- `jarvis refresh` — Clear the cached API schema (next call re-fetches it).

## sessions

- `jarvis sessions delete <session_id> --yes --dry-run` — Delete a text conversation thread.
- `jarvis sessions latest-turn --session-id` — Show the latest persisted user transcript and its complete turn.
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

## socials

- `jarvis socials add --json-body --yes --dry-run` — Add a social link.
- `jarvis socials delete <social_id> --yes --dry-run` — Delete a social link.
- `jarvis socials edit <social_id> --json-body --yes --dry-run` — Edit a social link (partial).
- `jarvis socials list` — List social links.

## system

- `jarvis system audio-devices --output --input` — List audio devices, or pick where the voice plays / which mic listens.
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

- `jarvis wiki backfill --days --max-sessions --preview --yes --dry-run` — Backfill recent Realtime sessions through evidence-safe Wiki capture.
- `jarvis wiki health` — Show wiki subsystem health: bootstrap, last write, chain failures, backlog (spec A5).
- `jarvis wiki ingest <text> --source --dry-run` — Store a fact through the guarded Wiki curator.
- `jarvis wiki page <slug>` — Read a wiki page by vault path / slug.
- `jarvis wiki recall <query>` — Full-text search the wiki.
- `jarvis wiki reindex --preview` — Rebuild the wiki search index from the active vault.
- `jarvis wiki tree` — Show the vault folder tree + stats.
- `jarvis wiki vaults` — List the user's registered Obsidian vaults (connect picker, spec A6).

## workflows

- `jarvis workflows create --def --yes --dry-run` — Create a workflow from a WorkflowDef JSON document.
- `jarvis workflows delete <workflow_id> --yes --dry-run` — Delete a workflow.
- `jarvis workflows list` — List workflows + a run summary.
- `jarvis workflows run <workflow_id> --yes --dry-run` — Trigger a workflow run.
- `jarvis workflows run-history --workflow-id` — List workflow runs (optionally for one workflow).
- `jarvis workflows show <workflow_id>` — Show one workflow + recent runs.

