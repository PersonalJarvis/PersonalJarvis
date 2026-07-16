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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [jarvis-agents, missions, workers, outputs, safety, automation]
related: [tasks-and-reminders, outputs-and-files, skills, safety-and-approvals]
---

Jarvis-Agents handle substantial work in the background, such as building a
file, changing a project, or producing a researched report. Jarvis can hand off
that work without keeping your chat or voice conversation blocked for the full
run.

A **Jarvis-Agent** is the worker role that does the job. A **mission** is the
saved job around it: your request, its progress, the review, the final status,
and any output files. Ordinary questions and quick actions should stay in the
main conversation; a mission is for work that genuinely needs more time or
several steps.

## Before You Start

- Open **API Keys > Jarvis-Agents** and make sure one provider card shows
  **Active**. A subscription login or a dedicated provider key can power the
  worker. Enter credentials only in the protected fields on that page, never in
  chat, voice, a mission request, or an output file.
- If the app says a provider or model change becomes active after a restart,
  restart before testing a new mission. Work already running keeps the worker
  it started with.
- The current mission runner needs Git and a source checkout because every
  mission receives an isolated working copy. A setup without either one can
  fail before a worker begins, including for some non-code requests.
- Connect an external service before asking a Jarvis-Agent to use it. A
  connected service does not automatically grant every action to every mission.

## Start and Follow a Mission

1. **Ask for a concrete deliverable in Chats or by voice.** State what should
   be produced and how you will judge it. For example, ask for a short report
   saved as a Markdown file, with the sources and comparison criteria named in
   the request.

2. **Listen or look for the handoff.** When Jarvis delegates the request, its
   acknowledgement names a **Jarvis-Agent** and explains that the work is now
   running in the background. You can continue using the main conversation.

3. **Open Jarvis-Agents from the sidebar.** The operations board shows the
   number of active, finished, and failed missions. A row identifies the task,
   current status, elapsed time, recorded tool-call count, and result summary
   when one is available.

4. **Expand the mission row.** If details were recorded, you can inspect tool
   names, short argument or result previews, context hints, and the mission's
   current result. A mission can remain **ACTIVE** while its work is being
   reviewed or corrected; active does not always mean the worker is still
   writing.

5. **Open Outputs for the retained result.** Jarvis-Agents is a live monitor:
   completed and failed rows normally leave the board after about one minute.
   **Outputs** keeps the mission card, status, summary, and approved deliverable
   files until normal startup cleanup removes mission folders older than 14
   days. Use the file preview there before opening a generated file elsewhere.
   Approved files are normally copied to a separate user-visible output folder
   for longer use.

6. **Stop or retry from Outputs when needed.** Hold **Abort mission** on a
   running card to cancel its mission and worker process. A cancelled card can
   be continued, while a failed or timed-out card can be restarted. Either
   action creates a linked new mission and keeps the old attempt until its
   mission folder reaches the same cleanup boundary.

> [!warning] **Clear** on the Jarvis-Agents board only clears the current board
> display. It does not cancel work, delete output, or remove the saved mission.
> An active item can appear again when the board refreshes.

## What Happens During the Work

| Stage | What Jarvis does | What you can observe |
|---|---|---|
| **Handoff** | Saves the request as a mission and releases the conversation | A Jarvis-Agent acknowledgement and an active board row |
| **Plan** | Turns the request into one or more bounded work steps | The task remains active; detailed plans are not shown on the live board |
| **Isolated work** | Gives each mission step an isolated Git working copy and only the capabilities selected for the mission | Runtime, worker activity, and some tool previews when recorded |
| **Review** | Checks the draft against the request and can ask for a correction | The row stays active while another attempt runs |
| **Finish** | Approves the result or records a failure, cancellation, or timeout | **DONE** or **FAILED**, followed by a persistent Outputs card |

The review can make up to three passes. Reaching the limit does not turn an
unfinished result into success: the mission fails and keeps any safe partial
artifacts that were archived. A worker timeout with usable work can still be
reviewed; a timeout with no usable result can trigger another attempt.

The selected Jarvis-Agent provider is the first choice. If that provider is
unavailable and another configured provider family is reachable, Jarvis can
move the work to that family. If no compatible worker is reachable, the
mission fails honestly instead of pretending to have completed.

### Tools, Connections, and Permissions

A Jarvis-Agent does not receive unrestricted access to the app or your
computer. It gets a short-lived, mission-specific list of approved
capabilities. Read-only Wiki tools, explicitly allowed app commands, and tools
from selected connections can be offered through the main Jarvis process.
Connection credentials stay with that supervisor path; they are not copied
into the mission prompt or reported on the operations board.

Every connected action still passes the normal safety policy. Safe actions can
run, monitored actions are recorded, confirmation-level actions can pause for a
decision, and blocked actions stay blocked. A Jarvis-Agent cannot start another
Jarvis-Agent, run a skill recursively, reveal credentials, or change protected
configuration through its worker tool set.

