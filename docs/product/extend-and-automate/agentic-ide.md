---
title: "Agentic IDE"
slug: agentic-ide
summary: "Open a project workspace, coordinate coding agents in live terminal panes, and keep every instruction and result visible."
section: "Extend and automate"
section_order: 5
order: 8
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [agentic-ide, coding-agents, terminals, workspaces, cli, voice]
related: [cli-connections, jarvis-agents, cli-reference, safety-and-approvals]
---

The Agentic IDE places local coding command-line programs in live project
panes. You can watch their work, assign tasks, and keep several projects open.

Four terms make the rest of this guide easier:

| Term | Meaning |
|---|---|
| **Workspace** | One project folder, its pane layout, and its coding mode |
| **Pane** | One live terminal in the workspace, identified by a call-sign such as **T1** |
| **Coding agent** | An installed coding CLI running inside a pane; a plain terminal is a shell, not a coding agent |
| **Jarvis-Agent** | A separate background mission worker with an isolated worktree and review flow |

The app names the background Agents area from your configured assistant name.
This documentation uses **Jarvis-Agents** for that separate feature.

## Before You Start

- Install and sign in to at least one supported coding CLI on the same computer
  as Jarvis. The Agentic IDE enables only programs it can actually launch.
- Open **API Keys** when you have more than one subscription. An
  account choice applies to new panes; a running pane keeps its original one.
- Choose a folder you trust. Opening a workspace marks that folder as trusted
  in supported coding CLIs so their own trust question does not block a hidden
  pane.
- Review the coding CLI's own permission mode. Agentic IDE panes run as your
  operating-system user; they are not the isolated, reviewed Jarvis-Agent
  mission environment.

> [!warning]
> Never put passwords, API keys, recovery codes, or private tokens in a prompt,
> terminal screenshot, or dropped document. Exact pane prompts are kept in
> local history.

## Open Your First Workspace

1. Open **Agentic IDE** from the sidebar and choose **New workspace**.
2. Select or paste the project folder. Agent briefs use this folder as context.
3. Choose the workspace shape and number of panes.
4. Assign an available coding CLI, optional account, and call-sign to each
   pane. You can also choose a plain terminal for commands you want to type
   yourself.
5. Review the plan, then choose **Open workspace**. Nothing starts before this
   confirmation.

Opening a workspace turns on focused coding mode, so Jarvis can use the active
folder and pane activity. Turning it off does not stop the panes.

Use the workspace bar to add, switch, rename, or close workspaces. Switching
tabs or app sections leaves panes running. Closing a workspace stops its panes;
files they wrote remain.

### Add a Coding CLI of Your Own

The pane step lists the coding CLIs Jarvis ships with. If yours is not among
them, choose **Add a CLI** at the bottom of that list and give it:

- a **name** — what you will see in the pickers and say out loud;
- the **command** — exactly what you would type in a terminal to start it;
- an optional **description** and **logo**.

It then appears everywhere the built-in ones do: the pane step, the "open a
terminal" menu, the pane split menu, and the voice catalogue. Editing or
removing it later is on its own row in the same list.

Two things to know about a CLI you added:

- Jarvis checks only that the command's program is installed, and reports no
  version number for it. It never runs your command to ask, because that would
  start an unknown program every time the app checks what this machine can run.
- Trust pre-seeding, several subscriptions, and reopening earlier conversations
  are not available for it. Each of those needs knowledge of one specific
  vendor's own files. Everything else — prompts, drops, recaps, call-signs —
  works as it does for a built-in CLI.

If your command is more than one program (a pipeline, a `VAR=value` prefix, or
two commands chained), the pane runs it through this computer's shell and
closes when the whole line is done. The dialog says so while you type.

## Arrange and Address Panes

- Split a pane right or down, optionally with another installed coding CLI.
- Drag a pane by its header. Drop it on another pane to swap them, or near an
  edge to place it left, right, above, or below.
- Rename a pane without restarting it or losing its conversation. Call-signs
  must be unique across open workspaces.
- Close one pane, a selection, or all panes of one coding-agent type only after
  reviewing the confirmation. Closing stops those processes.

For voice, name the pane and action: “Tell T2 to review the failing tests” or
“What is terminal three doing?” Near matches trigger a confirmation instead of
a guess. An explicit background-mission request still goes to Jarvis-Agents.

## Send Clear Work

Select a coding pane in the prompt bar, write a rough instruction, and press
Enter. Jarvis can prepare a fuller project-aware brief and show it before
delivery. You can send the prepared brief, keep your original words, or cancel.
A spoken instruction follows the same project-aware composition path.

For several panes, say whether they should receive the same work or divide it:

