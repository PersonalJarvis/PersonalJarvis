---
title: "Jarvis-Agents"
slug: jarvis-agents
summary: "Learn when Jarvis delegates longer work, how missions and workers fit together, and how to follow progress and results."
section: "Extend and automate"
section_order: 5
order: 1
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [jarvis-agents, missions, workers, outputs, safety, automation]
related: [tasks-and-reminders, outputs-and-files, skills, safety-and-approvals]
---

Jarvis-Agents is the internal name for the background mission system. In the
app, the visible name follows your assistant name. For example, an assistant
named Nova shows **Nova-Agents** in the sidebar and **Nova-Agent** on each row.
If no assistant name is available, the app uses **Assistant-Agents**.

A mission is a saved background job. It records your request, its progress,
review result, final status, and any retained files. Agents start only when you
explicitly ask for one. Quick questions and ordinary actions stay in the main
conversation.

## Before You Start

- Open **API Keys**, then select the Agents tab named after your assistant,
  such as **Nova-Agents**. Choose a provider card that shows **active**. You can
  use a supported subscription login or an API key. Enter credentials only in
  the protected fields in this view, never in chat, voice, a mission request,
  or an output file.
- Follow any restart notice shown after changing the worker model. A provider
  switch is used by the next mission. Work already running keeps the worker it
  started with.
- Install Git. Source-code work also needs a usable source checkout. A
  standalone report or file can run in a new, empty Git workspace, so it does
  not require a Personal Jarvis source checkout.
- Connect an external service before requesting work that needs it. A
  connection does not give every mission unrestricted access to that service.

## Start and Follow a Mission

1. **Ask explicitly in Chats or by voice.** Name the deliverable and how you
   will judge it. For example: "Start a background agent and create a Markdown
   report with three options, sources, and a recommendation."

2. **Wait for the handoff message.** Jarvis confirms that the named agent has
   taken the task. The mission then runs separately, so you can continue the
   main conversation.

3. **Open the Agents entry in the sidebar.** Its label follows your assistant
   name. The operations board shows totals for active, done, and failed work,
   plus recorded tool calls. Each row shows the task, status, tool count,
   runtime, and a short result when one is available.

4. **Expand a row for recorded details.** You may see tool names, bounded
   argument or result previews, context hints, a failure reason, and a trace
   identifier. A row can remain **ACTIVE** while the result is being reviewed
   or corrected.

5. **Open Outputs for retained results.** The live board uses **ACTIVE**,
   **DONE**, **FAILED**, and **CANCELLED**. Terminal rows remain there for about
   60 seconds. Outputs keeps the archive card and files until mission cleanup
   removes directories older than the configured retention period, which is 14
   days by default. Approved deliverables are also copied to a user-facing
   folder when possible: `Jarvis-Outputs` under Downloads on Windows, or
   `~/jarvis-outputs` on macOS and Linux.

6. **Stop or run the request again from Outputs.** Hold **Abort** on a running
   card to cancel the mission and its worker processes. **Continue** appears on
   a cancelled card, while **Restart** appears on a failed or timed-out card.
   Both actions create a new mission linked to the old one and reuse the saved
   prompt. They do not resume the old process or workspace. The old attempt
   remains as an audit record until cleanup.

> [!warning] **Clear** on the Agents board clears only the current display. It
> does not cancel a mission or delete its files. A running row can return after
> the next board refresh.

## What Happens During the Work

| Stage | What Jarvis does | What you can observe |
|---|---|---|
| **Handoff** | Saves the request as a mission and releases the conversation | A handoff message and an **ACTIVE** row |
| **Plan** | Splits the request into one to five bounded steps | The mission stays active |
| **Isolated work** | Gives each step its own Git workspace and selected capabilities | Runtime, tool count, and recorded previews |
| **Review** | Checks evidence against the request and can ask the worker for a correction | The row remains **ACTIVE** during another attempt |
| **Finish** | Approves the result or records a failure, timeout, or cancellation | A terminal board status and an Outputs card |

Repository work uses a registered Git worktree. Standalone artifact work uses
an empty Git workspace. Both are removed after their files and evidence have
been archived. Worker subprocess trees are contained on Windows, macOS, and
Linux, and cancellation closes that containment. File and research missions can
run on a headless server; desktop-only actions still need a graphical session.

Each step gets at most three worker and reviewer iterations. A review that
still does not approve the result at that point fails the mission. Genuine
partial files are retained and may appear as **needs review** in Outputs.
Timeouts also fail honestly. If a timed-out worker left useful files, the
reviewer can still assess them; without useful output, Jarvis can retry within
the remaining iterations.

