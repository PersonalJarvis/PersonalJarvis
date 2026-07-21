---
title: "Tasks and Reminders"
slug: tasks-and-reminders
summary: "Create, review, approve, and complete tasks, including scheduled and recurring work."
section: "Everyday use"
section_order: 2
order: 3
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [tasks, reminders, scheduling, recurring, automation, approvals]
related: [jarvis-agents, workflows-and-commands, safety-and-approvals]
---

Use **Tasks** to ask Jarvis to do one piece of work later, repeat it on a
schedule, or react when a Jarvis-Agent mission ends. The task stays in a local
queue, so you can follow its status without keeping a chat open.

A reminder is not a separate kind of record in the current app. It is the time
or event that starts a task. The task describes what Jarvis should do when that
moment arrives.

## Choose the Right Kind of Automation

| Concept | Use it for | What Jarvis keeps |
|---|---|---|
| **Task** | One saved action with one starting condition | A card, its current state, and a step-by-step timeline |
| **Reminder or schedule** | Deciding when a task starts, once or repeatedly | The next due time or the event the task is waiting for |
| **Mission** | Longer work handled by a Jarvis-Agent and reviewed before completion | Worker progress, review state, results, and output files |
| **Workflow** | A reusable sequence with several ordered steps | The workflow definition plus a history for each run |

A scheduled task runs one isolated assistant turn; it does not automatically
become a mission or gain a mission's review loop. A workflow is broader: it can
connect several prompts, tools, or announcements in a fixed order.

## Before You Start

- Keep the Jarvis app running when the task is due. A waiting task is stored
  locally and restored after a normal restart. An overdue task can run when the
  scheduler starts again. Jarvis runs one overdue occurrence rather than every
  missed interval. Work that was running when the app closed is marked
  **interrupted** instead of being repeated automatically.
- For a task that needs an assistant response, open **API Keys** and confirm
  that the Brain provider you want to use shows **Works**. The task uses the
  provider that is active when it runs, not necessarily the one that was active
  when you created it.
- Connect any service you want the task to use under **Skills, Plugins & MCPs**.
  Only plugins that currently show **connected** appear in the task form.

> [!warning] Do not put passwords, API keys, access tokens, recovery codes, or
> private account data in a task name or prompt. Enter credentials only through
> the protected connection fields in the app.

## Create a Scheduled Task

1. **Open Tasks.** Select **Tasks** in the sidebar, then select **New**. The
   **New scheduled task** window opens.

2. **Name the task.** Enter a short name that will still make sense in the task
   list later.

3. **Choose Schedule.** Under **Trigger**, select **Schedule**. Choose **Once**
   for one run or **Recurring** for repeated work.

4. **Set the time.** A one-time task can run **At date/time** or **After delay**.
   A recurring task can run **Hourly**, **Daily**, or at a **Custom** interval.
   The card shows a countdown after you create it.

   **Daily** starts at the next selected local time and then repeats every 24
   hours. It is an interval, not a calendar rule, so daylight-saving changes can
   move the displayed wall-clock time for later runs. Jarvis schedules the next
   interval when a run starts. If a run lasts longer than its interval, the next
   run can overlap it.

5. **Describe the result.** In **What should it do?**, state one clear outcome.
   Choose **Fast**, **Auto**, or **Deep** under **Model**. Fast and Auto both use
   the active provider's fast model. Deep uses that provider's deep model when
   one is configured and otherwise uses its fast model.

6. **Allow only the plugins it needs.** Turn on a connected plugin, then choose
   its scope. Leave every unrelated plugin off.

7. **Create the task.** Select **Create task**. The window closes and a card
   appears with a **scheduled** badge and its next due time.

After a one-time run succeeds, the badge changes to **done**. After a recurring
run succeeds, the same card returns to **scheduled** with its next due time.
Failed runs are not retried automatically.

## React to a Mission with When-Then

Select **When-Then** under **Trigger** when the action should start after a
Jarvis-Agent mission **succeeds**, **fails**, or **is cancelled**. You can then
choose an **Agent task**, **Computer-Use**, or **Just notify me** action. The
optional **Say when done** field publishes a completion announcement after an
Agent task or Computer-Use action succeeds.

The form also supports the mission replacement fields it displays, such as
`{result_uri}`, `{summary_en}`, and `{mission_id}`. Jarvis replaces a recognized
field with the matching value from the finished mission. An unrecognized field
stays visible instead of stopping the task.

| Action | Current behavior |
|---|---|
| **Agent task** | Runs one isolated Brain turn with the selected model tier and plugin grants. |
| **Computer-Use** | Runs through the Computer-Use harness. It needs a compatible graphical desktop and can fail when that capability is unavailable. |
| **Just notify me** | Currently ends as **failed**. The form creates a direct speech action, but the production task runner does not have a text-to-speech connection. |

> [!info] **When-Then is still a preview.** A standing rule can react to later
> matching missions during the current app session, but its card changes to
> **done** after the first match. It is not restored after an app restart.
> Delete the card if you want to stop the rule before then.

## Choose Plugin Permissions

The scope you choose applies only to that task and that connected plugin. It
does not grant a general permission to every task.

| Scope | What it allows during an unattended run |
|---|---|
| **Read** | Makes the plugin available but pre-approves nothing. An action that needs confirmation can time out or fail while nobody is present. |
| **Write** or **Full** | Makes the plugin available and pre-approves confirmation-level actions from it for this task. It may send, post, or change data without asking at run time. |
| Plugin off | Keeps that plugin out of the task's available tools. |

