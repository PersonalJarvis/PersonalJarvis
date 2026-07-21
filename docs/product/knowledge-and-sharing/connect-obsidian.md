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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [obsidian, wiki, markdown, vaults, backups]
related: [wiki-and-memory, privacy-and-local-data, troubleshooting]
---

Connect Obsidian to open and edit the Markdown files behind Jarvis's Wiki.
Obsidian is optional. Jarvis keeps using the same local files when Obsidian is
closed or not installed.

This connection shares one folder. It is not a cloud sync service, an import
of your whole Obsidian vault, or a replacement for backups.

## Before You Start

- Install Obsidian from the [official Obsidian download page](https://obsidian.md/download).
  Registration can create Obsidian's local vault list if the app has never
  been opened, but starting Obsidian once also registers its link handler on
  most Windows and macOS systems.
- Run Jarvis and Obsidian as the same operating-system user. Jarvis needs read
  and write access to its Wiki folder. A separate-vault connection also needs
  write access to Obsidian's user configuration.
- To use an existing vault, open or create it in Obsidian first. The picker
  only lists vaults recorded in Obsidian's local vault list. Close Obsidian
  before registration so its configuration does not change at the same time.
- Back up important notes and attachments before changing their location or
  connecting another sync tool. Jarvis snapshots cover only part of the Wiki.

> [!warning] A vault can contain private notes. Review the selected folder
> before connecting it, and never store credentials or recovery codes in Wiki
> pages.

## Choose a Vault Arrangement

| Choice | Where Jarvis works | What happens to other notes | Best when |
|---|---|---|---|
| **Create a separate Jarvis vault** | Jarvis keeps using its own Wiki folder and registers that folder with Obsidian | Other Obsidian vaults remain separate | You want a clear boundary around Jarvis pages |
| **Use my existing vault** | Jarvis creates or reuses a `Jarvis` subfolder inside the selected vault | Notes outside `Jarvis` are not read, searched, or changed by Jarvis | You want Jarvis pages beside an established vault |
| Do not connect Obsidian | Jarvis keeps its local Markdown Wiki and shows it in **Wiki** | Obsidian is not involved | You use a headless device or prefer the built-in view |

The existing-vault choice does not move pages from the current Wiki into the
new `Jarvis` folder. It also does not copy the parent vault into Jarvis.

## Connect the Vault

1. **Open Wiki.** Select **Wiki** in the desktop app. The setup dialog may open
   on your first visit. Otherwise, select the **Obsidian: not installed**,
   **Obsidian: not registered**, or **Obsidian: status unclear** status pill.

2. **Complete the Install step.** Install and start Obsidian if needed, then
   return to Jarvis and select **I installed it**. Detection checks
   common installation locations and can miss a custom installation.

3. **Choose the folder boundary.** Select **Create a separate Jarvis vault**,
   or select **Use my existing vault** and choose a registered vault. The
   existing-vault choice is disabled when Jarvis cannot read any entries from
   Obsidian's local vault list.

4. **Select Register now.** Separate-vault mode adds Jarvis's current Wiki
   folder to Obsidian's vault list. Existing-vault mode validates the selected
   vault, creates its `Jarvis` subfolder, saves that as the active Wiki root,
   and attempts to rebuild the derived search index.

5. **Restart Jarvis when prompted.** Existing-vault mode requires a restart so
   the Wiki writer and file watcher use the new folder. Wait for the restart
   before expecting Jarvis to read or write there.

6. **Test the handoff.** Select **Open in Obsidian**. Jarvis sends the absolute
   folder path to the local `obsidian://` link handler. Select **It worked**
   only after Obsidian shows the expected vault or nested `Jarvis` folder.
   Jarvis cannot detect that result automatically.

## Folders and Templates

Jarvis treats the active Wiki root as its complete boundary. Its structured
pages normally live in `entities`, `concepts`, `projects`, and `sessions`.
Root Markdown pages and Markdown in other visible folders can also appear in
the built-in Wiki.

The separate Jarvis vault includes its own Obsidian settings and starter
content, including `00-index`, `90-attachments`, and `99-templates`. Template
pages remain visible in the file tree but are not included in Jarvis search or
the knowledge graph.

Existing-vault mode only creates the nested `Jarvis` folder. It does not copy
the separate vault's starter pages or templates, and it does not change the
parent vault's themes, plugins, attachment settings, or template settings.
Jarvis creates the working folders and schema it needs after restart or when it
first writes a page.

## Understand File Ownership

| Item | Primary owner | What Jarvis does |
|---|---|---|
| Markdown inside the active Wiki root | You | Reads visible pages, may add or update structured pages, and indexes eligible Markdown for search |
| Notes outside `Jarvis` in an existing vault | You and Obsidian | Leaves them outside Jarvis Memory, search, and automated writes |
| Obsidian settings, themes, and plugins | Obsidian | Reads the vault list and, in separate-vault mode, may add one vault entry |
| Jarvis search index | Jarvis | Rebuilds this derived data from the active Wiki; the index is not the source copy |
| Wiki recovery snapshots | Jarvis | Takes limited snapshots before Jarvis-originated page updates; these are not complete backups |
| External backup or sync | You | Jarvis does not configure, monitor, or guarantee it |

Manual edits in Obsidian change the source Markdown directly. They bypass
Jarvis's guarded writer, so they do not create an immediate Jarvis snapshot.
Keep the frontmatter and page structure intact on Jarvis-managed pages, and
use a backup system you control for important files.

## What Sharing the Folder Means

1. **Both apps point to the same files.** There is no export or second copy to
   reconcile.
2. **Obsidian saves a Markdown change.** When the watcher is running, Jarvis
   updates its derived full-text search index after the file-system event.
   Hidden folders, archives, attachments, and `99-templates` are excluded from
   that index.
3. **Jarvis writes a Wiki page.** Its writer validates the proposed change,
   takes a recovery snapshot, and skips a page that was edited too recently.
   Obsidian then sees the saved file through its normal folder monitoring.
4. **Either app can close.** The Markdown remains on disk and is read again
   when the app starts.

This does not synchronize devices, merge cloud conflicts, preserve every
version, or copy notes outside the active Wiki root. Conflict handling for an
external sync service belongs to that service.

## Backups and Safe Recovery

Separate-vault mode changes Obsidian's local vault list. If that list already
exists, Jarvis copies it to a timestamped recovery file, replaces it
atomically, and verifies the new entry. If verification fails, Jarvis attempts
to restore the original. On a new Obsidian installation, Jarvis creates the
list instead and removes the new file if verification fails. These safeguards
cover Obsidian's vault list, not your Markdown pages.

Jarvis-originated Wiki updates use a different recovery layer. The rotating
snapshots omit some content, including archives and attachment storage, and a
manual Obsidian edit does not trigger one. Keep a separate backup of the whole
folder before a large edit, move, restore, or sync change.

The connected status pill has no disconnect or move action. If you selected
the wrong location, do not drag the active folder elsewhere or edit
application configuration by hand. Copy the vault to a safe location, leave
the original in place, and follow [Troubleshooting](troubleshooting) before
changing the connection.

## Platform and Headless Limits

Jarvis checks common Obsidian application and configuration locations on
Windows, macOS, and Linux. A custom location or some Linux package formats can
leave the status as unclear even when Obsidian is installed.

**Open in Obsidian** requires a graphical desktop and a registered
`obsidian://` handler. Starting Obsidian once is normally enough on Windows
and macOS. Linux installations may need additional desktop-file setup; see
Obsidian's [official URI troubleshooting](https://help.obsidian.md/Extending%2BObsidian/Obsidian%2BURI#Troubleshooting).

On a headless server, skip the Obsidian connection. The local Markdown Wiki,
Wiki API, and Jarvis memory features can still work, but the desktop setup
dialog and external-editor launch are unavailable.

## How It Fits Together

1. **Wiki and Memory supplies the content.** Jarvis records selected durable
   knowledge as Markdown in the active Wiki root. Obsidian is an optional
   editor for those files.
2. **The vault choice sets the boundary.** A separate vault isolates Jarvis
   pages. An existing vault contains them in one nested folder and leaves
   neighboring notes alone.
3. **The watcher connects edits to search.** Eligible Markdown changes update
   the derived search index. If it becomes stale, **Rebuild index** recreates
   it from the current files without rewriting them.
4. **Privacy follows the folder.** Jarvis does not upload the vault during
   setup, but an Obsidian plugin or external sync service may have its own data
   behavior. Review [Privacy and Local Data](privacy-and-local-data) before
   enabling one.
5. **Recovery protects specific writes.** Jarvis validates and snapshots its
   own page updates. Full-folder backups and external sync conflicts remain
   your responsibility.

## Check That It Works

1. After any requested restart, open **Wiki** and confirm that the status pill
   reads **Obsidian: connected**. Check that the health strip shows the
   expected active Wiki path.
2. Open a Wiki page and select **Open in Obsidian**. Confirm that Obsidian
   opens that same file in the expected vault.
3. Change one harmless sentence in Obsidian and save it. Return to the same
   page in Jarvis and confirm the saved text appears. If only search is stale,
   select **Rebuild index** and search again.

The connection works when both apps show the same file from the same folder.
This test says nothing about an external cloud sync or backup service.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Obsidian: not installed** after installation | Jarvis did not find Obsidian in a supported location | Start Obsidian once and select **I installed it** again. If detection still fails, use the built-in Wiki and follow the main troubleshooting guide. |
| **Use my existing vault** is unavailable | Obsidian's vault list is missing, unreadable, or contains no entries | Open the desired vault in Obsidian, close Obsidian, then reopen the Jarvis setup dialog. |
| Registration reports that it could not register | Jarvis could not read or write the selected folder, Obsidian's vault list, or Jarvis's own configuration | Run both apps as the same user and check read and write permissions. Do not repeatedly retry a permission error. |
| Registration reports a rollback or unclear status | Obsidian's local vault list could not be updated or verified safely | Confirm that Obsidian still opens normally and keep the recovery copy. Follow the main troubleshooting guide before attempting a manual restore. |
| **Open in Obsidian** does nothing | The local URI handler is missing or the selected path is not inside a registered vault | Start Obsidian once. On Linux, follow the official URI-handler instructions, then retry from the same Wiki page. |
| Obsidian opens, but Jarvis search is stale | The watcher or derived search index missed a change | Confirm that the health strip shows the expected path, select **Rebuild index**, and search again. |
| An existing-vault connection shows none of the parent vault's older notes | Jarvis intentionally reads only the nested `Jarvis` folder | Copy only the notes you deliberately want Jarvis to use after reviewing their privacy and making a backup. |

## Next Steps

- Read [Wiki and Memory](wiki-and-memory) to understand what Jarvis remembers,
  when it writes a page, and how recall uses the Wiki.
- Review [Privacy and Local Data](privacy-and-local-data) before enabling an
  Obsidian plugin or external service that may send vault data off the device.
- Use [Troubleshooting](troubleshooting) for stale indexes, unclear health
  states, restart failures, or recovery help beyond this connection flow.
