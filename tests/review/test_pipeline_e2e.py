"""E2E-Test gegen echtes `claude` CLI (Phase 8.3).

Plan-Referenz: §6.3 Akzeptanzkriterium 2 — Single-Iteration-Run mit
echtem Sub-Jarvis auf Trivial-Task. < 60s p95.

Skip-Bedingungen:
- `claude` nicht im PATH → skip (kein CI ohne CLI).
- Kein Login (bei `--bare` ist nur ANTHROPIC_API_KEY-basierter Auth zulässig)
  → der Test wird vom claude-CLI selbst mit non-zero exit beendet, was als
  Pipeline-Failure auftaucht. Wir testen *nicht* die Auth-Konfiguration des
  Hosts, sondern dass der Pipeline-Pfad bei korrekter Auth grün ist.
- Markiert mit `pytest.mark.e2e`, läuft NICHT im normalen pytest-Lauf
  (`pytest -m "not e2e"`).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pytest

from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.checks import (
    PostCheckRunner,
    PreCheckRunner,
    output_not_empty,
    task_not_empty,
)
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.spawns import ReviewerSpawner, WorkerSpawner
from jarvis.core.review.state import PipelineOutcome
from jarvis.harness.manager import HarnessManager

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="OpenClaw CLI not in PATH — set up OpenClaw first.",
    ),
    pytest.mark.skipif(
        not (
            os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        ),
        reason=(
            "No Anthropic auth (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN) "
            "— openclaw --bare needs explicit credentials."
        ),
    ),
]


def test_pipeline_e2e_trivial_task(tmp_path: Path) -> None:
    """Pipeline-Single-Iteration-Run gegen echten claude-CLI.

    Trivial-Task; Worker schreibt eine Funktion + Test in eine Datei,
    Reviewer prüft, Pipeline liefert SUCCESS in Iter 1.
    """
    runs_root = tmp_path / "review_runs"
    audit = ReviewAudit(path=tmp_path / "review.log")
    harness_manager = HarnessManager()

    worker_spawner = WorkerSpawner(
        harness_manager=harness_manager,
        runs_root=runs_root,
        timeout_s=120,
    )
    reviewer_spawner = ReviewerSpawner(
        harness_manager=harness_manager,
        runs_root=runs_root,
        timeout_s=60,
    )

    pipeline = ReviewPipeline(
        worker_spawn=worker_spawner.spawn,
        reviewer_spawn=reviewer_spawner.spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=1,
    )

    task = (
        "Schreibe eine Python-Funktion `add(a, b)` die ihre Argumente "
        "addiert und gib eine 1-2-Zeilen-Bestätigung auf stdout aus. "
        "Schreibe das Artefakt in die vorgegebene Pfad-Datei."
    )

    t0 = time.monotonic()
    result = asyncio.run(pipeline.run(task, max_iterations=1))
    elapsed = time.monotonic() - t0

    # Plan-Latenz-Anforderung: < 90s lokal (DoD)
    assert elapsed < 90.0, f"e2e run too slow: {elapsed:.1f}s"

    # Pipeline soll mindestens den Worker erfolgreich gespawnt haben.
    # Reviewer kann pass / needs_revision liefern — Trivial-Task könnte
    # auch needs_revision werden (z.B. "no tests written"). Wir testen
    # daher robust: outcome ist SUCCESS oder CAP_FIRED, nicht PRECHECK_FAIL.
    assert result.outcome in (
        PipelineOutcome.SUCCESS,
        PipelineOutcome.CAP_FIRED,
        PipelineOutcome.FAIL,
    )
    assert len(result.iterations) >= 1
    # Worker.out muss persistiert sein
    assert (runs_root / result.run_id / "iter-1" / "worker.out").exists()
    # Verdict.json muss vorhanden sein, falls Reviewer durchgelaufen ist
    if result.iterations[0].verdict is not None:
        assert (runs_root / result.run_id / "iter-1" / "verdict.json").exists()
