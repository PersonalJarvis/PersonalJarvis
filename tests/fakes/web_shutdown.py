"""Shutdown dependents that require their shared Society store to stay open."""

from __future__ import annotations

import asyncio


class SocietyStoreDependent:
    def __init__(self, store, events: list[str], name: str) -> None:
        self.store = store
        self.events = events
        self.name = name

    async def _finish(self) -> None:
        await self.store.set_meta("shutdown:" + self.name, "drained")
        self.events.append(self.name)

    async def stop(self) -> None:
        await self._finish()

    async def cancel_all(self) -> None:
        await self._finish()

    async def shutdown(self) -> None:
        await self._finish()


class DrainingSocietyHttpServer(SocietyStoreDependent):
    started = True

    def __init__(self, store, events: list[str], name: str) -> None:
        super().__init__(store, events, name)
        self._exit = asyncio.Event()

    @property
    def should_exit(self) -> bool:
        return self._exit.is_set()

    @should_exit.setter
    def should_exit(self, value: bool) -> None:
        if value:
            self._exit.set()

    async def serve(self) -> None:
        await self._exit.wait()
        await self._finish()


class IdleChatRunner:
    """A real chat service can run this until its normal cancellation token fires."""

    def __init__(self) -> None:
        self.starts: list[str] = []
        self.started = asyncio.Event()

    async def __call__(self, handle, prompt, **kwargs) -> None:
        self.starts.append(prompt)
        self.started.set()
        await handle.cancel.wait()


class DeliveryAfterChatDrain:
    """Force the scheduler race in the later task-shutdown phase, without a timer."""

    def __init__(self, society, service, session_id, message_id) -> None:
        self.society = society
        self.service = service
        self.session_id = session_id
        self.message_id = message_id
        self.observed: tuple[bool, str] | None = None

    async def shutdown(self) -> None:
        await self.society.scheduler.drain_deliveries()
        self.observed = (
            self.service.is_running(self.session_id),
            await self.society.store.delivery_status(self.message_id),
        )
        await self.society.store.set_meta("shutdown-writer", "finished after chat drain")


class GatedDelivery:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.completed: list[str] = []

    async def __call__(self, target, envelope) -> None:
        self.entered.set()
        await self.release.wait()
        self.completed.append(envelope.event_id)


class ResistantDelivery(GatedDelivery):
    async def __call__(self, target, envelope) -> None:
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()
        self.completed.append(envelope.event_id)


class GatedMetadataWriter:
    def __init__(self, write) -> None:
        self.write = write
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, key, value) -> None:
        await self.write(key, value)
        if key.startswith("coding_supervision:") and not self.entered.is_set():
            self.entered.set()
            await self.release.wait()


class RecordingCodingGateway:
    def __init__(self) -> None:
        self.quiesced = False
        self.sends_after_quiesce: list[bool] = []

    async def run(self, args):
        self.sends_after_quiesce.append(self.quiesced)
        return {"submitted": True}
