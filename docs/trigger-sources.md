# Routine trigger sources

Routines use the existing task store, scheduler, agent permissions and execution
history. Create and manage them in an agent chat or through **Routines** and
**Automations**. A trigger decides when to submit the saved action; it does not
grant access to an account or install an external subscription.

| Group | Entry points | Requirements |
| --- | --- | --- |
| Human | Manual, chat, typed form | Authenticated app or a current user turn in the owning agent's chat |
| Time | Delay, single date, interval, calendar, five-field cron | Explicit IANA zone for wall-clock schedules |
| API | Webhook, MCP | Scoped webhook verification or authenticated Agent MCP |
| External | GitHub, Linear, Gmail, Slack, Stripe | Provider callback/subscription and verification configured in-app |
| Stream | SSE, Kafka, RabbitMQ, MQTT, Redis Streams | Reachable source; optional broker client installed in Source connection |
| System | File changes, published system events, workflow activation/failure | Existing directory or actual event publisher |
| Internal | Routine/workflow completion or failure, named integration event | Existing upstream id or integration publisher |

Examples of chat requests:

- "Check my emails every weekday at 08:00 in America/Los_Angeles."
- "Create a form with required customer name and an optional priority checkbox;
  summarize each submission."
- "When a file changes in this folder, summarize the changes."
- "When this workflow succeeds, send its result to the reporting routine."
- "Create a Stripe webhook routine for invoice.paid events."

Names and account access must resolve to actual configured resources. The agent
can save a source before it is reachable, but must report the connection as
unavailable until its listener is ready. Credentials are entered through the app's
write-only connection fields, never through chat or URL parameters.

## Source schema and entry points

The stored task trigger is `{"type":"source","source":{"kind":"manual"}}`.
Chat proposals use `schedule.kind: "source"` with the same nested source object.
`GET /api/tasks/triggers/catalog` lists groups and installed capabilities; the
agent's read-only `society_routines` tool also exposes the source JSON schema.

Manual/form submission uses `POST /api/tasks/{id}/invoke` with `{"payload":{}}`.
Chat uses the action tool `society_invoke_routine`; Agent MCP uses `routine_invoke`
and `routine_status`. Each entry point refuses routines configured for another
mode. A form validates required fields, primitive types and enumerated choices.

Cron uses `{"type":"cron","expression":"0 8 * * 1-5",
"timezone":"America/Los_Angeles"}`. The timezone is saved, never inferred from
the server on each run. DST gaps are skipped and folds fire once, on the first
occurrence. Existing calendar and downtime policies still apply; see
[calendar routines](routines.md).

## External callbacks

All providers use `POST /api/tasks/hooks/{id}`. Select the provider and use
**Connect webhook** to configure verification. GitHub uses raw-body SHA-256
HMAC; Linear additionally checks its signed webhook timestamp. Slack verifies
the timestamped v0 signature and supports the signed URL challenge. Stripe
checks its timestamped v1 signature. Gmail accepts authenticated Google Pub/Sub
push with a verified service-account identity and audience, then decodes the
notification's history id. These are notification inputs; fetching account
content still requires the agent's normal integration and permissions.

Jarvis does not create provider subscriptions, Gmail watches, renewals, broker
resources, public tunnels or DNS records. A callback needs a reachable HTTPS
endpoint. Generic authenticated webhooks and named integration events remain
available for other providers.

## Listener lifecycle and delivery limits

All new sources enter the durable hook inbox before execution. `queued` means
persisted, not completed. Limits, deduplication, pause behavior and interrupted
execution policy are shared with [routine hooks](routines.md#delivery-behavior).
Busy agent chats defer accepted work instead of losing it. Payloads are untrusted
data and cannot approve actions or change permissions.

- A listener owns its client and closes it on pause, deletion, replacement or
  shutdown. Reconnects are jittered and share the optional connection budget.
- Kafka commits the consumed partition offset after durable admission. RabbitMQ
  acknowledges messages only after admission. Redis Streams recovers pending
  consumer-group entries before reading new entries, then acknowledges them.
- MQTT requests QoS 1 and acknowledges supported deliveries after admission.
  Publisher QoS 0 remains best effort. Stable application `event_id` values
  enable deduplication across reconnects; packet ids alone are not stable ids.
- SSE persists delivered event ids and resumes with `Last-Event-ID`. Recovery
  depends on the server honoring replay; events without ids are best effort.
- File monitoring polls metadata off the event loop (default five seconds,
  configurable one second to one hour). It supports create, modify and delete,
  filename patterns and optional recursion. The first snapshot is a baseline.
  The snapshot limit is 5,000 files, symlink children are excluded, and changes
  between polls can be missed. This is not an operating-system change journal.

Broker clients are optional and imported only when selected. Source connection
shows missing dependencies, connection errors, backpressure and readiness.
The normal base install does not require a running broker.

## Workflow chains

A workflow source names `upstream_kind: "task" | "workflow"`, `upstream_id` and
`when: "succeeded" | "failed" | "activated"`. The upstream must exist. Static
routine cycles are rejected at configuration time; trusted runtime ancestry also
blocks cycles across native workflows and limits chains to 16 resources.
External payload fields cannot replace that ancestry.

The downstream receives status, ids, an output preview (up to 16 KiB), an explicit
truncation flag and a reference to the stored result. A routine may dispatch an
existing enabled native workflow using `action.kind: "workflow"` and its
`workflow_id` (chat proposals accept `payload.workflow_id`). That action completes
when dispatch succeeds. Use the native workflow's completion source to wait for
the actual workflow result. Activation events describe real enabled-state
changes, not technical listener reconfiguration.

## Verification boundaries

`test_trigger_sources.py` and `test_external_trigger_protocols.py` cover all
families, admission before acknowledgement, shutdown, typed forms, mode isolation,
cron DST behavior, raw-body signatures, real RSA-signed OIDC claims, file changes,
SSE framing and workflow ancestry. Real local Kafka, RabbitMQ, MQTT and Redis
services also passed producer-to-inbox-to-runner checks. No production account
subscriptions were created by these tests. See [OS parity](os-parity.md) for
platform and installation evidence.
