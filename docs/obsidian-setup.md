# Obsidian Setup

## What this gives you

Personal Jarvis keeps a long-term knowledge wiki on disk as plain
Markdown. Wiring it up with [Obsidian](https://obsidian.md) gives you
a visual memory map of everything Jarvis has remembered, two-way
sync between the Desktop App and your own notes, and the comfort of
opening any of Jarvis's pages in a real editor whenever you want to
read or edit them by hand.

## Quick start

1. Install Obsidian from [obsidian.md/download](https://obsidian.md/download).
   The default installer puts Obsidian into your user profile and
   does not need administrator rights.

   ![Step 1 — Install screen](./images/obsidian-setup-step1.png)

2. Open the Jarvis Desktop App and switch to the **Wiki** tab. In
   the top-right corner you will see an orange pill labelled
   **"Obsidian: nicht registriert"** (Obsidian: not registered). Click
   it, then click **"Jetzt registrieren"** (Register now) in the dialog
   that opens. Jarvis writes the vault into Obsidian's index file and
   creates a timestamped backup of the previous version next to it.

   ![Step 2 — Register vault](./images/obsidian-setup-step2.png)

3. Click **"In Obsidian oeffnen"** (Open in Obsidian) in the same
   dialog. Obsidian opens directly into your Jarvis vault. Confirm the
   live test succeeded by clicking **"Hat geklappt"** (It worked) — the
   wizard then closes for good and never auto-opens again on this
   machine.

   ![Step 3 — Open in Obsidian](./images/obsidian-setup-step3.png)

## Troubleshooting

### "config_missing" — Obsidian not started yet

Obsidian only creates `%APPDATA%\obsidian\obsidian.json` the very
first time it launches. If the wizard reports `config_missing`, open
Obsidian once — even just briefly — close it again, then click
**"Jetzt registrieren"** (Register now) in the Jarvis wizard. The
second attempt will find the file and succeed.

### "rolled_back" — write failed and was undone

Jarvis writes the new vault entry through an atomic pipeline: backup
first, then a tempfile, then `os.replace`, then a re-read verification.
If anything in that pipeline fails, the original `obsidian.json` is
restored automatically and the wizard reports `rolled_back`.

If the automatic restore did not put `obsidian.json` back into a
state you trust, open `%APPDATA%\obsidian\` in Windows Explorer and
look for a file named
`obsidian.json.b9-backup-YYYYMMDD-HHMMSS`. Copy that file over the
real `obsidian.json` (Jarvis already did this internally, but the
backup is kept so you can verify by hand or recover from an older
state if needed).

### Vault shows in Obsidian but pages are stale

Press **Ctrl-R** inside Obsidian to force a reload. The Jarvis
Wiki-Watcher keeps the page index live-updated on the Jarvis side,
but Obsidian maintains its own file cache that occasionally lags by
a few seconds — especially right after the WikiCurator adds a new
page during a conversation.

## Where the vault lives

The vault is on disk at `wiki/obsidian-vault/` inside the Personal
Jarvis repository. Any change you make in Obsidian appears inside
the Jarvis Desktop App within seconds (the wiki-watcher monitors
that directory). Conversely, any page Jarvis adds through its
WikiCurator appears in Obsidian on the next file-system poll.

That symmetry is the whole point: one folder, two front-ends,
zero export/import.