> [!note] The current **Jarvis-Agents** board is a monitor, not the full mission
> control screen. It cannot start or cancel missions, reopen older history, or
> decide a paused supervisor tool approval. Until approval controls are exposed
> there, avoid unattended mission requests that depend on a confirmation-level
> external action; ask for a local draft or read-only result instead.

## How It Fits Together

1. **Chats or voice starts the handoff.** Jarvis answers simple work directly
   and delegates only a substantial build or multi-step request. A voice
   session can record that the handoff happened, but the later mission has its
   own lifecycle.
2. **A Skill can supply repeatable instructions.** A skill marked for mission
   execution can turn those instructions into a Jarvis-Agent request. The skill
   defines the method; the mission owns the isolated work and review. Read
   [Skills](skills) to understand draft, enabled, inline, and mission skills.
3. **Tools and connections supply selected capabilities.** A mission can use
   only the connection and app-command grants made available to it. Safety
   checks remain in the supervisor, outside the worker's isolated process.
4. **The reviewer decides whether the work is ready.** Failed tool calls,
   unfinished files, and rejected drafts cannot be hidden by a confident worker
   summary. The mission either reaches approval or records a terminal problem.
5. **Outputs receives the retained result.** The live board is temporary;
   [Outputs and Files](outputs-and-files) is where you preview deliverables,
   cancel a run, or start a linked retry while the mission folder remains.
   Keep the separate delivered copy for anything you need beyond 14 days.
6. **Tasks can wait for the outcome.** A When-Then task can react when a
   mission succeeds, fails, or is cancelled. A normal scheduled task does not
   automatically gain a Jarvis-Agent's isolated workspace or review loop. Read
   [Tasks and Reminders](tasks-and-reminders) before relying on event-based
   automation.
7. **Sessions preserve the starting conversation, not the whole mission.** Run
   Inspector may show that a voice turn started a Jarvis-Agent. Later worker
   events and generated files remain under Jarvis-Agents and Outputs.

## Check That It Works

1. Confirm that one card under **API Keys > Jarvis-Agents** is **Active** and
   that the Jarvis-Agents view shows no provider error banner.
2. In Chats, ask a Jarvis-Agent to create a small Markdown file containing a
   three-item, non-sensitive checklist and a one-sentence introduction.
3. Confirm that Jarvis acknowledges the handoff, then open **Jarvis-Agents** and
   find one **ACTIVE** row matching the request.
4. Wait for **DONE**, then open **Outputs**. Confirm that the newest card shows
   **success** and that the Markdown file can be previewed.

The path works when the request produces one mission, the live row reaches a
terminal state, and the approved file can be previewed in Outputs and found in
the separate user-visible output folder. A spoken completion is helpful but is
not a file record. The in-app mission card is normally removed after its folder
passes the 14-day startup-cleanup boundary.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Jarvis answers directly and no mission appears | The request looked like a quick question or single action | Ask for a concrete multi-step deliverable and explicitly request a background Jarvis-Agent |
| **No Jarvis-Agent provider is reachable** | The active worker login or key is missing, expired, out of quota, or unavailable, and no fallback family is ready | Open **API Keys > Jarvis-Agents**, reconnect or add a different provider family, set it active, and restart if prompted |
| The board is empty after a completed run | Finished rows are temporary, or the live connection refreshed after the row expired | Open **Outputs** for the retained mission and files; do not use the board as history |
| A mission stays **ACTIVE** for a long time | It may be queued, reviewing a draft, correcting work, or waiting on a slow provider | Check the health banner and Outputs status; hold **Abort mission** in Outputs if you no longer want the run |
| The mission fails before a worker starts | Git, the source checkout, the mission service, or the worker program is unavailable | Use the supported installer, confirm the app starts from a Git checkout, restart normally, and try one small file task |
| Work pauses or fails around an external action | The tool needed approval, was not granted to the mission, or was blocked | Never paste a credential into the request; cancel and retry with a read-only or local-draft goal, or perform the external action yourself |
| The result has a summary but no file | The request was informational, no deliverable was archived, or the worker failed before saving it | Ask for a named file and success criteria, then use **Restart** only after fixing the recorded failure |

For repeated startup, provider, or connection failures, follow the main
[Troubleshooting](troubleshooting) guide.

## Next Steps

- Read [Outputs and Files](outputs-and-files) to preview approved deliverables,
  stop active work, and continue or restart a mission safely.
- Read [Skills](skills) to turn repeatable instructions into inline work or a
  reviewed background mission.
- Read [Tasks and Reminders](tasks-and-reminders) to schedule an action or react
  to a mission's final state.
- Review [Safety and Approvals](safety-and-approvals) before letting a mission
  use connected services or make changes outside its isolated files.
