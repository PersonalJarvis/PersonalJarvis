---
title: "Automations"
slug: automations
summary: "Browse automation templates, control recurring work, and inspect schedules and run history."
section: "Everyday use"
section_order: 2
order: 9
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-10-01
phase: "-"
audience: end-user
tags: [automations, templates, schedules, tasks]
related: [tasks-and-reminders, costs, workflows-and-commands, safety-and-approvals]
---

The **Automations** section brings together reusable recurring work, upcoming
one-off schedules, completed runs, and a catalogue of templates. Depending on
your navigation layout, scheduled work may appear under **Scheduled**.

Use this section to answer three questions: what is enabled, what is due next,
and what actually happened on the last run.

## Choose the Right Starting Point

| Your goal | Start here |
|---|---|
| Reuse an available pre-built routine | Catalogue |
| Create your own recurring instruction | New automation or the custom catalogue tile |
| Run one instruction later | Schedules |
| Review a previous attempt | Runs |
| Pause or inspect recurring work | Automations |

A template saves form-filling. Once added, it creates a task with its own saved
settings. Browsing a template alone does not create or run that task.

For the detailed one-off task form, intervals, and task approval behavior, see
[Tasks and Reminders](tasks-and-reminders).

## Before You Add an Automation

- Keep Personal Jarvis running when the automation is due.
- Connect the services required by the intended task.
- Check that the selected agent or model is available for that work.
- Decide how often the result is useful before choosing its schedule.
- Write inputs that make sense without the current chat conversation.

Use the app's protected connection fields for credentials. Template inputs are
task content, not a place to save API keys or passwords.

## Browse the Catalogue

Open **Catalogue** to see the templates available in your installation.
Category filters reduce the list to the kind of work you need.

Each card describes the automation and exposes its add action. Requirements and
missing capabilities help explain why a template may not be ready to run.

The custom tile opens the task editor when no template fits your purpose.
An already-added template can offer a shortcut to its existing automation.

An empty catalogue does not mean your saved schedules have disappeared. Templates
and saved tasks are separate lists; check **Automations** and **Schedules**.

## Add a Template

1. Choose a template and select **Add**.
2. Review its prefilled title and give it a recognizable name if needed.
3. Complete the template's inputs, including all required fields.
4. Review any missing-service or capability message.
5. Choose the offered schedule kind, time, and weekday where applicable.
6. Select **Add** to save the automation.
7. Confirm that its row appears in **Automations**.

The schedule dialog uses your browser's time zone. Review the saved schedule
after adding it, especially when your device's time zone changes.

An error in the dialog means the addition did not complete. Keep the dialog open,
correct the indicated input or connection, and confirm a saved row before relying
on the schedule.

Adding an automation and completing its first run are separate events. Inspect
the run result before relying on a new routine unattended.

## Read the Automations Table

Each row brings together the recurring task's identity, schedule, last run, and
enabled state. The table gives an overview without opening every task.

Open a row to see more detail, including the latest result and its step timeline
when those records are available. A task that has never run has no result yet.

The row's action menu exposes operations such as **Run now** and deletion.
Actions that fail show an error notice; the existing row remains the reference
for the task's actual saved state.

## Arm or Pause Recurring Work

The arm switch controls whether the recurring automation is enabled.

1. Find the automation by name.
2. Turn its switch off to pause future scheduled execution.
3. Confirm the row shows the disabled state.
4. Turn it back on when you want scheduled execution to resume.

Pausing preserves the task and its settings. Deleting removes the task instead.
Do not treat a switch change as proof that an already-running operation stopped;
check the run's status separately.

## Run Once on Demand

Use **Run now** when you deliberately want an immediate attempt. This executes the
task's configured work and may call its connected services or model.

After requesting a run, open its details or the **Runs** tab. A successfully
accepted request does not establish that every step finished successfully.

If the task needs approval, handle the pending request through the app's normal
approval surface. The catalogue does not grant blanket permission to its tasks.

## Check One-Off Schedules

**Schedules** lists one-off work waiting to fire. Its time and countdown help you
confirm when an instruction is due.

Use the schedule creation action for a new one-time task. After saving, verify
the new item appears with the expected time.

A schedule is a plan for future execution. Its outcome belongs in **Runs**, where
you can check whether it completed, failed, or was cancelled.

## Review Run History

**Runs** shows previous attempts rather than only enabled recurring tasks.
Use its status filters to find completed, failed, or otherwise relevant work.

Open an entry to read the recorded result and inspect its step timeline.
The timeline can show which part finished and where a problem occurred.

When investigating a failure:

1. Read the run's result or error.
2. Check the required connection and model or agent availability.
3. Correct the cause before requesting another attempt.
4. Inspect the next run's result to confirm recovery.

Avoid repeatedly selecting **Run now** while an earlier attempt is still active.
Use the current run's state to decide what to do next.

## Troubleshooting

- **No catalogue:** the backend may not offer templates; existing tasks can still
  be reviewed in the other tabs.
- **No matching rows:** clear the search or status/category filter.
- **Missing service:** connect the named service before expecting its steps to work.
- **No latest result:** the task may not have completed a run yet.
- **A run did not finish:** inspect its recorded error or pending approval.

## How It Fits Together

A template fills in a saved task. The scheduler watches enabled tasks and starts
their configured work when due. Run records describe the resulting attempt;
arming a task and completing a run are distinct states.

## Check That It Works

1. Open an existing automation and check its saved schedule.
2. Compare the last-run status with its recorded result in **Runs**.
3. If it has never run, confirm that the view shows no completed result.
4. Check **Schedules** separately for upcoming one-off tasks.

## Next Steps

For the usage associated with scheduled activity, open
[Spend & Tokens](costs) and filter to the relevant activity and period.
