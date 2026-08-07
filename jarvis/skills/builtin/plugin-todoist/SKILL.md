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
  # The bare "to-?do" alternative also matched Spanish "todo" / "todos"
  # ("everything" / "all"), so ordinary Spanish speech pulled in a task skill —
  # the cross-language face of the substring defect behind BUG-121. A separator
  # or an explicit list noun is now required, which Spanish "todo" never has.
  # The separator alone was still not enough: "to do" with a SPACE is the
  # plain English infinitive, and "prompt terminal T1 to do a deep dive"
  # pulled this skill into a coding-workspace turn at score 1.0 (live
  # 2026-08-06 18:49). Singular "to do" therefore counts only with a hyphen
  # or a following list noun; the spaced form matches only as the plural
  # "to dos", which the infinitive never produces.
  - type: voice
    pattern: "\\b(todoist|aufgabenliste|einkaufsliste|to-dos?|to\\sdos|to[-\\s]do[-\\s]list\\w*|todo[-\\s]?list\\w*|shopping list|lista de tareas)\\b"  # i18n-allow: spoken-input vocabulary
requires_tools: [todoist]
risk_policy:
  default_tier: monitor
---

Use the todoist/* tools to read and manage the user's tasks and projects.
- Search or list before creating, so a task that already exists is updated rather than duplicated.
- When the user gives a date or time in words, resolve it to a concrete due date and say back what you set.
- Adding and completing tasks is safe to do directly; report the task title afterwards.
- Reminders and labels need a paid Todoist plan. If a call fails for that reason, say so plainly instead of retrying.
