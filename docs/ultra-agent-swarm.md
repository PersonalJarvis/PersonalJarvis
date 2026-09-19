# Ultra Agent Swarm

Ultra Agent Swarm creates temporary teams with their own task graph, event
history, collaboration records, artifacts, budgets and live team view. Its stores
and event stream are separate from ordinary agents and the Society world.

## Local operation

Open **Ultra Agent Swarm**, enter a natural-language goal or drop a UTF-8 text
or Markdown prompt file, and select **Clarify the goal**. Jarvis asks up to three
questions specific to the goal. Answer them or explicitly ask Jarvis to propose
an assumption, then select **Create plan**. Review the goal, acceptance checks,
assumptions, exclusions and initial work steps before **Approve plan and start
swarm**. Files are limited to 20,000 characters / 80 KB.
Questions, answers and the candidate plan survive navigation and restart. The
team remains created with no execution tasks until launch; planning model calls
count toward the same team's usage. Launch is bound to the exact saved plan
revision, digest and storage generation, and retry cannot launch another copy.
Additional acceptance criteria, budgets, access and capacity are optional under
**Options**. The default entry bounds a run to 250,000 tokens and 30 minutes;
an additional monetary ceiling is optional.
The logical worker ceiling is 1,000, with actual workers allocated only for work.
Local execution reserves capacity for coordination within its 32 active-call
limit. Available distributed execution is selected automatically; its configured
capacity still applies. A large worker ceiling does not create empty workers or
guarantee that a goal fits the budget.

Start dispatches work through a configured API brain that supports scoped execution.
Provider setup remains in the application. Missing provider capability or quota
is reported; a team does not silently gain a host coding terminal.

The lead plans work, coordinates workers and submits results for verification.
Pause and resume retain the team records. Stop or cancel prevents further
dispatch and fences outstanding attempts. A completed, failed or canceled team
remains inspectable. Each team opens its own lightweight activity simulation:
agent assignments, task dependencies, tool activity and recent durable events.
Select an agent, task or event to inspect its evidence. This default view needs
no WebGL; the existing 3D view is optional. Neither view invents progress.

Generated JavaScript runs in a fresh Wasm instance using the bundled QuickJS
interpreter. It has bounded memory, fuel, time and output, and inherits no host
filesystem, environment variables, subprocesses or sockets. Internet access
uses the team's explicit HTTP tool policy and byte quota. Native Python, shell
and CAD execution are not supplied by this sandbox; unsupported work must be
reported rather than executed on the host.

Usage is recorded per provider request. Exact decimal counters preserve large
token totals across storage, APIs and the UI. When a canceled or interrupted
request has no reliable final usage report, its reservation remains pending;
the system must not invent zero usage or claim the unreported cost was free.

In three measured Grok runs, explicit owner stop left no locally owned work
after 63-71 ms; pending exposure stayed reserved. Exhausting a budget prevents
new requests but lets already reserved calls settle, which took 19.46 seconds
in the separate measured run. These observations do not prove that remote
computation or billing stops at disconnection. See the [measurements and limits](verification/ultra-swarm-cancellation.md).

## Storage and recovery

The [storage fault measurements](verification/ultra-swarm-storage-faults.md)
cover real Redis restart, S3 outage/recovery and operator-triggered object expiry
for a deleted team. Scheduled background expiry remains a separate deployment
responsibility; these tests do not claim it was observed.

Local Swarm data lives under `swarm/` inside the application's configured data
directory. A team has its own database and object directory. Catalog creation
is lazy; opening the ordinary application does not create a team. Normal
installation and update preparation checks the offline prerequisites and
migrates existing team schemas without modifying ordinary-agent memory.

**Create backup** produces a downloadable ZIP containing one consistent team
snapshot, its referenced objects, and the bytes and provenance of its selected
publications. It excludes unrelated teams and credentials. The receipt includes
the archive size and SHA-256; the archive records individual file hashes.
Keep downloaded backups outside the installation directory.

**Restore backup** accepts an exported ZIP, including from another installation.
Replacing an existing team requires a separate confirmation for that identity.
The old local directory or PostgreSQL schema is kept in quarantine, execution
credentials are rotated, and nonterminal restored work is paused. Unlaunched
preparation drafts stay created and require a current plan approval. Completed or
canceled snapshots retain their terminal state; restoring never starts work.
Interrupted validated imports remain visible under **Storage & recovery** and
can resume even before their team becomes visible. Retrying an operation does
not stop a replacement that has already been installed. Deleted local team
identities remain reserved in that installation, so delayed cleanup cannot
delete restored data. Restore their backups into a separate installation.