Choose the narrowest scope that can finish the job. The app warns you when a
Write or Full grant can perform an external action unattended.

These are approval scopes, not separate tool catalogs. **Read** does not remove
write-capable methods from a plugin; the normal safety classification decides
whether a method needs confirmation. The current runner treats **Write** and
**Full** the same for unattended approval. A blocked action remains blocked.

## Review and Manage Tasks

Use **All**, **Active**, **Done**, and **Problems** to filter the list. The view
refreshes automatically every three seconds.

Expand a card to see its saved setup and **Timeline**. The timeline records the
action, result, and failure details. An assistant result remains there even
when audio output is muted or unavailable.

| State shown in Tasks | What it means | What you can do |
|---|---|---|
| **waiting** | The task is saved but has not entered the scheduler; tasks created in the app normally skip this state | Wait for startup to finish and let the list refresh |
| **scheduled** | It is waiting for a time, interval, or event | Expand it or select **Cancel** |
| **running** | Jarvis has started the current action | Watch the Timeline or select **Cancel** to signal the running action |
| **done** | A one-time run succeeded, or a When-Then rule matched at least once | Review the Timeline; note the When-Then preview limit above |
| **failed** | The current run could not finish and will not retry automatically | Read **Last error** and the Timeline before deleting or recreating it |
| **cancelled** | Future scheduling was removed and any current run received a cancellation request | Review or delete the record |
| **interrupted** | The app closed while the task was running | Review the partial timeline, then create a new task if the action is still needed |

**Cancel** removes waiting work and signals the current run. For a running
Computer-Use action, Jarvis also tries to stop the harness itself. That stop is
best effort, so use the Computer-Use emergency stop if desktop activity
continues. Other actions may not stop until their current operation finishes.
**Delete** becomes available only in a final state and permanently removes the
task and its Timeline. There is no undo.

## How It Fits Together

1. **A trigger starts the task.** The local scheduler watches the selected time
   or a mission-completion event. Waiting tasks survive a normal restart.
2. **Jarvis loads one saved action.** A scheduled prompt runs as an isolated
   Brain turn, without borrowing the current chat history. A When-Then rule can
   instead receive details from the mission that triggered it.
3. **The current Brain and plugins do the work.** The Brain provider that is
   active at run time handles the turn. Only plugins enabled for the task are
   offered. A disconnected or unavailable plugin is skipped, so the task may
   produce a limited answer or fail its intended goal.
4. **Safety rules still apply.** Read access does not pre-approve a risky
   change. Write or Full grants answer the confirmation gate only for the
   matching task and plugin; an action that Jarvis blocks is never approved by
   the schedule.
5. **The result returns to Tasks.** The badge and Timeline show the outcome. A
   finished assistant result can also be announced when the speech system is
   available; on a muted or headless system, use the Timeline as the reliable
   result.

Scheduling and local storage do not require a particular cloud provider or
desktop operating system. The action itself might. A Brain task currently uses
the provider that is active at run time and does not automatically switch to
another provider family when it fails. Computer-Use also needs a compatible
graphical desktop. **Just notify me** currently lacks its production speech
connection. When a required runner or capability is unavailable, Jarvis records
**failed** rather than claiming the task completed.

A recurring task is re-armed in memory when it starts. If that run fails, the
card shows **failed**, but the next interval can still run while the app stays
open. If the app restarts while the card is **failed**, that recurring task is
not restored. Delete or recreate the task instead of relying on a retry.

Read [Jarvis-Agents](jarvis-agents) for the longer mission path and
[Workflows and App Commands](workflows-and-commands) for reusable multi-step
automation.

## Check That It Works

1. Create a **Once** task with **After delay** set to one minute.
2. Use a neutral name such as **Schedule check** and ask for the exact short
   result **Scheduled check complete**. Leave all plugins off.
3. Select **Create task** and confirm that its card shows **scheduled** with a
   countdown.
4. Wait for the card to show **running**, then **done**. Expand it and confirm
   that the Timeline contains the assistant result.

The scheduler and Brain path work when the state changes are visible and the
requested result appears in the Timeline. Spoken playback is an additional
delivery path, not the success record.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Create task** is disabled or saving fails | A required name, prompt, action text, or date is empty or invalid | Complete every visible required field and choose a valid date or positive delay; a past date becomes due immediately |
| A plugin is missing from the form | It is not connected, needs you to sign in again, or is not callable | Open **Skills, Plugins & MCPs**, reconnect it, then reopen the task form |
| A card stays **scheduled** after its due time | The app or task service was not running, or startup is still restoring the queue | Keep the app open, wait for startup to finish, and let the list refresh; restart normally if the whole Tasks view remains unavailable |
| The task shows **failed**, or it finishes without a spoken result | The Brain, plugin, audio path, runner, or requested capability was unavailable | Read the Timeline first. **Just notify me** is a known runner limitation; for other actions, test the affected connection and recreate the task |
| A cancelled task's desktop action continues | The best-effort harness stop did not finish the active Computer-Use loop | Use the Computer-Use emergency stop, then review the Timeline before deleting the task |

## Next Steps

- Read [Jarvis-Agents](jarvis-agents) when the work needs a background worker,
  progress tracking, review, or output files.
- Read [Workflows and App Commands](workflows-and-commands) when you need a
  named automation with several ordered steps instead of one task action.
- Review [Safety and Approvals](safety-and-approvals) before allowing a task to
  send, publish, or change external data without a live confirmation.
