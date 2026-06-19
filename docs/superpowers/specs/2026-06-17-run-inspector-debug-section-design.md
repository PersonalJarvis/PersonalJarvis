# Run Inspector — per-run debugging & analysis section

- **Status:** Draft (approved direction, pending spec review)
- **Date:** 2026-06-17
- **Type:** Additive UI section + read-only analytics layer
- **Sibling spec:** `2026-05-27-subagent-section-design.md` (same "new section" wiring pattern), `2026-05-30-chats-conversation-manager-design.md` (Sessions/Chats data sources)

---

## 1. Context & motivation

The Transcription section (`SessionsView.tsx`, label "Transkription") is the
*readability lens* on a voice session: it renders a session turn-by-turn for a
human who wants to re-read the conversation. It deliberately hides the
mechanics.

The maintainer needs a second, *forensic* lens on the **same** unit of work —
a "Run", defined as the complete interaction from `Hey Jarvis` wake to hangup.
The goal is debugging and analysis: which plugins fired, how the brain decided
what to do, where each processing step spent its time, and exactly where and
why a failure happened.

The key finding from codebase exploration: **almost nothing new needs to be
captured.** The system is already richly instrumented. A Run is reconstructable
from data that already exists:

- The session is a first-class entity — `voice_sessions` + `voice_turns` +
  `voice_events` in `chats.db`, written by the read-only recorder
  (`jarvis/sessions/recorder.py`, a latency-neutral wildcard EventBus
  subscriber).
- The FlightRecorder writes every bus event to `data/flight_recorder/*.jsonl`
  with `trace_id` + `timestamp_ns`, including `LatencySpan` events with a
  defined `LatencyPhase` vocabulary and SLO budgets.
- Tool/CLI invocations live in `cli_usage.db` (`cli_invocations`: name,
  exit code, duration, risk-tier, `trace_id`, caller).
- Sub-agent missions live in `missions.db`, optionally linked to a session via
  `session_id` on `WorkerSpawned` / `WorkerDraftReady`.

This is therefore primarily an **aggregation + UI** task, not a telemetry
build-out. The one genuine capture gap (granular brain thinking) is explicitly
deferred to v2.

### Correlation asymmetry (drives the data model)

`session_id` binds all turns of a session; **each turn has its own
`trace_id`.** The FlightRecorder and latency tracking are `trace_id`-centric
(per-turn granularity); the Sessions DB is `session_id`-centric (aggregation
over turns). The loader therefore joins on two keys: `session_id` to gather the
turns, and each turn's `trace_id` to gather that turn's fine-grained events.

---

## 2. Goals & non-goals

### Goals (v1 — full scope, approved)

1. A standalone sidebar section ("Run Inspector") listing past runs with
   debug-oriented badges (latency status, tool count, error marker).
2. Master-detail layout: pick a run, see a session summary, then each turn as an
   expandable card with five panels.
3. Five per-turn panels covering the six requirements:
   - **Timeline** — chronological event trail (the "thinking path" as sequence).
   - **Latency waterfall** — per-phase breakdown with SLO traffic-light colors.
   - **Decision Path** — reconstructed routing / risk / brain / mission decisions.
   - **Tools** — plugin/CLI activity (name, duration, exit code, risk-tier, approval).
   - **Errors** — `ErrorOccurred` / `ActionDenied` / `MissionFailed` / CU-failure detail.
4. Cost/token summary in the turn header and session header.
5. **Live mode** — when the view is open, the currently-running run streams in
   over a WebSocket channel.
6. Raw export (JSONL / copy-to-clipboard), reusing the Transcription export UX.
7. Additional derived telemetry (§7) surfaced where it already exists in the
   event stream.

### Non-goals

- **No new write path on the hot path.** The layer is derive-on-read only. It
  must never run on the voice critical path (AP-9).
- **No new SQLite database** and no schema migration. Existing stores are the
  source of truth.
- **No granular brain streaming/thinking capture in v1.** The reconstructed
  Decision Path is sufficient. Persisting `BrainThinkingStep` is a gated v2
  item (§8).
- Not a metrics/SLO dashboard across many runs (cohorts, p50/p95 over time) —
  that is a possible later analytics view, out of scope here.
- Not a replacement for the Transcription view. Two lenses, one data source.

---

## 3. Key decisions (locked with the maintainer)

