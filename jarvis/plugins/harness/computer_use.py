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
from jarvis.harness.computer_use_context import (
    ComputerUseContext,
    cu_recently_cancelled,
    get_computer_use_context,
    register_active_cu_token,
    unregister_active_cu_token,
)

_log = logging.getLogger(__name__)

_TIMEOUT_EXIT_CODE = 124
_CANCEL_EXIT_CODE = 130


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
            # THIS mission without touching OpenClaw (BUG-CU-HANGUP). The
            # registry is a SET — concurrent CU missions each register so a
            # single hangup cancels them ALL (BUG-CU-CONCURRENT-CANCEL).
            register_active_cu_token(token)
            # Control-indicator contract (jarvis.cu.indicator): Started fires
            # once the token is registered — i.e. the moment this mission may
            # actually drive mouse/keyboard — and Ended ALWAYS fires in the
            # finally below, on every exit path.
            mission_id = uuid.uuid4().hex[:12]
            await _publish_cu_control(
                ctx.bus, CUControlStarted(mission_id=mission_id)
            )
            end_reason = "finished"
            run_cu_loop = _resolve_run_cu_loop()
            stream = run_cu_loop(task, ctx, cancel_token=token)
            try:
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
                        if chunk.exit_code == _CANCEL_EXIT_CODE:
                            end_reason = "cancelled"
                        elif chunk.exit_code != 0:
                            end_reason = "error"
                        return
            except TimeoutError:
                end_reason = "timeout"
                token.cancel("computer_use_harness_timeout")
                duration_ms = (time.time_ns() - t_start) // 1_000_000
                yield HarnessResult(
                    stderr=f"[cu] timeout after {timeout_s:.3g}s\n",
                    exit_code=_TIMEOUT_EXIT_CODE,
                    duration_ms=duration_ms,
                    is_final=True,
                )
            finally:
                await stream.aclose()
                self._active_token = None
                # Remove only THIS mission's token — a concurrently-running
                # sibling stays registered and cancelable (BUG-CU-CONCURRENT-
                # CANCEL: clearing the whole registry here orphaned the sibling
                # so a later hangup found no token at all).
                unregister_active_cu_token(token)
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
