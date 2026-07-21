---
title: "Workflows and App Commands"
slug: workflows-and-commands
summary: "See how reusable workflows, natural-language actions, REST routes, and CLI commands connect to the same underlying capability."
section: "Extend and automate"
section_order: 5
order: 7
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [workflows, app-commands, automation, voice, cli, safety]
related: [tasks-and-reminders, skills, cli-reference, app-command-reference]
---

Use an **app command** when you want Jarvis to perform one known action now,
such as showing your tasks, testing a provider, or changing the voice volume.
Each command points to an existing app route with a defined input schema and
safety level.

Use a **workflow** when you want several saved steps to run in order, either
manually or on a schedule. Jarvis stores the definition, supplied inputs, step
outputs, and errors in its local workflow database.

App commands and workflows are separate. The Command Registry does not
currently contain a run-workflow command, so voice and chat cannot reliably
start a saved workflow by name.

> [!info]
> Workflows are experimental. The desktop view can inspect, run, enable,
> disable, and delete existing custom workflows. Creating or changing a
> definition still requires the CLI or Control API.

## Choose the Right Building Block

| Building block | What it provides | Choose it when |
|---|---|---|
| **App command** | One curated action with known inputs and one app endpoint | You want a predictable action now from chat, voice, the desktop, or CLI |
| **Workflow** | A named sequence that runs step by step and keeps run history | The order is fixed and you want to reuse or schedule it |
| **Task** | One saved action that starts at a time, interval, or mission event | You need a reminder or one background turn, not a multi-step sequence |
| **Skill** | Reusable instructions that guide how Jarvis handles a request | The method and quality rules matter more than a fixed sequence |
| **Jarvis-Agent** | Isolated, reviewed work with progress and output files | The work is longer, exploratory, or needs several substantial decisions |
| **Connection** | Access to tools from a plugin, MCP server, or command-line app | Jarvis needs a capability; the connection does not define the workflow |

A workflow is not a longer app command. An app command maps one request to one
validated app action. A workflow loads a separate saved definition, runs its
steps sequentially, and stops at the first failure.

## Use an App Command

You normally do not need to know a command ID. Ask for a concrete action in
**Chats** or by voice. When the active Brain supports tools, Jarvis can select
the matching command, validate its inputs, call the app, and report the app's
actual response.

Current command families include these examples:

| Area | Requests you can make |
|---|---|
| Providers | List or test providers, or switch a supported Brain, voice, speech, Realtime, Computer Use, or Jarvis-Agent provider |
| Voice and settings | Show or change the wake phrase, choose the reply language or voice mode, set volume, list audio devices, or restart Jarvis |
| Work and history | List tasks or missions, read a mission result or recent voice turn, and cancel a task or mission |
| Knowledge and tools | Store a self-contained fact in the Wiki or list the tools currently available to Jarvis |

The registry is curated, so it does not contain every app control. Browse the
[App Command Reference](app-command-reference) to understand the fields, or
inspect the
[generated App Command catalog](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/commands-reference.md)
for the list generated from the current public registry source. The live
catalog in your installed version remains authoritative for that installation.

Each registry entry maps the action across four surfaces:

1. **Chat or voice:** Ask naturally. Jarvis runs a matching command only when
   the conversational tool path and the action's dependencies are available.
2. **Desktop:** Use the control in the command's named app section. The
   desktop does not have a separate command-catalog screen.
3. **CLI:** Use `jarvis commands list` or `jarvis commands show <command-id>`
   to browse the registry. These two commands are read-only; run an action
   through its feature command or the `jarvis api` interface.
4. **REST:** Each command points to one local control route, the validated
   app address used by the other surfaces. Most people do not need to call it
   directly.

