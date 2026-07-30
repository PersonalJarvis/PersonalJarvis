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
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [tasks, reminders, scheduling, recurring, automation, approvals]
related: [jarvis-agents, workflows-and-commands, safety-and-approvals]
---

Use **Tasks** when one instruction should run later or repeat. The saved card is
the source of truth: saying “remind me” in chat does not create a durable task.

A reminder is not a separate record. A time or mission event starts a task.
Time-based tasks run an isolated Brain turn; there is no alarm-only option.

## Before You Start

- Keep the app running when work is due. Schedules survive a normal restart;
  one overdue occurrence runs when the scheduler returns.
- Under **API Keys & Providers > Brain**, test the provider that should answer.
  The task uses whichever Brain is active when it runs.
- Connect required services under **Skills, Plugins & MCPs** first. Only
  connected plugins are offered.
- Write a self-contained prompt. Tasks do not receive the current chat history.

> [!warning] Never put credentials, recovery codes, or private keys in a task
> name or prompt. Use only the protected connection fields for that service.

## Choose the Right Path

Use **Once** for one later result, **Recurring** for an interval, and
**When-Then** to react to a mission outcome. Use **Agents** for longer reviewed
work with files, or **Workflows** for reusable ordered steps. A task does not
gain an agent workspace or review loop. Agent names follow your configured
assistant name.

## Create a One-Time Task or Reminder

1. Open **Tasks**, then select **New**.
2. Enter a short, recognizable **Name**.
3. Keep **Trigger** on **Schedule**, then select **Once**.
4. Choose **At date/time** or **After delay**. Dates use this device’s local
   time. A past date becomes due immediately.
5. Under **What should it do?**, request one observable result. For a reminder,
   ask for the exact short text to return. Trust the Timeline over speech.
6. Leave plugins off unless the result needs them. Choose a model tier, then
   select **Create task**.
7. Confirm a **scheduled** card and countdown appear.

Chat and voice can list tasks or confirmation-gate cancellation, but cannot
create them. Trust only a saved card.

## Create a Recurring Task

1. Choose **Schedule > Recurring**, then **Hourly**, **Daily**, or **Custom**.
   Custom accepts minutes or hours.
3. Choose an interval longer than a run. The next occurrence is queued when a
   run starts, so slow runs can overlap.
4. Complete the form and create the task.

**Daily** means every 24 hours after the first selected local time, not a
calendar rule. A daylight-saving change can shift later wall-clock times.
There is no pause or edit control; cancel and recreate to make changes.

Failure has no immediate retry. A queued next occurrence can run while the app
stays open, but a failed recurring card is not restored after restart.

## Choose Models and Plugin Permissions

- **Fast** and **Auto** both use the active provider’s fast model.
- **Deep** uses that provider’s deep model when configured, otherwise its fast
  model.
- Scheduled turns do not cross provider families after failure. Test or switch
  the active Brain before the due time.
- A disconnected plugin is skipped. Verify the Timeline rather than assuming
  an action ran.

| Plugin scope | Unattended boundary |
|---|---|
| **Read** | Offers the plugin but pre-approves nothing; confirmation-level calls can time out and fail |
| **Write** or **Full** | Pre-approves confirmation-level calls from that plugin for this task’s current turn |
| Off | Does not offer that plugin to the task |

Write and Full currently have the same effect. They cannot override blocked
actions or grant other plugins. The prompt is the explicit request; a broad
grant alone is not an instruction.

## React to Agent Work with When-Then

Choose **When-Then** to react when a mission succeeds, fails, or is cancelled.
Then choose:

- **Computer-Use** runs the written goal unattended on a compatible graphical
  desktop. Selecting it and creating the rule is the explicit authorization.
- **Agent task** for one isolated Brain turn with the selected plugins.
- **Just notify me** currently ends as failed because fixed speech is not
  connected to this task path.

For Computer-Use or Agent task, **Say when done** requests an announcement.
Mission fields such as `{result_uri}` can be inserted; unknown fields remain.

When-Then is still a preview: the rule can fire for every matching mission in
the current app session, but its card shows **done** after the first match and
is not restored after restart. Delete the done card to stop later matches.

## Review, Stop, Complete, and Delete

Tasks refresh about every three seconds. Filter by **All**, **Active**, **Done**,
or **Problems**. Expand a card for its saved **Spec**, results, and errors.

| State | Meaning | Available action |
|---|---|---|
| **scheduled** | Waiting for a time or event | Review or **Cancel** |
| **running** | The action has started | Review or request **Cancel** |
| **done** | A one-time task succeeded | Review or **Delete** |
| **failed** | The run ended without an automatic retry | Read the error, then delete or recreate |
| **cancelled** | Future scheduling was removed | Review or delete |
| **interrupted** | The app closed during the run | Review, then recreate if still needed |

Completion is automatic; there is no **Mark complete** button. Recurring tasks
return to scheduled. Cancel before deleting an active task. Delete removes its
local card and Timeline with no undo.

Cancel is a signal, not a reversal. Use the Computer-Use emergency stop if
desktop activity continues. A Brain turn may finish after cancellation. No
task control can undo external messages, posts, or file changes.

## How It Fits Together

1. The local scheduler restores saved cards and watches their time or event.
2. At the trigger, the app starts one isolated action with the active Brain and
   only the plugins selected for that task.
3. Safety checks classify each tool call. Read grants leave confirmation gates
   closed; Write or Full can answer them only for the matching plugin and turn.
4. Results and failures return to the Timeline; announcements are additional.
5. Background agents keep longer work, review, and files; Workflows keep
   reusable multi-step definitions.

Task records stay on this device. A remote Brain or plugin may receive the
prompt and relevant data under its service policy.

## Check That It Works

1. Create **Once > After delay** for one minute, with all plugins off.
2. Name it **Schedule check** and request exactly `Scheduled check complete`.
3. Confirm the card changes from **scheduled** to **running** to **done**.
4. Expand it and confirm the Timeline contains the requested result.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| **Create task** is disabled | A name, prompt, action text, or time is missing | Complete every visible required field |
| A plugin is absent | It is disconnected or needs sign-in again | Reconnect it, then reopen the task form |
| A task stays **scheduled** past its time | The app or scheduler was not running | Keep the app open; restart if the whole view remains unavailable |
| A task is **failed** | The Brain, plugin, runner, or requested capability was unavailable | Read the Timeline, test the dependency, then recreate if needed |
| Cancelled desktop work continues | The best-effort Computer-Use stop has not finished | Use the Computer-Use emergency stop, then review the Timeline |

## Next Steps

- Read [Agents and Background Work](jarvis-agents) for reviewed, longer work and
  generated files.
- Use [Workflows and App Commands](workflows-and-commands) for reusable
  multi-step automation.
- Review [Safety and Approvals](safety-and-approvals) before allowing
  unattended external changes.
