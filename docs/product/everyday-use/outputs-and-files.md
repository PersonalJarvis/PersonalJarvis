---
title: "Outputs and Files"
slug: outputs-and-files
summary: "Find generated files, preview them safely, open them in another app, and recover unfinished work when available."
section: "Everyday use"
section_order: 2
order: 5
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [outputs, files, jarvis-agents, missions, previews]
related: [chats, jarvis-agents, privacy-and-local-data]
---

**Outputs** is the results shelf for delegated file work. One mission becomes
one card with its status, summary, saved files, and recovery actions.

Outputs is not a chat attachment library or permanent storage. Review a result
there, then keep the separate delivered copy when the file matters to you.

## Before You Start

- Complete a file-producing mission. The Agents area follows your assistant's
  name—for example, **Nova-Agents**; the neutral fallback is
  **Assistant-Agents**. The underlying system is Jarvis-Agents.
- Use the local desktop app to launch another app or reveal a file in the
  host's file manager. A remote browser cannot open apps on your computer.
> [!warning] Review every generated file. A protected preview reduces browser
> risk but does not prove the file is correct or safe.

## Know Where Your Files Go

| Experience | What happens | In Outputs? |
|---|---|---|
| **Chats** | A dropped file is temporary request context, not a durable attachment. | No. Add it again when needed. |
| **Agentic IDE** | An IDE pane edits the project folder you opened. Those edits remain in that project. | No. The project folder is the source of truth. |
| **Jarvis-Agents mission** | Deliverables are archived, and approved files get a visible copy. | Yes. |

Add an IDE edit from its project folder or a mission file from its delivered
folder when a chat needs the actual contents.

## Find, Preview, and Open a Result

1. Select **Outputs** in the sidebar. It shows up to 20 of the newest retained
   mission cards.
2. Select a card to see its request, status, summary, failure reason,
   **Results**, and **Plan**.
3. Check the status. A running result may change. **success** means review
   approved it, not that every fact is correct.
4. Open **Results**. It lists up to 200 deliverables with archived paths and
   sizes.
5. Select a file row to expand it. Common text, data, configuration, and source
   files show up to 1 MiB. A larger preview is shortened; binary files show a
   notice instead of unreadable text.
6. Select **Open**. On first desktop use, choose the default app, browser, or a
   detected editor. You can remember or later change this choice.
7. Select **Reveal in folder** to locate the file. The card's **Desktop** action
   reveals the whole mission folder instead.

In a browser or on a headless server, native actions are unavailable. **Open**
can show common text and source files on a protected page up to 2 MiB, plus
PDF, HTML, and common images in the browser. Markdown is rendered, other text
is escaped, and HTML or SVG gets a no-script policy. Other formats need a
desktop app or secure host transfer. **Plan** may say **Single-shot run** when
no stored steps are available.

## Keep or Download a Generated File

Approved files are copied to a visible folder on the host running Jarvis.
Windows normally uses **Downloads > Jarvis-Outputs**, falling back to the
Desktop or home folder. macOS, Linux, and headless hosts normally use
`~/jarvis-outputs`; an unwritable home can fall back to a temporary folder.
The delivered copy uses the file name rather than the mission's internal path,
and name collisions never overwrite a different file.

Partial files on **error**, **cancelled**, or **Needs review** cards are not
copied automatically. Outputs has no separate **Download** button. Locally,
use the delivered folder or **Reveal in folder**. Remotely, use the host's
trusted file transfer; browser **Open** is only a viewing action.

## Understand Incomplete and Recovered Results

| Status | Meaning | Next action |
|---|---|---|
| **running** | The mission is active | Wait, or hold **Abort mission**. |
| **success** | Review approved it | Inspect and use the delivered copy. |
| **Needs review** | A file remains without final approval | Inspect carefully or select **Restart**. |
| **cancelled** | You stopped it | Inspect partial files or select **Continue**. |
| **error** | It failed, timed out, or was recovered | Inspect partial files or select **Restart**. |
| **unknown** | A folder remains without a matching mission record | Use only files you can review; the status cannot be rebuilt. |

After an interruption, a recent card may stay **running** briefly. Recovery
then restores a recorded terminal status or marks abandoned work **error**
while preserving identifiable archived files.

**Continue** and **Restart** create a linked mission from the stored request;
they do not resume or alter the old run. A destructive request requires
**Confirm re-run**. Request revisions to successful work in [Chats](chats).

## Retention, Deletion, and Privacy

- At startup, mission folders unchanged for at least 14 days are eligible for
  cleanup; their cards and previews disappear.
- Delivered files are separate copies and remain until you delete them in the
  operating system's file manager.
- Outputs has no per-card or per-file **Delete** control. Never remove a mission
  folder while its card is **running**.
- A card can fall outside the 20-item list before cleanup.

Results excludes worker settings, logs, review notes, credential state, and
scratch data. The preview is not an antivirus, secret, or correctness check.
Never put passwords, API keys, tokens, or private keys in a deliverable.

Delivered files, manual copies, chat uploads, transfers, and provider data have
separate lifetimes. [Privacy and Local Data](privacy-and-local-data) explains
why deleting one copy does not delete every trace.

Dragging a card to Chats adds only a request/status recap, not its files. This
action currently has no keyboard equivalent.

## How It Fits Together

1. A request is delegated when isolated work is needed.
2. The mission archives genuine deliverables and sends them through review.
3. Outputs shows results, partial files, and recovery actions.
4. Approval creates a separate visible copy. Cleanup can later remove the card
   without deleting that copy.
5. Chats can discuss a recap or an added file; [Agentic IDE](agentic-ide)
   continues to manage its own project files.

## Check That It Works

1. Start an Agent mission that creates a small, non-private text checklist.
2. Open **Outputs**, select the newest card, and wait for **success**.
3. Expand the file under **Results** and confirm that its preview appears.
4. Select **Open**, then find the approved copy in the host output folder.

It works when the result appears, opens appropriately, and an approved file has
a separate delivered copy.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| **No Agent sessions yet** | No retained file-producing mission exists | Start a concrete file task from Chats or the Agents area. |
| An older mission is missing | It is outside the 20-card list, or cleanup removed it | Check the host's Jarvis output folder. |
| **This session has no saved files** | No deliverable was archived | Read the status and reason, fix the underlying problem, then retry when offered. |
| A card stays **running** after interruption | Recovery is protecting recent work from a false failure | Wait for reconciliation; restart if it later becomes **error**. |
| Preview is shortened, binary, or lacks **Open** | Its size or format is unsupported there | Use a desktop app or transfer it securely from the host. |

For persistent loading, connection, or provider problems, see
[Troubleshooting](troubleshooting).

## Next Steps

- Read [Jarvis-Agents](jarvis-agents) for mission review and recovery.
- Read [Chats](chats) for temporary dropped-file context.
- Review [Privacy and Local Data](privacy-and-local-data) before sensitive work.
