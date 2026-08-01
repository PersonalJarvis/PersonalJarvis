# Jarvis-Agents worker bridge contract

**Status:** Accepted and implemented
**Public contract:** current as of v1.2.1

This document is the public architecture contract for delegating heavy work
from the Personal Jarvis router to Jarvis-Agent workers. Historical spike logs,
private machine measurements, and maintainer-specific setup details are not part
of this public version.

## 1. Boundary

The router is a pure dispatcher. It may call the `spawn-worker` action, but a
worker must never receive a spawn, recursive skill, secret-read, or unrestricted
configuration-mutation capability. `jarvis/missions/` owns worker selection,
isolation, retries, review, cancellation, and completion.

The canonical flow is:

1. the router recognizes a heavy task and creates a mission;
2. the Mission Manager selects a capable worker family;
3. isolation creates a fresh mission worktree and per-mission state root;
4. the worker streams progress and artifacts;
5. the Critic evaluates evidence and may request a bounded retry;
6. the Controller signs the terminal result before user-facing readback;
7. output is scrubbed before voice or chat presentation.

MCP and marketplace integrations are capabilities granted through the
supervisor tool broker. They are not alternate worker harnesses.

## 2. Binding decisions

### AD-1 — streaming process contract

Subprocess workers use an explicit argument vector, UTF-8 streams, a bounded
timeout, and platform containment. Windows processes use the shared no-window
flags and Job-Object containment; POSIX processes use a new process group with
tree reaping. Shell interpolation is forbidden.

The historical label `AD-OC1` refers to this same lazy, streaming worker
resolution contract; current code and documentation use `AD-1`.

### AD-2 — one mission, one isolation root

Every mission receives a fresh git worktree below the configured portable
mission-output root. Worker HOME/state directories live inside that mission
root. Existing user HOME, credentials, and unrelated repositories are not
mounted or copied into it.

### AD-3 — supervisor owns credentials

Workers receive only the minimum environment needed for the selected provider.
The supervisor resolves secrets at spawn time through `get_secret`; credentials
never enter mission prompts, persisted config, logs, or artifacts.

### AD-4 — capability-selected workers

Selection is based on available capabilities and usable credentials, not a
hardcoded provider or model. A dead or absent provider crosses to another
available family or degrades honestly.

### AD-5 — no secondary brain tier

Only the router tier exists. The retired sub-tier and a `SUB_TOOLS` roster must
not return. Heavy work goes through the Mission Manager.

### AD-6 — provider mapping is centralized

`jarvis/missions/worker_runtime/provider_map.py` is the single source of truth
for provider slugs and process environment names. Forward, reverse, and
environment mappings are derived from the same immutable rows. Direct CLI and
keyless local workers use their dedicated selection paths rather than inventing
fake provider rows.

### AD-7 — bounded review loop

`MAX_CRITIC_LOOPS = 3` is fixed. A worker cannot approve its own result, author
its own observation, or silently convert a failed review into success.

### AD-8 — event and evidence contract

Mission events are typed, immutable, and traceable. Subscriber failure is
isolated by the EventBus. Completion requires evidence appropriate to the task;
plain model confidence is not evidence.

### AD-9 — tool trust is supervisor-scoped

A worker may edit only inside its isolated mission worktree. Delegated external
tools use a short-lived mission grant and execute through `ToolExecutor` in the
supervisor. Secrets, recursive tools, skill activation, and unrestricted config
mutation are never exported.

### AD-10 — cancellation kills the process tree

Stop, timeout, shutdown, or supervisor failure cancels the mission and tears
down the entire worker process tree. Cancellation is idempotent and produces a
terminal audited state.

### AD-11 — hangup is not cancellation

Ending a voice session does not silently kill background work. Only an explicit
stop action, mission timeout, shutdown, or policy decision cancels a mission.

### AD-12 — status is a deterministic read

Status phrases map to Mission Manager reads without an LLM guess or a new
worker spawn. The UI and API read the same persisted mission state.

### AD-13 — artifacts are explicit

Deliverables are written below the mission output root, identified separately
from logs, and exposed through the Outputs API with safe download/view handling.

### AD-14 — headless is first-class

The bridge imports and boots on a base `python:3.11-slim` environment. Missing
desktop, audio, GPU, keyring, or local CLI capabilities produce an honest
degraded result, not an import or startup failure.

### AD-15 — configuration writes are atomic

Worker-related settings use the shared config writer and validation pipeline.
A worker cannot directly rewrite `jarvis.toml`.

### AD-16 — provider/model identity is recorded

Mission telemetry records the selected worker kind, provider family, and model
without recording credentials. This identity is present on terminal events.

### AD-17 — announcements reuse the normal output path

