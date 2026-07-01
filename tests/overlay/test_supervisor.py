"""OverlaySupervisor — job object + backoff + cap. Plan §4.3 + AD-9 + AD-10."""

from __future__ import annotations

import asyncio
import random
from typing import Any
from unittest import mock

import pytest

from jarvis.overlay.supervisor import (
    DEFAULT_RESTART_CAP_COUNT,
    OverlaySupervisor,
    _backoff_delay,
)


# -------------------------------------------------------------------------
# _backoff_delay — AD-10 formula
# -------------------------------------------------------------------------


def test_backoff_first_failure_around_half_second() -> None:
    """failures=0 -> base=0.5, +/- 20% jitter -> ~0.4 .. 0.6 s."""
    rng = random.Random(0)
    delays = [_backoff_delay(0, rng=rng) for _ in range(50)]
    assert all(0.35 < d < 0.65 for d in delays)


def test_backoff_caps_at_30_seconds() -> None:
    """Plan AD-10: ``min(30, ...)`` — no matter how many failures."""
    rng = random.Random(0)
    for f in [10, 20, 50, 100]:
        delay = _backoff_delay(f, rng=rng)
        assert delay <= 30 * 1.2  # +20% jitter ceiling


def test_backoff_grows_exponentially() -> None:
    """failures=0,1,2,3 -> base 0.5, 1, 2, 4."""
    rng = random.Random(0)
    avg = []
    for f in [0, 1, 2, 3]:
        samples = [_backoff_delay(f, rng=rng) for _ in range(50)]
        avg.append(sum(samples) / len(samples))
    # Each further step roughly doubles.
    assert avg[1] > 1.5 * avg[0]
    assert avg[2] > 1.5 * avg[1]
    assert avg[3] > 1.5 * avg[2]


# -------------------------------------------------------------------------
# Spawn / Mock-Subprocess
# -------------------------------------------------------------------------


def _make_mock_proc(alive: bool = True, pid: int = 1234) -> mock.MagicMock:
    """Mock Popen: poll() returns None when alive, 0 when finished."""
    proc = mock.MagicMock()
    proc.pid = pid
    proc.poll = mock.MagicMock(return_value=None if alive else 0)
    proc.wait = mock.MagicMock(return_value=0)
    proc.terminate = mock.MagicMock()
    proc.kill = mock.MagicMock()
    return proc


@pytest.mark.asyncio
async def test_spawn_calls_subprocess_with_python_and_overlay_args() -> None:
    captured = {}

    def spawn_fn(args: list[str], **kwargs: Any) -> mock.MagicMock:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _make_mock_proc()

    sup = OverlaySupervisor(ws_port=7842, spawn_fn=spawn_fn)
    try:
        await sup.start()
        # Right after start, the proc is there.
        assert sup.is_alive
        assert "args" in captured
        # Args: python sys.executable -m overlay --ws-port=7842
        assert captured["args"][1:] == ["-m", "overlay", "--ws-port=7842"]
    finally:
        await sup.stop()


@pytest.mark.asyncio
async def test_spawn_subprocess_env_unsets_jarvis_depth() -> None:
    """Plan: the overlay process itself is NOT a sub-agent."""
    captured = {}

    def spawn_fn(args: list[str], **kwargs: Any) -> mock.MagicMock:
        captured["env"] = kwargs.get("env")
        return _make_mock_proc()

    sup = OverlaySupervisor(spawn_fn=spawn_fn, env={"JARVIS_DEPTH": "2"})
    try:
        await sup.start()
        env = captured["env"]
        assert "JARVIS_DEPTH" not in env
    finally:
        await sup.stop()


@pytest.mark.asyncio
async def test_subprocess_creationflags_on_win32() -> None:
    """Plan: CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW on Windows."""
    import sys

    if sys.platform != "win32":
        pytest.skip("creationflags required only on Windows")

    captured = {}

    def spawn_fn(args: list[str], **kwargs: Any) -> mock.MagicMock:
        captured["creationflags"] = kwargs.get("creationflags")
        return _make_mock_proc()

    sup = OverlaySupervisor(spawn_fn=spawn_fn)
    try:
        await sup.start()
        # Should contain both bits.
        cf = captured["creationflags"]
        assert cf & 0x00000200  # CREATE_NEW_PROCESS_GROUP
        assert cf & 0x08000000  # CREATE_NO_WINDOW
    finally:
        await sup.stop()