| Decision | Choice | Consequence |
|---|---|---|
| Scope | **Read-only analytics layer** (`jarvis/runs/`, no new DB) | Additive read/aggregation only; zero hot-path risk; fastest path. |
| Navigation level | **Session as container, turns expandable** | Master-detail like Transcription; per-turn forensic drill-down. |
| Live vs. post-hoc | **Both: history + live** | REST for completed runs + a WS channel for the in-flight run. |
| Primary audience | **Maintainer debugging first** | Optimize depth for the workstation; still degrade cleanly on a VPS (doctrine remains binding). |
| v1 reach | **Full: all 5 panels + live** | Granular brain-thinking step stays v2. |

---

## 4. Architecture

A second lens on the existing session aggregate. No competing data store.

```
jarvis/runs/
  ├── __init__.py
  ├── constants.py   # NEW enum SSOTs: SLO status, decision-step kind (anti-drift)
  ├── model.py       # Run, RunTurn, RunAnalytics — read-only Pydantic DTOs (no SQL)
  ├── loader.py      # load_run(session_id) -> Run  (joins the existing stores)
  ├── analyzer.py    # derive metrics: latency waterfall + SLO status, tool mix, costs
  └── routes.py      # GET /api/runs, GET /api/runs/{session_id}, WS /api/runs/live

jarvis/ui/web/
  └── server.py      # app.include_router(runs_router)   (one line, like sessions)

jarvis/ui/web/frontend/src/
  ├── views/RunInspectorView.tsx
  ├── components/runs/
  │     ├── RunList.tsx           # left master list, debug badges
  │     ├── RunDetail.tsx         # session header + turn accordion + export
  │     ├── TurnTrace.tsx         # one expandable turn card hosting the 5 panels
  │     ├── TimelinePanel.tsx
  │     ├── LatencyWaterfall.tsx
  │     ├── DecisionPath.tsx
  │     ├── ToolTable.tsx
  │     └── ErrorPanel.tsx
  ├── hooks/useRuns.ts            # React-Query (history) + WS subscription (live)
  └── components/runs/api.ts      # fetchRuns / fetchRunDetail / runExportUrl
```

### Data flow

```
History:
  GET /api/runs                 -> list[RunListItem]      (newest-first, capped)
  GET /api/runs/{session_id}    -> Run
        loader.load_run(session_id)
          ├─ session_store.get_session(session_id)        # voice_sessions
          ├─ session_store.get_turns(session_id)          # voice_turns
          ├─ session_store.get_events(session_id)         # voice_events (per-session, indexed)
          ├─ usage_log.by_trace(turn.trace_id) for each   # cli_invocations
          └─ missions_store.by_session(session_id)        # optional MissionRef
        analyzer.analyze(run) -> attaches RunAnalytics + per-turn derived fields

Live:
  WS /api/runs/live             -> streams the same events the recorder sees,
                                   scoped to the active session_id; the open
                                   view appends them to the in-flight run.
```

### Why `voice_events` is the primary source (not the FlightRecorder JSONL)

`voice_events` is already keyed by `session_id` (and carries `turn_id`,
`ts_ms`, `kind`, `payload_json`) and is SQL-indexed. The FlightRecorder JSONL
is day-partitioned and **not** session-indexed, so reconstructing one session
from it means scanning a day file and filtering by the session's `trace_id`s —
expensive and only worth it for a raw deep-dive. Therefore:

- **Primary:** `voice_events` + `voice_turns` for timeline, tools, errors,
  decision path, costs.
- **On-demand only:** FlightRecorder JSONL for the optional Raw-export deep-dive
  per run.

**Implementation check (must resolve during planning):** confirm whether
`LatencySpan` events are in the recorder's `voice_events` whitelist
(`_RAW_EVENT_KINDS` in `jarvis/sessions/recorder.py`). If yes, the latency
waterfall reads straight from `voice_events`. If no, choose one:
(a) add `LatencySpan` to the whitelist (cleanest; per-session indexed; affects
**new** sessions only — historical runs predate it and show "no latency data"),
or (b) read latency lazily from the FlightRecorder day-file for that run. The
spec assumes (a) is preferred; the plan confirms and, if taken, documents that
pre-feature runs have no waterfall.

---

## 5. Data model (read-only DTOs)

No SQL. Pydantic models that compose existing rows + derived fields.

