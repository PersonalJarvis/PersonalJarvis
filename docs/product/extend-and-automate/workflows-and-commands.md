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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [workflows, app-commands, automation, voice, cli, safety]
related: [tasks-and-reminders, skills, cli-reference, app-command-reference]
---

Use an **app command** when you want Jarvis to perform one known action now,
such as showing your tasks, testing a provider, or changing the voice volume.
The command points to the same validated app action used by the corresponding
desktop control.

Use a **workflow** when you want several fixed steps to run in order, either
when you start them or on a schedule. A workflow keeps its definition and run
history locally, so you can inspect which step completed or failed.

> [!info] Workflows are experimental. The desktop view can run, enable,
> inspect, and delete existing custom workflows, but it cannot yet create or
> edit a workflow. Custom definitions require the CLI or control API.

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
validated app action. A workflow loads a saved definition and runs its steps
sequentially, stopping at the first failure.

## Use an App Command

You normally do not need to know a command ID. Ask for a concrete action in
**Chats** or by voice, and Jarvis can select the matching command, validate its
inputs, call the app, and report the app's actual response.

Current command families include these examples:

| Area | Requests you can make |
|---|---|
| Providers | List or test providers, or switch a supported Brain, voice, speech, Realtime, Computer Use, or Jarvis-Agent provider |
| Voice and settings | Show or change the wake phrase, choose the reply language or voice mode, set volume, list audio devices, or restart Jarvis |
| Work and history | List tasks or missions, read a mission result or recent voice turn, and cancel a task or mission |
| Knowledge and tools | Store a self-contained fact in the Wiki or list the tools currently available to Jarvis |

The list is curated, not a catalog of every button in the app. Browse the
reader-friendly [App Command Reference](app-command-reference), or inspect the
[canonical generated command catalog](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/commands-reference.md)
for the current source-generated list.

The same capability is available through four surfaces:

1. **Chat or voice:** ask naturally. Jarvis runs a matching command only when
   the conversational tool path is available.
2. **Desktop:** use the control in the command's named app section. The
   desktop does not have a separate command-catalog screen.
3. **CLI:** use `jarvis commands list` or `jarvis commands show <command-id>`
   to browse the registry. These two commands are read-only; run an action
   through its feature command or the `jarvis api` interface.
4. **REST:** each command points to one local control route - the validated
   app address used by the other surfaces. Most people do not need to call it
   directly.

A conversational action is complete only after Jarvis receives success from
the app. A spoken intention or ordinary prose answer is not proof that a
setting changed. Consequential commands, including restart and cancellation,
normally require a separate confirmation. A destructive CLI action requires
`--yes`; use `--dry-run` to preview a supported request without sending it.

## Use a Workflow

1. **Open Workflows.** Each card shows its name, Manual or scheduled trigger,
   step count, active state, last result, and next run when one is planned.

2. **Expand the card before running it.** Review its description, step labels,
   and recent runs. Labels are only a summary; for a custom or imported
   workflow, `jarvis workflows show <workflow-id>` is the read-only way to
   inspect the complete saved definition.

3. **Check its requirements.** A Brain prompt needs a reachable Brain
   provider. A local command needs the named program and suitable files. An
   external message needs its connection. Keep the workflow off when any
   requirement is missing.

4. **Select Run now.** A manual run starts immediately and the card refreshes
   as it moves through **pending**, **running**, and then **completed** or
   **failed**. The desktop asks for a URL only when the workflow name contains
   “URL”; it does not provide a general input form for other custom workflows.

5. **Open Recent runs.** Expand a run to see the saved output or error for each
   step. A failure ends the sequence; later steps do not run.

6. **Control future runs.** Turning on a scheduled workflow lets the local
   scheduler start it while Jarvis is running. Turning it off removes its next
   automatic run, but **Run now** still works as a manual test. The toggle does
   not stop a run that already started, and there is currently no active-run
   cancel control.

Seed workflows supplied with Jarvis cannot be deleted from the desktop. A
custom workflow has a confirmed **Delete** action. The command registry does
not currently include a dedicated run-workflow command, so the dependable ways
to start one are the Workflows view or `jarvis workflows run <workflow-id>`.

> [!warning] Treat a workflow definition as trusted automation. Direct local
> command and Telegram steps do not pause for a fresh approval before each
> step. Inputs, outputs, and errors remain in local run history. Never place a
> password, API key, token, or recovery code in a definition or input, and
> review the data boundary before a workflow sends local content to a provider.

Advanced definitions can name a connected-tool step or a Jarvis-Agent step,
but those two step types are not currently wired into the desktop workflow
runner and fail when reached. Use a [Skill](skills) for guided connected-tool
work or a [Jarvis-Agent](jarvis-agents) for longer reviewed work instead. The
shipped **URL Summary** is also an input-passing demonstration: it reasons from
the URL text and does not download or read the page.

## How It Fits Together

1. **A request, button, or schedule starts the path.** Chat and voice can start
   a curated app command. The desktop or CLI can start a workflow. A schedule
   can start an enabled workflow while Jarvis is running.
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
   adds its own confirmation gate for destructive requests. Workflow steps
   have the direct-step limitation described above, so review and activation
   are the important boundary. Read [Safety and Approvals](safety-and-approvals)
   before automating an external change.
5. **Unavailable capabilities fail honestly.** A command returns an error when
   the local app action or required capability is unavailable. A workflow
   records the failing step and stops; it does not skip ahead and claim the
   whole sequence completed.
6. **The result returns to the starting surface.** A command reports the
   server-confirmed outcome in the conversation, desktop, or CLI. A workflow
   stores the overall state and step results under Recent runs.

## Check That It Works

Check a read-only app command first:

1. In Chats or a voice conversation, ask **“What is my wake word?”**
2. Confirm that Jarvis returns the phrase currently shown in the app's wake
   settings. No setting should change and no confirmation should be needed.

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
| Workflows is empty or shows a load error | The local workflow store is still starting or could not open | Wait for app startup, reopen Workflows, and restart normally if the view remains unavailable |
| A run shows **failed** | The first unsuccessful step lacked its Brain, local program, input, file, or external connection | Expand the run, read the exact failed step, fix that requirement in the app, and run a supervised test |
| A schedule never starts | The workflow is off, Jarvis was not running, or no next run could be calculated | Turn it on, confirm a next-run time appears, and keep Jarvis running for the test |
| Jarvis describes an action but nothing changes | The conversational tool path was unavailable or no command actually returned success | Check the relevant desktop setting; retry there or use the documented CLI action instead of trusting the prose reply |
| A consequential command does not run | It is waiting for approval, the confirmation expired, or safety blocked it | Review the proposed action, answer the confirmation clearly once, or leave it cancelled; never weaken a block to force it through |
| A custom workflow needs input but no form appears | The desktop currently has only the URL-name input shortcut | Use a workflow with no input, or use the advanced CLI or API path with the definition's documented inputs |
| A connected-tool or Jarvis-Agent step fails immediately | That step type is defined but is not connected to the desktop workflow runtime | Move the work to a Skill, normal conversation, or Jarvis-Agent mission and keep the workflow disabled |

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
