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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [outputs, files, jarvis-agents, missions, previews]
related: [chats, jarvis-agents, privacy-and-local-data]
---

Use **Outputs** to find the files created during longer Jarvis-Agent work. Each
card represents one mission and keeps its status, generated files, and recovery
actions together while that mission folder remains on the host.

Outputs is not another chat history or a permanent archive. A conversation
explains what you asked for, while Outputs lets you review and open the
resulting documents, images, web pages, code, or other files during the
mission's retention period.

## Before You Start

- Complete a Jarvis-Agent mission that creates a file. A normal chat answer
  does not appear in Outputs unless Jarvis turns the request into a mission.
- On a desktop, make sure the app you want to use for the file is installed.
  In a browser or on a remote server, only browser-compatible previews may be
  available.

> [!warning] Treat a generated file like any other file you did not write
> yourself. Preview its contents when possible, and open it in an app with
> access only to the data that app needs.

## Find and Review an Output

1. **Open Outputs.** Select **Outputs** in the sidebar. The view shows up to 20
   of the newest Jarvis-Agent missions and selects the newest one automatically.

2. **Choose a mission.** Select its card in the list. The detail area shows the
   original request when available, the current status, elapsed time, results,
   and the Plan area.

3. **Check the status before using a file.** A running mission can still add or
   replace results. Wait for **success** when you need the final approved
   version.

4. **Open Results.** Each genuine deliverable appears with its relative path
   and size. Select a text-based file row to expand its preview. Large text
   previews stop after 1 MiB and show a notice; open the file to read the rest.
   Binary files do not expand as text.

5. **Open or locate the file.** Select **Open** beside a result. On the desktop,
   the first use asks which detected app should open it. Choose **Remember as
   default** if you want later files to use the same app. Use **Change how this
   opens** to choose again, or **Reveal in folder** to select the real file in
   the system file manager.

On a browser or remote installation, native file actions are hidden because
they would act on the server instead of your computer. A supported text,
Markdown, image, Portable Document Format (PDF), or web file opens through the
browser instead. Other binary formats may need to be transferred from the host
and opened locally.

Approved mission files are also copied to a user-visible Jarvis output folder
when the host permits it. On a normal Windows desktop this is usually the
**Jarvis-Outputs** folder inside Downloads. Jarvis avoids overwriting a
different file with the same name by adding a mission identifier to the copy.
That copy is separate from the Outputs card. During normal startup cleanup,
mission folders older than 14 days are removed, so their cards and previews
disappear from Outputs. Keep the separate copy when you need a file for longer.

### Understand the Status

| Status | What it means | Available action |
|---|---|---|
| **running** | A Jarvis-Agent is still working | Hold **Abort mission** to stop it deliberately. |
| **success** | The mission reached approval | Review and open the saved results. |
| **cancelled** | You stopped the mission | Select **Continue** to start a linked continuation. |
| **error** | The mission failed, timed out, or could not finish | Select **Restart** to start a new linked attempt. |
| **unknown** | The folder remains, but its mission record is unavailable | Review any listed files; the status cannot be reconstructed from the folder alone. |

**Continue** and **Restart** never change the original card. They dispatch a
new mission with the stored request, so you can compare the old and new runs.
If that request could perform a destructive action, Jarvis asks you to confirm
the rerun. While the continuation is active, the old card shows **Continued -
running**; select that indicator to jump to the new card.

A successful card has no rerun button. To rerun the same work or produce a
changed version, return to [Chats](chats) and submit the request again. This
creates a new mission instead of a linked retry from the original card.

## Preview Files Safely

The Results list is an allowlist of deliverables, not a view of the whole
worker folder. Jarvis excludes worker settings, logs, credentials-related
state, review notes, browser scratch data, and diagnostic patches from this
list and from direct file links.

Text and Markdown opened in the browser are rendered on a standalone page that
does not allow scripts. Plain text is escaped before display, and inline HTML
receives the same no-script browser policy. Other formats use the browser or
the app you choose, so their normal viewer protections still matter.

