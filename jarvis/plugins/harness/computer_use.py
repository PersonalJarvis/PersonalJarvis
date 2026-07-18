"""Computer-Use harness — in-process Plan-Observe-Act-Verify loop (ADR-0008).

This harness is the exception to the subprocess pattern used by other
harnesses: it runs in the main process because it requires direct access to
`VisionEngine`, `BrainManager`, `ToolExecutor`, `CancelToken`, and
`CostMeter`.

The actual loop lives in `jarvis.harness.computer_use_loop.run_cu_loop`.
This module only provides the protocol binding (health/invoke/cancel).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from collections.abc import AsyncIterator

from jarvis.control import CancelScope
from jarvis.core.events import CUControlEnded, CUControlStarted
from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.harness import cu_run_registry
from jarvis.harness.computer_use_context import (
    ComputerUseContext,
    cu_recently_cancelled,
    get_computer_use_context,
    register_active_cu_token,
    unregister_active_cu_token,
)

# env keys the run-control REST surface uses to thread identity/provenance
# through the (frozen) HarnessTask without a protocol change (H-09).
CU_MISSION_ID_ENV_KEY = "JARVIS_CU_MISSION_ID"
CU_SOURCE_ENV_KEY = "JARVIS_CU_SOURCE"

_log = logging.getLogger(__name__)

_TIMEOUT_EXIT_CODE = 124
_CANCEL_EXIT_CODE = 130

# Global desktop actuation lock (deep-dive 2026-07-15, H-10). There is ONE
# physical mouse/keyboard/foreground focus: two missions with DIFFERENT goals
# used to run concurrently (the tool only dedupes IDENTICAL goals) and raced
# each other's pointer moves and foreground guards. Every launch route (voice,
# LLM tool, scheduled task) funnels through this harness, so serializing here
# covers them all. A queued mission waits honoring its own deadline and
# cancellation; the wait is logged so "nothing happens" is diagnosable.
_DESKTOP_LOCK = asyncio.Lock()


async def _publish_cu_control(bus, event) -> None:
    """Publish a control-indicator event; a publish failure must never
    break the mission (the indicator is strictly best-effort)."""
    if bus is None:
        return
    try:
        await bus.publish(event)
    except Exception:  # noqa: BLE001
        _log.debug("[cu] control event publish failed", exc_info=True)


def _resolve_run_cu_loop():
    """Resolve every live Computer-Use mission to the guarded v2 engine.

    ``"v2"`` is the only action-capable engine: it owns live permission,
    monitor-topology and foreground-window guards. Historical ``current``,
    ``june13`` and ``stable`` values remain readable for config compatibility
    but route to v2; their frozen loops can still be imported by forensic tests
    and must never dispatch live desktop input. Read per mission so config
    recovery applies without restart. Never raises: a config-read problem also
    falls back to v2.
    """
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415

        cu = getattr(load_config(), "computer_use", None)
        engine = str(getattr(cu, "engine", "v2") or "v2")
    except Exception:  # noqa: BLE001 — a config read must never break a mission
        engine = "v2"
    if engine != "v2":
        _log.warning(
            "[cu] ENGINE = %s is retired for live input because legacy loops "
            "lack current permission, topology and foreground-window action "
            "guards on %s; using the maintained v2 engine instead.",
            engine,
            sys.platform,
        )
        engine = "v2"
    if engine == "v2":
        from jarvis.cu.engine import run_cu_loop as _loop  # noqa: PLC0415

        _log.debug("[cu] ENGINE = v2 (rebuilt perceive->act->verify engine)")
        return _loop
    if engine == "june13":
        from jarvis.harness.screenshot_only_loop_june13 import (  # noqa: PLC0415
            run_cu_loop as _loop,
        )
        _log.info(
            "[cu] ENGINE = june13 (frozen 2026-06-10 / 352a784f). "
            "Revert with [computer_use].engine = v2.",
        )
        return _loop
    if engine == "stable":
        # Frozen pre-Wave-1 snapshot — the known-good fallback the user flips to if
        # the new verification work misbehaves. See cu-restore-points/ + the
        # screenshot_only_loop_stable.py header.
        from jarvis.harness.screenshot_only_loop_stable import (  # noqa: PLC0415
            run_cu_loop as _loop,
        )
        _log.info(
            "[cu] ENGINE = stable (frozen pre-Wave-1 snapshot). "
            "Revert with [computer_use].engine = v2.",
        )
        return _loop
    from jarvis.harness.screenshot_only_loop import (  # noqa: PLC0415
        run_cu_loop as _loop,
    )
    _log.info(
        "[cu] ENGINE = current (legacy maintained loop). "
        "The default engine is v2 ([computer_use].engine = v2).",
    )
    return _loop


class ComputerUseHarness:
    """Plugin entry for `entry_points."jarvis.harness".screenshot`.

    The app must call
    `jarvis.harness.computer_use_context.set_computer_use_context(...)`
    once before the first dispatch. Without a context, `invoke()` raises a
    clear error.
    """

    name: str = "screenshot"
    version: str = "0.1.0"
    supports_versions: str = ">=0.1"

    def __init__(self, context: ComputerUseContext | None = None) -> None:
        # The constructor can be called with an explicit context (for
        # tests). Without a context, `invoke()` falls back to the global one.
        self._explicit_context = context
        self._cancelled = False
        self._active_token = None           # set while invoke() is running

    async def health(self) -> bool:
        """True when a context is set and all core deps exist."""
        try:
            ctx = self._explicit_context or get_computer_use_context()
        except RuntimeError:
            return False
        return all([
            ctx.vision_engine is not None,
            ctx.brain_manager is not None,
            ctx.tool_executor is not None,
        ])

    async def invoke(self, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        """Delegates to `run_cu_loop` inside its own CancelScope.

        The scope registers a dedicated token with the KillSwitch. The
        KillSwitch cancels IT on `KillRequested`, and the loop propagates
        that until the next `is_cancelled()` check. Without this scope, the
        kill switch would never reach the CU loop (ADR-0004).
        """
        ctx = self._explicit_context or get_computer_use_context()
        timeout_s = max(0.001, float(task.timeout_s))
        deadline = time.monotonic() + timeout_s
        t_start = time.time_ns()
        # BUG-CU-HANGUP-RACE (2026-05-28): if the user said "auflegen" in the
        # window between this mission being requested and it actually starting,
        # abort silently NOW -- do not click or speak after a hangup. Returns
        # success with empty output so the dispatcher stays silent (no spoken
        # "harness failed" after the user already hung up).
        if cu_recently_cancelled():
            import logging as _logging
            _logging.getLogger(__name__).info(
                "[cu] mission suppressed — a voice hangup fired just before it "
                "started; not running.",
            )
            yield HarnessResult(stdout="", exit_code=0, is_final=True)
            return
        async with CancelScope(ctx.kill_switch, holder="cu_loop") as token:
            self._active_token = token
            # Register CU-scoped so the voice hangup ("auflegen") can cancel
            # THIS mission without touching Jarvis-Agent (BUG-CU-HANGUP). The
            # registry is a SET — concurrent CU missions each register so a
            # single hangup cancels them ALL (BUG-CU-CONCURRENT-CANCEL).
            register_active_cu_token(token)
            # Run-control registry (H-09): the REST surface may pre-assign the
            # mission id via task.env so it can hand it back to the caller;
            # every other launch route gets a fresh one. Registered as
            # "queued" here, "running" once the desktop lock is held, and
            # terminal in the finally — so `jarvis api` status/cancel sees
            # every mission regardless of how it was started.
            task_env = task.env or {}
            mission_id = (
                str(task_env.get(CU_MISSION_ID_ENV_KEY, "") or "")
                or uuid.uuid4().hex[:12]
            )
            cu_run_registry.register_run(
                mission_id,
                task.prompt,
                token,
                source=str(task_env.get(CU_SOURCE_ENV_KEY, "") or "app"),
            )
            end_reason = "finished"
            final_exit_code: int | None = None
            final_stdout = ""
            control_started = False
            lock_acquired = False
            stream = None
            try:
                # H-10: ONE mission drives the desktop at a time. Waiting in
                # short slices keeps the queue responsive to the mission's own
                # deadline and to cancellation (hangup / Emergency Stop).
                if _DESKTOP_LOCK.locked():
                    _log.info(
                        "[cu] desktop busy — mission queued behind the "
                        "active one",
                    )
                while not lock_acquired:
                    if token.is_cancelled():
                        end_reason = "cancelled"
                        final_exit_code = _CANCEL_EXIT_CODE
                        yield HarnessResult(
                            stderr="[cu] cancelled while waiting for the desktop\n",
                            exit_code=_CANCEL_EXIT_CODE,
                            is_final=True,
                        )
                        return
                    remaining_s = deadline - time.monotonic()
                    if remaining_s <= 0:
                        end_reason = "timeout"
                        final_exit_code = _TIMEOUT_EXIT_CODE
                        yield HarnessResult(
                            stderr=(
                                f"[cu] timeout after {timeout_s:.3g}s waiting "
                                "for the desktop (another mission was active)\n"
                            ),
                            exit_code=_TIMEOUT_EXIT_CODE,
                            is_final=True,
                        )
                        return
                    try:
                        await asyncio.wait_for(
                            _DESKTOP_LOCK.acquire(),
                            timeout=min(0.25, remaining_s),
                        )
                        lock_acquired = True
                    except TimeoutError:
                        continue
                # Control-indicator contract (jarvis.cu.indicator): Started
                # fires the moment this mission may actually drive
                # mouse/keyboard (token registered AND desktop lock held);
                # Ended ALWAYS fires in the finally below, on every exit path.
                await _publish_cu_control(
                    ctx.bus, CUControlStarted(mission_id=mission_id)
                )
                control_started = True
                cu_run_registry.mark_running(mission_id)
                run_cu_loop = _resolve_run_cu_loop()
                stream = run_cu_loop(task, ctx, cancel_token=token)
                while True:
                    remaining_s = deadline - time.monotonic()
                    if remaining_s <= 0:
                        raise TimeoutError
                    try:
                        chunk = await asyncio.wait_for(
                            anext(stream),
                            timeout=remaining_s,
                        )
                    except StopAsyncIteration:
                        return
                    yield chunk
                    if chunk.is_final:
                        final_exit_code = chunk.exit_code
                        final_stdout = chunk.stdout or ""
                        if chunk.exit_code == _CANCEL_EXIT_CODE:
                            end_reason = "cancelled"
                        elif chunk.exit_code != 0:
                            end_reason = "error"
                        return
            except TimeoutError:
                end_reason = "timeout"
                final_exit_code = _TIMEOUT_EXIT_CODE
                token.cancel("computer_use_harness_timeout")
                duration_ms = (time.time_ns() - t_start) // 1_000_000
                yield HarnessResult(
                    stderr=f"[cu] timeout after {timeout_s:.3g}s\n",
                    exit_code=_TIMEOUT_EXIT_CODE,
                    duration_ms=duration_ms,
                    is_final=True,
                )
            finally:
                cu_run_registry.finish_run(
                    mission_id,
                    end_reason,
                    exit_code=final_exit_code,
                    result_text=final_stdout,
                )
                if stream is not None:
                    await stream.aclose()
                if lock_acquired:
                    _DESKTOP_LOCK.release()
                self._active_token = None
                # Remove only THIS mission's token — a concurrently-running
                # sibling stays registered and cancelable (BUG-CU-CONCURRENT-
                # CANCEL: clearing the whole registry here orphaned the sibling
                # so a later hangup found no token at all).
                unregister_active_cu_token(token)
                if control_started:
                    await _publish_cu_control(
                        ctx.bus,
                        CUControlEnded(mission_id=mission_id, reason=end_reason),
                    )

    async def cancel(self) -> None:
        """Aborts the running invoke.

        Cancels the active token — the loop reacts on the next
        `_is_cancelled()` check and yields a final chunk. If `invoke()` isn't
        running, the call is a no-op.
        """
        if self._active_token is not None:
            self._active_token.cancel("harness_direct_cancel")
