---
schema_version: "1"
name: plugin-notion
description: Read and edit the user's Notion pages and databases.
category: productivity
plugin_id: notion
intent_verbs: [zeig, lies, erstell, such, aktualisier, ergänz]  # i18n-allow
intent_objects: [notion, seite, seiten, page, datenbank, dokument]  # i18n-allow
triggers:
  - type: voice
    pattern: "(notion|notion-seite|notion-datenbank)"  # i18n-allow
requires_tools: [notion]
risk_policy:
  default_tier: monitor
---

Use the connected Notion tools to read and edit the user's pages and databases.

- Search for the page or database before editing; reference it by title.
- Read-first; confirm before overwriting existing content.
- Summarize plainly: page title, last edited, key properties.
