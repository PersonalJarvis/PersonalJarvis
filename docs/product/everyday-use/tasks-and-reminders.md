---
title: "Tasks and Reminders"
slug: tasks-and-reminders
summary: "Schedule recurring and one-off work as routines on your agents, then review, pause or stop each run."
section: "Everyday use"
section_order: 2
order: 3
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-10-01
phase: "-"
audience: end-user
tags: [tasks, reminders, scheduling, recurring, routines, agents]
related: [jarvis-agents, workflows-and-commands, safety-and-approvals]
---

Scheduled work lives on your **Agents**. Each agent can carry **routines**: a
saved job that runs on a schedule or when something happens, in that agent's
own chat, with its instructions and permissions. There is no separate
schedules page any more; the old Automations section and its catalogue of
ready-made schedules were retired in favour of agent routines.

## Before You Start

- Create an agent for the job first, or pick an existing one under **Agents**.
  The routine runs with that agent's model seat and permissions.
- Connect the services the job needs. A routine never grants an agent extra
  plugin permissions.
- Keep the app running when work is due. A slot missed while the app was
  closed is skipped, not caught up hours later.

> [!warning] Never put credentials, recovery codes, or private keys in a
> routine's title or prompt.

## Add a Routine

1. Open **Agents** and open the agent that should do the work.
2. In its card, open **Routines**, then **Add a routine**.
3. Enter a short **Title** and, under **What to do**, a self-contained
   instruction. The routine does not see your earlier chat.
4. Under **When**, choose **Every few hours**, **Every day at a time**, or
   **When something happens**, and fill in the time or event.
5. Select **Add**. The routine appears in the list with its **Next run**.

You can also ask the agent in its chat, for example "every weekday at 8, check
my inbox and summarize what needs an answer". The agent saves the routine and
reads back when it runs next.

## Review, Pause, and Stop

Open a routine in the agent's **Routines** list to see its schedule, its runs
and their results. From there you can run it now, pause and resume it, stop
it, or delete it. Chat and voice can list routines ("show me my tasks") and
cancel one after a confirmation.

## How It Fits Together

1. A routine is saved with its agent, its instruction, and its trigger.
2. The local scheduler watches the time or event. At the trigger it starts one
   run in the agent's own chat, on the agent's model seat.
3. The agent's permissions decide which tools the run may use; safety checks
   classify every tool call as usual.
4. The result lands in the routine's run history on the agent card.

Routine records stay on this device. The agent's model provider and any
connected service receive the prompt and the data the run needs.

## Check That It Works

1. Add a routine that runs soon with a harmless instruction, such as "reply
   with the words Routine check complete".
2. Wait for its next run, then open the routine.
3. Confirm the run shows the requested text.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| A routine did not run at its time | The app was closed at that moment | Keep the app open; the next slot runs normally |
| A run failed | The agent's model or a needed service was unavailable | Open the run, read the error, then fix the connection or the agent's seat |
| An old schedule is listed as cancelled | It came from the retired Automations catalogue | Give the job to an agent as a routine |

## Next Steps

- Read [Agents and Background Work](jarvis-agents) to set up the agents that
  own your routines.
- Use [Workflows and App Commands](workflows-and-commands) for reusable
  multi-step automation.
- Review [Safety and Approvals](safety-and-approvals) before letting an agent
  make external changes unattended.
