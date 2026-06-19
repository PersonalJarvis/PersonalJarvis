# Run Inspector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone "Run Inspector" section that shows each voice run (Hey-Jarvis → hangup) as a master-detail forensic view — per-turn timeline, latency waterfall with SLO traffic-lights, reconstructed decision path, tool activity, and errors — over a read-only analytics layer with both history and live modes.

**Architecture:** A read-only `jarvis/runs/` package aggregates the existing session aggregate (`SessionStore` / `chats.db`), the CLI usage log (`cli_usage.db`), and missions, into a `Run` DTO; a REST route serves history and a WebSocket route forwards the in-flight run. The frontend mirrors the existing Transcription section (`SessionsView` + `components/sessions/*`). One small capture change: the session recorder's event whitelist is widened so latency/decision/error events land in `voice_events` going forward.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, sqlite3 (sync, WAL), React 18 + TypeScript, TanStack Query, Vitest, pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-06-17-run-inspector-debug-section-design.md`

---

## File Structure

**Backend (new package `jarvis/runs/`):**
- `jarvis/runs/__init__.py` — package marker.
- `jarvis/runs/constants.py` — SSOT tuples `SLO_STATUSES`, `RUN_DECISION_KINDS` + symbolic constants (anti-drift).
- `jarvis/runs/model.py` — read-only Pydantic DTOs (`Run`, `RunTurn`, `RunListItem`, `ToolCall`, `LatencyEntry`, `DecisionStep`, `ErrorEntry`, `TurnExtras`, `RunAnalytics`, `MissionRef`).
- `jarvis/runs/analyzer.py` — pure functions: latency→SLO, decision-path reconstruction, tool extraction, per-turn extras, session analytics.
- `jarvis/runs/loader.py` — `load_run(session_id)` + `list_runs(limit)` joining the three stores.
- `jarvis/runs/routes.py` — REST: `GET /api/runs`, `GET /api/runs/{session_id}`.
- `jarvis/runs/runs_ws.py` — WebSocket `/api/runs/live` (forwards run-relevant bus events).

**Backend (modified):**
- `jarvis/sessions/recorder.py` — widen `_RAW_EVENT_KINDS` + `_payload_for` field whitelist.
- `jarvis/clis/usage_log.py` — add `list_for_trace(trace_id)`.
- `jarvis/ui/web/server.py` — `include_router(runs_router)` + `include_router(runs_ws_router)`.

**Frontend (new, under `jarvis/ui/web/frontend/src/`):**
- `components/runs/types.ts` — TS mirror of the run DTOs + SSOT arrays.
- `components/runs/api.ts` — `fetchRuns`, `fetchRunDetail`.
- `hooks/useRuns.ts` — React-Query list/detail + live merge from the event store.
- `views/RunInspectorView.tsx` — master-detail shell.
- `components/runs/RunList.tsx`, `RunDetail.tsx`, `TurnTrace.tsx`, `TimelinePanel.tsx`, `LatencyWaterfall.tsx`, `DecisionPath.tsx`, `ToolTable.tsx`, `ErrorPanel.tsx`.

**Frontend (modified):**
- `store/events.ts` — add `"run_inspector"` to `SectionId`, `SECTION_IDS`, `SECTION_LABELS`.
- `components/layout/Sidebar.tsx` — nav entry in `NAV_GROUPS[1]`.
- `components/layout/MainView.tsx` — `case "run_inspector"`.
- `i18n/locales/{de,en,es}.json` — `nav.run_inspector` + `run_inspector.*` keys.

**Tests:**
- `tests/unit/sessions/test_recorder_whitelist.py` (extend or create)
- `tests/unit/runs/test_constants_parity.py`, `test_analyzer.py`, `test_loader.py`, `test_routes.py`
- `tests/unit/clis/test_usage_log_trace.py`
- `jarvis/ui/web/frontend/src/components/runs/__tests__/{runEnumParity,useRuns,latencyWaterfall}.test.ts(x)`

---

## Conventions (read once)

- **Timestamps:** `_ms` = wall-clock ms since epoch (matches the rest of `sessions/`).
- **Enums on the wire are `str`, never `Literal`** (BUG-008). Document known values in a tuple/array + a parity test.
- **No `unittest.mock`** — use fakes in `tests/fakes/` (see existing). For these tasks, in-memory fakes are constructed inline.
- **Every subprocess** would need `NO_WINDOW_CREATIONFLAGS` — this feature spawns none.
- **Commits:** stage only the files the task names (shared working tree — never `git add -A`). Commit message in English, Conventional Commits. Append the `Co-Authored-By` trailer the harness mandates.
- **Run backend tests:** `pytest <path> -v`. **Frontend:** from `jarvis/ui/web/frontend/`, `npm run test -- <file>`.

---

## PHASE 1 — Capture gap (recorder whitelist)

The latency waterfall, decision path, and error panel need events that are **not** in the recorder's `voice_events` whitelist today. Widen it. This is additive and off the hot path (the recorder is a read-only wildcard subscriber). It affects **new** sessions only; runs recorded before this ships have no rows for these kinds and the UI shows "n/a" for those panels.

### Task 1: Widen the session recorder event + payload whitelist

**Files:**
- Modify: `jarvis/sessions/recorder.py` (`_RAW_EVENT_KINDS` ~line 68, `_payload_for` `fields_whitelist` ~line 654)
- Test: `tests/unit/sessions/test_recorder_whitelist.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/sessions/test_recorder_whitelist.py
"""The Run Inspector reads latency/decision/error events out of voice_events.
Guards that the recorder whitelist carries those kinds and their payload fields."""
from jarvis.sessions.recorder import _RAW_EVENT_KINDS, _payload_for
from jarvis.core.events import (
    IntentClassified, ActionProposed, ActionApproved, ActionDenied,
    ErrorOccurred, LatencySpan,
)


def test_decision_latency_error_kinds_are_whitelisted():
    for kind in (
        "IntentClassified", "ActionProposed", "ActionApproved",
        "ActionDenied", "ErrorOccurred", "LatencySpan",
    ):
        assert kind in _RAW_EVENT_KINDS


def test_payload_carries_decision_fields():
    p = _payload_for(ActionProposed(tool_name="cli_gcloud", risk_tier="ask"))
    assert p["tool_name"] == "cli_gcloud"
    assert p["risk_tier"] == "ask"
    p2 = _payload_for(ActionApproved(tool_name="cli_gcloud", approved_by="whitelist"))
    assert p2["approved_by"] == "whitelist"
    p3 = _payload_for(ActionDenied(tool_name="rm", reason="blacklist: destructive"))
    assert p3["reason"].startswith("blacklist")
    p4 = _payload_for(IntentClassified(intent="execute", risk_tier="monitor"))
    assert p4["intent"] == "execute" and p4["risk_tier"] == "monitor"


def test_payload_carries_latency_and_error_fields():
    p = _payload_for(LatencySpan(phase="intent_decision", duration_ms=42.0))
    assert p["phase"] == "intent_decision"
    assert p["duration_ms"] == 42.0
    e = _payload_for(ErrorOccurred(layer="brain", error_type="Timeout",
                                   message="provider chain unreachable", recoverable=False))
    assert e["layer"] == "brain" and e["error_type"] == "Timeout"
    assert e["message"].startswith("provider") and e["recoverable"] is False
```

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/unit/sessions/test_recorder_whitelist.py -v`
Expected: FAIL — `IntentClassified` not in `_RAW_EVENT_KINDS`; `phase`/`risk_tier`/etc. missing from payload.

- [ ] **Step 3: Add the kinds to `_RAW_EVENT_KINDS`**

In `jarvis/sessions/recorder.py`, inside the `_RAW_EVENT_KINDS` frozenset (after `"SpeechSpoken",`), add:

```python
        # Run Inspector forensic events (2026-06-17). The recorder is a read-only
        # wildcard subscriber, so persisting these adds no hot-path cost; they
        # power the latency waterfall, decision path, and error panel. _payload_for
        # already pulls fields by hasattr, so no new imports are needed here.
        "IntentClassified",
        "ActionProposed",
        "ActionApproved",
        "ActionDenied",
        "ErrorOccurred",
        "LatencySpan",
```

- [ ] **Step 4: Add the fields to `_payload_for`'s `fields_whitelist`**

In the `fields_whitelist` set inside `_payload_for`, add these members (anywhere in the set literal):

```python
        # Run Inspector: decision-path + latency + error fields (2026-06-17).
        "intent",        # IntentClassified.intent
        "risk_tier",     # IntentClassified / ActionProposed
        "approved_by",   # ActionApproved
        "reason",        # ActionDenied
        "phase",         # LatencySpan
        "layer",         # ErrorOccurred
        "error_type",    # ErrorOccurred
        "message",       # ErrorOccurred
        "recoverable",   # ErrorOccurred (bool — _payload_for skips None, keeps False)
```

Note: `_payload_for` skips a field only when its value `is None`; `False` is kept (verified by the `recoverable is False` assertion). `duration_ms` is already whitelisted.

- [ ] **Step 5: Run the test, verify it passes**

Run: `pytest tests/unit/sessions/test_recorder_whitelist.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the existing recorder/session suite for regressions**

Run: `pytest tests/unit/sessions/ -q`
Expected: PASS (no regressions from the widened whitelist).

- [ ] **Step 7: Commit**

```bash
git add jarvis/sessions/recorder.py tests/unit/sessions/test_recorder_whitelist.py
git commit -m "feat(sessions): widen recorder whitelist for run-inspector forensics"
```

---

## PHASE 2 — Runs data layer

### Task 2: `jarvis/runs/constants.py` + parity test

**Files:**
- Create: `jarvis/runs/__init__.py` (empty), `jarvis/runs/constants.py`
- Test: `tests/unit/runs/__init__.py` (empty), `tests/unit/runs/test_constants_parity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/runs/test_constants_parity.py
from jarvis.runs.constants import (
    SLO_STATUSES, SLO_OK, SLO_WARN, SLO_BREACH,
    RUN_DECISION_KINDS, DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
    DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
)


def test_slo_statuses_complete_and_stable():
    assert SLO_STATUSES == (SLO_OK, SLO_WARN, SLO_BREACH)
    assert SLO_STATUSES == ("ok", "warn", "breach")


def test_decision_kinds_complete_and_stable():
    assert RUN_DECISION_KINDS == (
        DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
        DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
    )
    assert set(RUN_DECISION_KINDS) == {
        "tier", "route", "risk", "brain", "mission", "fallback",
    }
```

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/unit/runs/test_constants_parity.py -v`
Expected: FAIL — module `jarvis.runs.constants` does not exist.

- [ ] **Step 3: Create the package + constants**

```python
# jarvis/runs/__init__.py
"""Read-only analytics layer over the voice-session aggregate (Run Inspector)."""
```

```python
# jarvis/runs/constants.py
"""Single source of truth for Run-Inspector wire-format enums.

Same anti-drift contract as jarvis/sessions/constants.py (BUG-008 class): these
values cross Python -> Pydantic -> TypeScript -> UI. They are carried as plain
``str`` on the wire (never Pydantic ``Literal``); the parity tests in
tests/unit/runs/test_constants_parity.py and the frontend runEnumParity test
fail on drift between the layers.
"""
from __future__ import annotations

