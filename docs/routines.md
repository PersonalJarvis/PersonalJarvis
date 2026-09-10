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
or application events require an integration that actually publishes
them. If unavailable, the agent explains the gap and offers interval polling.

## Webhooks and integration event hooks

Ask an agent, for example: "Create a webhook routine that summarizes a new
customer, but only when customer.vip is true." The saved trigger is
`{"kind":"webhook","conditions":{"customer.vip":true}}`. A named integration
event uses `{"kind":"event_hook","event_name":"crm.customer.created"}`.
Both kinds accept optional `conditions`, `max_firings` and `cooldown_seconds`.
Conditions compare scalar JSON fields; dotted paths access nested objects.

Use **Connect webhook** on the agent's routine or in its expanded Automations
row. The app shows the endpoint and a masked, copyable credential. Credentials
are stored through the existing portable secret store and never included in the
routine specification or returned by the agent's routine tools. Rotation revokes
the old credential without changing other routines.

Send a JSON object to `POST /api/tasks/hooks/{task_id}` using
`Authorization: Bearer <token copied in the app>`. GitHub-compatible senders can
instead use the same token as their webhook Secret: the receiver checks
`X-Hub-Signature-256` against the exact raw body, following
[GitHub's signature format](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).
External services need a reachable HTTPS address with the corresponding host
configured in Jarvis. Creating a routine does not open a tunnel or expose the
desktop automatically.

Authenticated integrations can publish named events to `POST /api/tasks/events`
with `{"event_name":"crm.customer.created","payload":{"customer":{"vip":true}}}`.
This endpoint needs the normal Control API credential or authenticated UI; a
single routine's webhook token cannot publish events or access other APIs.
It returns admission results for matching routines and cannot impersonate an
internal system event. Python integrations may publish `RoutineEventReceived`
on the existing EventBus after the task scheduler has bound its subscriptions.

### Delivery behavior

- A `queued` response means the payload was persisted, not that the task finished.
  Execution goes through the owning agent's current policy and canonical chat.
- Reuse `Idempotency-Key` for Bearer-authenticated retries. Signed requests use
  the signed body hash so changing an unsigned delivery header cannot replay it.
  The latest 4,096 delivery receipts per routine are retained for deduplication;
  pending deliveries are never pruned.
- Payloads are limited to 32 KiB, admission to 60 deliveries per minute per
  routine, and outstanding work to 100 deliveries. Cooldowns and finite lifetime
  budgets are checked before admission. Filtered payloads consume no budget.
- Pending work survives restart and waits while a routine is paused. New requests
  to paused or exhausted routines are refused. Deleting the task revokes its URL.
- Interrupted running work is recorded but never automatically replayed because
  an external side effect may already have happened. Inspect its result before
  manually retrying. Execution errors remain visible in the normal task history.
- External payloads are appended as untrusted data, not as new user instructions.
  Hooks grant no additional tool permissions and cannot authorize self-configuration.

`tests/contract/test_routine_hooks.py` covers scoped credentials, signatures,
tampering, replay, boolean filter roundtrips, durable queues, pause/resume,
limits and OpenAPI payload schemas. A live single-key Gemini check created a
webhook through chat, delivered an HTTP payload to fresh isolated SQLite stores,
executed the owner's chat, and suppressed a repeated delivery. Native macOS/Linux
execution and a completely fresh OS installation are not claimed.

## Verification

The portable calendar contracts exercise UTC persistence, DST gaps and folds,
fractional offsets, weekday/month/leap-day schedules, SQLite migration,
pause/resume/hydration, finite event limits, and HTTP timezone isolation.
Frontend tests cover schedule construction, both list projections and submission.
A live Gemini run with one credential and fresh isolated SQLite stores verified
chat creation and execution through the owning agent's canonical chat. This was
not a clean operating-system installation. Browser checks cover light/dark rendering
and creating a routine with the browser's timezone.
