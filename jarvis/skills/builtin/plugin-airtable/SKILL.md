---
schema_version: "1"
name: plugin-airtable
description: Read and edit records in the user's Airtable bases.
when_to_use: Use when the user mentions Airtable, a base, a table or records kept there.
category: productivity
plugin_id: airtable
intent_verbs: [zeig, lies, such, erstell, aktualisier, show, list, find, create, update, muestra, busca, crea, actualiza]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [airtable, air table, airtable-basis, airtable base, airtable-tabelle, airtable table, airtable-datensatz, airtable record, airtable view]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: "(airtable|air table)"  # i18n-allow: spoken-input vocabulary
requires_tools: [airtable]
risk_policy:
  default_tier: monitor
---

Use the airtable/* tools to read and edit the user's records.
- List the base and table first; field names differ per table and a guessed field silently writes nothing.
- Read before writing, and confirm before overwriting an existing record.
- Summarize plainly: table, record name, the fields you changed.