from typing import Final

# --- SLO traffic-light status (latency waterfall) ---------------------
SLO_OK: Final[str] = "ok"
SLO_WARN: Final[str] = "warn"        # >= 80% of the phase budget
SLO_BREACH: Final[str] = "breach"    # > 100% of the phase budget

SLO_STATUSES: Final[tuple[str, ...]] = (SLO_OK, SLO_WARN, SLO_BREACH)

# --- Decision-path step kinds -----------------------------------------
DECISION_TIER: Final[str] = "tier"          # routing tier chosen
DECISION_ROUTE: Final[str] = "route"        # force-spawn / direct heuristic
DECISION_RISK: Final[str] = "risk"          # risk evaluation + approval
DECISION_BRAIN: Final[str] = "brain"        # provider/model that answered
DECISION_MISSION: Final[str] = "mission"    # sub-agent mission spawned
DECISION_FALLBACK: Final[str] = "fallback"  # provider fallback fired

RUN_DECISION_KINDS: Final[tuple[str, ...]] = (
    DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
    DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
)

__all__ = [
    "SLO_OK", "SLO_WARN", "SLO_BREACH", "SLO_STATUSES",
    "DECISION_TIER", "DECISION_ROUTE", "DECISION_RISK",
    "DECISION_BRAIN", "DECISION_MISSION", "DECISION_FALLBACK",
    "RUN_DECISION_KINDS",
]
```

- [ ] **Step 4: Run it, verify it passes**

Run: `pytest tests/unit/runs/test_constants_parity.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/runs/__init__.py jarvis/runs/constants.py tests/unit/runs/__init__.py tests/unit/runs/test_constants_parity.py
git commit -m "feat(runs): add run-inspector enum SSOT + parity test"
```

### Task 3: `jarvis/runs/model.py` — read-only DTOs

**Files:**
- Create: `jarvis/runs/model.py`
- Test: `tests/unit/runs/test_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/runs/test_model.py
from jarvis.runs.model import (
    Run, RunTurn, RunListItem, ToolCall, LatencyEntry,
    DecisionStep, ErrorEntry, TurnExtras, RunAnalytics, MissionRef,
)


def test_run_turn_defaults_are_safe():
    t = RunTurn(idx=0, trace_id="t1")
    assert t.timeline == [] and t.latency == [] and t.tools == []
    assert t.decision_path == [] and t.errors == []
    assert t.extras.interrupted is False


def test_enum_fields_are_plain_strings():
    # str, not Literal — an unknown value must not raise (BUG-008).
    le = LatencyEntry(phase="future_phase", duration_ms=1.0, slo_status="weird")
    assert le.slo_status == "weird"
    ds = DecisionStep(kind="future_kind", label="x")
    assert ds.kind == "future_kind"


def test_run_list_item_shape():
    item = RunListItem(
        session_id="s1", started_ms=1, ended_ms=2, duration_s=0.001,
        hangup_reason="idle_timeout", wake_source="voice", turn_count=1,
        total_cost_usd=0.0, error_count=0, slo_status="ok", preview="hi",
    )
    assert item.session_id == "s1" and item.slo_status == "ok"
```

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/unit/runs/test_model.py -v`
Expected: FAIL — `jarvis.runs.model` does not exist.

- [ ] **Step 3: Write `model.py`**

```python
# jarvis/runs/model.py
"""Read-only DTOs for the Run Inspector. No SQL, no schema — these compose
existing rows (VoiceSessionRow/VoiceTurnRow) plus fields derived by analyzer.py.

All enum-like fields are plain ``str`` (never Literal) so an unknown value
degrades to a UI fallback instead of an HTTP 500 — see jarvis/runs/constants.py
and the BUG-008 history."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from jarvis.sessions.models import VoiceSessionRow


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: str
    offset_ms: int = 0          # relative to the turn's start
    ts_ms: int = 0
    summary: str = ""           # short human label derived from payload


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    caller: str = ""            # router_tool | openclaw_worker | ...
    risk_tier: str = ""         # safe | monitor | ask | block
    approved_by: str | None = None  # auto | user | whitelist | None
    duration_ms: int | None = None
    exit_code: int | None = None
    success: bool = True
    error_line: str | None = None   # scrubbed stderr ERROR line


class LatencyEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    phase: str
    duration_ms: float
    slo_status: str = "ok"     # see SLO_STATUSES


class DecisionStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: str                  # see RUN_DECISION_KINDS
    label: str
    detail: str | None = None


class ErrorEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str                # ErrorOccurred | ActionDenied | MissionFailed | cu_failure
    layer: str | None = None
    message: str = ""
    recoverable: bool | None = None


class TurnExtras(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interrupted: bool = False
    cache_hit: bool | None = None
    endpoint_reason: str | None = None   # silence | max_utterance | stt_stable
    context_tokens: int | None = None    # prompt size if known (tokens_in)


class MissionRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mission_id: str
    status: str = ""
    summary: str = ""


class RunTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    idx: int
    trace_id: str
    user_text: str = ""
    jarvis_text: str = ""
    tier: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    think_ms: int = 0
    speak_ms: int = 0
    timeline: list[TraceEvent] = Field(default_factory=list)
    latency: list[LatencyEntry] = Field(default_factory=list)
    decision_path: list[DecisionStep] = Field(default_factory=list)
    tools: list[ToolCall] = Field(default_factory=list)
    errors: list[ErrorEntry] = Field(default_factory=list)
    extras: TurnExtras = Field(default_factory=TurnExtras)


class RunAnalytics(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total_duration_s: float | None = None
    total_think_ms: int = 0
    total_speak_ms: int = 0
    cost_by_provider: dict[str, float] = Field(default_factory=dict)
    tool_counts: dict[str, int] = Field(default_factory=dict)
    interruptions: int = 0
    worst_slo_status: str = "ok"


class RunListItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str
    started_ms: int
    ended_ms: int | None = None
    duration_s: float | None = None
    hangup_reason: str = ""
    wake_source: str = ""       # voice | hotkey | channel:<name>
    turn_count: int = 0
    total_cost_usd: float = 0.0
    error_count: int = 0
    slo_status: str = "ok"      # worst across turns
    preview: str = ""


class Run(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session: VoiceSessionRow
    turns: list[RunTurn] = Field(default_factory=list)
    missions: list[MissionRef] = Field(default_factory=list)
    analytics: RunAnalytics = Field(default_factory=RunAnalytics)


__all__ = [
    "TraceEvent", "ToolCall", "LatencyEntry", "DecisionStep", "ErrorEntry",
    "TurnExtras", "MissionRef", "RunTurn", "RunAnalytics", "RunListItem", "Run",
]
```

- [ ] **Step 4: Run it, verify it passes**

Run: `pytest tests/unit/runs/test_model.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/runs/model.py tests/unit/runs/test_model.py
git commit -m "feat(runs): read-only Run/RunTurn DTOs"
```

### Task 4: `UsageLog.list_for_trace(trace_id)`

**Files:**
- Modify: `jarvis/clis/usage_log.py` (add method after `list_for`, ~line 165)
- Test: `tests/unit/clis/test_usage_log_trace.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/clis/test_usage_log_trace.py
from jarvis.clis.usage_log import UsageLog


def test_list_for_trace_filters_and_orders(tmp_path):
    log = UsageLog(db_path=tmp_path / "u.db")
    rid = log.record_start(cli_name="gcloud", full_command="gcloud projects list",
                           caller="router_tool", trace_id="trace-A", started_at_ms=1000)
    log.record_finish(rid, exit_code=0, stdout="ok", stderr="", finished_at_ms=1200)
    log.record_start(cli_name="gh", full_command="gh pr list",
                     caller="router_tool", trace_id="trace-B", started_at_ms=1100)

    rows = log.list_for_trace("trace-A")
    assert len(rows) == 1
    assert rows[0].cli_name == "gcloud" and rows[0].exit_code == 0
    assert log.list_for_trace("") == []
    log.close()
```

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/unit/clis/test_usage_log_trace.py -v`
Expected: FAIL — `UsageLog` has no attribute `list_for_trace`.

- [ ] **Step 3: Add the method**

In `jarvis/clis/usage_log.py`, after `list_for(...)`:

```python
    def list_for_trace(self, trace_id: str, *, limit: int = 200) -> list[UsageRow]:
        """All CLI invocations tagged with one trace_id, oldest first.

        Run Inspector joins a voice turn's trace_id to its tool calls. Returns
        an empty list for a falsy trace_id (turns without a captured trace)."""
        if not trace_id:
            return []
        sql = (
            "SELECT id, trace_id, cli_name, full_command, args_preview, "
            "       exit_code, stdout_len, stderr_len, stderr_preview, "
            "       duration_ms, caller, started_at, finished_at, cwd "
            "FROM cli_invocations WHERE trace_id = ? "
            "ORDER BY started_at ASC LIMIT ?"
        )
        with self._read_cursor() as cur:
            rows = cur.execute(sql, (trace_id, limit)).fetchall()
        return [_row_from_tuple(r) for r in rows]
```

- [ ] **Step 4: Run it, verify it passes**

Run: `pytest tests/unit/clis/test_usage_log_trace.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/clis/usage_log.py tests/unit/clis/test_usage_log_trace.py
git commit -m "feat(clis): UsageLog.list_for_trace for run-inspector tool join"
```

### Task 5: `jarvis/runs/analyzer.py` — pure derivations

This is the logic core. Pure functions over `VoiceEventRow` lists + `VoiceTurnRow` + `UsageRow`, so they test without I/O.

**Files:**
- Create: `jarvis/runs/analyzer.py`
- Test: `tests/unit/runs/test_analyzer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/runs/test_analyzer.py
from jarvis.runs.analyzer import (
    classify_latency, build_latency, build_decision_path,
    build_errors, build_extras, build_analytics,
)
from jarvis.sessions.models import VoiceEventRow, VoiceTurnRow


def _ev(kind, ts_ms=0, **payload):
    return VoiceEventRow(session_id="s", turn_id="t1", ts_ms=ts_ms, kind=kind, payload=payload)


def test_classify_latency_thresholds():
    # intent_decision budget = 150ms: <120 ok, 120..150 warn, >150 breach.
    assert classify_latency("intent_decision", 100.0) == "ok"
    assert classify_latency("intent_decision", 130.0) == "warn"
    assert classify_latency("intent_decision", 200.0) == "breach"
    # phase with no budget is always ok.
    assert classify_latency("tts_first_chunk", 99999.0) == "ok"


def test_build_latency_from_events():
    events = [_ev("LatencySpan", phase="intent_decision", duration_ms=200.0)]
    entries = build_latency(events)
    assert entries[0].phase == "intent_decision"
    assert entries[0].slo_status == "breach"


