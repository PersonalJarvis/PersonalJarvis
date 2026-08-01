# Personal Jarvis — Knowledge Vault

This directory is Jarvis' **long-term memory**. Every file in here is a
piece of what he knows about you, the projects you work on, and the
tools he uses.

## What lives here

- **[`schema.md`](schema.md)** — the rules every wiki edit must follow.
  Read this first if you ever want to manually edit anything.
- **[`index.md`](index.md)** — table of contents.
- **[`log.md`](log.md)** — chronological log of every change. Append-only.
- **`entities/`** — one file per person, tool, repository, service, or device.
- **`concepts/`** — one file per abstract idea (architectures, patterns).
- **`projects/`** — one file per active or recent workstream.
- **`sessions/`** — rolling summaries of the last 3-5 conversation sessions.
- **`attachments/`** — raw source files (PDFs, screenshots) referenced by pages.
- **`_archive/`** — retired pages and old session rollups.

## How to view this

### Option A — Obsidian (recommended, free for personal use)

1. Download Obsidian from [obsidian.md](https://obsidian.md). It's free
   for personal use and works offline. No account required.
2. Launch Obsidian and pick "Open folder as vault".
3. Point it at this directory:
   `<USER_HOME>\Desktop\Personal Jarvis\data\workspace`
4. Optional but useful core plugins (toggle in Settings → Core plugins):
   Graph view, Backlinks, Outgoing links, Page preview, Templates,
   Quick switcher.
5. Optional community plugins: Dataview (lets you query frontmatter).

Once open, you can:
- Browse pages in the file tree.
- Click any `[[wikilink]]` to jump to the linked page.
- Open the Graph view to see the whole knowledge network.
- Search across all pages (Ctrl-Shift-F).

### Option B — the Jarvis Desktop App (arriving in Phase B3)

The Desktop App will get its own Wiki section with a file tree,
markdown rendering, and an Obsidian-style graph view — built on
MIT-licensed React components, no Obsidian dependency. Until B3 lands,
use Obsidian.

## How edits happen

Jarvis writes here automatically through the `WikiCurator` (Phase B1).
You can also edit files manually in Obsidian — your edits survive
subsequent curator runs as long as you keep the frontmatter and section
structure intact (see `schema.md`).

If you delete or rename a page manually, the next curator run will
notice and update backlinks. If you break the schema (missing
frontmatter, malformed wikilinks), the curator's validator will refuse
to write further updates to that page until you fix it.

## Backups

The legacy flat workspace files (`USER.md`, `MEMORY.md`,
`people/*.md`) are backed up to `data/backups/wiki-migrate-<timestamp>.tar.gz`
the first time the migration script runs. Recover via `tar -xzf
<backup>.tar.gz -C /tmp/recover/` and copy files back manually.

## What NOT to store here

- Secrets, API keys, credentials → use the Windows Credential Manager
  (Jarvis handles these via `get_secret()`).
- Conversation transcripts verbatim → that's the SQLite recall store's
  job (`data/jarvis.db`). Wiki holds the *distilled* knowledge, not the
  raw log.
- Live state ("user is in VS Code right now") → that's the short-term
  awareness layer, lives in RAM.

## See also

- `docs/adr/0013-knowledge-wiki-architecture.md` — the architecture
  decision record explaining why this exists.
- `JARVIS_AWARENESS_PLAN.md` — the short-term + mid-term memory tiers
  that feed into this vault.