The selected worker provider is the first choice. Before a new attempt, Jarvis
checks whether that provider is usable and can cross to another configured
provider family after authentication, quota, or availability failures. The
reviewer also needs a reachable compatible provider. If no suitable worker or
reviewer is available, the mission fails instead of reporting success.

The provider setup strip shows its configured time and concurrency limits.
Missions can queue, and worker, review, budget, or safety limits can end a run
earlier. Treat the displayed limit as a cap, not a promised runtime.

### Tools, Connections, and Permissions

An agent receives a short-lived, mission-specific capability grant. Eligible
read-only Wiki tools, selected app commands, and tools from connected services
can be offered through the main Jarvis process. Tool objects and credentials
stay with that supervisor. They are not copied into the worker prompt or shown
on the operations board.

Every connected action still passes the normal safety policy. Safe actions can
run, monitored actions are recorded, and blocked actions remain blocked. An
action that requires confirmation pauses for one exact decision and expires if
it is not answered. Agents cannot start another agent, run a skill recursively,
read credentials, or change protected configuration through the worker grant.

> [!note] The current sidebar opens the operations board, not the separate
> mission-control view that contains approval controls. You cannot approve a
> paused tool call from the board or Outputs. For now, avoid unattended missions
> that depend on a confirmation-level external action. Ask for a read-only or
> local draft, then perform the external action yourself.

## How It Fits Together

1. **Chats or voice starts the mission.** You must explicitly ask for a
   background agent. Jarvis saves the request and returns the handoff message.
2. **A skill can provide repeatable instructions.** A skill configured for
   mission execution can start the same isolated and reviewed workflow. Read
   [Skills](skills) before enabling generated or mission-based skills.
3. **Connections provide selected tools.** The supervisor exposes only the
   mission's allowed capabilities and keeps safety checks outside the worker.
4. **The reviewer checks the evidence.** A confident worker summary does not
   override missing files, failed tools, or a rejected review.
5. **Outputs retains the result.** Use [Outputs and Files](outputs-and-files)
   to preview files, cancel active work, or create a linked continuation or
   restart.
6. **Tasks can react to the outcome.** A When-Then task can listen for a final
   mission result. A normal scheduled task does not automatically gain an
   agent workspace or review loop. Read [Tasks and Reminders](tasks-and-reminders)
   before relying on mission events.

## Check That It Works

1. In **API Keys**, open the Agents tab named after your assistant and confirm
   that one provider card shows **active**.
2. In Chats, explicitly ask a background agent to create a Markdown file with
   a one-sentence introduction and a three-item, non-sensitive checklist.
3. Open the Agents sidebar entry and confirm that one matching row appears as
   **ACTIVE**, then reaches **DONE**.
4. Open **Outputs**. Confirm that the newest card shows **success** and that the
   Markdown file can be previewed.

This verifies dispatch, the live board, review, archive creation, and file
preview. A spoken completion message is useful, but it is not the file record.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Jarvis answers directly and no mission appears | The request did not explicitly ask for an agent | Ask for a background agent and name a concrete deliverable |
| The board reports that no provider is reachable | The selected login or key is missing, expired, out of quota, or unavailable, and no fallback family is usable | Open **API Keys**, reconnect the selected provider or activate another family, then try a small mission |
| The board is empty after a completed run | Terminal rows leave the live board after about 60 seconds | Open **Outputs** for the retained card and files |
| A mission stays **ACTIVE** for a long time | It can be queued, working, under review, correcting a draft, or waiting on a provider | Check the provider banner and Outputs card; hold **Abort** if you no longer want the run |
| A mission fails before useful work appears | Git, a required source checkout, the selected worker, or the reviewer was unavailable; a tool may also have required an approval the visible board cannot provide | Read the recorded reason, fix that prerequisite, then use **Restart** or request a read-only or local result |

For repeated startup, provider, or connection failures, follow
[Troubleshooting](troubleshooting).

## Next Steps

- Read [Outputs and Files](outputs-and-files) to preview retained deliverables
  and understand where copied files are stored.
- Read [Skills](skills) to turn repeatable instructions into a reviewed
  background mission.
- Read [Tasks and Reminders](tasks-and-reminders) to react to a mission's final
  state.
- Review [Safety and Approvals](safety-and-approvals) before a mission uses a
  connected service or changes anything outside its isolated files.
