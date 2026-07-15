---
title: "Privacy and Local Data"
slug: privacy-and-local-data
summary: "Learn what Jarvis stores locally, what may be sent to a connected service, and how each optional feature changes that boundary."
section: "Privacy, safety, and support"
section_order: 6
order: 1
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [privacy, local-data, retention, deletion, providers, backups]
related: [credentials-and-secrets, permissions, wiki-and-memory, jarvis-board]
---

Jarvis keeps much of its working history on the computer or server where the
app runs. It contacts another service only when a feature needs that service,
such as a remote Brain, speech provider, connected tool, phone service, or a
sharing destination you choose.

This page explains those boundaries and their current limits. It is a product
map, not a promise about a provider's own storage, training, or deletion rules.

> [!info] **Local** means the Jarvis host. If Jarvis runs on a remote server,
> its local databases and files are on that server, not automatically on the
> laptop or phone used to open the app.

## Follow One Request

A normal request can cross several boundaries:

1. **You provide input.** Chat text, microphone audio, a dropped file, a
   screenshot, or a scheduled task starts the work.
2. **Jarvis adds context locally.** Depending on the feature, this can include
   recent conversation history, standing instructions, a Profile summary,
   contact names and relationships, or relevant Wiki excerpts.
3. **The selected capability processes it.** A local provider works on the
   Jarvis host. A remote provider or connected service receives the input and
   context required for its part of the request.
4. **Tools act within their own boundary.** A local command can stay on the
   host, while a plugin or Model Context Protocol (MCP) connection can send its
   arguments to another service. Safety approval controls whether an action may
   run; it does not make a remote request local.
5. **Jarvis stores the result that belongs to the feature.** A chat reply,
   voice transcript, task timeline, mission record, Wiki update, output file,
   or Board total can be saved in a different local store.

## Know What Is Stored

Jarvis currently uses several local storage areas rather than one single data
file. The important areas are the installation data folder, the app's user-data
folder, the Wiki vault, the Jarvis-Agent output area, and any exports you save.

| Area | What it can contain | Current retention or removal boundary |
|---|---|---|
| Chats | Text-thread metadata and the messages that are successfully persisted | Text threads are pruned after 365 days at startup. **Delete** removes one text thread, not related audit records or provider copies. |
| Voice sessions | Transcripts, replies, provider and model names, tool names, timings, token and cost totals, and selected event details | The default retention is 30 days. Sessions can be copied or saved, but the current app has no per-session delete action. |
| Tasks and missions | Task instructions, schedules, steps, results, errors, mission prompts, review events, and status history | A finished task can be deleted. Mission database history has a separate life and is not removed with the task. |
| Profile and Contacts | Profile fields and observations, an optional avatar, contact details, aliases, relationships, and notes | You can clear Profile fields, remove the avatar, and delete contacts. Clearing a field does not automatically remove earlier observations that mention it. |
| Wiki and memory | Markdown pages, contact companion notes, candidate facts, a derived search index, archives, and local recovery snapshots | Wiki content persists until changed locally. Curator actions archive instead of hard-delete, and the current Wiki view has no permanent-delete control. |
| Jarvis-Agents and Outputs | Mission work areas, logs, reviews, diffs, and approved deliverables | Mission output folders older than 14 days are removed during the normal startup cleanup. Files copied to Downloads or another destination are separate and remain there. |
| Jarvis Board | Daily totals, tool categories, streaks, achievements, generated biography text, and reactions | These derived summaries are local and have no current clear-all control. Older summaries can remain after their source voice sessions are pruned. |
| Local audit data | Daily event logs and captured screenshot blobs used for diagnostics and replay | The flight recorder is on by default. Screenshot blobs are removed after 10 days by default; the daily event logs do not currently have the same age-based cleanup. |

The embedded app also keeps small interface preferences, such as panel sizes,
sound choices, and an optional Board share handle, in browser-style local
storage. Clearing that storage resets the preferences but does not remove the
databases and files listed above.

### Understand What Is Not Saved Together

One request can leave records in more than one place. Deleting a chat does not
delete a task it started, a Wiki fact learned from it, a Jarvis-Agent mission,
an output file, a Board total, an exported transcript, or an audit-log event.

