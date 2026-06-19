# Prototype Contracts — read this before writing any module

This file is the **single source of truth** for the three parallel sub-agent
modules of the Optimistic-Execution prototype. The two shared contracts
(`optimistic/events.py`, `optimistic/registry.py`) and the integration tests are
already written and the orchestrator owns them — **do not edit them**.

## Architecture (one uninterrupted spoken conversation)

```
user text
   │
   ▼
Talker (orchestrator)  ──emit AckEmitted FIRST──▶  (spoken "Geht klar")   [AD-OE1]
   │  classify() decides: SMALLTALK | DUMB_TOOL | SMART_TOOL              [router.py — SA1]
   │
   ├─ SMALLTALK  → reply directly (never wakes worker)
   ├─ DUMB_TOOL  → DumbTool.fire() in-process, milliseconds (never wakes worker)  [tools.py — SA2]
   └─ SMART_TOOL → bus.publish(MissionSpawn)  ── EventBus ──▶  HeavyDutyWorker      [bus.py SA1 / worker.py SA2]
                       (publish returns instantly; worker runs async off-transcript)
                                                   │
                          success → WorkerCompleted │  failure → WorkerCorrectionNeeded (invisible)
                                                                         │
                                                          OopsProtocol injects into Talker context [oops.py — SA3]
                                                          speak ONLY at next VAD turn-boundary, scrubbed  [AD-OE5]
```

## HARD CONSTRAINTS (every module, non-negotiable)

1. **Standard library only.** No `pip install`, no third-party imports. No
   `import jarvis.*`. (Cloud-first €5-VPS doctrine, AD-OE2 — the in-process
   EventBus IS the queue; no Redis/RabbitMQ/Celery.)
2. **English** for all code, comments, docstrings, log messages. (Repo output-language policy.)
3. Import shared types only from `optimistic.events` and `optimistic.registry`.
4. **TDD, strictly.** Write your unit test first, run it, watch it fail for the
   right reason, then implement until green. Use `asyncio.run(...)` inside sync
   test functions — do **not** rely on `pytest-asyncio` (avoid the extra dep).
5. Do **not** edit files you do not own. Do **not** edit `talker.py`/`demo.py`
   (the orchestrator writes those). Touch only your assigned files + your test file.
6. Bus handlers must be `async def`. A handler that raises must never break the
   publisher or other handlers (production parity AP-18).
7. After implementing, run `python -m pytest tests/<your_test>.py -q` from the
   `prototype/` directory and confirm green. Report the exact output.

## Shared contract already provided (DO NOT MODIFY)

`optimistic/events.py` exports (all `frozen=True, slots=True, kw_only=True`):
- `Event(trace_id: uuid.UUID, timestamp_ns: int)` — base, both auto-stamped.
- `UserUtterance(text)`, `AckEmitted(text)`
- `MissionSpawn(command, context: dict, tool_name: str|None, mission_id: str)`
- `WorkerStarted(mission_id, tool_name)`, `WorkerCompleted(mission_id, result)`
- `WorkerCorrectionNeeded(mission_id, reason: CorrectionReason, detail: str, command: str="")`
- `DumbToolFired(action)`
- enums `RouteKind {SMALLTALK, DUMB_TOOL, SMART_TOOL}`,
  `CorrectionReason {MISSING_INFO, AUTH_REQUIRED, NETWORK_ERROR, FATAL}`
- Construct with keywords; propagate correlation with `trace_id=source.trace_id`.

`optimistic/registry.py` exports:
- `ToolDef(name, kind, triggers: tuple[str,...], description)`
- `DUMB_TOOLS`, `SMART_TOOLS`, `ALL_TOOLS`, `SMALLTALK_TRIGGERS`, `ACTION_VERBS`
- `match_tool(command) -> ToolDef | None` (dumb scanned before smart).

## `FakeBus` for SA2/SA3 unit tests (copy into your test file)

SA1 tests the real `EventBus`. SA2/SA3 use this stub so they stay independent of
SA1's implementation:

```python
class FakeBus:
    def __init__(self):
        self.published = []
        self._subs = {}
        self._all = []
    def subscribe(self, event_type, handler):
        self._subs.setdefault(event_type, []).append(handler)
    def subscribe_all(self, handler):
        self._all.append(handler)
    async def publish(self, event):
        self.published.append(event)
        for et, hs in self._subs.items():
            if isinstance(event, et):
                for h in hs:
                    await h(event)
        for h in self._all:
            await h(event)
```

---

## SUB-AGENT 1 — Event-Bus & Routing → `optimistic/bus.py`, `optimistic/router.py`

### `optimistic/bus.py`
```python
class EventBus:
    def subscribe(self, event_type: type[Event], handler) -> None: ...
    def subscribe_all(self, handler) -> None: ...        # wildcard (flight recorder)
    async def publish(self, event: Event) -> None: ...
```
Semantics:
- `publish` calls, in registration order: every `subscribe(et, h)` handler where
  `isinstance(event, et)` is true, then every `subscribe_all` handler. It
  `await`s each handler.
