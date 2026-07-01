---
schema_version: "1"
name: plugin-linear
description: Manage the user's Linear issues, projects, and cycles.
when_to_use: Use when the user mentions Linear or wants to view, create, update, or close Linear issues and cycles.
category: developer
plugin_id: linear
intent_verbs: [zeig, lies, erstell, aktualisier, schließ, zuweis]  # i18n-allow
intent_objects: [linear, linear-issue, linear-ticket, linear-projekt, linear-cycle, linear-zyklus]  # i18n-allow
triggers:
  - type: voice
    pattern: "(linear|linear-issue|linear-ticket|in linear)"  # i18n-allow
requires_tools: [linear]
risk_policy:
  default_tier: monitor
---

Use the connected Linear tools to manage the user's issues, projects, and cycles.

- Search or list issues before acting; reference an issue by its identifier.
- Confirm before closing or reassigning.
- Summarize plainly: issue title, identifier, state, assignee.