Text chat history also has a current saving gap: user messages are not always
written to the Chat store even though replies may be. This does not mean the
input was never processed or recorded elsewhere. Do not use an empty reopened
chat as proof that no local or remote copy exists.

## Know What Can Leave the Jarvis Host

| Feature | What another service may receive | When it happens |
|---|---|---|
| Chat and the Brain | Your request, recent context, standing instructions, relevant Profile or Wiki context, dropped text, images, and tool results | When the selected Brain is remote rather than local |
| Voice | Microphone audio for remote speech recognition or Realtime voice, transcripts for the Brain, and reply text for remote speech output | While you use the corresponding remote voice capability |
| Vision and Computer Use | Screenshots, visible window context, the requested action, and action results | When a remote vision or action model is needed; approvals still apply to consequential actions |
| Plugins, MCP, and CLI connections | The arguments and context passed to the tool, plus any result it returns | When you or Jarvis runs that connection; a local CLI can itself contact services outside Jarvis |
| Jarvis-Agents | The mission request, selected files or repository context, scoped tool calls, worker output, and review material | When a remote worker or model handles the mission |
| Wiki processing | Candidate facts and relevant page excerpts | When a remote Wiki extractor or curator reviews a possible memory update |
| Board and sharing | A share card when you send it; approved aggregate statistics when optional federation is enabled | Only for the chosen share destination or configured federation, except for the biography limitation below |
| Feedback and community links | Whatever you post or attach, under the destination's rules | The current Feedback screen opens an external community site; Jarvis does not attach the active chat automatically |

Phone calls and connected messaging channels necessarily pass audio or messages
through their configured services. Once data reaches a provider, channel,
community, or recipient, its retention and deletion rules apply separately.
Removing the local Jarvis record does not send a deletion request to them.

An installation can also expose an optional feedback relay through the Control
API. When an operator has connected that relay and someone uses it, the report
title and description, an optional screenshot, the app and runtime versions,
the host operating-system description, and the submission time are sent to
Discord. The current Feedback screen does not use this relay; it opens the
community site so you can review and submit the post yourself.

> [!warning] Never put a password, API key, recovery code, or other credential
> into chat, voice, a Wiki page, a task, feedback, or an output file. Use the
> protected fields in **API Keys & Providers**. Pattern-based guards reduce some
> accidental exposure, but they do not recognize every secret or personal fact.

### Current Board Biography Limitation

Viewing or refreshing the Board does not publish its statistics. Optional
Board federation is also off by default.

A separate background biography feature is less obvious: after Jarvis has
enough activity, it can ask the currently selected Brain provider to summarize
Board facts. That request can include tool and task totals, activity timing,
selected recent activity summaries, mission statistics or titles, and an older
biography. The current Board screen does not show this biography or provide an
on/off control. Disabling federation does not disable this Brain request.

### Current Audit and Redaction Limitation

The flight recorder stores the event stream for diagnostics, not only anonymous
counts. Its daily files can contain message text, transcripts, action details,
provider metadata, and other event fields. The current app has no in-app switch
or clear button for this store, and only its screenshot blobs have the default
10-day cleanup.

Jarvis masks common credential shapes in selected session previews and refuses
Wiki writes that match several credential patterns. The flight recorder does
not pass every event through that same masking step. These guards reduce risk;
they are not a complete personal-data or secret filter.

## Remove or Back Up Data

### Remove data with the right expectation

| What you want to remove | Available action | What can remain |
|---|---|---|
| A text conversation | Use **Delete** in Chats | Voice sessions, tasks, Wiki changes, outputs, Board totals, exports, audit events, and provider-side records |
| A Profile detail or avatar | Clear the field or remove the avatar in Profile | Earlier Profile observations, Wiki facts, backups, and context already sent to a provider |
| A contact | Use **Delete** in Contacts | Its Wiki companion page is moved to the Wiki archive; it can retain the name, aliases, relationship, and note, but not phone, email, or street address from the contact record |
| A task | Cancel it if active, then delete it after it reaches a final state | Mission history, generated outputs, audit events, and actions already completed |
| A voice session, Board history, or audit log | No item-level removal control is available in the current app | The applicable automatic retention may remove some data later, but not every related or derived record |
| A saved export or output copy | Delete the file with the operating system or destination app | The original Jarvis record and any copies, backups, recipients, or provider records |

