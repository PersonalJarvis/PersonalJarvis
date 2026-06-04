"""Unit-Tests fuer den OpenClaw-Worker-Aggregator (Phase 9 Welle 4 UI).

Pure-helper-Tests ohne FastAPI / DB-IO. Deckt:
- Detection-Heuristik (step.harness == 'openclaw' UND fallback ueber model+sid)
- State-Dir-Konvention (matcht ``OpenClawHarness._build_spec``)
- Reattach-Status: live -> killed -> ended (mission terminal)
- Cost/Tokens-Aggregation aus Progress + DraftReady
- Empty-Stream / Non-OpenClaw-Workers
"""
from __future__ import annotations

from jarvis.missions.events import (
    CriticVerdictReady,
    EventEnvelope,
    MissionApproved,
    WorkerDraftReady,
    WorkerKilled,
    WorkerProgress,
    WorkerSpawned,
    now_ms,
)
from jarvis.ui.web.missions_worker import extract_worker_missions


def _envelope(payload, *, mission_id: str = "mid-1", worker_id: str | None = None, ts: int | None = None) -> EventEnvelope:
    return EventEnvelope(
        mission_id=mission_id,
        worker_id=worker_id,
        source_actor="worker",
        ts_ms=ts if ts is not None else now_ms(),
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_no_workers_returns_empty():
    assert extract_worker_missions([]) == []


def test_non_openclaw_worker_is_ignored():
    """Reine Claude/Codex-Worker (kein step.harness, kein /-Modell) tauchen
    nicht in der OpenClaw-Liste auf."""
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"task": "build foo"},
        pid=1234,
        cli="claude",
        model="claude-sonnet-4-6",
        worktree="C:/wt/agent-1",
        session_id=None,
    )
    workers = extract_worker_missions([_envelope(spawn, worker_id="w1")])
    assert workers == []


def test_step_harness_marker_is_canonical():
    """``step["harness"] == "openclaw"`` triggert die Detection auch ohne
    session_id oder /-Modell."""
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"harness": "openclaw"},
        pid=42,
        cli="python",  # OpenClaw kann sich noch nicht als 'openclaw' eintragen
        model="any",
        worktree="C:/wt/agent-1",
        session_id=None,
    )
    workers = extract_worker_missions([_envelope(spawn, worker_id="w1")])
    assert len(workers) == 1
    assert workers[0]["worker_id"] == "w1"


def test_session_id_plus_provider_slash_fallback_detection():
    """Fallback: session_id present + Provider-Prefix-Modell (gemini/...)."""
    spawn = WorkerSpawned(
        worker_id="w1",
        step={},
        pid=42,
        cli="python",
        model="gemini/gemini-3.1-pro-preview",
        worktree="C:/wt/agent-1",
        session_id="sess-abc",
    )
    workers = extract_worker_missions([_envelope(spawn, worker_id="w1")])
    assert len(workers) == 1
    assert workers[0]["model"] == "gemini/gemini-3.1-pro-preview"
    assert workers[0]["session_id"] == "sess-abc"


# ---------------------------------------------------------------------------
# State-Dir + Logfile
# ---------------------------------------------------------------------------


def test_state_dir_matches_openclaw_harness_convention():
    """Der state_dir-Pfad MUSS exakt der ``OpenClawHarness._build_spec``-
    Konvention folgen — sonst zeigt die UI den falschen Ort an."""
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"harness": "openclaw"},
        pid=42,
        cli="python",
        model="gemini/gemini-3.1-pro-preview",
        worktree="C:/wt/agent-1",
        session_id="sess-abc",
    )
    workers = extract_worker_missions([_envelope(spawn, worker_id="w1")])
    expected = "C:/wt/agent-1/.openclaw_state/sess-abc/openclaw_state"
    assert workers[0]["state_dir"] == expected
    assert workers[0]["log_path"] == expected + "/run.log"


def test_state_dir_empty_when_session_or_worktree_missing():
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"harness": "openclaw"},
        pid=42,
        cli="python",
        model="any",
        worktree="",
        session_id=None,
    )
    workers = extract_worker_missions([_envelope(spawn, worker_id="w1")])
    assert workers[0]["state_dir"] == ""
    assert workers[0]["log_path"] == ""


# ---------------------------------------------------------------------------
# Reattach-Status
# ---------------------------------------------------------------------------


def test_live_status_when_only_spawned():
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"harness": "openclaw"},
        pid=42,
        cli="python",
        model="x/y",
        worktree="C:/wt",
        session_id="s1",
    )
    workers = extract_worker_missions([_envelope(spawn, worker_id="w1")])
    assert workers[0]["reattach_status"] == "live"
    assert workers[0]["ended_ms"] is None
    assert workers[0]["ended_reason"] is None


def test_killed_status_after_worker_killed():
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"harness": "openclaw"},
        pid=42,
        cli="python",
        model="x/y",
        worktree="C:/wt",
        session_id="s1",
    )
    kill = WorkerKilled(worker_id="w1", reason="user")
    workers = extract_worker_missions([
        _envelope(spawn, worker_id="w1", ts=1000),
        _envelope(kill, worker_id="w1", ts=2000),
    ])
    assert workers[0]["reattach_status"] == "killed"
    assert workers[0]["ended_ms"] == 2000
    assert workers[0]["ended_reason"] == "user"