def test_decision_path_reconstruction():
    events = [
        _ev("IntentClassified", ts_ms=1, intent="execute", risk_tier="ask"),
        _ev("ActionProposed", ts_ms=2, tool_name="cli_gcloud", risk_tier="ask"),
        _ev("ActionApproved", ts_ms=3, tool_name="cli_gcloud", approved_by="whitelist"),
        _ev("BrainTurnStarted", ts_ms=4, provider="claude-api", model="opus",
            intent_level="direct_action"),
    ]
    steps = build_decision_path(events)
    kinds = [s.kind for s in steps]
    assert "tier" in kinds and "risk" in kinds and "brain" in kinds
    risk = next(s for s in steps if s.kind == "risk")
    assert "whitelist" in (risk.detail or "")


def test_decision_path_denied_and_fallback():
    events = [
        _ev("ActionDenied", ts_ms=1, tool_name="rm", reason="blacklist: destructive"),
        _ev("BrainTurnStarted", ts_ms=2, provider="gemini", model="flash"),
        _ev("BrainTurnStarted", ts_ms=3, provider="grok", model="grok-2"),
    ]
    steps = build_decision_path(events)
    # two distinct providers across the turn -> a fallback step.
    assert any(s.kind == "fallback" for s in steps)
    assert any(s.kind == "risk" and "blacklist" in (s.detail or "") for s in steps)


def test_build_errors():
    events = [
        _ev("ErrorOccurred", layer="brain", error_type="Timeout",
            message="chain down", recoverable=False),
        _ev("ActionDenied", tool_name="rm", reason="blacklist: x"),
    ]
    errs = build_errors(events)
    sources = {e.source for e in errs}
    assert "ErrorOccurred" in sources and "ActionDenied" in sources


def test_build_extras_interrupt_and_cache_and_endpoint():
    events = [
        _ev("BrainTTFT", cache_hit=True),
        _ev("SpeechSpoken", spoken_kind="other", detail="endpoint=silence"),
    ]
    extras = build_extras(events, tokens_in=1234)
    assert extras.cache_hit is True
    assert extras.context_tokens == 1234


def test_build_analytics_aggregates_and_worst_slo():
    from jarvis.runs.model import RunTurn, LatencyEntry
    turns = [
        RunTurn(idx=0, trace_id="a", provider="claude-api", cost_usd=0.01,
                think_ms=100, speak_ms=200,
                latency=[LatencyEntry(phase="intent_decision", duration_ms=1, slo_status="ok")]),
        RunTurn(idx=1, trace_id="b", provider="gemini", cost_usd=0.02,
                latency=[LatencyEntry(phase="ack_first_audio", duration_ms=9999, slo_status="breach")],
                extras=__import__("jarvis.runs.model", fromlist=["TurnExtras"]).TurnExtras(interrupted=True)),
    ]
    a = build_analytics(turns, started_ms=0, ended_ms=1000)
    assert a.total_duration_s == 1.0
    assert a.cost_by_provider["claude-api"] == 0.01
    assert a.worst_slo_status == "breach"
    assert a.interruptions == 1
```

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/unit/runs/test_analyzer.py -v`
Expected: FAIL — `jarvis.runs.analyzer` does not exist.

- [ ] **Step 3: Write `analyzer.py`**

```python
# jarvis/runs/analyzer.py
"""Pure derivations for the Run Inspector — no I/O, no store access.

Inputs are the rows the loader already fetched (VoiceEventRow / VoiceTurnRow /
UsageRow); outputs are the run model DTOs. Keeping this pure makes the forensic
logic unit-testable without a database."""
from __future__ import annotations

from jarvis.runs.constants import (
    SLO_OK, SLO_WARN, SLO_BREACH,
    DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
    DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
)
from jarvis.runs.model import (
    DecisionStep, ErrorEntry, LatencyEntry, RunAnalytics, RunTurn,
    ToolCall, TraceEvent, TurnExtras,
)
from jarvis.sessions.models import VoiceEventRow, VoiceTurnRow

# Per-phase SLO budget in ms. Phases not listed have no gate (always SLO_OK).
# Budgets mirror the documented voice SLOs: wake->ACK < 1.2s, intent->ACK < 3.0s,
# router decision < 150ms (CLAUDE.md "Optimistic Execution").
_PHASE_SLO_MS: dict[str, float] = {
    "intent_decision": 150.0,
    "ack_first_audio": 1200.0,
    "ack_first_token": 1200.0,
    "brain_first_audio": 3000.0,
    "brain_first_token": 3000.0,
    "turn_to_first_audio": 3000.0,
}
_WARN_FRACTION = 0.8

_SLO_RANK = {SLO_OK: 0, SLO_WARN: 1, SLO_BREACH: 2}


def classify_latency(phase: str, duration_ms: float) -> str:
    budget = _PHASE_SLO_MS.get(phase)
    if budget is None:
        return SLO_OK
    if duration_ms > budget:
        return SLO_BREACH
    if duration_ms >= budget * _WARN_FRACTION:
        return SLO_WARN
    return SLO_OK


def build_latency(events: list[VoiceEventRow]) -> list[LatencyEntry]:
    out: list[LatencyEntry] = []
    for e in events:
        if e.kind != "LatencySpan":
            continue
        phase = str(e.payload.get("phase", ""))
        dur = float(e.payload.get("duration_ms", 0.0) or 0.0)
        if not phase:
            continue
        out.append(LatencyEntry(phase=phase, duration_ms=dur,
                                slo_status=classify_latency(phase, dur)))
    return out


def build_decision_path(events: list[VoiceEventRow]) -> list[DecisionStep]:
    steps: list[DecisionStep] = []
    providers_seen: list[str] = []
    for e in sorted(events, key=lambda x: x.ts_ms):
        p = e.payload
        if e.kind == "IntentClassified":
            steps.append(DecisionStep(
                kind=DECISION_TIER,
                label=f"intent: {p.get('intent', '?')}",
                detail=f"risk={p.get('risk_tier', '?')}",
            ))
        elif e.kind == "ActionProposed":
            steps.append(DecisionStep(
                kind=DECISION_ROUTE,
                label=f"proposed: {p.get('tool_name', '?')}",
                detail=f"risk={p.get('risk_tier', '?')}",
            ))
        elif e.kind == "ActionApproved":
            steps.append(DecisionStep(
                kind=DECISION_RISK,
                label=f"approved: {p.get('tool_name', '?')}",
                detail=f"by={p.get('approved_by', 'auto')}",
            ))
        elif e.kind == "ActionDenied":
            steps.append(DecisionStep(
                kind=DECISION_RISK,
                label=f"denied: {p.get('tool_name', '?')}",
                detail=str(p.get("reason", "")),
            ))
        elif e.kind == "BrainTurnStarted":
            provider = str(p.get("provider", ""))
            model = str(p.get("model", ""))
            if provider:
                providers_seen.append(provider)
            steps.append(DecisionStep(
                kind=DECISION_BRAIN,
                label=f"brain: {provider or '?'}",
                detail=(f"model={model}" if model else None),
            ))
        elif e.kind == "OpenClawTaskStarted":
            steps.append(DecisionStep(
                kind=DECISION_MISSION,
                label="spawned sub-agent mission",
                detail=str(p.get("model", "")) or None,
            ))
    # A second distinct provider across the turn means the smart-fallback fired.
    distinct = [p for i, p in enumerate(providers_seen) if p and p not in providers_seen[:i]]
    if len(distinct) > 1:
        steps.append(DecisionStep(
            kind=DECISION_FALLBACK,
            label="provider fallback",
            detail=" -> ".join(distinct),
        ))
    return steps


def build_errors(events: list[VoiceEventRow]) -> list[ErrorEntry]:
    out: list[ErrorEntry] = []
    for e in events:
        p = e.payload
        if e.kind == "ErrorOccurred":
            out.append(ErrorEntry(
                source="ErrorOccurred",
                layer=str(p.get("layer", "")) or None,
                message=str(p.get("error_type", "")) + ": " + str(p.get("message", "")),
                recoverable=p.get("recoverable"),
            ))
        elif e.kind == "ActionDenied":
            out.append(ErrorEntry(
                source="ActionDenied",
                message=f"{p.get('tool_name', '?')}: {p.get('reason', '')}",
            ))
        elif e.kind == "SpeechSpoken" and p.get("detail"):
            # The non-spoken CU-failure detail track ("exit 5 · <reason>").
            out.append(ErrorEntry(source="cu_failure", message=str(p.get("detail"))))
    return out


def build_extras(events: list[VoiceEventRow], *, tokens_in: int = 0) -> TurnExtras:
    extras = TurnExtras(context_tokens=tokens_in or None)
    for e in events:
        p = e.payload
        if e.kind == "BrainTTFT" and "cache_hit" in p:
            extras.cache_hit = bool(p.get("cache_hit"))
        if e.kind == "SpeechSpoken":
            detail = str(p.get("detail", ""))
            if detail.startswith("endpoint="):
                extras.endpoint_reason = detail.split("=", 1)[1]
    return extras


def build_timeline(events: list[VoiceEventRow], *, turn_started_ms: int) -> list[TraceEvent]:
    out: list[TraceEvent] = []
    for e in sorted(events, key=lambda x: x.ts_ms):
        out.append(TraceEvent(
            kind=e.kind,
            ts_ms=e.ts_ms,
            offset_ms=max(0, e.ts_ms - turn_started_ms),
            summary=_summarize(e),
        ))
    return out


def tools_from_usage(usage_rows: list) -> list[ToolCall]:
    """UsageRow list (jarvis.clis.usage_log.UsageRow) -> ToolCall DTOs."""
    out: list[ToolCall] = []
    for r in usage_rows:
        first_err = None
        if r.stderr_preview:
            first_err = next(
                (ln for ln in r.stderr_preview.splitlines() if "error" in ln.lower()),
                r.stderr_preview.splitlines()[0] if r.stderr_preview else None,
            )
        out.append(ToolCall(
            name=r.cli_name,
            caller=r.caller,
            duration_ms=r.duration_ms,
            exit_code=r.exit_code,
            success=(r.exit_code == 0),
            error_line=first_err,
        ))
    return out


def merge_action_tools(events: list[VoiceEventRow], cli_tools: list[ToolCall]) -> list[ToolCall]:
    """Add non-CLI tool calls (ActionProposed/Approved) so router-tier tools that
    are not CLI invocations still appear, carrying their risk-tier + approval."""
    by_name = {t.name: t for t in cli_tools}
    risk: dict[str, str] = {}
    approval: dict[str, str] = {}
    for e in events:
        p = e.payload
        if e.kind == "ActionProposed" and p.get("tool_name"):
            risk[str(p["tool_name"])] = str(p.get("risk_tier", ""))
        if e.kind == "ActionApproved" and p.get("tool_name"):
            approval[str(p["tool_name"])] = str(p.get("approved_by", ""))
    for name, tier in risk.items():
        if name in by_name:
            by_name[name].risk_tier = tier
            by_name[name].approved_by = approval.get(name)
        else:
            cli_tools.append(ToolCall(name=name, risk_tier=tier,
                                      approved_by=approval.get(name)))
    return cli_tools


def build_analytics(turns: list[RunTurn], *, started_ms: int,
                    ended_ms: int | None) -> RunAnalytics:
    cost_by_provider: dict[str, float] = {}
    tool_counts: dict[str, int] = {}
    worst = SLO_OK
    interruptions = 0
    total_think = total_speak = 0
    for t in turns:
        if t.provider:
            cost_by_provider[t.provider] = cost_by_provider.get(t.provider, 0.0) + t.cost_usd
        for tc in t.tools:
            tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
        total_think += t.think_ms
        total_speak += t.speak_ms
        if t.extras.interrupted:
            interruptions += 1
        for le in t.latency:
            if _SLO_RANK.get(le.slo_status, 0) > _SLO_RANK.get(worst, 0):
                worst = le.slo_status
    duration_s = ((ended_ms - started_ms) / 1000.0) if ended_ms is not None else None
    return RunAnalytics(
        total_duration_s=duration_s,
        total_think_ms=total_think,
        total_speak_ms=total_speak,
        cost_by_provider=cost_by_provider,
        tool_counts=tool_counts,
        interruptions=interruptions,
        worst_slo_status=worst,
    )


def _summarize(e: VoiceEventRow) -> str:
    p = e.payload
    if e.kind == "TranscriptFinal":
        return str(p.get("text", ""))[:80]
    if e.kind == "ResponseGenerated":
        return str(p.get("text", ""))[:80]
    if e.kind == "IntentClassified":
        return f"{p.get('intent', '')} (risk={p.get('risk_tier', '')})"
    if e.kind in ("ActionProposed", "ActionApproved", "ActionDenied"):
        return str(p.get("tool_name", ""))
    if e.kind == "BrainTurnStarted":
        return f"{p.get('provider', '')}/{p.get('model', '')}"
    if e.kind == "SystemStateChanged":
        return f"{p.get('previous', '')} -> {p.get('new_state', '')}"
    return ""


__all__ = [
    "classify_latency", "build_latency", "build_decision_path", "build_errors",
    "build_extras", "build_timeline", "tools_from_usage", "merge_action_tools",
    "build_analytics",
]
```