```python
# jarvis/runs/model.py  (illustrative shape, finalized in implementation)

class RunListItem(BaseModel):
    session_id: str
    started_ms: int
    ended_ms: int | None
    duration_s: float | None
    hangup_reason: str            # string, NOT Literal (BUG-008 defense)
    wake_source: str              # voice | hotkey | channel:<name>
    turn_count: int
    total_cost_usd: float
    error_count: int
    slo_status: str               # worst SLO status across turns (see constants)
    preview: str                  # first user utterance, truncated

class ToolCall(BaseModel):
    name: str
    caller: str                   # router_tool | openclaw_worker | ...
    risk_tier: str                # safe | monitor | ask | block
    approved_by: str | None       # auto | user | whitelist | blacklist | None
    duration_ms: int | None
    exit_code: int | None
    success: bool
    error_line: str | None        # scrubbed stderr ERROR line if present

class LatencyEntry(BaseModel):
    phase: str                    # LatencyPhase value (reused, not redefined)
    duration_ms: float
    slo_status: str               # ok | warn | breach (see constants)

class DecisionStep(BaseModel):
    kind: str                     # see RUN_DECISION_KINDS in constants
    label: str                    # human-readable, English
    detail: str | None            # e.g. "force-spawn: action-verb heuristic"

class ErrorEntry(BaseModel):
    source: str                   # ErrorOccurred | ActionDenied | MissionFailed | cu_failure
    layer: str | None
    message: str
    recoverable: bool | None

class RunTurn(BaseModel):
    idx: int
    trace_id: str
    user_text: str
    jarvis_text: str
    tier: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    think_ms: int | None
    speak_ms: int | None
    timeline: list[TraceEvent]
    latency: list[LatencyEntry]
    decision_path: list[DecisionStep]
    tools: list[ToolCall]
    errors: list[ErrorEntry]
    extras: TurnExtras            # §7 derived telemetry

class RunAnalytics(BaseModel):
    total_duration_s: float
    total_think_ms: int
    total_speak_ms: int
    cost_by_provider: dict[str, float]
    tool_counts: dict[str, int]
    interruptions: int
    worst_slo_status: str

class Run(BaseModel):
    session: VoiceSessionRow      # reuse existing row model
    turns: list[RunTurn]
    missions: list[MissionRef]
    analytics: RunAnalytics
```

---

## 6. UI structure

Master-detail, mirroring the Transcription view's layout and React-Query
patterns. Left = run list with debug badges; right = selected run with a
session summary header and a turn accordion.

```
┌─ RUNS ─────────┬─ RUN · 2026-06-17 14:03 · 4 turns · hangup: idle_timeout ────────────┐
│ ● 14:03 idle   │  Σ 11.4s · $0.038 · router→deep · ⚠1 · SLO wake→ACK ✔1.05s           │
│   14:01 hotkey │ ┌──────────────────────────────────────────────────────────────────┐│
│   13:58 voice  │ │ ▸ Turn 2  "spawn a sub-agent for…"   deep · opus · 2.8s · $0.015  ││
│   …            │ │   ├ Timeline    wake→stt→intent→route→brain→tools→speech→done     ││
│ [Live ●]       │ │   ├ Latency     ▓▓▓░ stt .4 │ intent .1 │ brain 1.9 │ tts .3       ││
│                │ │   ├ Decision    tier=router → force-spawn(verb) → risk=ask ✔auto   ││
│                │ │   ├ Tools       cli_gcloud exit0 245ms safe · spawn_oc→mission#a3  ││
│                │ │   └ Errors      —                                                  ││
│                │ └──────────────────────────────────────────────────────────────────┘│
└────────────────┴────────────────────────────────────────────────────────────────────┘
```

### The five per-turn panels

1. **Timeline** — the turn's events in order: `WakeWordDetected` (turn 1 only),
   `ListeningStarted`, `UtteranceCaptured`, `TranscriptFinal`,
   `IntentClassified`, `ActionProposed/Approved/Denied`, `BrainTurnStarted`,
   `BrainTTFT`, `ResponseGenerated`, `SpeechSpoken`, `SystemStateChanged`. Each
   row shows a relative offset (`+0.42s`) from turn start. This *is* the
   "thinking path" as an ordered sequence.

2. **Latency waterfall** — horizontal bars per `LatencyPhase`, colored by SLO
   status against the documented budgets (wake→ACK < 1.2s, intent→ACK < 3.0s,
   router decision < 150ms): green = `ok`, amber = `warn` (≥80% of budget),
   red = `breach`. The first red bar names the bottleneck immediately.

3. **Decision Path** — see §6.1.

