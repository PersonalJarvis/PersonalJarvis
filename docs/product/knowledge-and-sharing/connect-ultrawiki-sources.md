---
title: "Connect UltraWiki Sources"
slug: connect-ultrawiki-sources
summary: "Connect folders, exports, feeds, and supported services to UltraWiki, then control consent, refreshes, and copied data."
section: "Knowledge and sharing"
section_order: 4
order: 7
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [ultrawiki, sources, imports, consent, privacy, sync]
related: [ultrawiki, plugins, connect-obsidian, privacy-and-local-data]
---

An UltraWiki source copies existing information into your searchable knowledge
base without editing the original. Registration saves its scope as **Pending**;
**Approve & import everything** is the separate consent step.

## Before You Start

- Finish storage and embedding setup in [Use UltraWiki](ultrawiki).
- Test a small, non-sensitive source before adding a mailbox, drive, or phone
  export.
- Connect online services under **Plugins** first. UltraWiki cannot widen that
  connection's account access.
- Never place a password, token, recovery code, or private key in a source
  label, URL, folder, chat, or voice request.

> [!warning] A local source can still leave the device during later model
> processing. Remote embedding, vision, speech-to-text, distillation, rerank,
> and answer providers may receive the relevant content.

## Choose a Source

Open **Wiki > Ultra > Sources**, select **Add source**, and choose the smallest
boundary you need.

| Source | What it copies | Important boundary |
|---|---|---|
| **Local folder** | Supported files and media below one folder | Hidden, dependency, cache, and application-data folders are skipped |
| **Obsidian vault** | Visible Markdown notes | Vault settings and trash are skipped; the vault is not edited |
| **Normal Wiki** or **Conversations** | Existing app knowledge or conversation records | UltraWiki makes a separate indexed copy |
| **Export or import** | A staged service or phone archive | Preview reports what is recognized, skipped, or unreadable |
| **Connected app** | Records visible through a supported plugin connection | The provider account and granted scopes decide what is visible |
| **Custom source** | RSS/Atom or mapped items from a JSON HTTP endpoint | Private network addresses are blocked unless deliberately allowed |

### Local folders and files

Enter the root and optional comma-separated exclusions. UltraWiki does not
follow symlinked directories and skips nested linked Git worktrees, which are
usually generated project copies. Selecting a linked worktree itself as the
root is an explicit opt-in.

Stable identities prevent duplicates within one source. Connecting overlapping
roots or the same export twice still creates independent copies.

### Staged exports and phone media

Choose **Export or import**, select a path or upload one file, then select
**Preview**. Upload only stages a copy; preview reads it and reports estimated
mail, event, chat, table, archive, media, and skipped-file counts without
registering or importing. Create and approve the source only when that report
matches your intent.

Common phone material includes contacts, calendars, WhatsApp text exports,
photos, recordings, and video. Keep the original until processing finishes;
media enrichment may need to reopen it.

### Supported connected services

Supported sources are GitHub, Notion, Slack, Google Drive, Gmail, Google
Calendar, Asana, Linear, Todoist, ClickUp, Dropbox, Airtable, Discord, and
Telegram. Connect under **Plugins**, then select its ready tile. Disconnected
tiles are disabled; unverifiable connections are withheld.

GitHub imports visible issues and pull requests. Other connectors follow the
documents, messages, events, tasks, or records their connection can read.
Telegram starts with messages delivered to the bot after connection; it cannot
backfill earlier chats. Source revocation does not revoke the plugin login.

### RSS and generic HTTP

The reader supports public RSS/Atom or JSON HTTP with mappings for identity,
title, body, date, link, and author. Authentication refers to a credential
already stored by the app, never one in source configuration. Only web URLs
are accepted; responses are bounded and private networks are denied by default.

The current desktop **Custom source** card does not expose those fields. Do not
approve an empty source or invent values; use a connected service or export
until the complete form appears in your build.

## Approve, Sync, and Watch Progress

Add an optional label and area, select **Create source**, review its scope, then
select **Approve & import everything**. One job runs per source. Its card shows
the phase and imported count; many providers do not reveal a reliable total.

Compare **Captured**, **Keyword-indexed**, **Embedded**, **Distilled**, and
**Failed**. **Sync now** requests newer material. Scheduled refreshes run only
in Ultra mode, skip active jobs, cap parallel work, and delay retries after a
failure. Full reconciliation detects removals only where supported.

Media is searchable by filename, folder, date, and metadata before enrichment.
The policy is **Frugal** by default (one item while other stages are idle),
**Eager** (one alongside other work), or **Off**. Images use an available
vision-capable provider; recordings use compatible configured speech-to-text.
**Sources** reports outcomes but does not expose this advanced switch.

## Stop or Remove a Source

- **Cancel** stops the current job. It is not a permanent pause.
- Switching to **Normal** mode deactivates UltraWiki processing and scheduled
  refreshes without deleting its store.
- **Revoke** stops future source reads but keeps imported and derived data.
- Disconnecting or deleting without purge disables the source and keeps its
  copied data.
- A **purge** removes its items, derived records, and sync state from the active
  store, not identity audit history, exports, backups, originals, provider
  data, or copies retained by remote processors.

The current **Sources** screen exposes **Revoke**; delete and purge are separate
control actions. There is no permanent per-source **Pause** state. Cancel a job
temporarily, or revoke when refresh permission must end.

## How It Fits Together

1. A connector reads within its approved boundary and captures a copy.
2. Keyword indexing enables exact matches; embedding and optional distillation
   add meaning search, topics, people, and moments.
3. Throttled freshness jobs revisit approved sources.
4. Ask retrieves evidence from the copy before a compatible answer provider
   composes a cited response.
5. Obsidian import reads Markdown into UltraWiki. Explore export creates a
   separate generated Markdown projection and leaves **My notes** untouched.
   Do not reconnect that export as a source; it can duplicate knowledge. See
   [Connect an Obsidian Vault](connect-obsidian).

## Check That It Works

1. Confirm the source says **Approved** and its active job reaches completion.
2. Check that **Captured** is greater than zero and **Failed** is zero, or open
   the visible failure reason.
3. Open **Contents**, filter by this source, and inspect one harmless record.
4. Ask a question answered by that record and verify its numbered citation
   opens the expected original or captured evidence.

## Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| Source remains **Pending** | It was registered but not approved | Review its scope, then approve it deliberately |
| Folder imports nothing | The path is missing, unreadable, excluded, hidden, or a skipped linked worktree | Select the intended root and reduce exclusions; do not broaden it blindly |
| Connected-app tile is disabled or absent | The plugin is disconnected, needs renewed access, or its state could not be verified | Repair it under **Plugins**, then return to Sources |
| Job appears slow | Indexing and media are throttled background work | Watch stage counts; use **Sync now** only after the current job ends |
| Some media has no description or transcript | Processing is off, no compatible provider is ready, the file moved, or the format or size was rejected | Open the item's visible reason and keep the original available |
| A removed provider item remains | Incremental sync did not observe a deletion | Run the available full reconciliation path, or purge the source if the entire copy must go |

## Next Steps

- Read [Use UltraWiki](ultrawiki) to search, ask with citations, and inspect
  topics, moments, and people.
- Review [Plugins](plugins) before changing a connected service's access.
- Read [Privacy and Local Data](privacy-and-local-data) before importing a
  personal archive or enabling remote processing.