Mission announcements use the existing EventBus announcement path and pass
through output-language resolution and `scrub_for_voice` before TTS.

### AD-18 — recovery is resumable

Interrupted missions retain enough state for safe inspection and supported
resume/retry flows. Recovery never assumes a process survived a supervisor
crash.

### AD-19 — CLI login and API-key workers are distinct

Subscription-backed native CLIs and API-key workers have separate capability
and credential probes. One path must not masquerade as the other.

### AD-20 — worker version drift is visible

External CLI compatibility is probed before use. Unsupported versions degrade
honestly and are reported by diagnostics.

### AD-21 — external worker versions are pinned or constrained

Release packaging declares tested worker compatibility. An upstream update is
accepted only after contract tests and a clean mission smoke test.

### AD-22 — progress cannot extend a task forever

Progress and stall budgets reset per unit of work but remain bounded. Repeated
heartbeat output without evidence does not defeat the mission timeout.

### AD-23 — state isolation is per mission

Any external worker state directory is placed under the mission root. The
worker cannot inherit a global persona/workspace directory that could leak
another mission's data or override the configured assistant identity.

### AD-24 — schema compatibility is explicit

Legacy configuration aliases are read-only compatibility paths. New writes use
the current schema and names. Unknown extra configuration is handled according
to the public Pydantic contract rather than silently discarded.

## 3. Architecture

### 3.1 Runtime shape

The Router creates a mission through the Mission Manager. The manager selects a
worker capability, creates an isolated worktree and state root, launches the
worker through the platform adapter, brokers permitted tools, and passes
evidence to the Critic and Controller. UI, CLI, voice, and API consumers observe
the same typed mission events; none owns a parallel worker lifecycle.

### 3.2 Anti-patterns

- **AP-OC1:** forking or patching a third-party worker invisibly;
- **AP-OC2:** shell-built process commands;
- **AP-OC3:** exposing supervisor secrets or the full environment;
- **AP-OC4:** running a worker outside a mission worktree;
- **AP-OC5:** using an LLM to invent mission status;
- **AP-OC6:** unbounded retries or critic loops;
- **AP-OC7:** a worker approving its own evidence;
- **AP-OC8:** bypassing `ToolExecutor` for delegated tools;
- **AP-OC9:** exporting recursive, skill, secret, or config-mutation tools;
- **AP-OC10:** treating voice hangup as task cancellation;
- **AP-OC11:** creating the output directory after worker spawn;
- **AP-OC12:** omitting trace, worker, provider, or model identity from events;
- **AP-OC13:** allowing implicit provider/model defaults to drift;
- **AP-OC14:** restoring the retired sub-tier or `SUB_TOOLS`;
- **AP-OC15:** sharing worker HOME/state/persona files across missions.

## 4. Integration contracts

### 4.2 Configuration schema

`[harness.jarvis_agent]` is canonical. It carries enabled state, worker
selection, notification behavior, bounded timeouts and concurrency, plus
compatible legacy read aliases. New writes use only current field names.
Provider mappings remain centralized in
`jarvis/missions/worker_runtime/provider_map.py`.

### 4.3 Setup and wizard extension

The wizard and REST settings expose capabilities, not a mandatory provider. A
downloader may select a subscription CLI, API-key family, or available local
capability. Missing credentials produce an actionable degraded state repairable
in-app; the wizard never accepts secrets through voice or chat.

## 5. Source and tests

The live implementation is under:

- `jarvis/missions/` — lifecycle, isolation, workers, Critic, Controller;
- `jarvis/plugins/tool/spawn_worker.py` — router spawn action;
- `jarvis/missions/worker_runtime/provider_map.py` — provider mapping;
- `jarvis/missions/workers/worker_tool_broker.py` — mission-scoped tool grants;
- `jarvis/ui/web/missions_routes.py` and related routes — API surface.

Primary guards live under `tests/missions/`, `tests/unit/brain/test_routing.py`,
`tests/contract/`, and worker-runtime tests. `docs/adr/0009-self-healing-worker-critic.md`
defines the bounded review loop; ADR-0025 through ADR-0027 cover tool grants,
native Windows sandboxing, and portable mission isolation.

## 11. Migration contract

The completed migration removed the retired secondary brain tier and routes all
heavy work through missions. One router spawn entrypoint remains; workers never
receive it. Legacy names are read-only aliases. Session/event schemas keep
compatible read paths while new events use current Jarvis-Agent names. Status,
cancellation, announcements, and deliverables use the shared mission APIs.

**R-6 — authored skills:** heavy skill-authoring work is a normal mission. The
result stays a draft until review; authored skill code never spawns another
worker directly or self-activates.