4. **Tools** — a table from `cli_invocations` (by `trace_id`) plus
   `ActionExecuted` events: name, caller, risk-tier, approval, duration, exit
   code, scrubbed error line. Spawned missions render as a link to the mission
   (`mission#<id>`), reusing the existing MissionsView deep-link if available.

5. **Errors** — entries from `ErrorOccurred`, `ActionDenied` (with the
   blacklist/deny reason), `MissionFailed` (reason + last_state), and the
   non-spoken CU-failure `detail` track (`"exit 5 · <harness reason>"`). Empty =
   a single muted "—".

Cost/tokens sit in the turn header and aggregate into the session header.
A **Raw export** control (JSONL download + copy) reuses Transcription's
`robustCopy` / `downloadAs` helpers and filename builder.

### 6.1 Decision Path reconstruction

The most valuable derivation — and it needs **no new capture**. `analyzer.py`
walks a turn's events and emits an ordered list of `DecisionStep`s answering
"why did Jarvis do that":

- `IntentClassified` → tier chosen (`router` / `fast` / `deep` / `code`).
- Routing → if the router force-spawned, which heuristic matched
  (`action-verb`, `external-system marker`, or `smalltalk-allowlist → no spawn`),
  derived from the proposed action vs. spawn markers in the event payloads.
- `ActionProposed` → tool + risk-tier.
- Risk evaluation → `ActionApproved` / `ActionDenied` with `approved_by`
  (`auto` / `user` / `whitelist` / `blacklist`).
- `BrainTurnStarted` → provider + model; if the smart-fallback chain swapped
  providers, surface the fallback.
- Mission spawn → linked mission and, if present, Critic-loop iteration count
  and verdict confidence from `missions.db`.

This is a best-effort narrative built from what is logged; it is explicitly NOT
the model's internal chain-of-thought (that is the v2 gap).

---

## 7. Additional derived telemetry (beyond the six requirements)

All of these already exist in the event stream — display only, no new capture.
They live under `RunTurn.extras` / `RunAnalytics`:

- **Barge-in / interruptions** — how often the user interrupted Jarvis
  mid-speech (UX friction signal).
- **ACK-brain behavior** — was the optimistic preamble spoken or suppressed by
  the suppress-if-fast gate (relevant to AD-OE1).
- **Endpointing reason** — turn ended via `silence` (+ `silence_ms`),
  `max_utterance`, or `stt_stable` (the axis behind the auto-submit bugs).
- **Cache hit & provider fallback** — `BrainTTFT.cache_hit` and whether a
  fallback provider answered.
- **Context/token budget** — prompt size per turn (a 112k-token context bloat
  would be visible at a glance).
- **Wake source & audio path** — voice / hotkey / channel (Telegram/Discord/Web)
  + mic/speaker host-API (BUG-014 debugging aid; desktop-only, absent on VPS).

When a datum is unavailable (e.g. host-API on a headless VPS, latency on a
pre-feature run), the panel shows a muted "n/a" rather than implying zero.

---

## 8. Live mode

When `RunInspectorView` is mounted it opens a WebSocket to `/api/runs/live`.
The server side is a thin adapter over the same events the recorder already
subscribes to (no new subscription semantics, no hot-path coupling): it
forwards run-relevant events scoped to the active `session_id`, and the client
appends them to the in-flight run, growing the timeline/latency/tools panels in
real time ("Flight Recorder Live").

- Subscription exists **only while the view is open** → no idle cost; VPS-safe.
- The WS receive loop treats any non-clean read error as terminal (`break`,
  never `continue`) per AP-20, to avoid the dead-socket log storm.
- On disconnect the view falls back to REST polling of the in-flight session,
  so live is an enhancement, not a hard dependency.

---

## 9. Performance & cloud-first posture

Audience is maintainer-debugging-first, but the CLOUD.md doctrine remains
binding: the section must boot and degrade on a 1 vCPU / 1 GB VPS.

- **Lazy:** a run is only assembled when its detail is requested; the list query
  reads cheap header rows.
- **Capped:** run list limited (e.g. last 100), like `/api/sessions`.
- **Opt-in depth:** the FlightRecorder raw deep-dive is per-run on demand, never
  eager.
- **No hot-path work:** all reads are post-hoc or live-forwarded; nothing runs
  inside `_handle_utterance` (AP-9).
