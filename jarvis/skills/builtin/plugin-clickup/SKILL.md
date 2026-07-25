---
schema_version: "1"
name: plugin-clickup
description: Read and manage the user's ClickUp tasks, lists and docs.
when_to_use: Use when the user mentions ClickUp, or a task, list, space or doc that lives there.
category: productivity
plugin_id: clickup
intent_verbs: [zeig, lies, erstell, aktualisier, zuweis, schließ, show, list, create, update, assign, close, muestra, crea, actualiza, asigna]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [clickup, click up, clickup-aufgabe, clickup-liste, clickup task, clickup list, clickup space, clickup doc]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: "(clickup|click up)"  # i18n-allow: spoken-input vocabulary
requires_tools: [clickup]
risk_policy:
  default_tier: monitor
---

Use the clickup/* tools to read and manage the user's tasks, lists and docs.
- Find the list or space before creating a task, so it lands where the user expects.
- Name the status explicitly when you change one; ClickUp statuses are per-space and easy to confuse.
- Summarize plainly: task name, list, status, assignee, due date.