Note: `build_extras` does not set `interrupted` from events in v1 (no reliable barge-in event is whitelisted yet); `interrupted` defaults False and the analytics test sets it directly on the DTO. Wiring a real barge-in signal is a v2 follow-up — leave the field and the analytics path in place.

- [ ] **Step 4: Run it, verify it passes**

Run: `pytest tests/unit/runs/test_analyzer.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/runs/analyzer.py tests/unit/runs/test_analyzer.py
git commit -m "feat(runs): pure analyzer — latency SLO, decision path, errors, analytics"
```

### Task 6: `jarvis/runs/loader.py` — assemble a Run

**Files:**
- Create: `jarvis/runs/loader.py`
- Test: `tests/unit/runs/test_loader.py`

- [ ] **Step 1: Write the failing test** (uses an in-memory `SessionStore` + `UsageLog`, no mocks)

```python
# tests/unit/runs/test_loader.py
from jarvis.runs.loader import RunLoader
from jarvis.sessions.store import SessionStore
from jarvis.clis.usage_log import UsageLog


def _store(tmp_path):
    s = SessionStore(tmp_path / "chats.db")
    s.open()
    return s


def test_load_run_assembles_turns_and_analytics(tmp_path):
    store = _store(tmp_path)
    store.upsert_session(session_id="s1", started_ms=1000, wake_keyword="hey jarvis")
    store.upsert_turn(turn_id="t1", session_id="s1", idx=0, started_ms=1000)
    store.finalize_turn(
        turn_id="t1", ended_ms=1500, user_text="hi", user_lang="en",
        jarvis_text="hello", jarvis_lang="en", tier="router", provider="claude-api",
        model="opus", tokens_in=10, tokens_out=5, cost_usd=0.01,
        latency_total_ms=500, tool_calls=["cli_gcloud"], think_ms=200, speak_ms=300,
    )
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1100, kind="LatencySpan",
                       payload={"phase": "intent_decision", "duration_ms": 200.0})
    store.append_event(session_id="s1", turn_id="t1", ts_ms=1200, kind="ActionApproved",
                       payload={"tool_name": "cli_gcloud", "approved_by": "whitelist"})
    store.finalize_session(session_id="s1", ended_ms=2000, hangup_reason="idle_timeout",
                           turn_count=1, total_cost_usd=0.01, total_tokens_in=10,
                           total_tokens_out=5, providers_used=["claude-api"])

    usage = UsageLog(db_path=tmp_path / "u.db")
    loader = RunLoader(session_store=store, usage_log=usage, missions_lookup=None)

    run = loader.load_run("s1")
    assert run is not None
    assert run.session.id == "s1"
    assert len(run.turns) == 1
    turn = run.turns[0]
    assert turn.latency and turn.latency[0].slo_status == "breach"
    assert any(s.kind == "risk" for s in turn.decision_path)
    assert run.analytics.cost_by_provider["claude-api"] == 0.01
    store.close(); usage.close()


def test_list_runs_maps_headers(tmp_path):
    store = _store(tmp_path)
    store.upsert_session(session_id="s1", started_ms=1000, wake_keyword="hey jarvis")
    store.finalize_session(session_id="s1", ended_ms=2000, hangup_reason="idle_timeout",
                           turn_count=0, total_cost_usd=0.0, total_tokens_in=0,
                           total_tokens_out=0, providers_used=[])
    loader = RunLoader(session_store=store, usage_log=UsageLog(db_path=tmp_path / "u.db"),
                       missions_lookup=None)
    items = loader.list_runs(limit=10)
    assert items and items[0].session_id == "s1"
    assert items[0].wake_source == "voice"  # default; hotkey/channel only if known
    store.close()


def test_load_run_unknown_returns_none(tmp_path):
    store = _store(tmp_path)
    loader = RunLoader(session_store=store, usage_log=UsageLog(db_path=tmp_path / "u.db"),
                       missions_lookup=None)
    assert loader.load_run("nope") is None
    store.close()
```

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/unit/runs/test_loader.py -v`
Expected: FAIL — `jarvis.runs.loader` does not exist.

- [ ] **Step 3: Write `loader.py`**

```python
# jarvis/runs/loader.py
"""Assemble a Run from the existing stores. Read-only; never on the hot path.

The voice critical path never calls this — it runs only when a REST request or
the live WS forward asks for a run (AP-9). Each datum is fetched defensively: a
missing missions lookup or an empty usage log degrades to an empty slice, never
an exception."""
from __future__ import annotations

import logging
from typing import Callable

from jarvis.clis.usage_log import UsageLog
from jarvis.runs import analyzer
from jarvis.runs.model import MissionRef, Run, RunListItem, RunTurn
from jarvis.sessions.models import VoiceEventRow, VoiceTurnRow
from jarvis.sessions.store import SessionStore

log = logging.getLogger(__name__)

# Optional callable: session_id -> list[MissionRef]. None disables the slice.
MissionsLookup = Callable[[str], list[MissionRef]]


class RunLoader:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        usage_log: UsageLog | None,
        missions_lookup: MissionsLookup | None = None,
    ) -> None:
        self._sessions = session_store
        self._usage = usage_log
        self._missions = missions_lookup

    def list_runs(self, *, limit: int = 100) -> list[RunListItem]:
        items: list[RunListItem] = []
        for s in self._sessions.list_sessions(limit=limit):
            events = self._sessions.get_events(s.id)
            error_count = sum(
                1 for e in events if e.kind in ("ErrorOccurred", "ActionDenied")
            )
            worst = analyzer.SLO_OK
            for e in events:
                if e.kind == "LatencySpan":
                    st = analyzer.classify_latency(
                        str(e.payload.get("phase", "")),
                        float(e.payload.get("duration_ms", 0.0) or 0.0),
                    )
                    if analyzer._SLO_RANK.get(st, 0) > analyzer._SLO_RANK.get(worst, 0):
                        worst = st
            items.append(RunListItem(
                session_id=s.id,
                started_ms=s.started_ms,
                ended_ms=s.ended_ms,
                duration_s=s.duration_s,
                hangup_reason=s.hangup_reason,
                wake_source=_wake_source(s.wake_keyword),
                turn_count=s.turn_count,
                total_cost_usd=s.total_cost_usd,
                error_count=error_count,
                slo_status=worst,
                preview=s.preview,
            ))
        return items

    def load_run(self, session_id: str) -> Run | None:
        session = self._sessions.get_session(session_id)
        if session is None:
            return None
        turn_rows = self._sessions.get_turns(session_id)
        events = self._sessions.get_events(session_id)
        events_by_turn: dict[str | None, list[VoiceEventRow]] = {}
        for e in events:
            events_by_turn.setdefault(e.turn_id, []).append(e)

        run_turns = [
            self._build_turn(tr, events_by_turn.get(tr.id, []))
            for tr in turn_rows
        ]
        missions = self._safe_missions(session_id)
        analytics = analyzer.build_analytics(
            run_turns, started_ms=session.started_ms, ended_ms=session.ended_ms
        )
        return Run(session=session, turns=run_turns, missions=missions, analytics=analytics)

    def _build_turn(self, tr: VoiceTurnRow, events: list[VoiceEventRow]) -> RunTurn:
        cli_tools = []
        if self._usage is not None:
            # trace_id is not a column on voice_turns; the turn's CLI calls are
            # tagged with the per-turn trace_id only in cli_usage.db. We use the
            # turn id as the correlation key the recorder writes; fall back to an
            # empty list when nothing matches.
            try:
                rows = self._usage.list_for_trace(tr.id)
                cli_tools = analyzer.tools_from_usage(rows)
            except Exception as exc:  # noqa: BLE001 — usage log is best-effort
                log.debug("usage join failed for turn %s: %s", tr.id, exc)
        tools = analyzer.merge_action_tools(events, cli_tools)
        return RunTurn(
            idx=tr.idx,
            trace_id=tr.id,
            user_text=tr.user_text,
            jarvis_text=tr.jarvis_text,
            tier=tr.tier,
            provider=tr.provider,
            model=tr.model,
            tokens_in=tr.tokens_in,
            tokens_out=tr.tokens_out,
            cost_usd=tr.cost_usd,
            think_ms=tr.think_ms,
            speak_ms=tr.speak_ms,
            timeline=analyzer.build_timeline(events, turn_started_ms=tr.started_ms),
            latency=analyzer.build_latency(events),
            decision_path=analyzer.build_decision_path(events),
            tools=tools,
            errors=analyzer.build_errors(events),
            extras=analyzer.build_extras(events, tokens_in=tr.tokens_in),
        )

    def _safe_missions(self, session_id: str) -> list[MissionRef]:
        if self._missions is None:
            return []
        try:
            return self._missions(session_id)
        except Exception as exc:  # noqa: BLE001 — missions are an optional slice
            log.debug("missions lookup failed for %s: %s", session_id, exc)
            return []


