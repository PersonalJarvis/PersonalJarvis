"""``OverlaySupervisor`` — subprocess lifecycle. Plan §4.3 + AD-9 + AD-10.

Spawns the overlay subprocess under a Win32 job object with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. The job handle stays in the
main-Jarvis process; on a main-Jarvis crash, Windows closes the
handle and kills the overlay as a job member within 1 s
(the Raymond Chen pattern).

Lifecycle::

    sup = OverlaySupervisor()
    await sup.start()
    sup.notify_heartbeat()           # called by the IPC listener
    ...
    await sup.stop()

Restart backoff (AD-10)::

    delay = min(30, 0.5 * 2**failures) * jitter(0.8, 1.2)

Cap: 5 restarts within a 5-minute window. When the cap fires,
``cap_fired_callback`` is invoked (tray notification, disable switch).
Stable reset: after ``stable_reset_s`` (60 s) of uptime without a crash,
the failure counter is reset.

On non-Windows: the subprocess is spawned, but no job object is
wired up; auto-kill there only works via the explicit shutdown
in the stop() path. Phase-9.8 tests focus on Windows.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import subprocess
import sys
import time
from collections import deque
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# AD-10 Defaults.
DEFAULT_HEARTBEAT_TIMEOUT_S: float = 3.0
DEFAULT_RESTART_CAP_COUNT: int = 5
DEFAULT_RESTART_CAP_WINDOW_S: float = 300.0  # 5 minutes
DEFAULT_STABLE_RESET_S: float = 60.0

# Plan AD-9: KILL_ON_JOB_CLOSE constant (Win32 winnt.h).
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: int = 0x2000

# Plan requirement: subprocess args.
DEFAULT_OVERLAY_ENTRY: tuple[str, ...] = ("-m", "overlay")


CapFiredCallback = Callable[[], None]


def _backoff_delay(failures: int, *, rng: random.Random) -> float:
    """AD-10 formula: ``min(30, 0.5 * 2**failures) * jitter(0.8, 1.2)``."""
    base = min(30.0, 0.5 * (2 ** max(0, failures)))
    jitter = rng.uniform(0.8, 1.2)
    return max(0.05, base * jitter)


class OverlaySupervisor:
    """Process manager for the overlay subprocess."""

    def __init__(
        self,
        *,
        ws_port: int = 7842,
        python_executable: Optional[str] = None,
        entry_args: tuple[str, ...] = DEFAULT_OVERLAY_ENTRY,
        env: Optional[dict[str, str]] = None,
        heartbeat_timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
        restart_cap_count: int = DEFAULT_RESTART_CAP_COUNT,
        restart_cap_window_s: float = DEFAULT_RESTART_CAP_WINDOW_S,
        stable_reset_s: float = DEFAULT_STABLE_RESET_S,
        cap_fired_callback: Optional[CapFiredCallback] = None,
        # Test hooks (production defaults are the real APIs):
        spawn_fn: Optional[Callable[..., subprocess.Popen]] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self._ws_port = ws_port
        self._python = python_executable or sys.executable
        self._entry_args = tuple(entry_args)
        self._env = env
        self._heartbeat_timeout = heartbeat_timeout_s
        self._cap_count = restart_cap_count
        self._cap_window = restart_cap_window_s
        self._stable_reset = stable_reset_s
        self._cap_fired_callback = cap_fired_callback
        self._spawn_fn = spawn_fn or self._default_spawn
        self._rng = rng or random.Random()

        # Lifecycle-State (under _lock).
        self._lock = asyncio.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._job_handle: Any = None  # PyHANDLE auf Win32, None sonst
        self._failures: int = 0
        self._cap_active: bool = False
        self._spawn_attempts: deque[float] = deque()  # monotonic ts der letzten spawns
        self._last_spawn_ts: float = 0.0
        self._last_heartbeat_ts: float = 0.0
        self._stop_requested: bool = False
        self._monitor_task: Optional[asyncio.Task[Any]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def cap_active(self) -> bool:
        """True when the 5-in-5-min cap has fired — auto-restart is paused."""
        return self._cap_active

    @property
    def failure_count(self) -> int:
        return self._failures

    def notify_heartbeat(self) -> None:
        """Sync API. Called by the WS listener when the overlay checks in."""
        self._last_heartbeat_ts = time.monotonic()

    async def start(self) -> None:
        """Idempotent. Spawnt Subprocess + startet Monitor-Task."""
        async with self._lock:
            if self.is_alive or self._monitor_task is not None:
                return
            self._stop_requested = False
            self._failures = 0
            self._cap_active = False
            await self._spawn_locked()

        # Monitor-Task ausserhalb des Locks starten — er nimmt selbst
        # den Lock.
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="overlay-supervisor-monitor"
        )

    async def stop(self) -> None:
        """Stops the monitor + subprocess. Idempotent."""
        self._stop_requested = True
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._monitor_task = None

        async with self._lock:
            await self._terminate_locked()

    def manual_reset(self) -> None:
        """User klickt Tray -> 'Overlay neu aktivieren'. Cap-State weg."""
        self._cap_active = False
        self._failures = 0
        self._spawn_attempts.clear()

    async def force_respawn(self) -> None:
        """Voice-driven recovery: clear cap-state and force a fresh spawn.

        Idempotent. Mirrors ``manual_reset`` but also actively terminates
        a (possibly hidden / hung) subprocess and re-spawns it in the
        same critical section. Used by the ``respawn_mascot`` local-action
        tool so the user can say "Hey Jarvis, Maskottchen wieder
        auftauchen" and get the overlay back even when the subprocess is
        still alive but invisible. Sub-agent processes (start_overlay
        never called) should not reach this — the tool guards on the
        singleton.
        """
        async with self._lock:
            self._cap_active = False
            self._failures = 0
            self._spawn_attempts.clear()
            await self._terminate_locked()
            await self._spawn_locked()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _default_spawn(self, args: list[str], **popen_kwargs: Any) -> subprocess.Popen:
        return subprocess.Popen(args, **popen_kwargs)

    def _build_args(self) -> list[str]:
        base = [self._python, *self._entry_args]
        base.append(f"--ws-port={self._ws_port}")
        return base

    def _build_creationflags(self) -> int:
        """Plan requirement: CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW."""
        if sys.platform != "win32":
            return 0
        # subprocess constants (also via creationflags bits from winbase.h).
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        return CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

    def _build_env(self) -> dict[str, str]:
        """Subprocess env: parent inherit + JARVIS_DEPTH unset/0 so the
        overlay is not recognized as a sub-agent."""
        env = dict(os.environ)
        if self._env is not None:
            env.update(self._env)
        # The overlay process itself is NOT a sub-agent.
        env.pop("JARVIS_DEPTH", None)
        return env

    async def _spawn_locked(self) -> None:
        """Spawns the subprocess + binds it to the job object. The lock must be held."""
        if self._cap_active:
            logger.warning("Supervisor: cap active, no auto-spawn")
            return

        now = time.monotonic()
        # Cap check (rolling 5-min window).
        self._spawn_attempts.append(now)
        cutoff = now - self._cap_window
        while self._spawn_attempts and self._spawn_attempts[0] < cutoff:
            self._spawn_attempts.popleft()
        if len(self._spawn_attempts) > self._cap_count:
            logger.error(
                "Supervisor: cap fired (%d restarts in %.0f s) - auto-restart off",
                len(self._spawn_attempts),
                self._cap_window,
            )
            self._cap_active = True
            if self._cap_fired_callback is not None:
                try:
                    self._cap_fired_callback()
                except Exception:  # noqa: BLE001
                    logger.exception("cap_fired_callback raised")
            return

        args = self._build_args()
        creationflags = self._build_creationflags()
        env = self._build_env()

        # Stderr to a file instead of DEVNULL: otherwise subprocess crashes
        # are invisible (no log). Append mode, so all restarts land in the
        # same file — the user/agent can do forensics after a crash.
        # The data/ directory is in .gitignore.
        from pathlib import Path
        log_dir = Path("data")
        log_dir.mkdir(parents=True, exist_ok=True)
        stderr_path = log_dir / "overlay-stderr.log"
        try:
            stderr_handle = open(stderr_path, "ab", buffering=0)  # noqa: SIM115
        except OSError as exc:
            logger.warning(
                "Supervisor: stderr-log open failed (%s), falling back to DEVNULL",
                exc,
            )
            stderr_handle = subprocess.DEVNULL

        try:
            proc = self._spawn_fn(
                args,
                creationflags=creationflags,
                env=env,
                close_fds=False,  # the job handle must be inheritable — must NOT be closed
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Supervisor spawn failed")
            self._failures += 1
            return

        self._proc = proc
        self._last_spawn_ts = now
        self._last_heartbeat_ts = now  # Initial-Grace: 1 Heartbeat-Period

        # Plan AD-9: job object via pywin32. The test path passes a
        # spawn_fn that can skip the hooking (proc is then a
        # MagicMock).
        if sys.platform == "win32" and not _is_mock_proc(proc):
            try:
                self._assign_to_job(proc.pid)
            except Exception:  # noqa: BLE001
                logger.exception("Job-Object assign failed (continuing)")

        logger.info(
            "Supervisor: spawned PID=%s args=%s",
            getattr(proc, "pid", "?"),
            args,
        )

    def _assign_to_job(self, pid: int) -> None:
        """Plan AD-9: CreateJobObject + SetInformationJobObject +
        AssignProcessToJobObject."""
        # Lazy-import pywin32 so non-Windows hosts don't crash.
        import win32api
        import win32con
        import win32job

        if self._job_handle is None:
            # Job-Handle ist non-inheritable (Plan AD-9).
            sa = None  # NULL SecurityAttributes -> default inheritability=False
            self._job_handle = win32job.CreateJobObject(sa, "")
            # JOBOBJECT_EXTENDED_LIMIT_INFORMATION.BasicLimitInformation
            # .LimitFlags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
            info = win32job.QueryInformationJobObject(
                self._job_handle, win32job.JobObjectExtendedLimitInformation
            )
            info["BasicLimitInformation"]["LimitFlags"] |= (
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                self._job_handle,
                win32job.JobObjectExtendedLimitInformation,
                info,
            )

        # Process-Handle holen + assign.
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        proc_handle = win32api.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid
        )
        try:
            win32job.AssignProcessToJobObject(self._job_handle, proc_handle)
        finally:
            win32api.CloseHandle(proc_handle)

    async def _terminate_locked(self) -> None:
        """Kills the subprocess + closes the job handle. The lock must be held."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                # Plan §4.3: shutdown should take effect within 1 s.
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(proc.wait), timeout=1.5
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(proc.wait), timeout=1.0
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Supervisor: proc PID=%s not killable", proc.pid
                        )
            except Exception:  # noqa: BLE001
                logger.exception("Subprocess terminate failed")

        self._proc = None

        # Close the job handle — KILL_ON_JOB_CLOSE automatically kills
        # all job members (including children of the overlay).
        if self._job_handle is not None:
            try:
                import win32api

                win32api.CloseHandle(self._job_handle)
            except Exception:  # noqa: BLE001
                logger.debug("Job-Handle close failed", exc_info=True)
            self._job_handle = None

    async def _monitor_loop(self) -> None:
        """Heartbeat-Watcher + Auto-Restart-Driver."""
        try:
            while not self._stop_requested:
                await asyncio.sleep(0.5)
                if self._stop_requested:
                    return

                async with self._lock:
                    proc = self._proc
                    cap_active = self._cap_active

                if cap_active:
                    continue

                now = time.monotonic()

                # Stable reset (AD-10): if proc is alive AND
                # last_spawn > stable_reset_s, then failures=0.
                if (
                    proc is not None
                    and proc.poll() is None
                    and (now - self._last_spawn_ts) >= self._stable_reset
                    and self._failures > 0
                ):
                    logger.debug("Supervisor: stable uptime -> reset failures")
                    self._failures = 0

                # Heartbeat timeout: if proc is alive but no heartbeat
                # for 3 s -> kill + respawn.
                if proc is not None and proc.poll() is None:
                    last_hb = self._last_heartbeat_ts or now
                    if (now - last_hb) > self._heartbeat_timeout:
                        logger.warning(
                            "Supervisor: heartbeat-timeout (%.1fs) -> kill+respawn",
                            now - last_hb,
                        )
                        async with self._lock:
                            await self._terminate_locked()
                        await self._restart_with_backoff()
                        continue

                # Process died / never started -> respawn.
                if proc is None or proc.poll() is not None:
                    await self._restart_with_backoff()

        except asyncio.CancelledError:
            return

    async def _restart_with_backoff(self) -> None:
        """AD-10 Backoff-Delay + spawn."""
        if self._stop_requested:
            return
        async with self._lock:
            if self._cap_active:
                return
            self._failures += 1
            delay = _backoff_delay(self._failures - 1, rng=self._rng)
        logger.info(
            "Supervisor: restart in %.2f s (failures=%d)", delay, self._failures
        )
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self._stop_requested:
            return
        async with self._lock:
            await self._spawn_locked()


def _is_mock_proc(proc: Any) -> bool:
    """Test helper: a MagicMock has no real pid. We detect this
    by the type name so we can skip the job hooking without
    needing pywin32 in tests."""
    return type(proc).__module__.startswith("unittest.mock")


__all__ = [
    "DEFAULT_HEARTBEAT_TIMEOUT_S",
    "DEFAULT_OVERLAY_ENTRY",
    "DEFAULT_RESTART_CAP_COUNT",
    "DEFAULT_RESTART_CAP_WINDOW_S",
    "DEFAULT_STABLE_RESET_S",
    "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
    "OverlaySupervisor",
    "_backoff_delay",
]