- A handler raising is caught, logged via `logging.getLogger("optimistic.bus")`,
  and **swallowed** — publish continues to the remaining handlers and returns
  normally.
- `publish` must NOT itself do heavy work; it just fans out. (Handlers that need
  to do heavy work schedule a task themselves — see the worker.)

### `optimistic/router.py`
```python
def classify(command: str) -> RouteKind: ...
def ack_for(command: str, route: RouteKind) -> str: ...
```
- `classify` is **pure and fast** (no I/O, no await, < 150 ms even cold). Order:
  1. `match_tool(command)` returns a DUMB tool → `RouteKind.DUMB_TOOL`
  2. `match_tool(command)` returns a SMART tool → `RouteKind.SMART_TOOL`
  3. a `SMALLTALK_TRIGGERS` substring present (and no `ACTION_VERBS`) → `RouteKind.SMALLTALK`
  4. any `ACTION_VERBS` substring present → `RouteKind.SMART_TOOL` (unknown action ⇒ delegate)
  5. default → `RouteKind.SMALLTALK`
- `ack_for` returns the spoken text:
  - `SMART_TOOL` → an optimistic ACK, German, butler tone, e.g. `"Geht klar, ich kümmere mich drum."`
  - `DUMB_TOOL` → a short confirmation, e.g. `"Mach ich."`
  - `SMALLTALK` → a brief friendly direct reply, e.g. `"Mir geht's gut, danke der Nachfrage!"`
  - Never empty.

### Your unit tests: `tests/test_bus.py`, `tests/test_router.py`
Cover at least: publish fan-out reaches typed + wildcard subscribers; a raising
handler is swallowed and does not stop others; `subscribe` matches by isinstance;
`classify` returns the right kind for every `ROUTER_SAMPLES`-style case incl.
dumb-before-smart and smalltalk; `classify` worst-case < 150 ms over ~20 samples;
`ack_for` is non-empty for all three kinds.

---

## SUB-AGENT 2 — MCP & Tooling → `optimistic/tools.py`, `optimistic/worker.py`

### `optimistic/tools.py`
```python
class MissingInfoError(Exception):
    def __init__(self, reason: CorrectionReason, detail: str): ...
    # attributes: .reason, .detail

class DumbTool:
    def __init__(self, name: str): ...
    async def fire(self, command: str) -> str: ...   # trivial in-process action, returns instantly

class SmartTool:
    def __init__(self, name: str, *, work_seconds: float = 0.15): ...
    async def execute(self, command: str, context: dict) -> str: ...

def get_dumb_tool(name: str) -> DumbTool: ...
def get_smart_tool(name: str | None) -> SmartTool: ...   # name=None -> a generic smart tool
```
- `DumbTool.fire` returns immediately (no real sleep; `async` for uniformity),
  e.g. `f"[{self.name}] erledigt"`.
- `SmartTool.execute` simulates an async MCP round-trip: `await asyncio.sleep(work_seconds)`,
  then returns a result string, e.g. `f"[{self.name}] '{command}' gesendet"`.
- **Dummy "Max"/missing-info scenario (canonical, required):** for the `gmail`
  smart tool, extract the recipient name from `command` (first capitalised
  word that is not the leading word; helper `_extract_recipient`). If a recipient
  is present and absent from `context.get("contacts", {})` (case-insensitive),
  raise `MissingInfoError(CorrectionReason.MISSING_INFO, f"no email address on file for {recipient}")`.
  The `detail` MUST name the recipient (the Oops phrasing surfaces it).
- Optional: simulate `CorrectionReason.NETWORK_ERROR` if the command contains the
  literal token `"flaky"` (lets the worker exercise its retry path). Not required.

### `optimistic/worker.py`
```python
class HeavyDutyWorker:
    def __init__(self, bus): ...                 # subscribes to MissionSpawn
    @property
    def in_flight(self) -> int: ...              # number of not-yet-finished mission tasks
    async def drain(self) -> None: ...           # await all in-flight mission tasks
```
Behaviour:
- On construction: `bus.subscribe(MissionSpawn, self._on_mission_spawn)`.
- `_on_mission_spawn(ev)` MUST schedule the heavy work as a task
  (`asyncio.create_task(self._run(ev))`), track it, and **return immediately** —
  this is what makes delegation instant and keeps the Talker non-blocking (AD-OE2).