> [!note] The **Plan** area currently does not receive stored mission steps. It
> can show **Single-shot run - no structured plan for this session** even when
> the work involved several steps. Use the card status and files as the current
> source of truth for completion.

## Discuss an Output in Chats

Drag a mission card from Outputs onto the Jarvis drop target to add a short
recap of that mission to the active conversation. Jarvis can then discuss the
request, status, summary, or recorded error.

This action does not attach the generated files, duplicate them, or rerun the
mission. If Jarvis needs a file's contents, add that file separately and send a
clear request about it.

## How It Fits Together

1. **Chats, voice, a command, or a workflow starts the request.** A concrete
   file-building request can be delegated to a [Jarvis-Agent](jarvis-agents)
   when it needs longer or isolated work.
2. **The Jarvis-Agent runs a mission.** Outputs polls that mission's status and
   shows only files archived as user deliverables. If the selected provider or
   worker is unavailable, the mission can end in error; fix that dependency
   before choosing **Restart**.
3. **Outputs presents the retained result.** It previews readable content,
   opens the archived file, and normally copies approved files into a simpler
   local output folder. The card follows the 14-day mission-folder cleanup;
   the separate copy does not. Outputs does not store the conversation that
   requested the work.
4. **Sessions and Run Inspector preserve different evidence.** [Sessions and
   Run Inspector](sessions-and-run-inspector) helps you review a recorded voice
   conversation or app run. Outputs remains the place for mission deliverables;
   neither view replaces the other.
5. **Board summarizes activity, not file contents.** [Jarvis Board](jarvis-board)
   can turn local activity into counts, trends, and achievements, including
   Jarvis-Agent use. It does not preview, copy, or publish the files shown in
   Outputs.

## Check That It Works

1. In Chats, ask Jarvis to create a small text file containing a three-item
   checklist. Do not include private information in the example.
2. Open **Outputs** and select the newest mission. Wait for its status to become
   **success**.
3. Under **Results**, select the text file and confirm that the checklist
   appears in the expanded preview.
4. Select **Open**. On a desktop, choose an app; in a browser, confirm that the
   readable file opens in a separate view.

The feature works when the completed mission, its deliverable, the preview,
and an appropriate open action are all available.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **No Jarvis-Agent sessions yet** | No file-producing mission has reached the output store | Ask for a concrete file in Chats and follow the Jarvis-Agent progress. |
| An older mission is missing | Outputs shows only the 20 newest cards, and startup cleanup removes mission folders older than 14 days | Check the user-visible Jarvis output folder for its separate delivered files. |
| **This session has no saved files** | The mission produced no archived deliverable, or archiving failed | Read the mission status, fix any provider or worker error, then use **Restart** when available. |
| **Single-shot run** appears for multi-step work | Structured plan persistence is not connected to Outputs yet | Use the status and Results list; do not treat the Plan message as a step count. |
| A preview is shortened or says the file is binary | Inline preview is limited by size or file type | Select **Open** or **Reveal in folder** and use a suitable local app. |
| **Open** does nothing or the chosen app is gone | The remembered opener is no longer available | Select **Change how this opens**, choose another detected app, or reveal the file in its folder. |
| The status is **unknown** | The file folder survived but the matching mission database row did not | Use any visible results normally. Restart the work from Chats if you need a new verified run. |

For persistent loading, connection, or provider problems, follow the main
[Troubleshooting](troubleshooting) guide.

## Next Steps

- Read [Jarvis-Agents](jarvis-agents) to understand delegation, mission
  progress, review, and recovery before a file reaches Outputs.
- Use [Sessions and Run Inspector](sessions-and-run-inspector) when you need to
  examine the conversation or run that led to a result.
- Explore [Jarvis Board](jarvis-board) to see aggregate activity and
  achievements without treating it as file storage.
- Review [Privacy and Local Data](privacy-and-local-data) to learn where
  conversations, mission records, and generated files live.
