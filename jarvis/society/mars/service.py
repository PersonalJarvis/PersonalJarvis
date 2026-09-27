"""Station resource coordination over an injected trusted executor, never a renderer.

The application owns this service and calls ``reconcile`` periodically while its
backend runs. This class does not run agents, choose providers, or install another
job scheduler. Only the existing execution adapter can accept or complete work.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .models import (
    CAPABILITY_ID,
    TERMINAL_STATES,
    CommandRecord,
    CommandState,
    DispatchRejected,
    ExecutionReceipt,
    StationCommand,
    StationError,
    StationEventBatch,
    StationSnapshot,
    reject_draft_credentials,
)
from .store import MarsStore

if TYPE_CHECKING:
    from jarvis.core.protocols import MarsStationExecutor

log = logging.getLogger(__name__)


class MarsStationService:
    def __init__(
        self,
        store: MarsStore,
        executor: MarsStationExecutor,
        *,
        operation_timeout_s: float = 10.0,
        cancellation_timeout_s: float = 20.0,
    ) -> None:
        if operation_timeout_s <= 0 or cancellation_timeout_s <= 0:
            raise ValueError("operation timeout must be positive")
        self.store = store
        self.executor = executor
        self._timeout = operation_timeout_s
        self._cancel_timeout = cancellation_timeout_s
        self._coordination = asyncio.Lock()
        self._lifecycle = asyncio.Lock()
        self._background: set[asyncio.Task[None]] = set()
        self._started = False

    async def start(self) -> None:
        async with self._lifecycle:
            if not self._started:
                await self.store.open()
                self._started = True

    async def close(self) -> None:
        """Release local resources. Backend stop/cancel policy remains application-owned."""
        async with self._lifecycle:
            self._started = False
            tasks = tuple(self._background)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._background.clear()
            async with self._coordination:
                await self.store.close()

    async def _authorize(self, agent_id: str) -> None:
        try:
            await asyncio.wait_for(
                self.executor.authorize(agent_id=agent_id, capability_id=CAPABILITY_ID),
                timeout=self._timeout,
            )
        except StationError:
            raise
        except Exception as exc:
            log.warning("Mars station authorization unavailable (%s)", type(exc).__name__)
            raise StationError("station_authority_unavailable", 503) from exc

    def _wake(self) -> None:
        # At most one queued wakeup; periodic reconciliation is owned by the app.
        if not self._started or self._background:
            return
        task = asyncio.create_task(self._reconcile_safely(), name="mars-station-reconcile")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _reconcile_safely(self) -> None:
        try:
            await self.reconcile()
        except Exception as exc:
            # No provider response or private draft is copied into logs or event labels.
            log.warning("Mars station reconciliation failed (%s)", type(exc).__name__)

    async def submit(self, agent_id: str, command: StationCommand) -> CommandRecord:
        reject_draft_credentials(command.draft)
        await self.start()
        await self._authorize(agent_id)
        record = await self.store.submit(agent_id, command)
        self._wake()
        return record

    async def cancel(self, agent_id: str, command_id: str) -> CommandRecord:
        await self.start()
        # The authenticated controller must be able to stop its own job after a
        # capability was revoked. Store ownership guards this path independently.
        record = await self.store.request_cancel(agent_id, command_id)
        self._wake()
        return record

    async def snapshot(self) -> StationSnapshot:
        await self.start()
        return await self.store.snapshot()

    async def events(self, after_seq: int, *, limit: int = 100) -> StationEventBatch:
        await self.start()
        return await self.store.events(after_seq, limit=limit)

    async def reconcile(self) -> None:
        """One bounded station step. No browser connection or animation is involved."""
        if not self._started:
            return
        async with self._coordination:
            if not self._started:
                return
            current = await self.store.current()
            if current is not None:
                if current.cancel_requested and not current.cancel_dispatched:
                    current = await self.store.begin_cancel(current.command_id, current.fence)
                    await self._execution_step(current, "cancel")
                else:
                    await self._execution_step(current, "inspect")
                return
            claimed = await self.store.claim_next()
            if claimed is None:
                return
            # A queued command's grants/agent state may have changed while waiting.
            try:
                await self._authorize(claimed.agent_id)
            except StationError as exc:
                await self.store.apply(
                    claimed.command_id,
                    claimed.fence,
                    ExecutionReceipt(state=CommandState.FAILED),
                    reason=exc.reason,
                )
                return
            await self._execution_step(claimed, "dispatch")

    async def _execution_step(self, record: CommandRecord, operation: str) -> None:
        args = {
            "agent_id": record.agent_id,
            "command_id": record.command_id,
            "trace_id": record.trace_id,
        }
        reason = ""
        try:
            if operation == "dispatch":
                call = self.executor.dispatch(**args, draft=record.draft)
            elif operation == "cancel":
                call = self.executor.cancel(**args, task_ref=record.task_ref)
            else:
                call = self.executor.inspect(**args, task_ref=record.task_ref)
            timeout = self._cancel_timeout if operation == "cancel" else self._timeout
            raw = await asyncio.wait_for(call, timeout=timeout)
            receipt = ExecutionReceipt.model_validate(raw, strict=False)
            if operation != "cancel" and receipt.cancel_attempt is not None:
                raise ValueError("cancellation attempt evidence belongs only to cancel receipts")
        except DispatchRejected as exc:
            if operation == "dispatch":
                receipt = ExecutionReceipt(state=CommandState.FAILED)
                reason = exc.reason
            else:
                receipt = ExecutionReceipt(state=CommandState.UNKNOWN)
                reason = "execution_outcome_unknown"
        except asyncio.CancelledError:
            await self.store.apply(
                record.command_id,
                record.fence,
                ExecutionReceipt(state=CommandState.UNKNOWN),
                reason="execution_outcome_unknown",
            )
            raise
        except Exception as exc:
            log.warning("Mars station %s outcome uncertain (%s)", operation, type(exc).__name__)
            receipt = ExecutionReceipt(state=CommandState.UNKNOWN)
            reason = "execution_outcome_unknown"
        if receipt.state in {CommandState.UNKNOWN, CommandState.INTERRUPTED} and not reason:
            reason = (
                "execution_outcome_unknown"
                if receipt.state is CommandState.UNKNOWN
                else "execution_interrupted"
            )
        if operation == "cancel" and receipt.cancel_attempt == "not_attempted":
            reason = "cancel_not_attempted" if receipt.state not in TERMINAL_STATES else ""
        if record.cancel_requested and receipt.state not in TERMINAL_STATES and not reason:
            reason = "cancel_pending"
        await self.store.apply(record.command_id, record.fence, receipt, reason=reason)
