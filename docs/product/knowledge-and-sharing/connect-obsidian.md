---
title: "Connect an Obsidian Vault"
slug: connect-obsidian
summary: "Use an existing Obsidian vault as a readable knowledge source while keeping file ownership and backups clear."
section: "Knowledge and sharing"
section_order: 4
order: 2
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [obsidian, wiki, markdown, vaults, backups]
related: [wiki-and-memory, privacy-and-local-data, troubleshooting]
---

Connect Obsidian when you want to browse and edit Jarvis's long-term wiki in a
dedicated Markdown editor. Obsidian is optional: Jarvis keeps using the same
plain-text wiki files when Obsidian is closed or not installed.

The connection is a shared-folder arrangement, not a cloud synchronization
service. Jarvis and Obsidian read the same Markdown files on your device. The
setup does not upload your notes, copy an entire existing vault into Jarvis, or
turn Jarvis backups into a device-to-device backup plan.

## Before You Start

- Install Obsidian from the [official Obsidian download page](https://obsidian.md/download).
  You do not need to open it first — when Obsidian has never been launched,
  Jarvis creates the local vault list during registration and Obsidian adopts
  it on its first start.
- If you want to use an existing vault, open or create that vault in Obsidian
  before starting the Jarvis connection (the existing-vault picker can only
  offer vaults Obsidian already knows). Close Obsidian before registration so
  both apps do not try to update its local vault list at the same time.
- Decide who should back up the folder. Jarvis keeps limited recovery snapshots
  around its own writes, but you still need your normal backup or version-history
  plan for important notes and attachments.

> [!warning] A vault can contain private notes. Review what is already in the
> folder before connecting it, and do not store credentials or recovery codes in
> wiki pages.

## Choose a Vault Arrangement

The setup dialog offers two choices. Neither choice sends the vault to an
online service.

| Choice | Where Jarvis works | What happens to other notes | Best when |
|---|---|---|---|
| **Create a separate Jarvis vault** | Jarvis uses its own wiki folder and adds that folder to Obsidian's local vault list | Your other Obsidian vaults stay separate | You want the clearest ownership boundary |
| **Use my existing vault** | Jarvis creates or uses a `Jarvis` subfolder inside the vault you select | Notes outside that subfolder are not imported, searched, or changed by Jarvis | You want Jarvis pages visible beside an established vault |
| Do not connect Obsidian | Jarvis keeps its wiki as local Markdown and shows it in **Wiki** | Obsidian is not involved | The device is headless, or you prefer the built-in view |

Choosing an existing vault does not make its whole contents part of Jarvis
Memory. Only the nested `Jarvis` folder becomes the active Jarvis wiki. The
setup also does not move pages from a previously connected location into the
new folder.

## Connect the Vault

1. **Open Wiki.** Select **Wiki** in the desktop app. On the first visit, the
   setup dialog may open automatically. Otherwise, select the **Obsidian: not
   installed**, **not registered**, or **status unclear** status pill.

2. **Finish the Install step.** If needed, use the download link, install and
   start Obsidian once, then return to Jarvis and use the installation step's
   continue button. Detection is best effort, so an uncommon installation
   location can still appear as not detected.

3. **Choose where the wiki belongs.** Select **Create a separate Jarvis vault**
   for an isolated folder, or **Use my existing vault** and select a vault from
   the list. The existing-vault option remains unavailable until Obsidian has a
   registered vault that Jarvis can read from its local vault list.

4. **Select Register now.** For a separate vault, Jarvis safely adds its folder
   to Obsidian's local vault list. For an existing vault, Jarvis creates the
   nested `Jarvis` folder, saves it as the active wiki location, and rebuilds
   the derived search index for that location.

5. **Restart when asked.** The existing-vault choice shows **Restart now**
   because the running file watcher and memory writer still point to the old
   location until Jarvis restarts. Do not start editing the new folder before
   that restart finishes.

6. **Run the live test.** Choose **Open in Obsidian**. Obsidian should open the
   connected folder through its local link handler. Return to Jarvis and
   confirm that the live test worked only after you see the expected vault or
   folder.

## Understand File Ownership

| Item | Primary owner | What Jarvis does |
|---|---|---|
| Markdown inside the active Jarvis wiki | You | Reads it, may add or update guarded wiki pages, and makes visible Markdown searchable |
| Notes elsewhere in an existing vault | You and Obsidian | Leaves them outside Jarvis Memory, search, and automated writes |
| Obsidian settings, themes, and plugins | Obsidian | Does not manage them; setup only reads the vault list and may add the separate Jarvis vault entry |
| Jarvis search index | Jarvis | Rebuilds this derived data from the active wiki; it is not the source copy of your notes |
| Wiki recovery snapshots | Jarvis | Creates rolling snapshots before Jarvis-originated page writes; they are not a complete backup service |
| Your external backup or sync service | You | Jarvis does not configure, monitor, or guarantee it |

Manual edits in Obsidian change the source Markdown directly. They do not go
through Jarvis's guarded writer, so they do not create an immediate Jarvis
snapshot. If a note matters, keep it in a backup system you control even when
Jarvis snapshots are present.

## What Synchronization Means Here

1. **Both apps point to one folder.** There is no export and no second copy to
   reconcile.
2. **Obsidian saves a Markdown change.** Jarvis's file watcher notices visible
   Markdown changes and updates its derived text-search index. Hidden Obsidian
   configuration and non-Markdown files are not added to wiki text search.
3. **Jarvis writes a wiki page.** Its guarded writer validates the change,
   creates a recovery snapshot, and avoids overwriting a page that was edited
   very recently. Obsidian sees the resulting file through its normal folder
   monitoring.
4. **Either app can be closed.** Files remain on disk. When the app opens again,
   it reads the same active folder.

This process does not synchronize devices, merge conflicting edits from a
cloud service, preserve every historical version, or copy notes outside the
active Jarvis wiki. If an external sync tool edits files simultaneously, its
conflict handling belongs to that tool.

## Backups and Safe Recovery

Connecting a separate vault changes Obsidian's local vault list. Jarvis first
keeps a timestamped copy, writes the updated list atomically, and verifies the
result. If verification fails, it attempts to restore the original and reports
that the operation was rolled back. The retained copy is for recovery; it is
not a backup of the Markdown pages themselves.

Jarvis-originated wiki updates use a different recovery layer: a limited,
rotating snapshot of the curated wiki content is taken before the update.
Archived content and attachments are not a complete part of that safety net.
Keep a separate backup of the whole folder before moving it, changing external
sync settings, or making a large manual edit.

The current connected status pill does not provide a disconnect or move action.
If you selected the wrong location, do not drag the active folder elsewhere or
edit application configuration by hand. Make a full copy of the vault, leave
the original in place, and follow [Troubleshooting](troubleshooting) before
changing the connection.

## Platform and Headless Limits

Jarvis checks common Obsidian application and configuration locations on
Windows, macOS, and Linux. Package formats or custom install locations can make
the status appear unclear even when Obsidian is present. Opening through the
**Open in Obsidian** button also requires a graphical desktop and a working
`obsidian://` link handler.

On a headless server, skip the Obsidian connection. The Jarvis wiki can still
exist as local Markdown, and Jarvis can still use its own wiki features. The
desktop-only live test and external editor launch are unavailable there.

## How It Fits Together

1. **Wiki and Memory supplies the content.** Jarvis records selected durable
   knowledge as Markdown in the active wiki folder; Obsidian is an optional
   second view of those files.
2. **The vault choice defines the boundary.** A separate vault isolates Jarvis
   pages. An existing vault keeps Jarvis inside one nested folder and leaves
   neighboring notes alone.
3. **The watcher connects file edits to search.** Changes to visible Markdown
   update Jarvis's derived index. If that index becomes stale, **Rebuild index**
   recreates it from the files without rewriting the pages.
4. **Privacy follows the folder.** Local storage avoids an automatic upload,
   but Obsidian plugins or an external sync service can have their own data
   behavior. Review [Privacy and Local Data](privacy-and-local-data) before
   enabling either.
5. **Recovery protects writes, not every storage failure.** Jarvis validates
   and snapshots its own changes, while full-folder backups and external sync
   conflicts remain your responsibility.

## Check That It Works

1. In **Wiki**, confirm the status pill reads **Obsidian: connected** and the
   health strip shows the expected active vault location.
2. Choose **Open in Obsidian** during the setup live test and confirm the
   connected vault or nested `Jarvis` folder appears.
3. Edit one harmless Markdown sentence in that folder and save it. Return to
   Jarvis, open the same page, and confirm the saved text appears. If search is
   stale, use **Rebuild index** and search again.

The connection works when both apps show the same saved file from the same
folder. A successful open alone does not prove that an external cloud sync or
backup service is working.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Obsidian: not installed** after installation | Jarvis did not find the app in a common location, or setup has not refreshed | Start Obsidian once, return to the setup dialog, and retry. A custom install may require using Jarvis without the external-editor launch. |
| **Use my existing vault** is unavailable | Obsidian's local vault list is missing or has no readable registered vault | Open the desired vault in Obsidian, close Obsidian, then reopen the Jarvis setup dialog. |
| Registration asks you to open Obsidian first | The vault-list write failed unexpectedly (Jarvis normally creates the list itself when Obsidian has never been launched) | Open Obsidian once, close it, and select **Register now** again. |
| Registration reports a rollback or unclear status | The local vault-list file could not be read, updated, or verified safely | Stop retrying, confirm Obsidian still opens normally, and keep the recovery copy. Follow the main troubleshooting guide before any manual restore. |
| Obsidian opens, but Jarvis shows old search results | The files and the derived search index are out of step, or the watcher was unavailable | Confirm the health strip names the expected vault, select **Rebuild index**, and try the search again. |
| An existing-vault connection shows no older notes | Jarvis intentionally reads only the nested `Jarvis` folder | Move or copy only the notes you deliberately want Jarvis to use, after backing them up and reviewing their privacy. |

## Next Steps

- Read [Wiki and Memory](wiki-and-memory) to understand what Jarvis remembers,
  when it writes a page, and how recall uses the wiki.
- Review [Privacy and Local Data](privacy-and-local-data) before enabling an
  Obsidian plugin or external service that may send vault data off the device.
- Use [Troubleshooting](troubleshooting) for stale indexes, unclear health
  states, failed restarts, or recovery help beyond this connection flow.
