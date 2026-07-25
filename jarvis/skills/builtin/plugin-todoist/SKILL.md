---
schema_version: "1"
name: plugin-todoist
description: Read and manage the user's Todoist tasks and projects.
when_to_use: Use when the user mentions Todoist, a task, a to-do, a shopping list, or asks what is due.
category: productivity
plugin_id: todoist
intent_verbs: [zeig, lies, erstell, ergänz, erledig, plan, show, list, add, create, complete, remind, muestra, añade, crea, completa]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [todoist, todoist-aufgabe, todoist-liste, todoist task, todoist list, todoist projekt, todoist project, einkaufsliste, shopping list, lista de la compra]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: "(todoist|aufgabenliste|einkaufsliste|to-?do|shopping list|lista de tareas)"  # i18n-allow: spoken-input vocabulary
requires_tools: [todoist]
risk_policy:
  default_tier: monitor
---

Use the todoist/* tools to read and manage the user's tasks and projects.
- Search or list before creating, so a task that already exists is updated rather than duplicated.
- When the user gives a date or time in words, resolve it to a concrete due date and say back what you set.
- Adding and completing tasks is safe to do directly; report the task title afterwards.
- Reminders and labels need a paid Todoist plan. If a call fails for that reason, say so plainly instead of retrying.
