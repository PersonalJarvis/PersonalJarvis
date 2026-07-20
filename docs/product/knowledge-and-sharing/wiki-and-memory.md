---
title: "Wiki and Memory"
slug: wiki-and-memory
summary: "Understand what Jarvis can remember, how the Wiki connects ideas, and how memory supports chats, contacts, and agents."
section: "Knowledge and sharing"
section_order: 4
order: 1
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [wiki, memory, context, profile, contacts, privacy]
related: [profile-and-contacts, connect-obsidian, jarvis-agents, privacy-and-local-data]
---

Jarvis uses several kinds of memory for different jobs. The **Wiki** is the
long-term layer: a collection of readable notes about durable information such
as people, preferences, projects, decisions, and recurring ideas. Links between
notes show how those subjects relate.

The Wiki is not a recording of everything you say or do. Current conversation
context, saved chats, your Profile, Contacts, activity episodes, and Wiki notes
remain separate so each can keep the right level of detail and control.

## Understand the Memory Layers

| Memory area | What it is for | What it does not mean |
|---|---|---|
| **Current conversation context** | The recent messages Jarvis uses to follow the conversation you are having now | A message is not automatically a permanent Wiki fact |
| **Chat and voice history** | Stored turns and sessions that let you review or search past conversations | A transcript is not the same as a cleaned-up knowledge note |
| **Activity episodes** | Short summaries of periods of computer activity when awareness features are enabled | They are not a complete screen recording or a verified personal fact |
| **Profile** | Explicit facts and preferences about you that directly shape how Jarvis responds | Editing the Profile does not guarantee that a matching Wiki page changes |
| **Contacts** | The authoritative address book for names and contact details | Phone numbers, email addresses, and street addresses are deliberately kept out of Wiki person notes |
| **Wiki** | Durable Markdown notes that can be linked, searched, reviewed, and corrected | It is not guaranteed to capture every useful detail from a conversation |

Use the dedicated **Profile** or **Contacts** view when information must stay in
a structured field. Use the Wiki for knowledge that benefits from explanation,
history, and links to other subjects.

## Explore the Wiki

1. **Open Wiki from the sidebar.** The view opens on **Wiki · Memory Map** and
   shows the number of pages and links currently available.

2. **Read the health strips.** They show whether the vault is available,
   whether a write succeeded or failed, whether facts are waiting for review,
   and whether the search index needs rebuilding. A neutral status can simply
   mean that no write has been attempted yet.

3. **Browse by subject.** Expand **entities**, **concepts**, **projects**, or
   **sessions** in the Vault list, then choose a page. Entities are people or
   things, concepts are recurring ideas, projects describe ongoing work, and
   session pages are summaries rather than full transcripts.

4. **Use the Memory Map.** Select a node to open its page. A connection means
   that one page contains a Wiki link to another; it does not prove that the
   relationship is current or correct.

5. **Follow links and backlinks.** A link inside a page opens the referenced
   subject. **Backlinks** show pages that point to the page you are reading.
   A broken link points to a page that does not currently exist.

The in-app Wiki is a reader. To edit the underlying notes yourself, connect an
optional Markdown editor through [Connect Obsidian](connect-obsidian). Jarvis
works without Obsidian because the files remain ordinary Markdown on your
computer.

## Save Something Deliberately

Background review can discover durable information from [Chats](chats) and
voice conversations, but it may decide that a turn is temporary, already
known, unsupported, or unsafe to store. If a detail must be saved, make the
request explicit and name the Wiki, for example: `Save to my wiki that I prefer
written summaries.`

Jarvis acknowledges that the write has started, then reports whether it was
saved or failed. Wait for that completion result before relying on the note.
Open **Wiki** and inspect the relevant page when the detail matters.

> [!warning] Never ask Jarvis to remember a password, credential, recovery
> code, or access token. The write guard blocks common secret shapes, but the
> safe place for credentials is the app's protected connection screen.

## How a Conversation Becomes a Wiki Note

The background memory path uses two checks so that an early guess cannot write
straight into a page:

| Step | What Jarvis does | Possible result |
|---|---|---|
| 1. Review the turn | Looks for a durable user-stated fact; assistant text may help resolve context but is not accepted as evidence | No candidate, or a short candidate tied to the user's turn |
| 2. Compare knowledge | Reads the most relevant complete Wiki pages and checks the candidate against them | Add, update, no change, or invalidate outdated information |
| 3. Guard the write | Checks the destination, secret patterns, links, and page format; protects recent manual edits | Apply the change, leave it waiting, or reject it |
| 4. Refresh readers | Updates the local search index and Wiki status after a successful write | The revised page becomes available to Wiki recall |

This is an automated curator review, not a human approval queue. A **no change**
result usually means the Wiki already says the same thing. **Invalidate** keeps
the older information as history and marks it as superseded instead of silently
deleting it. A provider outage or malformed response is not treated as a fact
decision; the item can remain waiting or the write can fail visibly.