def _wake_source(wake_keyword: str) -> str:
    kw = (wake_keyword or "").lower()
    if "hotkey" in kw:
        return "hotkey"
    if kw.startswith("channel:") or kw in ("telegram", "discord", "web"):
        return f"channel:{kw}" if not kw.startswith("channel:") else kw
    return "voice"


__all__ = ["RunLoader", "MissionsLookup"]
```

Note on the trace/turn correlation: CLI invocations are tagged with the per-turn `trace_id` in `cli_usage.db`. The recorder stores the turn under its own `turn_id`. These are the same identifier in the common case (the pipeline uses the turn's trace as `turn_id`); where they differ, the join returns empty and the Action-event tools still surface via `merge_action_tools`. Confirm the identifier equality during integration (open question carried from the spec) — if they diverge, thread the trace_id onto `VoiceTurnRow` in a follow-up. The decision-path/latency/error panels do **not** depend on this join.

- [ ] **Step 4: Run it, verify it passes**

Run: `pytest tests/unit/runs/test_loader.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/runs/loader.py tests/unit/runs/test_loader.py
git commit -m "feat(runs): RunLoader assembles a Run from sessions + usage + missions"
```

---

## PHASE 3 — Routes + WebSocket

### Task 7: `jarvis/runs/routes.py` — REST

**Files:**
- Create: `jarvis/runs/routes.py`
- Test: `tests/unit/runs/test_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/runs/test_routes.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.runs.routes import router as runs_router
from jarvis.sessions.store import SessionStore


def _app(tmp_path, with_store=True):
    app = FastAPI()
    app.include_router(runs_router)
    if with_store:
        store = SessionStore(tmp_path / "chats.db")
        store.open()
        store.upsert_session(session_id="s1", started_ms=1000, wake_keyword="hey jarvis")
        store.finalize_session(session_id="s1", ended_ms=2000, hangup_reason="idle_timeout",
                               turn_count=0, total_cost_usd=0.0, total_tokens_in=0,
                               total_tokens_out=0, providers_used=[])
        app.state.session_store = store
    else:
        app.state.session_store = None
    return app


def test_list_runs_ok(tmp_path):
    client = TestClient(_app(tmp_path))
    res = client.get("/api/runs")
    assert res.status_code == 200
    body = res.json()
    assert body and body[0]["session_id"] == "s1"


def test_detail_404_for_unknown(tmp_path):
    client = TestClient(_app(tmp_path))
    assert client.get("/api/runs/nope").status_code == 404


def test_503_when_store_absent(tmp_path):
    client = TestClient(_app(tmp_path, with_store=False))
    assert client.get("/api/runs").status_code == 503
```

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/unit/runs/test_routes.py -v`
Expected: FAIL — `jarvis.runs.routes` does not exist.

- [ ] **Step 3: Write `routes.py`**

```python
# jarvis/runs/routes.py
"""REST routes for the Run Inspector (forensic lens over voice sessions).

    GET /api/runs                 -> list[RunListItem]  (newest first, capped)
    GET /api/runs/{session_id}    -> Run

Read-only; reuses app.state.session_store (set by bootstrap_sessions) and a
process-local UsageLog. Loopback-only, no auth token (mirrors sessions_routes)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from jarvis.clis.usage_log import UsageLog
from jarvis.runs.loader import RunLoader
from jarvis.runs.model import Run, RunListItem
from jarvis.sessions.store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])

# A single process-local UsageLog reader; it finds its own db via cli_usage_db_path().
_usage_log: UsageLog | None = None


def _get_usage_log() -> UsageLog | None:
    global _usage_log
    if _usage_log is None:
        try:
            _usage_log = UsageLog()
        except Exception as exc:  # noqa: BLE001 — usage log is an optional slice
            log.debug("UsageLog unavailable for run-inspector: %s", exc)
            _usage_log = None
    return _usage_log


def _loader(request: Request) -> RunLoader:
    store: SessionStore | None = getattr(request.app.state, "session_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="session-recorder-disabled")
    return RunLoader(session_store=store, usage_log=_get_usage_log(), missions_lookup=None)


@router.get("", response_model=list[RunListItem])
async def list_runs(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> list[RunListItem]:
    return _loader(request).list_runs(limit=limit)


@router.get("/{session_id}", response_model=Run)
async def get_run(session_id: str, request: Request) -> Run:
    run = _loader(request).load_run(session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run-not-found")
    return run
```

- [ ] **Step 4: Run it, verify it passes**

Run: `pytest tests/unit/runs/test_routes.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/runs/routes.py tests/unit/runs/test_routes.py
git commit -m "feat(runs): REST routes for run list + detail"
```

### Task 8: `jarvis/runs/runs_ws.py` — live forward

The live channel forwards run-relevant bus events to the open view. Model it on the existing `missions_ws_router` (`jarvis/ui/web/missions_ws.py`). Before writing, **read that file** to match its lifecycle exactly: how it accepts the socket, subscribes to the bus from `app.state`, serializes events, and — critically — that it `break`s the receive loop on any read error (AP-20), never `continue`s.

**Files:**
- Create: `jarvis/runs/runs_ws.py`
- Test: `tests/unit/runs/test_runs_ws.py`

- [ ] **Step 1: Read the reference**

Run: open `jarvis/ui/web/missions_ws.py` and note its bus access (`app.state.bus` or similar), the `await ws.accept()` call, the JSON frame shape, and the disconnect handling.

- [ ] **Step 2: Write the failing smoke test**

```python
# tests/unit/runs/test_runs_ws.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.runs.runs_ws import router as runs_ws_router


def test_ws_connect_and_welcome():
    app = FastAPI()
    app.include_router(runs_ws_router)

    class _Bus:
        def subscribe_all(self, cb): self._cb = cb
        def unsubscribe(self, cb): pass
    app.state.bus = _Bus()

    client = TestClient(app)
    with client.websocket_connect("/api/runs/live") as ws:
        first = ws.receive_json()
        assert first["type"] == "welcome"
```

- [ ] **Step 3: Run it, verify it fails**

