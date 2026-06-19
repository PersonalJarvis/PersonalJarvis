"""Fail-closed recovery gate — only a proven primary instance sweeps live missions.

Three fail-open defaults used to conspire to sweep a live desktop app's
in-flight missions when any side-process (smoke script, eval harness, --no-lock
parallel session) opened the same missions.db:

  1. server.py: env unset → default "1" (primary)   [fixed: require == "1"]
  2. init.py:   recover_missions=True by default      [fixed: default False]
  3. manager.py: recover=True by default              [fixed: default False]

These tests pin that behaviour so the defaults cannot silently revert.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState


# ---------------------------------------------------------------------------
# Test 1: bootstrap_missions must NOT call startup_recover when
#         JARVIS_PRIMARY_INSTANCE is unset (env absent → not primary).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_does_not_recover_without_explicit_primary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """bootstrap_missions() with no recover_missions arg must NOT sweep missions.

    When JARVIS_PRIMARY_INSTANCE is absent from the environment, any process
    that calls bootstrap_missions with the default recover_missions flag is a
    side-process (smoke script, eval harness, etc.) and must not run
    startup_recover.  Only an explicit recover_missions=True (set by the
    launcher after proving it holds the single-instance lock) may sweep.
    """
    # Remove the env var entirely so no implicit primary claim is possible.
    monkeypatch.delenv("JARVIS_PRIMARY_INSTANCE", raising=False)

    db_path = tmp_path / "missions.db"
    isolation_root = tmp_path / "sub-agents"

    spy_called = False

    async def _spy_recover(store, **kwargs):  # noqa: ANN001
        nonlocal spy_called
        spy_called = True
        return []

    with patch("jarvis.missions.manager.startup_recover", side_effect=_spy_recover):
        from jarvis.missions.init import bootstrap_missions

        result = await bootstrap_missions(
            db_path=db_path,
            isolation_root=isolation_root,
            # recover_missions NOT passed → must default to False (fail-closed)
        )

    assert not spy_called, (
        "startup_recover must NOT be called when recover_missions is not "
        "explicitly set to True — unset env must not default to primary"
    )

    # Clean up the manager so the DB is released.
    manager = result.get("manager")
    if manager is not None:
        await manager.stop()


# ---------------------------------------------------------------------------
# Test 2: MissionManager.start() must default to no recovery (fail-closed).
# ---------------------------------------------------------------------------


async def test_manager_start_defaults_to_no_recovery(tmp_path: Path) -> None:
    """MissionManager.start() with no args must not sweep a stale non-terminal mission.

    The old default was recover=True — any fresh start() would sweep in-flight
    missions on a shared DB.  The new default is recover=False (fail-closed):
    only an explicit start(recover=True) from a proven primary sweeps missions.
    """
    db_path = tmp_path / "missions.db"

    # Seed the DB with a non-terminal (RUNNING) mission via a first manager
    # instance that explicitly passes recover=False so it does not sweep itself.
    seeder = MissionManager(db_path)
    await seeder.start(recover=False)
    mid = await seeder.dispatch(prompt="primary-live-mission")
    await seeder.transition_state(mid, MissionState.RUNNING, reason="worker-spawn")
    await seeder.stop()

    # A second instance (simulating any side-process) boots with the DEFAULT args.
    # It must NOT sweep the stale RUNNING mission.
    observer = MissionManager(db_path)
    recovered = await observer.start()  # ← default, no explicit recover=
    try:
        assert recovered == [], (
            "MissionManager.start() default must not recover anything; "
            f"got {recovered!r}"
        )
        view = await observer.mission(mid)
        assert view is not None
        assert view.state == MissionState.RUNNING, (
            f"The seeded RUNNING mission must remain RUNNING, got {view.state}"
        )
    finally:
        await observer.stop()


# ---------------------------------------------------------------------------
# Test 3: an explicit recover_missions=True still recovers — the primary path works.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_primary_still_recovers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """bootstrap_missions(recover_missions=True) must call startup_recover.

    The fail-closed default must not accidentally break the legitimate primary
    launch path.  The launcher acquires the single-instance lock and then calls
    bootstrap_missions with recover_missions=True — that must still sweep orphaned
    missions from a genuine crash.
    """
    monkeypatch.delenv("JARVIS_PRIMARY_INSTANCE", raising=False)

    db_path = tmp_path / "missions.db"
    isolation_root = tmp_path / "sub-agents"

    spy_called = False

    async def _spy_recover(store, **kwargs):  # noqa: ANN001
        nonlocal spy_called
        spy_called = True
        return []

    with patch("jarvis.missions.manager.startup_recover", side_effect=_spy_recover):
        from jarvis.missions.init import bootstrap_missions

        result = await bootstrap_missions(
            db_path=db_path,
            isolation_root=isolation_root,
            recover_missions=True,  # ← explicit opt-in: the proven primary path
        )

    assert spy_called, (
        "startup_recover MUST be called when recover_missions=True is passed "
        "explicitly — the legitimate primary launch path must still work"
    )

    manager = result.get("manager")
    if manager is not None:
        await manager.stop()