A conversational action is complete only after Jarvis receives success from
the app. A spoken intention or ordinary prose answer is not proof that a
setting changed. Commands marked **Requires confirmation** wait for a separate
approval turn, and policy can still block an action. Dangerous CLI operations
require `--yes`; use `--dry-run` to preview a supported request without
sending it. A direct REST client does not receive an interactive confirmation
dialog from registry metadata.

## Use a Workflow

1. **Open Workflows.** Each card shows its name, Manual or cron trigger, step
   count, active switch, last-run indicator, and next run when one is planned.

2. **Expand the card before running it.** Review its description, step labels,
   and **Recent runs**. Labels are previews. Use
   `jarvis workflows show <workflow-id>` to inspect the complete saved
   definition and recent run records.

3. **Check its requirements.** A Brain prompt needs a reachable Brain
   provider. A local command needs the named program and suitable files. An
   external message needs its connection. Keep the workflow off when any
   requirement is missing.

4. **Select Run now.** A manual run starts immediately and the card refreshes
   as it moves through **pending**, **running**, and then **completed** or
   **failed**. The desktop asks for a URL only when the workflow name contains
   `URL`; it does not provide a general input form for other custom workflows.

5. **Open Recent runs.** Expand a run to see the saved output or error for each
   step. A failure ends the sequence; later steps do not run.

6. **Control future runs.** Enable a cron workflow to let the local scheduler
   start it while Jarvis is running. Schedules currently use the Jarvis host's
   local time, and the next-run value can take up to one minute to appear.
   Disable it to clear the next automatic run. **Run now** still works for a
   disabled workflow.

The switch does not stop a run that already started, and there is no active-run
cancel control. Custom workflows have a confirmed **Delete** action. Deleting
one also deletes its saved run history. Seed workflows cannot be deleted in
the desktop; deleting one through the CLI or API only removes it until the next
startup seeds it again.

### Manage Definitions from the CLI or API

| Action | Current supported path | Important limit |
|---|---|---|
| List or inspect | `jarvis workflows list` and `jarvis workflows show <workflow-id>` | The desktop shows only step previews and its most recent runs |
| Create | `jarvis workflows create --def <json>` or `POST /api/workflows` | The desktop has no definition editor |
| Edit | Submit a complete definition with the same ID through the create route | `PATCH /api/workflows/{workflow_id}` changes only `enabled` |
| Run | **Run now**, `jarvis workflows run <workflow-id>`, or `POST /api/workflows/{workflow_id}/run` | The curated CLI sends an empty input object |
| Schedule | Save a five-field cron trigger, then enable the workflow | Only manual and cron triggers are supported |
| Delete | Desktop **Delete**, `jarvis workflows delete <workflow-id> --yes`, or the REST delete route | Deletion removes the definition and its run history |

Every workflow route also appears under `jarvis api workflows`. The dynamic
run operation accepts an input object through `--json-body`, as does the REST
run route. The desktop asks for a `url` input only when the workflow name
contains `URL`; it has no general input form.

### Know Which Steps Work

| Step type | What it does | Current requirement or limit |
|---|---|---|
| Brain prompt | Sends text to the active Brain and saves its reply | A reachable Brain path is required |
| Speak | Publishes text to the voice output path | The step records success even when no text-to-speech output is available |
| Local command | Starts one local executable and saves standard output | The program, working directory, and files must exist on the Jarvis host; shell pipes are not interpreted automatically |
| Telegram message | Sends text through the Telegram Bot API | A configured bot credential and chat ID are required |
| Tool call | Requests a named registered tool through the safety executor | The current app bootstrap does not attach the required executor, so this step fails when reached |
| Harness dispatch | Sends a prompt to a named harness, including Jarvis-Agent definitions | The current app bootstrap does not attach a harness manager, so this step fails when reached |

Text fields can use `{{prev.output}}`, `{{step_N.output}}`, and
`{{input.key}}`. String values in tool arguments support the same substitution.
Workflows do not currently branch, retry a failed step, or run steps in
parallel.