- `_run(ev)`:
  1. `await bus.publish(WorkerStarted(mission_id=ev.mission_id, tool_name=ev.tool_name, trace_id=ev.trace_id))`
  2. `logging.getLogger("optimistic.worker").info("Heavy-Duty-Worker processing task %s: %s", ev.mission_id, ev.command)`
  3. `tool = get_smart_tool(ev.tool_name)`
  4. `try`: `result = await tool.execute(ev.command, ev.context)` →
     `await bus.publish(WorkerCompleted(mission_id=ev.mission_id, result=result, trace_id=ev.trace_id))`
  5. `except MissingInfoError as e`: publish
     `WorkerCorrectionNeeded(mission_id=ev.mission_id, reason=e.reason, detail=e.detail, command=ev.command, trace_id=ev.trace_id)`
  6. `except` other `Exception`: ONE silent retry; if it fails again publish
     `WorkerCorrectionNeeded(..., reason=CorrectionReason.FATAL, detail=str(exc), ...)`. Never let `_run` raise out of the task.
- All published events carry `trace_id=ev.trace_id` for correlation.

### Your unit tests: `tests/test_tools.py`, `tests/test_worker.py`
Cover at least: `DumbTool.fire` returns fast & non-empty; `SmartTool.execute`
returns a result for a normal command; the gmail "Max"-without-contact case
raises `MissingInfoError(MISSING_INFO, detail contains 'Max')`; worker publishes
`WorkerStarted`+`WorkerCompleted` on success (use `FakeBus` + `await worker.drain()`);
worker publishes `WorkerCorrectionNeeded(MISSING_INFO)` on the Max case;
`_on_mission_spawn` returns before `_run` finishes (in_flight ≥ 1 right after publish).

---

## SUB-AGENT 3 — Error Handling / "Oops" protocol → `optimistic/oops.py`

```python
class OopsProtocol:
    def __init__(self, bus): ...                 # subscribes to WorkerCorrectionNeeded
    @property
    def pending(self) -> list[WorkerCorrectionNeeded]: ...   # injected, not yet spoken
    def is_user_speaking(self) -> bool: ...
    def set_user_speaking(self, speaking: bool) -> None: ...
    def injected_context(self) -> list[str]: ...  # context lines for the Talker's window (internal, unspoken)
    def vad_turn_boundary(self) -> list[str]: ...  # called at end of user turn: returns scrubbed phrases, clears buffer
    def phrase(self, ev: WorkerCorrectionNeeded) -> str: ...  # organic, scrubbed correction text
```
Behaviour:
- On construction: `bus.subscribe(WorkerCorrectionNeeded, self._on_correction)`.
- `_on_correction(ev)` appends `ev` to the pending buffer. **This is the
  "inject the invisible event into the Talker context" step.** It does NOT speak.
- `injected_context()` returns one internal line per pending correction, e.g.
  `f"[pending correction: {ev.reason.value}] {ev.detail}"` (these would go into
  the model's context window; not spoken, not scrubbed).
- `vad_turn_boundary()` is the Silero-VAD end-of-turn signal. It returns
  `[self.phrase(ev) for ev in pending]` (scrubbed), clears the pending buffer,
  and sets `user_speaking = False`. While the user is speaking, corrections just
  accumulate — they are spoken only here (AD-OE5: never mid-utterance).
- `phrase(ev)` builds an organic German correction from `ev.reason` + `ev.detail`
  (+ optionally `ev.command`) and runs it through a local `_scrub` regex.
  - For `MISSING_INFO` naming a recipient (e.g. detail "... for Max"), produce
    something like: `"Kurzer Nachtrag zur Mail an Max: mir fehlt noch seine
    E-Mail-Adresse. Hast du die kurz für mich?"` — it MUST contain the recipient
    name (extract a capitalised name from `ev.detail` or `ev.command`).
  - For `AUTH_REQUIRED` / `NETWORK_ERROR` / `FATAL`: a short, polite organic
    sentence appropriate to the reason.
- `_scrub(text)` is **regex only, no LLM** (production AP-11). It must: remove
  markdown backticks/asterisks, remove any tool-name tokens
  (`gmail|calendar|drive|mcp`) case-insensitively, and collapse whitespace.
  (The phrasing shouldn't contain tool names anyway; scrub is the guard.)

### Your unit test: `tests/test_oops.py`
Use `FakeBus`. Cover at least: a published `WorkerCorrectionNeeded` lands in
`pending` (injection) and is NOT auto-spoken; nothing surfaces while
`is_user_speaking()` is True except via `vad_turn_boundary()`; `vad_turn_boundary()`
returns the organic phrase, clears `pending`, flips speaking to False; the
MISSING_INFO phrase contains the recipient name and does NOT contain `gmail` or a
backtick after scrubbing; `injected_context()` reflects pending count.

---

## What the orchestrator does after you finish
Writes `optimistic/talker.py` (wires router + bus + tools + worker + oops) and
`demo.py`, then runs the full suite — including the already-written
`tests/test_acceptance.py`, `tests/test_latency.py`, `tests/test_e2e_oops.py` —
which must all go green. Build your module so those integration tests can pass.