Jarvis does not currently provide one **Export all my data** or **Delete all my
data** workflow. If complete removal matters, treat each local store, every
export, every backup, and every connected service as a separate copy.

### Back up the full set

The Wiki curator keeps up to ten recent local recovery snapshots before its
own changes. Those snapshots are for rollback, not a complete personal backup:
Wiki archives and attachments are excluded.

For a broader backup, close Jarvis first and include all of these locations in
your operating-system backup:

- the installation data folder, including conversations, sessions, tasks,
  missions, Profile files, memory databases, and audit logs;
- the app user-data folder, including Contacts, Board data, user skills, and
  preferences;
- the active Wiki vault, including its archive and attachments, plus the Wiki
  recovery-snapshot folder if you want rollback history;
- the Jarvis-Agent output area and any exported copies in Downloads or another
  folder you chose.

An Obsidian Sync account, cloud-drive folder, system backup, or server snapshot
creates another copy governed by that system. Check its retention before using
it for sensitive material.

## How It Fits Together

1. **Chats and Voice collect requests.** The active Brain may receive recent
   context, Profile guidance, contact summaries, and Wiki excerpts. Speech and
   vision services receive only the media needed for their capabilities, but a
   remote service remains an external boundary.
2. **Tasks preserve future work.** A scheduled action can later call the Brain,
   a plugin, MCP server, or CLI. Deleting the task does not undo an action that
   already ran.
3. **Profile, Contacts, and Wiki supply durable context.** Profile describes
   you, Contacts stores an address book, and Wiki keeps longer-lived knowledge.
   A contact mirror deliberately leaves phone, email, and street address out of
   the Wiki, but names, relationships, aliases, and notes can be copied there.
4. **Jarvis-Agents create a separate mission trail.** Their prompts, worker
   evidence, and files live outside Chats. Approved files appear in Outputs and
   can be copied again to a user-visible folder.
5. **Board derives summaries.** It reads supported activity and session records
   into smaller totals. Sharing those totals is optional, but the background
   biography can still use the selected Brain provider as described above.
6. **Feedback is a deliberate external handoff.** The current screen opens a
   community destination. Review text and screenshots yourself before posting;
   Jarvis does not treat a safety approval as permission to publish private
   context.

If a provider or connection is unavailable, the corresponding remote step can
fail or use another compatible provider you configured. Local records and
unrelated features remain available, but switching providers changes who
receives the next request; it does not move or erase older provider-side data.

## Check That It Works

1. Start a new text chat and send a harmless request that contains no personal
   information.
2. Confirm that the reply appears, then return to **History** and delete that
   text conversation.
3. Confirm that the conversation disappears from Chats.
4. Open **Sessions**, **Outputs**, and **Board**. Confirm that deleting the text
   thread did not claim to clear those separate areas.

This check confirms the current store-specific delete behavior. It does not
prove that audit logs or a remote provider deleted their records.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A deleted item still appears in another view | The other view uses a separate source, archive, or derived summary | Identify the feature that owns that copy and use its removal option where available; do not repeat the delete blindly. |
| An old voice session disappears | The session crossed its retention window | Check any transcript export you intentionally saved. The app does not restore a pruned session from Board totals. |
| An output is missing but a downloaded file remains | Startup cleanup removed the mission folder, while the exported copy has its own life | Keep or delete the downloaded copy with the operating system. |
| A Wiki search result survives a local edit | The derived search index has not caught up | Open Wiki and use **Rebuild index**. Also check the Wiki archive and recovery snapshots when removal matters. |
| You expected a request to stay local | A remote Brain, speech service, plugin, MCP server, channel, or worker handled part of it | Review the selected provider and connected features before the next request. Choose a local capability where one is available. |
| You need a complete data export or erasure | The app has no global workflow for it yet | Back up what you need, then handle each Jarvis store, export, backup, and provider account separately. |

## Next Steps

- Read [Credentials and Secrets](credentials-and-secrets) to understand where
  connection credentials belong and how to replace or remove them safely.
- Review [App Permissions](permissions) to grant only the microphone, screen,
  notification, and file access required by the features you use.
- Use [Wiki and Memory](wiki-and-memory) to understand durable facts, local
  indexing, archives, and remote curator processing in more detail.
- Read [Jarvis Board](jarvis-board) before sharing statistics or enabling an
  experimental connection to another Board service.