- “Tell T1 and T2 to run the same checks” sends one shared instruction.
- “Have T1 and T2 split the accessibility audit” creates separate assignments.

Voice and API fan-out can open and brief new panes. Existing panes are untouched
unless named. Read **delivered** and **undelivered**; opening a pane does not
prove it received the work.

Each pane shows a receipt. **Submitted** means it visibly accepted the prompt.
If text is waiting in its input box, press Enter there. **Unconfirmed** means
Jarvis could not prove delivery, so inspect before retrying. The history button
shows exact prompts and their recorded submission states.

### Add Files and Screenshots

Drop a project file directly on a pane to type its path without submitting it;
add your instruction, then press Enter. Drag it either from your computer's own
file manager or from the workspace explorer beside the grid — a folder works the
same way as a file. Drop or paste a screenshot or document on the prompt bar to
let Jarvis extract or describe its contents for the prepared brief.

External files and clipboard images are copied into the Git-ignored
`.jarvis/drops` folder; project files stay where they are. Later drops clean up
old copies. This is local working data, not a secure or permanent archive.

## Resume, Continue, or Forget

The local restore point records folders, tab names, pane positions, call-signs,
account choices, and conversation handles. **Resume** reopens only the most
recent session; older remembered folders are not silently reopened.

A pane is **available** when its folder and CLI can open. It is **resumable**
only when that account's CLI history proves the conversation exists. Otherwise
it opens fresh in the same layout. Resumed conversations wait at their prompts;
review them before **Continue interrupted work**. **Queued** or **unconfirmed**
does not mean running.

**Forget** removes only the restore point. It does not stop running agents,
delete project changes, clear prompt-history files, remove dropped files, or
erase a coding CLI's own conversation history.

## How It Fits Together

| Related feature | Relationship to Agentic IDE |
|---|---|
| [CLI Connections](cli-connections) | General CLI connections expose cataloged command tools to the assistant. Agentic IDE launches supported coding CLIs as interactive panes. |
| [Jarvis-Agents](jarvis-agents) | Jarvis-Agents run reviewed background missions in isolated worktrees. Agentic IDE panes work interactively in the folder you opened. |
| [Safety and Approvals](safety-and-approvals) | Jarvis safety governs assistant tools. A coding pane also follows its external CLI's own permission and approval settings. |
| [CLI Reference](cli-reference) | The Jarvis CLI controls the same local Agentic IDE routes. Curated `jarvis ide` commands cover common pane actions; `jarvis api agentic-ide ...` exposes the full API while the app server is running. |

The desktop installation includes terminal backends for Windows, macOS, and
Linux. A minimal or headless installation may have none. Native folder browsing
and voice also need desktop capabilities; the API cannot replace a missing
terminal backend.

## Check That It Works

1. Open a non-sensitive project with one installed coding CLI.
2. Send: “Summarize the purpose of this project from its top-level README. Do
   not edit files.”
3. Confirm that the receipt says **Submitted** and that the pane begins showing
   output.
4. Open that pane's prompt history and confirm the exact instruction appears.
5. When a completion notice appears, inspect the output yourself. **Finished**
   means the terminal went quiet; it does not mean the answer or code is correct.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| No coding CLI is available | Jarvis cannot resolve an installed program on its process path | Install or sign in with the CLI's official instructions, restart Jarvis if the process path changed, then recheck |
| A pane says the account is not signed in | That pane's selected subscription has no usable CLI login | Open the account controls, test the intended account, and create a new pane with it |
| A spoken instruction reaches no pane | The call-sign was missing, ambiguous, or referred to a pane that no longer exists | Read the current header, use its exact call-sign such as **T2**, and answer any clarification question |
| The receipt is waiting or unconfirmed | The terminal did not visibly accept the prompt | Inspect the pane; press Enter only when the text is waiting, or retry after confirming it was missed |
| Resume says a pane starts fresh | The folder or CLI is unavailable, or the selected CLI/account no longer has that conversation | Restore the folder or login, or continue in the fresh pane with the needed context |
| **Finished** appears but work is incomplete | The pane stopped drawing its busy state; correctness was not evaluated | Read the recap and terminal output, inspect changed files, and run the appropriate checks |

## Next Steps

- Read [Jarvis-Agents](jarvis-agents) when a task needs isolation, background
  execution, and a review verdict.
- Read [CLI Connections](cli-connections) for ordinary non-interactive command
  tools rather than live coding panes.
- Review [Safety and Approvals](safety-and-approvals) before giving a coding CLI
  broader write, execution, or network permissions.
- Use the [CLI Reference](cli-reference) to control Agentic IDE routes from a
  terminal or automation.