# -------------------------------------------------------------------------
# Heartbeat-Timeout -> Respawn
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_timeout_triggers_respawn() -> None:
    """When no heartbeat for 3 s: kill + respawn with backoff."""
    spawn_count = [0]

    def spawn_fn(args: list[str], **kwargs: Any) -> mock.MagicMock:
        spawn_count[0] += 1
        return _make_mock_proc()

    sup = OverlaySupervisor(
        spawn_fn=spawn_fn,
        heartbeat_timeout_s=0.05,  # 50 ms for the test
        rng=random.Random(0),
    )
    try:
        await sup.start()
        assert spawn_count[0] == 1
        # Wait until the monitor loop sees the heartbeat timeout + respawns.
        # 50 ms timeout + 0.5s monitor tick + ~0.5s backoff = ~1.5 s.
        await asyncio.sleep(2.5)
        assert spawn_count[0] >= 2, f"expected >=2 spawns, got {spawn_count[0]}"
    finally:
        await sup.stop()


@pytest.mark.asyncio
async def test_notify_heartbeat_prevents_respawn() -> None:
    """When heartbeats keep coming steadily, no respawn."""
    spawn_count = [0]

    def spawn_fn(args: list[str], **kwargs: Any) -> mock.MagicMock:
        spawn_count[0] += 1
        return _make_mock_proc()

    sup = OverlaySupervisor(
        spawn_fn=spawn_fn,
        heartbeat_timeout_s=0.1,
        rng=random.Random(0),
    )
    try:
        await sup.start()
        # 6x heartbeat over 0.6 s.
        for _ in range(6):
            sup.notify_heartbeat()
            await asyncio.sleep(0.05)
        # Should still be exactly 1 spawn.
        assert spawn_count[0] == 1
    finally:
        await sup.stop()


# -------------------------------------------------------------------------
# Cap (5 in 5 min)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_fired_after_too_many_restarts() -> None:
    """Plan AD-10: cap after 5 restarts in the default window. We use
    cap_count=2 + a tiny backoff for test speed; the logic is the same."""
    cap_called = [0]

    spawn_count = [0]

    def spawn_fn(args: list[str], **kwargs: Any) -> mock.MagicMock:
        spawn_count[0] += 1
        return _make_mock_proc()

    # With cap_count=2, the cap fires on the 3rd spawn attempt.
    sup = OverlaySupervisor(
        spawn_fn=spawn_fn,
        heartbeat_timeout_s=0.02,
        restart_cap_count=2,
        restart_cap_window_s=30.0,
        cap_fired_callback=lambda: cap_called.__setitem__(0, cap_called[0] + 1),
        # Deterministic small backoff — we want to reach the cap quickly.
        rng=random.Random(0),
    )
    # Backoff override: significantly shorter for test performance.
    sup._stable_reset = 9999.0  # noqa: SLF001 — no stable resets in the test
    try:
        await sup.start()
        # 4 attempts: initial + 3 restarts. Backoff 0.5+1+2 = ~3.5 s, plus
        # monitor-tick latency. We give it 6 s.
        await asyncio.sleep(6.0)
        assert sup.cap_active is True, f"cap not fired (spawns={spawn_count[0]})"
        assert cap_called[0] == 1
    finally:
        await sup.stop()


@pytest.mark.asyncio
async def test_manual_reset_clears_cap_state() -> None:
    sup = OverlaySupervisor(spawn_fn=lambda *a, **k: _make_mock_proc())
    # Set the cap directly without the spawn loop.
    sup._cap_active = True  # noqa: SLF001
    sup._failures = 99  # noqa: SLF001
    sup.manual_reset()
    assert sup.cap_active is False
    assert sup.failure_count == 0


# -------------------------------------------------------------------------
# Lifecycle
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_is_idempotent() -> None:
    spawn_count = [0]

    def spawn_fn(args: list[str], **kwargs: Any) -> mock.MagicMock:
        spawn_count[0] += 1
        return _make_mock_proc()

    sup = OverlaySupervisor(spawn_fn=spawn_fn)
    try:
        await sup.start()
        await sup.start()
        await sup.start()
        assert spawn_count[0] == 1
    finally:
        await sup.stop()


@pytest.mark.asyncio
async def test_stop_terminates_subprocess() -> None:
    proc = _make_mock_proc()

    sup = OverlaySupervisor(spawn_fn=lambda *a, **k: proc)
    await sup.start()
    await sup.stop()
    proc.terminate.assert_called()