- **Graceful absence:** if a store is unavailable (`missions.db` absent,
  desktop-only host-API), the loader returns the run without that slice and the
  UI shows "n/a" — it never 503s the whole run.

---

## 10. New wire-format enums — anti-drift (BUG-008 / multi-layer enum drift)

The layer introduces two new vocabularies that cross Python → Pydantic →
TypeScript → UI: **SLO status** (`ok` / `warn` / `breach`) and **decision-step
kind** (`tier` / `route` / `risk` / `brain` / `mission` / `fallback`, final set
in implementation). Per the project's hard rule, these get the five-layer
treatment preemptively (`jarvis/sessions/constants.py` is the reference):

- `jarvis/runs/constants.py` is the single source of truth (tuples
  `SLO_STATUSES`, `RUN_DECISION_KINDS` + symbolic constants).
- `model.py` keeps these fields as `str` (never `Literal`) on the wire, exactly
  like `hangup_reason`, so a new value can never collapse the list API into an
  HTTP 500 (AP-4).
- A parity test (`tests/unit/runs/test_run_enum_parity.py`) compares the Python
  tuples against the TS const and the UI label map, failing on drift — mirroring
  `test_hangup_reason_parity.py` / `test_spoken_kind_parity.py`.

Reused vocabularies (`hangup_reason`, `tier`, `spoken_kind`, `LatencyPhase`,
risk-tier) are imported from their existing SSOTs, never re-declared.

---

## 11. Wiring checklist (mirrors the Transcription section)

- **Backend route:** `jarvis/runs/routes.py` → `app.include_router(runs_router)`
  in `jarvis/ui/web/server.py`. Stores pulled from `app.state` (503 if missing).
- **Frontend view:** `RunInspectorView.tsx` + `case "run_inspector"` in
  `MainView.tsx`.
- **Sidebar:** entry in `Sidebar.tsx` `NAV_GROUPS` (Content & Data group, next to
  Sessions), icon e.g. `Activity` / `Gauge`.
- **i18n:** root key `run_inspector` (+ `nav.run_inspector`) added to
  `de.json`, `en.json`, `es.json`. **English source strings only** (i18n key +
  English value), per the output-language policy.
- **Hook:** `useRuns.ts` — React-Query (`["runs"]`, refetch 30s, invalidate on
  `VoiceSessionEnded`) + WS subscription for live.

---

## 12. Testing

- **Unit (Python):** `loader.load_run` against fixture stores (fake sessions +
  cli_usage + missions) → asserts a fully assembled `Run`; `analyzer` latency
  → SLO status mapping (ok/warn/breach boundaries); decision-path
  reconstruction from a canned event list.
- **Parity:** `tests/unit/runs/test_run_enum_parity.py` (§10).
- **Route:** `GET /api/runs`, `GET /api/runs/{id}` happy path + 503 when store
  absent + 404 for unknown session; WS smoke (connect → receive a forwarded
  event → clean close).
- **Frontend (vitest):** `useRuns` history + live merge; `LatencyWaterfall`
  color thresholds; `DecisionPath` renders each step kind; empty-state panels.
- **Graceful degradation:** loader returns a run with missions/host-API slices
  absent without raising.

Follow project conventions: fakes in `tests/fakes/`, no `unittest.mock`.

---

## 13. Open questions for the implementation plan

1. `LatencySpan` in the `voice_events` whitelist? (§4 implementation check —
   decides whether the waterfall is per-session-indexed or FlightRecorder-lazy,
   and whether historical runs have latency data).
2. Final `RUN_DECISION_KINDS` set — confirm against the actual routing events
   emitted by `BrainManager` (force-spawn heuristics, fallback chain).
3. Mission deep-link target — reuse an existing MissionsView route param or add
   a query param.
4. Section label in the UI (German chat term): "Run Inspector" vs. "Runs" vs.
   "Run-Analyse" — cosmetic, maintainer's call; code identifiers stay
   `run_inspector` / `runs` regardless.

---

## 14. v1 / v2 split

- **v1 (this spec, full):** sidebar section, master-detail, all five per-turn
  panels, cost/token headers, §7 derived telemetry, live WS, raw export,
  anti-drift enums + parity test, graceful degradation.
- **v2 (deferred):** persisted granular brain thinking (`BrainThinkingStep`,
  gated, off the hot path); cross-run analytics dashboard (p50/p95 over time,
  cohorts); per-run annotations/ratings (would introduce the optional `runs.db`
  from the rejected Scope option C).
