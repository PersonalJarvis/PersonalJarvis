# Agent routines

Routines reuse the Automations task store and scheduler. Each routine belongs
to an agent, appears in its Routines rail and in Automations, and executes
through that agent's canonical chat with its current instructions and permissions.
Creating a routine never grants additional plugin permissions.

## Configuring routines by chat

Examples of supported requests:

- "Every day at 8 in the morning, summarize new email."
- "Every weekday at 09:30 Los Angeles time, prepare my briefing."
- "On the first of each month, summarize last month's results."
- "Every January 15 at noon, prepare the annual checklist."
- "Every 30 minutes, check for changes and report new findings."
- "In two hours, remind me to review the report."
- "When a mission succeeds, summarize its result in this chat."
- "Move the morning briefing to 9, pause it, resume it, or delete it."

The agent inspects existing routine IDs before editing and reads back the saved
schedule. Explicit requests use the existing direct-user authorization check;
suggestions require approval. Scheduled turns cannot authorize their own
configuration changes by quoting a previous user message.

## Time and recurrence

The browser sends its IANA timezone with each chat message. Calendar routines
persist that zone together with a local `HH:MM` time. An explicitly requested
location overrides the browser zone. Without either, the agent asks; the server
timezone is not a substitute. Traveling does not silently move existing routines:
ask the agent to change their saved zone.

Calendar filters support Monday-based weekdays, days of the month, months, and
an optional starting date. Combined filters must all match. Invalid month dates
are skipped. A nonexistent spring-forward clock time is skipped, and a repeated
autumn clock time fires only on its first occurrence. Normal daily 08:00 runs
therefore remain at 08:00 across DST transitions.

Elapsed intervals retain their existing semantics. Older interval schedules are
not guessed into calendar schedules; ask to update them. The frontend's daily
composer and new template requests now include calendar timezone information.
Legacy template API requests without a timezone retain interval semantics.

Jarvis must be running for triggers to execute. The scheduler's existing misfire
policy skips stale recurring slots after sleep or downtime rather than replaying
a backlog. The rail shows the saved zone and the next run in the viewer's zone.

## Events

`society_routines` exposes loaded event names and fields. Routine creation rejects
unknown events, unknown filter fields, and unsupported filter operations. Filters
support equality, inequality, and `and`/`or`/`not`. `max_firings: null` means a
standing rule; finite limits are persisted through the event-delivery log.

A schema's presence does not guarantee a live publisher. External inbox, file,
webhook, or application events require an integration that actually publishes
them. If unavailable, the agent explains the gap and offers interval polling.

## Verification

The portable calendar contracts exercise UTC persistence, DST gaps and folds,
fractional offsets, weekday/month/leap-day schedules, SQLite migration,
pause/resume/hydration, finite event limits, and HTTP timezone isolation.
Frontend tests cover schedule construction, both list projections and submission.
A live Gemini run with one credential and fresh isolated SQLite stores verified
chat creation and execution through the owning agent's canonical chat. This was
not a clean operating-system installation. Browser checks cover light/dark rendering
and creating a routine with the browser's timezone.
