---
plugin_id: asana
keywords: asana, aufgabe, aufgaben, task, tasks, projekt, projekte, project, projects, deadline, fällig, fälligkeitsdatum, due, assignee, zuweisen, team, workspace, section, abschnitt  # i18n-allow: German keyword-matching vocabulary for plugin relevance routing
---
Use asana/* tools to create, update, search, and complete tasks and projects.
- Resolve the workspace and project GIDs before creating tasks; do not guess IDs.
- Assign due dates in ISO 8601 format (YYYY-MM-DD); do not assume timezone.
- Create and update tasks directly (full autonomy); report task name, GID, and URL afterwards.
- Use search to find existing tasks before creating duplicates.
