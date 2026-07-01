# Optimistic Execution — runnable prototype

A **self-contained, dependency-free** demonstrator of the four pillars from the
seed `Architektur-Spezifikation: Personal Jarvis (v1.0)`:

1. **Optimistic Execution** — the Talker acknowledges instantly ("Geht klar") and
   never blocks on a tool round-trip.
2. **Router / Worker split** — a lightning-fast Talker up front; a Heavy-Duty Worker
   that does the real work asynchronously in the background, off the transcript.
3. **Smart vs. Dumb tool routing** — local "dumb" scripts fire in-process in
   milliseconds; complex "smart" (MCP) calls are delegated to the worker.
4. **The "Oops" protocol** — a background failure is injected invisibly into the
   Talker's context and surfaced as an organic spoken correction at the next
   voice turn-boundary, never mid-utterance.

> This is a **demonstrator**, not the production system. Phase 0–7 of this repo
> already implement and exceed the v1.0 vision (see the parent
> [`README.md`](../README.md) close-the-gap plan and `CLAUDE.md` → "Optimistic
> Execution & the 'Oops' Protocol"). The prototype mirrors that architecture in
> ~600 lines of pure-stdlib Python so the pattern can be run, read, and tested in
> isolation — with **zero** third-party dependencies, so it runs on a fresh
> `python:3.11-slim` container (cloud-first €5-VPS doctrine, AD-OE2: no Redis /
> RabbitMQ / Celery — the in-process `EventBus` *is* the queue).

## Run it

From this directory (`docs/plans/optimistic-execution-v1/prototype/`):

```bash
python demo.py                # scripted walkthrough of all four scenarios
python demo.py --interactive  # type your own prompts; \boundary surfaces a pending correction, \quit exits
python -m pytest tests/ -q    # the full TDD suite (122 tests)
```

The scripted demo prints, for each turn: the instant ACK **with measured latency**,
the background worker's `|bg|` log line (proving async execution), and — for the
failing mission — the invisible correction injection plus the organic turn-boundary
correction.

## What the Definition of Done looks like

```
[you]    Trag mir morgen 15 Uhr einen Termin mit dem Steuerberater ein <!-- i18n-allow: test content — user voice utterance DE -->
[jarvis] (instant, 0.05 ms)  Geht klar, ich kümmere mich drum. <!-- i18n-allow: product voice output DE -->
        |bg| optimistic.worker: Heavy-Duty-Worker processing task 025a99eb: Trag mir ...
[result] background mission done -> [calendar] '...' gesendet
```

Prompt in → **instant** reply → the Heavy-Duty Worker logs that it is processing
the task **asynchronously in the background**. Exactly the goal's success contract.

## Module map

| File | Owner | Role |
|---|---|---|
| `optimistic/events.py` | shared contract | Frozen events + `RouteKind` / `CorrectionReason` `StrEnum`s, each carrying `trace_id` + `timestamp_ns`. |
| `optimistic/registry.py` | shared contract | Tool catalog (dumb + smart) and routing data; `match_tool()` scans dumb before smart. |
| `optimistic/bus.py` | sub-agent 1 | In-process async `EventBus`: typed + wildcard fan-out, per-handler exception isolation. |
| `optimistic/router.py` | sub-agent 1 | Pure `classify()` (< 150 ms, no I/O) + `ack_for()` optimistic phrasing. |
| `optimistic/tools.py` | sub-agent 2 | `DumbTool` (instant, in-process) and `SmartTool` (async MCP sim, raises `MissingInfoError`). |
| `optimistic/worker.py` | sub-agent 2 | `HeavyDutyWorker`: schedules each `MissionSpawn` as a task and returns instantly; emits `WorkerCompleted` / `WorkerCorrectionNeeded`. |
| `optimistic/oops.py` | sub-agent 3 | `OopsProtocol`: invisible injection + VAD-gated, scrubbed organic correction. |
| `optimistic/talker.py` | orchestrator | Wires everything; emits the ACK **before** dispatch (AD-OE1). |
| `demo.py` | orchestrator | Scripted + interactive runner. |
| `CONTRACTS.md` | orchestrator | The interface spec the three sub-agents built against. |

## Architecture decisions exercised (mirror the production `CLAUDE.md`)

- **AD-OE1** ACK emitted before the worker dispatch — see `talker.py` SMART branch and `tests/test_latency.py::test_ack_emitted_before_worker_completes`.
- **AD-OE2** No external broker; the Talker never awaits the heavy work — `worker._on_mission_spawn` schedules a task and returns.
- **AD-OE3** Dumb tools never wake the worker — `tests/test_acceptance.py::test_dumb_tool_fires_in_process_without_worker` (0 false-spawns).
- **AD-OE4** Only the worker issues the (simulated) MCP call — `worker._run` → `SmartTool.execute`.
- **AD-OE5** Oops loop: invisible inject → VAD turn-boundary → scrubbed correction — `tests/test_e2e_oops.py`.
- **AD-OE6** Zero silent drops: every failure becomes a retry or a `WorkerCorrectionNeeded` — `worker._run` except-paths.

## Measured against the plan's KPIs

| KPI | Target | Prototype (in-process) |
|---|---|---|
| M1 p95 intent → ACK | < 3.0 s (< 1.2 s budget) | ~0.05 ms |
| M2 router decision | < 150 ms | < 1 ms |
| M5 dumb-tool false-spawn rate | 0 % | 0 % |

## How this was built

Test-driven, with three sub-agents dispatched in parallel against `CONTRACTS.md`
(event-bus & routing / MCP & tooling / error-handling), then integrated by the
orchestrator. The acceptance + latency + Oops tests were written **first** (RED)
and drove the build to GREEN.
