---
schema_version: "1"
name: plugin-google_drive
description: Search, read, and manage files in the user's Google Drive.
when_to_use: Use when the user mentions Google Drive or wants to search, read, share, or manage Drive files.
category: productivity
plugin_id: google_drive
intent_verbs: [zeig, lies, such, öffne, teil, lade] <!-- i18n-allow -->
intent_objects: [drive, google-drive, google-drive-datei, google-drive-ordner, gdrive]
triggers:
  - type: voice
    pattern: "(google.?drive|gdrive|in (meinem )?drive)"
requires_tools: [google_drive]
risk_policy:
  default_tier: monitor
---

Use the connected Google Drive tools to search, read, and manage the user's files.

- Search by name before opening; reference a file by title.
- Confirm before sharing or moving files.
- Summarize plainly: file name, type, owner, last modified.