Automatic review favors information that should still help weeks later, such
as identity, preferences, relationships, owned items, recurring activities,
projects, plans, and decisions. Greetings, pure questions, immediate commands,
and passing status updates should not become long-term notes. The review also
scores how central each fact is to your own life, so world-knowledge trivia is
skipped while personal facts are kept.

Preferences do not have to be stated literally. When you describe first-person
experience — "I love being out on golf courses with my buddies" — the curator
may record the inferred habit, marked *(inferred)* on the page with its source
noted as behavioral. Saying it explicitly later ("Golf is my favourite sport")
upgrades the note and removes the marker. Asking about a topic ("Tell me about
Monaco") never creates a personal note. This filtering can still miss or
misclassify information, so verify important pages and correct them in the
Markdown vault when needed.

## How It Fits Together

1. **A chat or voice turn supplies possible knowledge.** Jarvis can keep the
   current exchange in conversation context and review the user-stated part in
   the background. The reply does not wait for this review.

2. **A connected provider performs the language review.** The Wiki setting
   normally follows the active Brain and chooses a lower-cost model. If that
   provider is unavailable, Jarvis can try another connected provider family.
   If none works, the conversation can still finish while the Wiki reports that
   nothing was written.

3. **The curator updates local notes.** Approved changes pass through guarded
   writes, backups, validation, and a local search index. Relevant snippets can
   then help the main Jarvis answer a later question; a slow or missing Wiki
   lookup is skipped rather than holding up the reply.

4. **Profile and Contacts remain authoritative for structured data.** The
   Profile directly supplies response preferences. Contacts keep full contact
   details. Jarvis can create a companion person note with a contact's name,
   aliases, relationship, and your note, while excluding phone, email, and
   street address. The current in-app Wiki tree does not list those companion
   contact pages, so review the authoritative record in **Contacts**.

5. **Jarvis-Agents receive only scoped access.** A mission can be granted
   short-lived, read-only Wiki tools so a Jarvis-Agent can list, search, or read
   relevant pages. It does not automatically receive the whole vault, cannot
   receive credentials through the grant, and cannot edit Wiki pages through
   that read-only surface. If the grant or Wiki is unavailable, the agent must
   continue without that context or report that it is missing.

6. **Obsidian is an optional second view of the same files.** Manual Markdown
   edits stay on your computer and can appear in the app after the Wiki refreshes.
   Obsidian does not own the data and is not required for Jarvis to run.

The storage is local, but processing is not necessarily local. When you use a
remote Wiki provider, relevant user excerpts and related Wiki page content may
be sent to that provider for extraction and review. A Jarvis-Agent using a
remote model may also process the Wiki content it explicitly reads. Review
[Privacy and Local Data](privacy-and-local-data) before storing sensitive
personal information.

## Check That It Works

1. In a chat or voice conversation, say: `Save to my wiki that I prefer written
   summaries.`
2. Wait for Jarvis to report a successful save. A message that it is still
   writing is not the final result.
3. Open **Wiki** and confirm that the health strip reports a successful last
   write rather than a pending or failed one.
4. Open the relevant profile or preference page and confirm that the statement
   appears once and in the right context.

The memory path works when Jarvis confirms the completed write, the Wiki health
stays clear, and the page contains the expected statement. Delete or correct
the test sentence in the Markdown vault if it does not describe a real
preference.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The Wiki is empty after ordinary conversations | No durable candidate has passed both review stages, or the Wiki has not completed its first write | Make one explicit, non-sensitive Wiki save request and wait for the completion result |
| **Facts are waiting to be written** | Review work is queued, the app closed before consolidation, or a provider is temporarily unavailable | Keep the app running, check the Wiki provider status, and wait before repeating the same note |
| **Chain failure** or **Last write failed** | Every eligible Wiki provider failed, timed out, or returned unusable output | Open **API Keys > Advanced > Wiki (long-term memory)**, connect or choose a working provider, then retry a harmless test note |
| **Search index** shows fewer indexed pages than vault pages | The Markdown files and the derived local index are out of sync | Select **Rebuild index**, then reopen the affected page |
| A contact's companion note is not in the Wiki tree | The current tree lists four Wiki page groups but not contact companion pages | Use **Contacts** for the saved record, or inspect the vault through Obsidian until the tree supports those pages |
| A linked page cannot be opened | The target page is missing, invalid, or the page list has not refreshed | Reopen Wiki, rebuild the index if it is stale, and correct the link in the Markdown vault if the target truly does not exist |

## Next Steps

- Read [Profile and Contacts](profile-and-contacts) to choose the authoritative
  place for personal preferences and contact details.
- Follow [Connect Obsidian](connect-obsidian) to view and edit the same Markdown
  vault with a dedicated knowledge editor.
- Read [Jarvis-Agents](jarvis-agents) to understand when a mission can receive
  scoped, read-only Wiki access.
- Review [Privacy and Local Data](privacy-and-local-data) before saving personal
  information or using a remote provider for memory processing.
