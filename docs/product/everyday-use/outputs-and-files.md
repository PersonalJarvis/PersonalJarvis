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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [outputs, files, jarvis-agents, missions, previews]
related: [chats, jarvis-agents, privacy-and-local-data]
---

Use **Outputs** to find files created by Jarvis-Agent missions. Each card keeps
one mission's request, status, saved results, and available recovery actions in
one place while its mission folder remains on the computer or server running
Jarvis.

Outputs is not a chat history or permanent storage. Use it to inspect a
retained result, then keep the separate delivered copy if you need the file for
longer.

## Before You Start

- Complete a Jarvis-Agent mission that creates a file. A normal chat answer
  does not appear here unless Jarvis delegates it as a mission.
- To open or reveal a file on your computer, run the local desktop app and have
  a suitable viewer installed. A browser connected to a remote or headless
  host cannot launch apps on your computer.

> [!warning] Review a generated file before opening it in another app. That app
> receives normal access to the file and may process active content that the
> protected browser preview blocks.

## Find and Review an Output

1. **Open Outputs.** Select **Outputs** in the sidebar. The view shows up to 20
   of the newest retained mission cards and selects the first one automatically.

2. **Choose a mission.** Select its card in the list. The detail area shows the
   original request when available, its status, elapsed time, summary, results,
   and the **Plan** area.

3. **Check the status before using a file.** A running mission can still change
   its results. **success** means the mission was approved. **Needs review** or
   **error** can still include retained files, but those files are not approved.

4. **Review Results.** Outputs lists up to 200 deliverables with their paths
   inside the mission folder and their sizes. Select a file row to expand it.
   Supported text files show up to 1 MiB; larger files show a truncation notice.
   Other formats show a binary file notice instead of decoding their contents
   as text.

5. **Open or locate the file.** Move to the controls beside the file path and
   select **Open**. On a local desktop, the first use opens **Open with...** so
   you can choose the system default app, a browser, or a detected editor.
   Select **Remember as default** to reuse that choice. Use **Change how this
   opens** to choose again or **Reveal in folder** to select the archived file
   in the system file manager.

You can use Tab and Shift+Tab to move between a card, its file rows, and the
three file actions. Press Enter or Space to activate the focused control.

The **Desktop** action on a mission card is different from the file actions. It
tries to reveal the whole mission folder on the host. Use it only in the local
desktop app. On a headless or remote host it may do nothing because there is no
host file manager to open.

### Know Which Files Can Open Here

| Action | Supported files and limits |
|---|---|
| Expand in **Results** | Extensionless files and `.md`, `.txt`, `.json`, `.jsonl`, `.yaml`, `.yml`, `.toml`, `.log`, `.patch`, `.diff`, `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.html`, `.css`, `.csv`, `.env`, `.cfg`, `.ini`, `.sh`, and `.ps1`; up to 1 MiB of text. |
| **Open** as a protected browser page | `.md`, `.markdown`, `.txt`, `.json`, `.jsonl`, `.csv`, `.yaml`, `.yml`, `.toml`, `.log`, `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.css`, `.sh`, and `.ps1`; the page must be no larger than 2 MiB. |
| **Open** in the browser's own viewer | `.pdf`, `.html`, `.htm`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, and `.svg`. |
| **Open** or **Change how this opens** on a local desktop | Any listed deliverable that the chosen app can handle. |

In a normal browser connected to a remote or headless host, **Change how this
opens** and **Reveal in folder** are hidden. **Open** appears only for a format
listed in the browser rows above. Outputs does not currently show a separate
**Download** action. To copy another format from a remote host, use the file
transfer method provided by that host, then open the local copy yourself.

### Find the Delivered Copy

Approved mission files are also copied to a user-visible Jarvis output folder
on the host. On Windows this is normally **Downloads > Jarvis-Outputs**. If
Downloads is unavailable, Jarvis tries the Desktop and then your home folder.
On macOS and Linux, including a headless server, the default is
`~/jarvis-outputs`. If the home folder is not writable, Jarvis uses a temporary
system folder as a last resort.

The delivered copy uses the file's base name, without the mission's internal
folder structure. Jarvis reuses an identical existing file. If a different
file already has the same name, it adds the mission identifier instead of
overwriting that file.

This copy is separate from the Outputs card. At normal startup, mission folders
whose modification time is at least 14 days old are removed. Their cards and
previews then disappear, but the delivered copy remains. Keep or move that copy
when you need it for longer.

### Understand the Status

| Status | What it means | Available action |
|---|---|---|
| **running** | A Jarvis-Agent is still working | Hold **Abort mission** to stop it deliberately. |
| **success** | The mission reached approval | Review and open the saved results. |
| **Needs review** | Review ended without approval, but a deliverable was retained | Inspect the file carefully or select **Restart**. |
| **cancelled** | You stopped the mission | Review any retained files or select **Continue**. |
| **error** | The mission failed, timed out, or could not finish | Review any retained files or select **Restart**. |
| **unknown** | The folder remains, but its mission record is unavailable | Review any listed files; the status cannot be reconstructed from the folder alone. |