The portable format accepts at most 512 MiB per archive, 1 GiB expanded and
20,000 file entries. Duplicate paths, traversal, links, excessive compression,
unsupported schemas and executable SQLite schema objects are rejected.
Distributed table hashes, columns and object provenance are checked before
live work is fenced. Its row constraints are tested in a temporary PostgreSQL
schema whose transaction is always rolled back. **Recover storage** remains the
lighter retry for schema
and interrupted-attempt recovery; corrupt bytes need a valid backup.

**Delete team** requires confirmation, stops active work and removes its
workspace. Completed published output remains in the separate archive. The
saved export files remain independent backups. Distributed object bytes follow
the configured bucket's lifecycle policy after their PostgreSQL references are
removed; the deletion receipt reports that distinction. The
explicit 30-day cleanup removes expired delivered messages, old unreferenced
local objects, old backup files and completed recovery copies, including
quarantined PostgreSQL schemas. It retains tasks, referenced evidence, published
content and pending recoveries. Cleanup processes at most 2,000 historical
operations per request; repeat it when a large backlog remains.

Artifact and publication downloads read in 64 KiB chunks, with at most eight
open streams per service. Stream acquisition and cleanup retain ownership even
when a client disconnects during a blocking storage call. Backup uploads allow
two concurrent requests and use bounded multipart headers and disk spooling.
S3 snapshot transfers occur outside PostgreSQL authority transactions.

Publication is an explicit selection of an artifact. The separate publication
archive retains that selected content when temporary team data is deleted;
unselected task conversations and artifacts do not become ordinary memory.

## Optional distributed mode

Distributed setup requires three services chosen by the user: PostgreSQL for
authoritative state, Redis Streams for delivery hints, and an S3-compatible
bucket for objects. Configure them in the application's Swarm storage setup.
Passwords and object credentials are write-only inputs backed by the existing
secret service; public settings retain credential references and endpoints.
Remote PostgreSQL verifies certificate and hostname, Redis uses TLS, and object
storage uses HTTPS. Public deployment must not disable those checks.

The PostgreSQL role needs permission to create the Swarm catalog and team
schemas. Superuser and row-security-bypass roles are rejected. Redis is not the
task authority: delivery can be retried from the durable PostgreSQL outbox.
Object uploads reserve quota before I/O and finalize only while their attempt
is still authorized. An interrupted upload retains its reservation for recovery.
Connection pools, admission, delivery batches and world snapshots are bounded.

Source installations leave the three service clients optional until setup.
Native desktop installers carry those clients, their TLS roots, the S3 service
models and binary libpq so the same setup works without an external Python
installation. Those bundled clients do not install or start any external service.

For maintainers, prepare the isolated native build environment with
`python -m pip install -e '.[desktop,dev,swarm-distributed]'`, then use the
platform script under `packaging/`. The shared spec fails when a required
client is missing or outdated, or a native library is missing. It preserves
sibling wheel libraries such as `psycopg_binary.libs`; collecting Python imports
alone is insufficient.
This follows the [PyInstaller hook contract](https://pyinstaller.org/en/latest/hooks.html).

## Verification boundaries

The contract suites cover scoped storage, fenced claims, budgets, actual Wasm
isolation, provider behavior and distributed adapters. Packaging tests exercise
Windows, macOS and Linux wheel layouts and the native release workflow. These
are distinct from executing a signed installer on each native OS. See the
[OS parity register](os-parity.md) for which native checks remain unverified.

A Windows frozen-runtime probe has loaded all optional clients and TLS/S3
data, created a local team and produced the expected value `49` using the
bundled interpreter. It made no external service or model request. Its first
run correctly exposed an outdated certificate package; the build now rejects
that version mismatch before freezing.

A fresh `python:3.11-slim` Linux container installed the base wheel, passed
`pip check` and 402 Swarm tests, and served the headless application over HTTP.
The Stage-2 installer entry ran fresh and again after reinstalling the wheel.
A legacy Swarm schema migrated once; ordinary memory, Society data,
configuration and team identities were preserved. This is a synthetic legacy
profile upgrade test, not a download of a previous public release. The model
provider was synthetic, while sandbox execution used the real Linux Wasmtime
library. No audio device, GPU, distributed service or live model key was used.

Large historical catalogs and synthetic simultaneous workers measure storage
and scheduling behavior. They do not prove the same concurrency against a live
model provider, whose quota, latency and cost remain external constraints.
