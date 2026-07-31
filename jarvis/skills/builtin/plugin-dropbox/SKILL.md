---
schema_version: "1"
name: plugin-dropbox
description: Find, read and share the user's Dropbox files.
when_to_use: Use when the user mentions Dropbox or asks for a file, folder or document stored there.
category: productivity
plugin_id: dropbox
intent_verbs: [zeig, such, lies, öffne, teil, lade, show, find, read, open, share, upload, muestra, busca, abre, comparte]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [dropbox, drop box, dropbox-datei, dropbox-ordner, dropbox file, dropbox folder, dropbox-link]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: "(dropbox|drop box)"  # i18n-allow: spoken-input vocabulary
requires_tools: [dropbox]
risk_policy:
  default_tier: monitor
---

Use the dropbox/* tools to find, read and share the user's files.
- Search by name first and confirm which file you mean before acting on it.
- Sharing creates a link that anyone holding it can open. Say so before you create one.
- Report the file name and folder, not the internal path id.
