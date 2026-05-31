---
name: phase6-smoke-test
description: Use to run a quick end-to-end smoke test against the Phase-6 Mission-Manager + Critic-Loop. Spawns a mock-task through the full pipeline (Mission-spawn → Worker-stub → Critic-verdict → Kontrollierer signature → Bus-event trace) and reports a pass/fail with structured output. Use after Phase-6 modifications or before merging changes that touch jarvis/missions/.
---

# Phase-6 Smoke-Test

This skill runs a fast end-to-end smoke test against the Phase-6 pipeline. It is not as exhaustive as the real test suite, but in under 30s it delivers a confidence check that the mission pipeline runs through without hanging.

## When to use

- After changes to `jarvis/missions/manager.py`, `critic/`, `kontrollierer/`, `workers/`, `isolation/`.
- Before a merge PR to `main`, so that pipeline integrity is confirmed.
- When you suspect a Mission-Manager hang (reattach, concurrency).

## Steps

1. **Check prerequisites:**
   - `python -c "from jarvis.missions.manager import MissionManager"` — an ImportError catches drift early.
   - `pytest tests/missions/ --collect-only -q` — lists the tests, catches collection errors.

2. **Run the smoke-test script** (already exists under `scripts/smoke_phase6_p1.py`, `scripts/smoke_phase6_p2.py`, `scripts/smoke_phase6_p3.py`):
   ```bash
   python scripts/smoke_phase6_p1.py    # Foundation: Bus + Store + Manager
   python scripts/smoke_phase6_p2.py    # Worker-Layer: Job-Object + Worktree
   python scripts/smoke_phase6_p2_jobkill.py  # Job-Kill-Pfad
   python scripts/smoke_phase6_p3.py    # Critic-Loop + Kontrollierer
   ```
   If a script is missing — mark it as `MISSING` in the report and continue.

3. **Test run** with the `jarvis-test-runner` subagent against `tests/missions/`:
   ```
   pytest tests/missions/ -v --tb=short --no-header --maxfail=15
   ```

4. **Generate the report:**
   ```
   ## Phase-6 Smoke-Test — <timestamp>
   
   ### Smoke scripts
   - p1 Foundation: <PASS|FAIL — short>
   - p2 Worker-Layer: <PASS|FAIL>
   - p2 Job-Kill: <PASS|FAIL>
   - p3 Critic-Loop: <PASS|FAIL>
   
   ### Test suite
   - Tests: <N total, M green, K red>
   - Failures: <if present, top 3 with test name>
   
   ### Verdict
   <SMOKE PASS | SMOKE FAIL — N Failures>
   ```

## Strictly forbidden

- NO modifying mission code to repair it — the smoke test is diagnostic, not reactive.
- NO "skipping over" tests when they are red — the report shows it.
- NO re-run on flaky — the output shows the behavior exactly as it is.

## Edge cases

- **Smoke scripts missing:** if all four `scripts/smoke_phase6_*.py` are missing, Phase 6 is probably not installed. Report with `PHASE_6_NOT_INSTALLED`.
- **Tests collect 0:** either Phase 6 is not there yet, or the pytest path is wrong. Report with `NO_TESTS_COLLECTED`.
- **Smoke script hangs > 30s:** time cap violated — report it as MAJOR.