**Continue** and **Restart** never change the original card. They dispatch a
new mission with the stored request, so you can compare the old and new runs.
If the stored request looks destructive, the first selection changes the
button to **Confirm re-run**. Select it again to proceed. While the continuation
is active, the old card shows **Continued · running**; select that indicator to
jump to the new card.

A successful card has no rerun button. To rerun the same work or produce a
changed version, return to [Chats](chats) and submit the request again. This
creates a new mission instead of a linked retry from the original card.

## Preview Files Safely

The **Results** list shows only files archived as deliverables. It does not
expose the rest of the mission folder. Worker settings, logs,
credentials-related state, review notes, browser scratch data, and diagnostic
patches are excluded from the list and its direct file links.

Markdown opened in the browser is rendered on a standalone page. Other
supported text is escaped before display. Inline HTML and Scalable Vector
Graphics (SVG) receive the same no-script browser policy. Opening any file in
another app leaves protection to that app.

Files stay on the host unless you transfer, upload, or share them. Anyone with
access to the host account or the Jarvis app can potentially read retained
outputs. Do not ask a mission to place credentials or other secrets in a
deliverable. Provider privacy still depends on the services used to run the
mission; see [Privacy and Local Data](privacy-and-local-data) before using
sensitive source material.

> [!note] The **Plan** area does not currently receive stored mission steps. It
> can show **Single-shot run** even when the work involved several steps. Use
> the status and **Results** as the current record of completion.

## Discuss an Output in Chats

Drag a mission card from Outputs. The Jarvis drop target appears while you drag;
drop the card on it to add a short recap request to the active conversation.
The recap includes the card's request, status, summary, and recorded error when
those fields are available. Jarvis answers inline instead of starting another
mission.

This action does not attach the generated files, duplicate them, or rerun the
mission. If Jarvis needs a file's contents, add that file separately and send a
clear request about it.

Card-to-chat drag and drop does not currently have a keyboard equivalent. If
you use a keyboard or screen reader, open **Chats**, identify the mission by
its request text, and add the delivered file separately when its contents are
needed.

## How It Fits Together

1. **A request starts in Chats, voice, a command, or a workflow.** Jarvis can
   delegate longer, isolated file work to a [Jarvis-Agent](jarvis-agents).
2. **The mission writes and reviews its results.** Outputs reads the retained
   mission record and shows only files archived as deliverables. A provider,
   worker, review, or safety problem can leave the mission in **error** or
   **Needs review** even when a partial file exists.
3. **Outputs lets you inspect the retained file.** Browser viewing uses the
   protected browser view described above. Local desktop actions hand the
   archived file to the host operating system and the app you choose.
4. **Approval creates a simpler host copy.** The mission card remains tied to
   cleanup of the mission folder, while the separately delivered copy does not.
5. **Chats can discuss the result.** Dragging a card adds summary context only.
   Adding a file to [Chats](chats) is a separate action that gives Jarvis the
   file's contents.

## Check That It Works

1. In Chats, start a Jarvis-Agent mission that creates a small text file with a
   three-item checklist. Do not include private information.
2. Open **Outputs** and select the newest mission. Wait for its status to become
   **success**.
3. Under **Results**, select the text file and confirm that the checklist
   appears in the expanded preview.
4. Select **Open**. In the local desktop app, choose an opener. In a normal
   browser, confirm that the text opens on a separate protected page.

The feature works when the completed mission, its deliverable, the preview,
and an appropriate open action are all available.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **No Jarvis-Agent sessions yet** | No retained file-producing mission is available | Start a concrete file task in Chats and follow its Jarvis-Agent progress. |
| An older mission is missing | Outputs shows 20 cards, or startup cleanup removed an old mission folder | Check the delivered output folder on the host. |
| **This session has no saved files** | The mission did not archive a deliverable | Read the status and failure reason, fix the provider or worker problem, then select **Restart** when available. |
| A preview is shortened, says the file is binary, or has no browser **Open** action | The file exceeds a preview limit or its format is not supported by that browser action | Use a suitable local desktop app. On a remote host, transfer the file with the host's file tools first. |
| **Open** or **Reveal in folder** does nothing | No suitable app or graphical file manager opened, or a remembered opener is unavailable | In the local desktop app, select **Change how this opens** and try another detected app. You can also locate the delivered copy manually. |

For persistent loading, connection, or provider problems, follow the main
[Troubleshooting](troubleshooting) guide.

## Next Steps

- Read [Jarvis-Agents](jarvis-agents) to understand delegation, mission
  progress, review, and recovery before a file reaches Outputs.
- Return to [Chats](chats) to request a changed version or add a delivered file
  to a conversation.
- Review [Privacy and Local Data](privacy-and-local-data) to understand where
  mission records and generated files live.