> [!warning]
> Treat a workflow definition as trusted automation. Starting a workflow does
> not add a fresh approval before each local command or Telegram step. Never
> place a password, API key, token, or recovery code in a definition or input.
> Review what may leave the computer before enabling a schedule.

The shipped **URL Summary** demonstrates input substitution only. It reasons
from the URL text and does not download or read the page.

## How It Fits Together

1. **A request, button, or schedule starts the path.** Chat and voice can start
   a curated app command. The desktop, CLI, or REST API can start a workflow.
   A cron schedule can start an enabled workflow while Jarvis is running.
2. **Jarvis chooses one action or one definition.** The command registry maps
   one command to one existing app route and its accepted inputs. A workflow
   instead loads its saved steps and processes them in order.
3. **Adjacent features supply context or capability.** A Skill supplies
   repeatable instructions; a plugin, MCP connection, or CLI connection
   supplies a tool; a Task supplies a time or event trigger; a Jarvis-Agent
   supplies isolated work and review. None of them silently grants another
   feature access.
4. **Safety applies at the action boundary.** Conversational commands run
   through Jarvis's safety policy before their app route is called, and the CLI
   adds its own confirmation gate for destructive requests. Direct workflow
   steps have the limitation described above, so definition review and
   activation are the safety boundary. Read
   [Safety and Approvals](safety-and-approvals) before automating an external
   change.
5. **Unavailable capabilities fail clearly.** A command returns an error when
   the app action or required capability is unavailable. A Brain step uses the
   active provider path and its configured fallbacks. If no provider can
   answer, the step fails. A workflow records the failing step and stops.
6. **The result returns to the starting surface.** A command reports the
   server-confirmed outcome in the conversation, desktop, or CLI. A workflow
   stores the overall state and step results under Recent runs.

## Check That It Works

Check a read-only app command first:

1. Run `jarvis commands show wake-word-get`.
2. Confirm that the result identifies `GET /api/settings/wake-word` and marks
   the command as non-dangerous. No setting changes.

Then check the workflow path:

1. Open **Workflows**, expand **URL Summary**, and select **Run now**.
2. Enter `https://example.com`, which is a reserved example address.
3. Expand the newest run. Success is a **completed** state with output on its
   first step. The result should describe what can be inferred from the URL;
   it is not evidence that Jarvis read the web page.

This verifies the registry read path, manual workflow trigger, Brain step, and
saved run timeline without changing an external account.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Workflows is empty or shows a load error | The local workflow store is still starting or could not open | Wait for app startup, reopen **Workflows**, and restart normally if the view remains unavailable |
| A run shows **failed** | The first unsuccessful step lacked its Brain, local program, input, file, connection, or runtime dependency | Expand **Recent runs**, read the exact failed step, fix that requirement, and run a supervised test |
| A schedule never starts | The workflow is disabled, Jarvis was not running, the cron expression is invalid, or the host time differs from what you expected | Enable it, wait up to one minute for **Next run**, and verify the Jarvis host's local clock |
| A custom workflow needs input but no form appears | The desktop supports only the workflow-name-based URL shortcut | Use the dynamic CLI or REST run route with an input object, then inspect the saved run input and result |
| Jarvis describes an app action or workflow run but nothing changes | The conversational tool path was unavailable, no app command returned success, or workflows are not exposed as conversational commands | Check the target state in the desktop, then use the documented CLI or desktop control |

For repeated provider, connection, or startup failures, follow the main
[Troubleshooting](troubleshooting) guide.

## Next Steps

- Read [Tasks and Reminders](tasks-and-reminders) when one saved action should
  start at a time, interval, or mission event.
- Read [Skills](skills) when you want repeatable instructions that can choose
  from currently connected capabilities.
- Use the [CLI Reference](cli-reference) to browse safe workflow and feature
  commands without copying long command lists into this page.
- Open the [App Command Reference](app-command-reference) for the generated,
  current catalog and each command's accepted inputs.
