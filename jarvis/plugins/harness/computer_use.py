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
import time
from collections.abc import AsyncIterator

from jarvis.control import CancelScope
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


def _resolve_run_cu_loop():
    """Select the Computer-Use engine per ``[computer_use].engine`` (reversible).

    ``"current"`` (default) -> the maintained engine; ``"june13"`` -> the frozen
    2026-06-10 / 352a784f snapshot kept as a known-good fallback. Read PER
    MISSION so a config flip applies on the next mission (no restart needed).
    Logs the live engine — INFO for ``june13`` (the unusual state) so it is never
    ambiguous which version is running. Never raises: a config-read problem falls
    back to the maintained engine.
    """
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415

        cu = getattr(load_config(), "computer_use", None)
        engine = str(getattr(cu, "engine", "current") or "current")
    except Exception:  # noqa: BLE001 — a config read must never break a mission
        engine = "current"
    if engine == "june13":
        from jarvis.harness.screenshot_only_loop_june13 import (  # noqa: PLC0415
            run_cu_loop as _loop,
        )
        _log.info(
            "[cu] ENGINE = june13 (frozen 2026-06-10 / 352a784f). "
            "Revert with [computer_use].engine = current.",
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
            "Revert with [computer_use].engine = current.",
        )
        return _loop
    from jarvis.harness.screenshot_only_loop import (  # noqa: PLC0415
        run_cu_loop as _loop,
    )
    _log.debug("[cu] ENGINE = current")
    return _loop


class ComputerUseHarness:
    """Plugin-Eintrag fuer `entry_points."jarvis.harness".screenshot`.

    Die App muss vor dem ersten Dispatch einmal
    `jarvis.harness.computer_use_context.set_computer_use_context(...)`
    aufrufen. Ohne Kontext wirft `invoke()` einen klaren Fehler.
    """

    name: str = "screenshot"
    version: str = "0.1.0"
    supports_versions: str = ">=0.1"

    def __init__(self, context: ComputerUseContext | None = None) -> None:
        # Der Constructor kann mit explicit Context aufgerufen werden (fuer
        # Tests). Ohne Context faellt `invoke()` auf den globalen zurueck.
        self._explicit_context = context
        self._cancelled = False
        self._active_token = None           # gesetzt waehrend invoke() laeuft

    async def health(self) -> bool:
        """True wenn ein Context gesetzt ist und alle Kern-Deps existieren."""
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
        """Delegiert an `run_cu_loop` innerhalb eines eigenen CancelScope.

        Der Scope registriert einen dedizierten Token beim KillSwitch. Der
        KillSwitch cancelt IHN bei `KillRequested`, und der Loop propagiert
        das bis zum naechsten `is_cancelled()`-Check. Ohne diesen Scope
        wuerde der Kill-Switch den CU-Loop nicht erreichen (ADR-0004).
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
            run_cu_loop = _resolve_run_cu_loop()
            stream = run_cu_loop(task, ctx, cancel_token=token)
            try:
                while True:
                    remaining_s = deadline - time.monotonic()
                    if remaining_s <= 0:
                        raise asyncio.TimeoutError
                    try:
                        chunk = await asyncio.wait_for(
                            anext(stream),
                            timeout=remaining_s,
                        )
                    except StopAsyncIteration:
                        return
                    yield chunk
                    if chunk.is_final:
                        return
            except asyncio.TimeoutError:
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

    async def cancel(self) -> None:
        """Bricht den laufenden Invoke ab.

        Cancelt den aktiven Token — der Loop reagiert beim naechsten
        `_is_cancelled()`-Check und yieldet einen finalen Chunk. Wenn
        `invoke()` nicht laeuft, ist der Call ein No-Op.
        """
        if self._active_token is not None:
            self._active_token.cancel("harness_direct_cancel")
