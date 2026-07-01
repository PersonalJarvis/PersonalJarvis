---
schema_version: "1"
name: plugin-supabase
description: Query and manage the user's Supabase database, tables, and projects.
when_to_use: Use when the user mentions Supabase or wants to query tables, run migrations, or inspect projects.
category: developer
plugin_id: supabase
intent_verbs: [zeig, lies, erstell, migrier, query, abfrag]  # i18n-allow
intent_objects: [supabase, supabase-datenbank, supabase-tabelle, supabase-projekt, supabase-migration]  # i18n-allow
triggers:
  - type: voice
    pattern: "(supabase|supabase-datenbank|supabase-tabelle|in supabase)"  # i18n-allow
requires_tools: [supabase]
risk_policy:
  default_tier: ask
---

Use the connected Supabase tools to query and manage the user's database and projects.

- List tables or projects before acting; reference them by name.
- Treat schema changes and migrations as consequential; confirm first.
- Summarize plainly: table or project name, row counts, key columns.
