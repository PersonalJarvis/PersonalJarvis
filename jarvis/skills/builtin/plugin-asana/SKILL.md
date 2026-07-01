---
schema_version: "1"
name: plugin-asana
description: Manage the user's Asana tasks and projects.
when_to_use: Use when the user mentions Asana or wants to view, create, assign, or close Asana tasks and projects.
category: productivity
plugin_id: asana
intent_verbs: [zeig, lies, erstell, zuweis, schließ, aktualisier] <!-- i18n-allow -->
intent_objects: [asana, asana-task, asana-aufgabe, asana-projekt, asana-team]
triggers:
  - type: voice
    pattern: "(asana|asana-task|asana-projekt|in asana)"
requires_tools: [asana]
risk_policy:
  default_tier: monitor
---

Use the connected Asana tools to manage the user's tasks and projects.

- Search or list tasks before acting; reference a task by name.
- Confirm before closing or reassigning.
- Summarize plainly: task name, project, assignee, due date.