Run: `pytest tests/unit/runs/test_runs_ws.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 4: Write `runs_ws.py`** (adapt the exact bus API names to what `missions_ws.py` uses)

```python
# jarvis/runs/runs_ws.py
"""WebSocket /api/runs/live — forwards run-relevant bus events to the open
Run Inspector so the in-flight run grows in real time.

Thin read-only adapter: it subscribes to the same EventBus the recorder uses,
filters to the forensic event kinds, and pushes compact frames. The receive
loop treats any non-clean read error as terminal (break, never continue) — an
unclean client teardown raises RuntimeError, not WebSocketDisconnect, and a
continue would spin on a dead socket (AP-20)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

router = APIRouter()

# Event kinds the inspector cares about live (superset of the recorder forensic
# additions). Keep in sync with jarvis/sessions/recorder.py::_RAW_EVENT_KINDS.
_LIVE_KINDS: frozenset[str] = frozenset({
    "VoiceSessionStarted", "VoiceSessionEnded", "VoiceTurnStarted", "VoiceTurnCompleted",
    "WakeWordDetected", "ListeningStarted", "TranscriptFinal",
    "IntentClassified", "ActionProposed", "ActionApproved", "ActionDenied",
    "BrainTurnStarted", "BrainTurnCompleted", "BrainTTFT",
    "ToolCallStarted", "ToolCallCompleted", "ActionExecuted",
    "ResponseGenerated", "SystemStateChanged", "LatencySpan",
    "ErrorOccurred", "SpeechSpoken", "OpenClawTaskStarted", "OpenClawTaskCompleted",
})


@router.websocket("/api/runs/live")
async def runs_live(ws: WebSocket) -> None:
    await ws.accept()
    await ws.send_json({"type": "welcome", "channel": "runs.live"})

    bus = getattr(ws.app.state, "bus", None)
    if bus is None:
        await ws.send_json({"type": "unavailable", "reason": "no-bus"})
        await ws.close()
        return

    async def _forward(event) -> None:
        kind = type(event).__name__
        if kind not in _LIVE_KINDS:
            return
        try:
            await ws.send_json({
                "type": "event",
                "kind": kind,
                "ts_ms": getattr(event, "timestamp_ns", 0) // 1_000_000,
                "session_id": getattr(event, "session_id", None),
                "trace_id": str(getattr(event, "trace_id", "")),
            })
        except Exception:  # noqa: BLE001 — socket gone; the recv loop will end it
            pass

    bus.subscribe_all(_forward)
    try:
        while True:
            try:
                await ws.receive_text()  # keepalive / client pings; ignore content
            except WebSocketDisconnect:
                break
            except RuntimeError:
                # Unclean disconnect raises RuntimeError, not WebSocketDisconnect.
                # Treat as terminal (AP-20) — never continue on a dead socket.
                break
    finally:
        _unsub = getattr(bus, "unsubscribe", None)
        if callable(_unsub):
            _unsub(_forward)
```

- [ ] **Step 5: Run it, verify it passes**

Run: `pytest tests/unit/runs/test_runs_ws.py -v`
Expected: PASS. (If `missions_ws.py` uses a different bus accessor, align `_forward`/`subscribe_all` names and update the fake in the test to match.)

- [ ] **Step 6: Commit**

```bash
git add jarvis/runs/runs_ws.py tests/unit/runs/test_runs_ws.py
git commit -m "feat(runs): live WebSocket forward for in-flight run"
```

### Task 9: Wire both routers into the server

**Files:**
- Modify: `jarvis/ui/web/server.py` (imports near the other route imports; `include_router` calls near line 297 where `sessions_router` is mounted)

- [ ] **Step 1: Add imports** (next to the `from .sessions_routes import router as sessions_router` import)

```python
from jarvis.runs.routes import router as runs_router
from jarvis.runs.runs_ws import router as runs_ws_router
```

- [ ] **Step 2: Mount them** (immediately after `app.include_router(sessions_router)`)

```python
        # Run Inspector — forensic lens over the same voice sessions. Read-only;
        # 503 until app.state.session_store is set, like sessions_router.
        app.include_router(runs_router)
        app.include_router(runs_ws_router)
```

- [ ] **Step 3: Smoke-check the app builds**

Run: `python -c "from jarvis.ui.web.server import *; print('ok')"`
Expected: prints `ok` (no import error). If the server module needs more to import, instead run `pytest tests/unit/runs/test_routes.py -v` which imports the router in isolation.

- [ ] **Step 4: Commit**

```bash
git add jarvis/ui/web/server.py
git commit -m "feat(runs): mount run-inspector REST + WS routers"
```

---

## PHASE 4 — Frontend data layer

### Task 10: `components/runs/types.ts` + parity test

**Files:**
- Create: `jarvis/ui/web/frontend/src/components/runs/types.ts`
- Test: `jarvis/ui/web/frontend/src/components/runs/__tests__/runEnumParity.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// components/runs/__tests__/runEnumParity.test.ts
import { describe, it, expect } from "vitest";
import { SLO_STATUSES, RUN_DECISION_KINDS } from "../types";

describe("run enum parity", () => {
  it("SLO statuses match the Python SSOT order", () => {
    expect(SLO_STATUSES).toEqual(["ok", "warn", "breach"]);
  });
  it("decision kinds match the Python SSOT set", () => {
    expect([...RUN_DECISION_KINDS].sort()).toEqual(
      ["brain", "fallback", "mission", "risk", "route", "tier"],
    );
  });
});
```

- [ ] **Step 2: Run it, verify it fails**

Run (from `jarvis/ui/web/frontend/`): `npm run test -- runEnumParity`
Expected: FAIL — `../types` not found.

- [ ] **Step 3: Write `types.ts`**

```ts
// components/runs/types.ts
// 1:1 mirror of jarvis/runs/model.py + jarvis/runs/constants.py.
// Enum-like values are `string` (not unions) for the same BUG-008 reason as the
// sessions mirror — an unknown value must degrade, not crash. Parity guard:
// components/runs/__tests__/runEnumParity.test.ts + tests/unit/runs/test_constants_parity.py.

export const SLO_STATUSES = ["ok", "warn", "breach"] as const;
export const RUN_DECISION_KINDS = [
  "tier", "route", "risk", "brain", "mission", "fallback",
] as const;

export type SloStatus = string;

export interface TraceEvent { kind: string; offset_ms: number; ts_ms: number; summary: string; }
export interface ToolCall {
  name: string; caller: string; risk_tier: string;
  approved_by: string | null; duration_ms: number | null;
  exit_code: number | null; success: boolean; error_line: string | null;
}
export interface LatencyEntry { phase: string; duration_ms: number; slo_status: SloStatus; }
export interface DecisionStep { kind: string; label: string; detail: string | null; }
export interface ErrorEntry { source: string; layer: string | null; message: string; recoverable: boolean | null; }
export interface TurnExtras {
  interrupted: boolean; cache_hit: boolean | null;
  endpoint_reason: string | null; context_tokens: number | null;
}
export interface MissionRef { mission_id: string; status: string; summary: string; }

export interface RunTurn {
  idx: number; trace_id: string; user_text: string; jarvis_text: string;
  tier: string; provider: string; model: string;
  tokens_in: number; tokens_out: number; cost_usd: number;
  think_ms: number; speak_ms: number;
  timeline: TraceEvent[]; latency: LatencyEntry[]; decision_path: DecisionStep[];
  tools: ToolCall[]; errors: ErrorEntry[]; extras: TurnExtras;
}
export interface RunAnalytics {
  total_duration_s: number | null; total_think_ms: number; total_speak_ms: number;
  cost_by_provider: Record<string, number>; tool_counts: Record<string, number>;
  interruptions: number; worst_slo_status: SloStatus;
}
export interface RunListItem {
  session_id: string; started_ms: number; ended_ms: number | null;
  duration_s: number | null; hangup_reason: string; wake_source: string;
  turn_count: number; total_cost_usd: number; error_count: number;
  slo_status: SloStatus; preview: string;
}
// VoiceSessionRow is reused from the sessions mirror.
import type { VoiceSessionRow } from "@/components/sessions/types";
export interface Run {
  session: VoiceSessionRow;
  turns: RunTurn[];
  missions: MissionRef[];
  analytics: RunAnalytics;
}
```

- [ ] **Step 4: Run it, verify it passes**

Run: `npm run test -- runEnumParity`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/runs/types.ts jarvis/ui/web/frontend/src/components/runs/__tests__/runEnumParity.test.ts
git commit -m "feat(runs-ui): TS types + enum parity test"
```

### Task 11: `components/runs/api.ts` + `hooks/useRuns.ts`

**Files:**
- Create: `jarvis/ui/web/frontend/src/components/runs/api.ts`
- Create: `jarvis/ui/web/frontend/src/hooks/useRuns.ts`
- Test: `jarvis/ui/web/frontend/src/components/runs/__tests__/useRuns.test.tsx`

- [ ] **Step 1: Write `api.ts`** (mirrors `components/sessions/api.ts`)

```ts
// components/runs/api.ts
import type { Run, RunListItem } from "./types";

export async function fetchRuns(limit = 100): Promise<RunListItem[]> {
  const res = await fetch(`/api/runs?limit=${limit}`);
  if (!res.ok) throw new Error(`HTTP ${res.status} — runs list`);
  return (await res.json()) as RunListItem[];
}

export async function fetchRunDetail(id: string): Promise<Run> {
  const res = await fetch(`/api/runs/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status} — run detail`);
  return (await res.json()) as Run;
}

export function runExportUrl(id: string): string {
  // Reuse the sessions JSON export for the raw dump (same session_id).
  return `/api/sessions/${encodeURIComponent(id)}/export?format=json`;
}
```

- [ ] **Step 2: Write `useRuns.ts`** (mirrors `hooks/useSessions.ts`; the live merge invalidates detail on any run-relevant event for the open session)

```ts
// hooks/useRuns.ts
import { useEffect, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { useEventStore } from "@/store/events";
import { fetchRunDetail, fetchRuns } from "@/components/runs/api";

const RUNS_QUERY_KEY = ["runs"] as const;

export function useRuns() {
  const queryClient = useQueryClient();
  const events = useEventStore((s) => s.events);

  const listQuery = useQuery({
    queryKey: RUNS_QUERY_KEY,
    queryFn: () => fetchRuns(100),
    refetchInterval: 30_000,
    retry: (failureCount, err) => {
      if (err instanceof Error && /HTTP 503/.test(err.message)) return false;
      return failureCount < 1;
    },
  });

  const lastBoundary = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev.name === "VoiceSessionStarted" || ev.name === "VoiceSessionEnded") return ev;
    }
    return null;
  }, [events]);

  useEffect(() => {
    if (lastBoundary === null) return;
    queryClient.invalidateQueries({ queryKey: RUNS_QUERY_KEY });
    if (lastBoundary.name === "VoiceSessionEnded") {
      const sid = (lastBoundary.payload as { session_id?: string } | null)?.session_id;
      if (typeof sid === "string" && sid.length > 0) {
        queryClient.invalidateQueries({ queryKey: ["run-detail", sid] });
      }
    }
  }, [lastBoundary, queryClient]);

  return listQuery;
}

// Live merge: while a run is open, any run-relevant event for that session
// invalidates its detail so the panels refetch (the in-flight run grows).
const LIVE_KINDS = new Set([
  "VoiceTurnStarted", "VoiceTurnCompleted", "TranscriptFinal", "IntentClassified",
  "ActionProposed", "ActionApproved", "ActionDenied", "BrainTurnStarted",
  "BrainTurnCompleted", "ResponseGenerated", "SystemStateChanged", "LatencySpan",
  "ErrorOccurred",
]);

export function useRunDetail(sessionId: string | null) {
  const queryClient = useQueryClient();
  const events = useEventStore((s) => s.events);

  const query = useQuery({
    queryKey: ["run-detail", sessionId],
    queryFn: () => {
      if (!sessionId) throw new Error("sessionId required");
      return fetchRunDetail(sessionId);
    },
    enabled: sessionId !== null,
  });

  const lastLive = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      if (LIVE_KINDS.has(events[i].name)) return events[i];
    }
    return null;
  }, [events]);

  useEffect(() => {
    if (sessionId === null || lastLive === null) return;
    const sid = (lastLive.payload as { session_id?: string } | null)?.session_id;
    if (sid === sessionId) {
      queryClient.invalidateQueries({ queryKey: ["run-detail", sessionId] });
    }
  }, [lastLive, sessionId, queryClient]);

  return query;
}
```

- [ ] **Step 3: Write a test for the hook wiring**

```tsx
// components/runs/__tests__/useRuns.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

vi.mock("@/components/runs/api", () => ({
  fetchRuns: vi.fn(async () => [{ session_id: "s1", started_ms: 1, ended_ms: 2,
    duration_s: 0.001, hangup_reason: "idle_timeout", wake_source: "voice",
    turn_count: 0, total_cost_usd: 0, error_count: 0, slo_status: "ok", preview: "hi" }]),
  fetchRunDetail: vi.fn(),
}));

import { useRuns } from "@/hooks/useRuns";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useRuns", () => {
  beforeEach(() => vi.clearAllMocks());
  it("loads the runs list", async () => {
    const { result } = renderHook(() => useRuns(), { wrapper });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data?.[0].session_id).toBe("s1");
  });
});
```

- [ ] **Step 4: Run the hook test, verify it passes**

Run: `npm run test -- useRuns`
Expected: PASS. (If the event store import path differs, align the mock; the test only exercises the list query.)

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/runs/api.ts jarvis/ui/web/frontend/src/hooks/useRuns.ts jarvis/ui/web/frontend/src/components/runs/__tests__/useRuns.test.tsx
git commit -m "feat(runs-ui): api client + useRuns/useRunDetail hooks with live merge"
```

---

## PHASE 5 — Frontend view + components

These components follow the established Transcription pattern (`views/SessionsView.tsx` + `components/sessions/{SessionList,SessionDetail,TurnCard}.tsx`). **Read those three first** to match the master-detail layout, the ScrollArea usage, the badge/typography classes, and `robustCopy`/`downloadAs` for the export button. Reuse Tailwind tokens and `useT()` exactly as they do.

### Task 12: `RunInspectorView.tsx` + `RunList.tsx`

**Files:**
- Create: `views/RunInspectorView.tsx`, `components/runs/RunList.tsx`
- Test: `components/runs/__tests__/runList.test.tsx`

- [ ] **Step 1: Write `RunList.tsx`** — left master list with debug badges.

```tsx
// components/runs/RunList.tsx
import { cn } from "@/lib/utils";
import type { RunListItem } from "./types";

const SLO_DOT: Record<string, string> = {
  ok: "bg-emerald-400", warn: "bg-amber-400", breach: "bg-destructive",
};

export function RunList({
  items, selectedId, onSelect,
}: {
  items: RunListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ul className="space-y-0.5 p-2" data-testid="run-list">
      {items.map((r) => (
        <li key={r.session_id}>
          <button
            type="button"
            onClick={() => onSelect(r.session_id)}
            className={cn(
              "flex w-full flex-col gap-1 rounded-lg px-3 py-2 text-left text-sm transition-colors",
              r.session_id === selectedId ? "bg-background" : "hover:bg-background/60",
            )}
          >
            <div className="flex items-center gap-2">
              <span className={cn("h-2 w-2 rounded-full", SLO_DOT[r.slo_status] ?? SLO_DOT.ok)} />
              <span className="flex-1 truncate">{r.preview || r.session_id.slice(0, 8)}</span>
              {r.error_count > 0 && (
                <span className="rounded-full bg-destructive/20 px-1.5 text-[10px] text-destructive">
                  ⚠ {r.error_count}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
              <span>{new Date(r.started_ms).toLocaleTimeString()}</span>
              <span>· {r.turn_count} turns</span>
              {r.duration_s !== null && <span>· {r.duration_s.toFixed(1)}s</span>}
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 2: Write `RunInspectorView.tsx`** — master-detail shell.

```tsx
// views/RunInspectorView.tsx
import { useEffect, useState } from "react";
import { useT } from "@/i18n";
import { useRuns } from "@/hooks/useRuns";
import { RunList } from "@/components/runs/RunList";
import { RunDetail } from "@/components/runs/RunDetail";

export function RunInspectorView() {
  const t = useT();
  const { data: runs, isError } = useRuns();
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (selected === null && runs && runs.length > 0) setSelected(runs[0].session_id);
  }, [runs, selected]);

  if (isError) {
    return <div className="p-6 text-sm text-muted-foreground">{t("run_inspector.unavailable")}</div>;
  }

  return (
    <div className="flex h-full">
      <div className="w-[300px] shrink-0 overflow-y-auto border-r border-border">
        <div className="px-4 py-3">
          <h2 className="text-sm font-semibold">{t("run_inspector.title")}</h2>
          <p className="text-xs text-muted-foreground">{t("run_inspector.subtitle")}</p>
        </div>
        <RunList items={runs ?? []} selectedId={selected} onSelect={setSelected} />
      </div>
      <div className="flex-1 overflow-y-auto">
        {selected ? <RunDetail sessionId={selected} /> : (
          <div className="p-6 text-sm text-muted-foreground">{t("run_inspector.empty")}</div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Write `runList.test.tsx`**

```tsx
// components/runs/__tests__/runList.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RunList } from "../RunList";

const item = {
  session_id: "s1", started_ms: Date.now(), ended_ms: null, duration_s: 1.2,
  hangup_reason: "idle_timeout", wake_source: "voice", turn_count: 3,
  total_cost_usd: 0, error_count: 2, slo_status: "breach", preview: "do a thing",
};

describe("RunList", () => {
  it("renders the error badge and fires onSelect", () => {
    const onSelect = vi.fn();
    render(<RunList items={[item]} selectedId={null} onSelect={onSelect} />);
    expect(screen.getByText("do a thing")).toBeInTheDocument();
    expect(screen.getByText(/⚠ 2/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("do a thing"));
    expect(onSelect).toHaveBeenCalledWith("s1");
  });
});
```

- [ ] **Step 4: Run it, verify it passes**

Run: `npm run test -- runList`
Expected: PASS. (RunDetail is imported by the view but not exercised here; create a stub `RunDetail` in Task 13 before running the full build.)

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/views/RunInspectorView.tsx jarvis/ui/web/frontend/src/components/runs/RunList.tsx jarvis/ui/web/frontend/src/components/runs/__tests__/runList.test.tsx
git commit -m "feat(runs-ui): RunInspectorView shell + RunList"
```

### Task 13: `RunDetail.tsx` + `TurnTrace.tsx`

**Files:**
- Create: `components/runs/RunDetail.tsx`, `components/runs/TurnTrace.tsx`
- Test: `components/runs/__tests__/runDetail.test.tsx`

- [ ] **Step 1: Write `RunDetail.tsx`** — session header (analytics) + turn accordion + raw export.

```tsx
// components/runs/RunDetail.tsx
import { useRunDetail } from "@/hooks/useRuns";
import { runExportUrl } from "@/components/runs/api";
import { TurnTrace } from "@/components/runs/TurnTrace";
import { useT } from "@/i18n";

export function RunDetail({ sessionId }: { sessionId: string }) {
  const t = useT();
  const { data: run, isLoading } = useRunDetail(sessionId);
  if (isLoading || !run) return <div className="p-6 text-sm text-muted-foreground">…</div>;

  const a = run.analytics;
  return (
    <div className="p-4" data-testid="run-detail">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="font-semibold text-foreground">
          {new Date(run.session.started_ms).toLocaleString()}
        </span>
        <span>· {run.turns.length} turns</span>
        <span>· hangup: {run.session.hangup_reason || "—"}</span>
        {a.total_duration_s !== null && <span>· Σ {a.total_duration_s.toFixed(1)}s</span>}
        <span>· ${run.session.total_cost_usd.toFixed(3)}</span>
        <SloBadge status={a.worst_slo_status} />
        <a className="ml-auto underline hover:text-primary"
           href={runExportUrl(sessionId)} target="_blank" rel="noreferrer">
          {t("run_inspector.export_raw")}
        </a>
      </div>
      <div className="space-y-2">
        {run.turns.map((turn) => <TurnTrace key={turn.trace_id} turn={turn} />)}
      </div>
    </div>
  );
}

function SloBadge({ status }: { status: string }) {
  const cls = status === "breach" ? "bg-destructive/20 text-destructive"
    : status === "warn" ? "bg-amber-400/20 text-amber-500"
    : "bg-emerald-400/20 text-emerald-500";
  return <span className={`rounded-full px-1.5 py-0.5 text-[10px] ${cls}`}>SLO {status}</span>;
}
```

- [ ] **Step 2: Write `TurnTrace.tsx`** — one expandable turn card hosting the five panels.

```tsx
// components/runs/TurnTrace.tsx
import { useState } from "react";
import type { RunTurn } from "./types";
import { TimelinePanel } from "./TimelinePanel";
import { LatencyWaterfall } from "./LatencyWaterfall";
import { DecisionPath } from "./DecisionPath";
import { ToolTable } from "./ToolTable";
import { ErrorPanel } from "./ErrorPanel";
import { useT } from "@/i18n";

export function TurnTrace({ turn }: { turn: RunTurn }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-border" data-testid="turn-trace">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
      >
        <span className="text-muted-foreground">{open ? "▾" : "▸"}</span>
        <span className="font-medium">#{turn.idx + 1}</span>
        <span className="flex-1 truncate text-muted-foreground">{turn.user_text || "—"}</span>
        <span className="text-[10px] text-muted-foreground">
          {turn.tier} · {turn.model || turn.provider} · ${turn.cost_usd.toFixed(3)}
        </span>
        {turn.errors.length > 0 && <span className="text-destructive">⚠</span>}
      </button>
      {open && (
        <div className="space-y-3 border-t border-border/60 px-3 py-3 text-xs">
          <Section label={t("run_inspector.panel.timeline")}><TimelinePanel turn={turn} /></Section>
          <Section label={t("run_inspector.panel.latency")}><LatencyWaterfall entries={turn.latency} /></Section>
          <Section label={t("run_inspector.panel.decision")}><DecisionPath steps={turn.decision_path} /></Section>
          <Section label={t("run_inspector.panel.tools")}><ToolTable tools={turn.tools} /></Section>
          <Section label={t("run_inspector.panel.errors")}><ErrorPanel errors={turn.errors} /></Section>
        </div>
      )}
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}
```

- [ ] **Step 3: Write `runDetail.test.tsx`** (mock `useRunDetail`)

```tsx
// components/runs/__tests__/runDetail.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/hooks/useRuns", () => ({
  useRunDetail: () => ({
    isLoading: false,
    data: {
      session: { id: "s1", started_ms: Date.now(), ended_ms: null, hangup_reason: "idle_timeout",
        turn_count: 1, total_cost_usd: 0.012, total_tokens_in: 0, total_tokens_out: 0,
        providers_used: [], language: "en", wake_keyword: "" },
      turns: [{ idx: 0, trace_id: "t1", user_text: "hi", jarvis_text: "yo", tier: "router",
        provider: "claude-api", model: "opus", tokens_in: 0, tokens_out: 0, cost_usd: 0.012,
        think_ms: 0, speak_ms: 0, timeline: [], latency: [], decision_path: [], tools: [],
        errors: [], extras: { interrupted: false, cache_hit: null, endpoint_reason: null, context_tokens: null } }],
      missions: [],
      analytics: { total_duration_s: 1.0, total_think_ms: 0, total_speak_ms: 0,
        cost_by_provider: {}, tool_counts: {}, interruptions: 0, worst_slo_status: "ok" },
    },
  }),
}));
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));

import { RunDetail } from "../RunDetail";

describe("RunDetail", () => {
  it("renders the header and one turn", () => {
    render(<RunDetail sessionId="s1" />);
    expect(screen.getByTestId("run-detail")).toBeInTheDocument();
    expect(screen.getByText(/SLO ok/)).toBeInTheDocument();
    expect(screen.getByTestId("turn-trace")).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run it, verify it passes** (requires the five panel components to at least exist as stubs — create them in Task 14 first, OR create empty stubs here and flesh out next; recommended: do Task 14 before running this test)

Run: `npm run test -- runDetail`
Expected: PASS once panels exist.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/runs/RunDetail.tsx jarvis/ui/web/frontend/src/components/runs/TurnTrace.tsx jarvis/ui/web/frontend/src/components/runs/__tests__/runDetail.test.tsx
git commit -m "feat(runs-ui): RunDetail header + analytics + TurnTrace accordion"
```

### Task 14: The five panels

**Files:**
- Create: `components/runs/TimelinePanel.tsx`, `LatencyWaterfall.tsx`, `DecisionPath.tsx`, `ToolTable.tsx`, `ErrorPanel.tsx`
- Test: `components/runs/__tests__/latencyWaterfall.test.tsx`

- [ ] **Step 1: Write the failing test** for the latency colors (the panel with real logic)

```tsx
// components/runs/__tests__/latencyWaterfall.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { LatencyWaterfall } from "../LatencyWaterfall";

describe("LatencyWaterfall", () => {
  it("colors each phase by slo_status and shows an empty note", () => {
    const { rerender } = render(<LatencyWaterfall entries={[]} />);
    expect(screen.getByText("n/a")).toBeInTheDocument();
    rerender(<LatencyWaterfall entries={[
      { phase: "intent_decision", duration_ms: 200, slo_status: "breach" },
      { phase: "stt_finalize", duration_ms: 40, slo_status: "ok" },
    ]} />);
    expect(screen.getByText(/intent_decision/)).toBeInTheDocument();
    expect(screen.getByTestId("lat-intent_decision")).toHaveAttribute("data-slo", "breach");
  });
});
```

- [ ] **Step 2: Run it, verify it fails**

Run: `npm run test -- latencyWaterfall`
Expected: FAIL — `../LatencyWaterfall` not found.

- [ ] **Step 3: Write the five panels**

```tsx
// components/runs/LatencyWaterfall.tsx
import type { LatencyEntry } from "./types";

const BAR: Record<string, string> = {
  ok: "bg-emerald-400/70", warn: "bg-amber-400/80", breach: "bg-destructive/80",
};

export function LatencyWaterfall({ entries }: { entries: LatencyEntry[] }) {
  if (entries.length === 0) return <span className="text-muted-foreground/60">n/a</span>;
  const max = Math.max(...entries.map((e) => e.duration_ms), 1);
  return (
    <div className="space-y-1">
      {entries.map((e) => (
        <div key={e.phase} className="flex items-center gap-2"
             data-testid={`lat-${e.phase}`} data-slo={e.slo_status}>
          <span className="w-40 shrink-0 truncate font-mono text-[10px]">{e.phase}</span>
          <div className="h-2 flex-1 rounded bg-background">
            <div className={`h-2 rounded ${BAR[e.slo_status] ?? BAR.ok}`}
                 style={{ width: `${Math.max(3, (e.duration_ms / max) * 100)}%` }} />
          </div>
          <span className="w-14 shrink-0 text-right font-mono text-[10px]">
            {e.duration_ms.toFixed(0)}ms
          </span>
        </div>
      ))}
    </div>
  );
}
```

```tsx
// components/runs/TimelinePanel.tsx
import type { RunTurn } from "./types";

export function TimelinePanel({ turn }: { turn: RunTurn }) {
  if (turn.timeline.length === 0) return <span className="text-muted-foreground/60">n/a</span>;
  return (
    <ol className="space-y-0.5 font-mono text-[10px]">
      {turn.timeline.map((ev, i) => (
        <li key={i} className="flex gap-2">
          <span className="w-12 shrink-0 text-right text-muted-foreground">+{(ev.offset_ms / 1000).toFixed(2)}s</span>
          <span className="w-44 shrink-0 truncate">{ev.kind}</span>
          <span className="flex-1 truncate text-muted-foreground">{ev.summary}</span>
        </li>
      ))}
    </ol>
  );
}
```

```tsx
// components/runs/DecisionPath.tsx
import type { DecisionStep } from "./types";

const KIND_ICON: Record<string, string> = {
  tier: "◆", route: "→", risk: "⚖", brain: "🧠", mission: "⚙", fallback: "↺",
};

export function DecisionPath({ steps }: { steps: DecisionStep[] }) {
  if (steps.length === 0) return <span className="text-muted-foreground/60">n/a</span>;
  return (
    <ol className="space-y-0.5 text-[11px]">
      {steps.map((s, i) => (
        <li key={i} className="flex gap-2">
          <span className="w-4 shrink-0 text-center text-muted-foreground">{KIND_ICON[s.kind] ?? "·"}</span>
          <span>{s.label}</span>
          {s.detail && <span className="text-muted-foreground">— {s.detail}</span>}
        </li>
      ))}
    </ol>
  );
}
```

```tsx
// components/runs/ToolTable.tsx
import type { ToolCall } from "./types";

export function ToolTable({ tools }: { tools: ToolCall[] }) {
  if (tools.length === 0) return <span className="text-muted-foreground/60">—</span>;
  return (
    <table className="w-full text-[11px]">
      <tbody>
        {tools.map((t, i) => (
          <tr key={i} className="border-t border-border/40">
            <td className="py-0.5 font-mono">{t.name}</td>
            <td className="text-muted-foreground">{t.risk_tier || "—"}</td>
            <td className="text-muted-foreground">{t.approved_by ?? ""}</td>
            <td className="text-right">{t.duration_ms != null ? `${t.duration_ms}ms` : ""}</td>
            <td className={`text-right ${t.success ? "text-emerald-500" : "text-destructive"}`}>
              {t.exit_code != null ? `exit ${t.exit_code}` : (t.success ? "ok" : "fail")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

```tsx
// components/runs/ErrorPanel.tsx
import type { ErrorEntry } from "./types";

export function ErrorPanel({ errors }: { errors: ErrorEntry[] }) {
  if (errors.length === 0) return <span className="text-muted-foreground/60">—</span>;
  return (
    <ul className="space-y-1">
      {errors.map((e, i) => (
        <li key={i} className="rounded bg-destructive/10 px-2 py-1 text-[11px]">
          <span className="font-semibold text-destructive">{e.source}</span>
          {e.layer && <span className="text-muted-foreground"> · {e.layer}</span>}
          <span className="text-muted-foreground"> — {e.message}</span>
        </li>
      ))}
    </ul>
  );
}
```

- [ ] **Step 4: Run the latency test + the deferred RunDetail test**

Run: `npm run test -- latencyWaterfall runDetail`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/runs/TimelinePanel.tsx jarvis/ui/web/frontend/src/components/runs/LatencyWaterfall.tsx jarvis/ui/web/frontend/src/components/runs/DecisionPath.tsx jarvis/ui/web/frontend/src/components/runs/ToolTable.tsx jarvis/ui/web/frontend/src/components/runs/ErrorPanel.tsx jarvis/ui/web/frontend/src/components/runs/__tests__/latencyWaterfall.test.tsx
git commit -m "feat(runs-ui): five per-turn forensic panels"
```

---

## PHASE 6 — Wiring + i18n + final verification

### Task 15: Register the section (store + sidebar + main view)

**Files:**
- Modify: `store/events.ts` (`SectionId` union ~line 12, `SECTION_IDS` ~line 61, `SECTION_LABELS` ~line 67)
- Modify: `components/layout/Sidebar.tsx` (`NAV_GROUPS[1]`, the "Content & data" group, ~line 63)
- Modify: `components/layout/MainView.tsx` (import + `case`)

- [ ] **Step 1: Add the id to `store/events.ts`**

In the `SectionId` union, add `| "run_inspector"`. In the `SECTION_IDS` array add `"run_inspector",`. In `SECTION_LABELS` add `run_inspector: "Run Inspector",`.

- [ ] **Step 2: Add the sidebar entry** — in `Sidebar.tsx`, import `Gauge` from `lucide-react` (add to the existing import block), and add to `NAV_GROUPS[1]` right after the `sessions` row:

```tsx
    { id: "run_inspector", labelKey: "nav.run_inspector", icon: Gauge },
```

- [ ] **Step 3: Add the view switch** — in `MainView.tsx`, add the import and the case:

```tsx
import { RunInspectorView } from "@/views/RunInspectorView";
```
```tsx
    case "run_inspector":
      return <RunInspectorView />;
```

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors. (If `Gauge` is not exported by the installed lucide version, use `Activity` or `BarChart3` instead.)

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/store/events.ts jarvis/ui/web/frontend/src/components/layout/Sidebar.tsx jarvis/ui/web/frontend/src/components/layout/MainView.tsx
git commit -m "feat(runs-ui): register Run Inspector section in nav + router"
```

### Task 16: i18n strings (de / en / es)

**Files:**
- Modify: `i18n/locales/en.json`, `de.json`, `es.json`

Per the Output-Language Policy the English value is the source; de/es are translations of the UI label (UI strings are user-facing, so they are translated — this is the one place non-English text is correct, exactly like `sessions_view`).

- [ ] **Step 1: Add to `en.json`**

Add `"nav": { ... "run_inspector": "Run Inspector" }` (merge into the existing `nav` object) and a new root block:

```json
  "run_inspector": {
    "title": "Run Inspector",
    "subtitle": "Per-run forensics: timeline, latency, decisions, tools, errors.",
    "empty": "Select a run to inspect.",
    "unavailable": "Run recorder is disabled.",
    "export_raw": "Export raw (JSON)",
    "panel": {
      "timeline": "Timeline",
      "latency": "Latency",
      "decision": "Decision path",
      "tools": "Tools",
      "errors": "Errors"
    }
  }
```

- [ ] **Step 2: Add the German block to `de.json`** (`nav.run_inspector": "Run Inspector"` + )

```json
  "run_inspector": {
    "title": "Run Inspector",
    "subtitle": "Forensik pro Run: Timeline, Latenz, Entscheidungen, Tools, Fehler.",
    "empty": "Wähle einen Run zur Analyse.",
    "unavailable": "Der Run-Recorder ist deaktiviert.",
    "export_raw": "Roh-Export (JSON)",
    "panel": {
      "timeline": "Timeline",
      "latency": "Latenz",
      "decision": "Entscheidungspfad",
      "tools": "Tools",
      "errors": "Fehler"
    }
  }
```

- [ ] **Step 3: Add the Spanish block to `es.json`** (`nav.run_inspector": "Run Inspector"` + )

```json
  "run_inspector": {
    "title": "Run Inspector",
    "subtitle": "Análisis por run: cronología, latencia, decisiones, herramientas, errores.",
    "empty": "Selecciona un run para analizar.",
    "unavailable": "El grabador de runs está desactivado.",
    "export_raw": "Exportar bruto (JSON)",
    "panel": {
      "timeline": "Cronología",
      "latency": "Latencia",
      "decision": "Ruta de decisión",
      "tools": "Herramientas",
      "errors": "Errores"
    }
  }
```

- [ ] **Step 4: Validate JSON**

Run (from `frontend/`): `node -e "['de','en','es'].forEach(l=>require('./src/i18n/locales/'+l+'.json'))" && echo ok`
Expected: `ok` (no JSON parse error).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "feat(runs-ui): i18n strings for Run Inspector (en/de/es)"
```

### Task 17: Full build + suite + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Backend suite (new + adjacent)**

Run: `pytest tests/unit/runs/ tests/unit/sessions/ tests/unit/clis/test_usage_log_trace.py -q`
Expected: all PASS.

- [ ] **Step 2: Lint the touched Python**

Run: `ruff check jarvis/runs/ jarvis/sessions/recorder.py jarvis/clis/usage_log.py jarvis/ui/web/server.py`
Expected: clean on touched lines (pre-existing warnings elsewhere are out of scope).

- [ ] **Step 3: Frontend tests + build**

Run (from `frontend/`): `npm run test -- runs` then `npm run build`
Expected: tests PASS; `vite build` succeeds into `jarvis/ui/web/dist`.

- [ ] **Step 4: Manual smoke (requires a running app + at least one recorded session AFTER Task 1 shipped)**

Restart the app via `POST /api/settings/restart-app` (not Stop-Process). Then:
- `curl http://127.0.0.1:47821/api/runs` returns a JSON array.
- `curl http://127.0.0.1:47821/api/runs/<session_id>` returns a `Run` with `turns`, `analytics`.
- In the UI, the "Run Inspector" sidebar entry opens the master-detail; expanding a turn shows the five panels; a run recorded before Task 1 shows "n/a" for latency/decision/errors (expected).
- Optional: a live run grows while the view is open (WS).

- [ ] **Step 5: Final commit (if any verification fixes were needed)**

```bash
git add -p   # stage only run-inspector files you touched
git commit -m "test(runs): verification fixes for run-inspector v1"
```

---

## Notes carried from the spec (for the implementer)

- **Capture-gap honesty:** Task 1 widens the recorder whitelist, so latency/decision/error panels are populated only for sessions recorded *after* this ships. Older runs render "n/a" for those panels — this is correct, not a bug. A FlightRecorder backfill for historical runs is explicitly v2.
- **trace_id ↔ turn_id:** the tool join in `loader._build_turn` assumes the turn's `id` equals the `trace_id` CLI calls are tagged with. Verify during Task 17; if they diverge, the decision/latency/error panels still work (they read `voice_events`), only the CLI-duration/exit-code enrichment is affected — fix by threading `trace_id` onto `VoiceTurnRow` in a follow-up.
- **AP-9 / no hot path:** nothing in `jarvis/runs/` may be called from the voice pipeline. It runs only on REST/WS request.
- **AP-20 / WS loop:** the live socket loop must `break` on `RuntimeError`, never `continue` (Task 8).
- **Cloud-first:** the layer is lazy, capped, and degrades (503 / empty slices) — it boots on a headless VPS even though it is optimized for maintainer debugging.
- **v2 deferred:** granular brain-thinking persistence (`BrainThinkingStep`), cross-run p50/p95 dashboard, per-run annotations (`runs.db`), real barge-in signal feeding `TurnExtras.interrupted`.