def test_ended_status_when_mission_terminal_without_kill():
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"harness": "openclaw"},
        pid=42,
        cli="python",
        model="x/y",
        worktree="C:/wt",
        session_id="s1",
    )
    approved = MissionApproved(
        result_uri="artifact://x",
        tokens_used=100,
        cost_usd=0.5,
        wall_ms=1000,
        summary_de="fertig",
        summary_en="done",
    )
    workers = extract_worker_missions([
        _envelope(spawn, worker_id="w1", ts=1000),
        _envelope(approved, ts=3000),
    ])
    assert workers[0]["reattach_status"] == "ended"
    assert workers[0]["ended_ms"] == 3000
    assert workers[0]["ended_reason"] == "mission_approved"


def test_killed_wins_over_terminal():
    """Wenn Worker bereits explizit gekillt wurde, soll Mission-Terminal das
    nicht ueberschreiben (killed != ended)."""
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"harness": "openclaw"},
        pid=42,
        cli="python",
        model="x/y",
        worktree="C:/wt",
        session_id="s1",
    )
    kill = WorkerKilled(worker_id="w1", reason="timeout")
    approved = MissionApproved(
        result_uri="artifact://x",
        tokens_used=100,
        cost_usd=0.5,
        wall_ms=1000,
        summary_de="fertig",
        summary_en="done",
    )
    workers = extract_worker_missions([
        _envelope(spawn, worker_id="w1", ts=1000),
        _envelope(kill, worker_id="w1", ts=2000),
        _envelope(approved, ts=3000),
    ])
    assert workers[0]["reattach_status"] == "killed"
    assert workers[0]["ended_reason"] == "timeout"


# ---------------------------------------------------------------------------
# Cost / Tokens
# ---------------------------------------------------------------------------


def test_cost_aggregated_from_progress_then_draft_ready():
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"harness": "openclaw"},
        pid=42,
        cli="python",
        model="x/y",
        worktree="C:/wt",
        session_id="s1",
    )
    p1 = WorkerProgress(worker_id="w1", pct=0.3, note=None, stalled=False, tokens_so_far=500, cost_so_far=0.01)
    p2 = WorkerProgress(worker_id="w1", pct=0.6, note=None, stalled=False, tokens_so_far=900, cost_so_far=0.02)
    draft = WorkerDraftReady(
        worker_id="w1",
        artifact_uri="artifact://w1",
        diff="--- a\n+++ b",
        tokens_used=1500,
        cost_usd=0.05,
        session_id="s1",
    )
    workers = extract_worker_missions([
        _envelope(spawn, worker_id="w1", ts=1000),
        _envelope(p1, worker_id="w1", ts=1100),
        _envelope(p2, worker_id="w1", ts=1200),
        _envelope(draft, worker_id="w1", ts=1300),
    ])
    # DraftReady wins over Progress (last word)
    assert workers[0]["cost_usd"] == 0.05
    assert workers[0]["tokens_used"] == 1500
    assert workers[0]["reattach_status"] == "ended"
    assert workers[0]["ended_reason"] == "draft_ready"


# ---------------------------------------------------------------------------
# Multi-Worker
# ---------------------------------------------------------------------------


def test_multiple_openclaw_workers_preserved_in_spawn_order():
    s1 = WorkerSpawned(worker_id="w1", step={"harness": "openclaw"}, pid=1, cli="python", model="a/b", worktree="C:/wt", session_id="s1")
    s2 = WorkerSpawned(worker_id="w2", step={"harness": "openclaw"}, pid=2, cli="python", model="a/b", worktree="C:/wt", session_id="s2")
    other = WorkerSpawned(worker_id="w3", step={}, pid=3, cli="claude", model="claude-sonnet-4-6", worktree="C:/wt", session_id=None)
    workers = extract_worker_missions([
        _envelope(s1, worker_id="w1", ts=1000),
        _envelope(other, worker_id="w3", ts=1100),
        _envelope(s2, worker_id="w2", ts=1200),
    ])
    assert [w["worker_id"] for w in workers] == ["w1", "w2"]


def test_unknown_event_types_are_ignored_safely():
    """Critic-Verdicts oder andere Non-Worker-Events crashen den Aggregator nicht."""
    spawn = WorkerSpawned(
        worker_id="w1",
        step={"harness": "openclaw"},
        pid=1,
        cli="python",
        model="a/b",
        worktree="C:/wt",
        session_id="s1",
    )
    verdict = CriticVerdictReady(
        worker_id="w1",
        verdict="approve",
        summary="ok",
        confidence=0.9,
        axes={},
        iteration=0,
    )
    workers = extract_worker_missions([
        _envelope(spawn, worker_id="w1"),
        _envelope(verdict, worker_id="w1"),
    ])
    assert len(workers) == 1
    assert workers[0]["reattach_status"] == "live"
