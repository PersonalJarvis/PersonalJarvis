"""One lifecycle owner for configured listeners, sharing durable routine admission."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from contextlib import aclosing
from typing import Any

from jarvis.core.socket_budget import should_defer_optional_io

from . import source_credentials
from .source_catalog import available
from .source_drivers import DRIVERS
from .source_schema import LISTENER_KINDS

log = logging.getLogger(__name__)


class SourceSupervisor:
    def __init__(self, store: Any, admit: Any, *, drivers: dict | None = None):
        self.store, self.admit = store, admit
        self.drivers = DRIVERS if drivers is None else drivers
        self.injected = drivers is not None
        self.tasks: dict[str, asyncio.Task] = {}
        self._source_locks: dict[str, asyncio.Lock] = {}
        self._all_tasks: set[asyncio.Task] = set()
        self._connection_gate = asyncio.Lock()
        self._last_connect = 0.0

    def start(self, spec: Any) -> None:
        settings = spec.trigger.source
        if settings.kind not in LISTENER_KINDS:
            return
        task_id = str(spec.id)
        previous = self.tasks.get(task_id)
        if previous:
            previous.cancel()

        lock = self._source_locks.setdefault(task_id, asyncio.Lock())

        async def replace():
            async with lock:
                await self._run(task_id, settings)

        task = asyncio.create_task(replace(), name=f"routine-source-{task_id}")
        self.tasks[task_id] = task
        self._all_tasks.add(task)

        def finished(done):
            self._all_tasks.discard(done)
            if self.tasks.get(task_id) is done:
                self.tasks.pop(task_id, None)
                self._source_locks.pop(task_id, None)

        task.add_done_callback(finished)

    def cancel(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task:
            task.cancel()

    async def _connect_budget(self) -> None:
        async with self._connection_gate:
            while should_defer_optional_io():  # noqa: ASYNC110 - the shared budget exposes a polling probe
                await asyncio.sleep(random.SystemRandom().uniform(3, 6))
            delay = max(0, self._last_connect + 0.25 - time.monotonic())
            await asyncio.sleep(delay + random.SystemRandom().uniform(0, 0.2))
            self._last_connect = time.monotonic()

    async def _run(self, task_id: str, settings: Any) -> None:
        state = self.store.sources
        fingerprint = hashlib.sha256(settings.model_dump_json().encode()).hexdigest()[:20]
        backoff = 1.0
        try:
            if not self.injected and not available(settings.kind):
                await state.status(
                    task_id,
                    "dependency_missing",
                    "Install the trigger-streams extra in Source connection",
                )
                return
            while True:
                await state.status(task_id, "connecting")
                row = await self.store.get(task_id)
                if row is None:
                    return
                credentials = await asyncio.to_thread(source_credentials.read, row)
                saved = json.loads((await state.read(task_id))["cursor_json"])
                cursor = saved.get("data", {}) if saved.get("fingerprint") == fingerprint else {}
                try:
                    if settings.kind != "file":
                        await self._connect_budget()
                    async with aclosing(
                        self.drivers[settings.kind](
                            settings, credentials, "jarvis-" + task_id, cursor
                        )
                    ) as packets:
                        async for packet in packets:
                            if packet.ready:
                                if packet.checkpoint is not None:
                                    await state.checkpoint(
                                        task_id,
                                        {"fingerprint": fingerprint, "data": packet.checkpoint},
                                    )
                                await state.status(task_id, "listening")
                                continue
                            current = await self.store.get_spec(task_id)
                            if (
                                current is None
                                or current.trigger.type != "source"
                                or current.trigger.source != settings
                            ):
                                return
                            if packet.checkpoint and packet.checkpoint.get("baseline"):
                                await state.checkpoint(
                                    task_id, {"fingerprint": fingerprint, "data": packet.checkpoint}
                                )
                                await state.status(task_id, "listening")
                                continue
                            identifier = hashlib.sha256(
                                (fingerprint + ":" + packet.delivery_id).encode()
                            ).hexdigest()
                            while True:
                                status = await self.admit(
                                    task_id,
                                    json.loads(packet.body_json),
                                    identifier,
                                    source="source",
                                )
                                if status in {"queued", "duplicate", "filtered"}:
                                    break
                                if status in {"inactive", "exhausted", "not_found", "changed"}:
                                    return
                                if status == "id_conflict":
                                    raise ValueError(
                                        "A producer reused a message id with different data"
                                    )
                                await state.status(task_id, "backpressure", status)
                                await asyncio.sleep(random.SystemRandom().uniform(2, 4))
                            if packet.checkpoint is not None:
                                await state.checkpoint(
                                    task_id, {"fingerprint": fingerprint, "data": packet.checkpoint}
                                )
                            await packet.acknowledge()
                            backoff = 1.0
                            await state.status(task_id, "listening")
                    raise ConnectionError("Source connection closed")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Driver exceptions can include passwords and connection URLs.
                    # Keep their type, never their raw text, in logs and UI state.
                    detail = f"Check connection settings and credentials ({type(exc).__name__})"
                    log.warning("Routine source %s disconnected (%s)", task_id, type(exc).__name__)
                    await state.status(task_id, "retrying", detail)
                    await asyncio.sleep(random.SystemRandom().uniform(backoff, backoff * 1.5))
                    backoff = min(60, backoff * 2)
        except asyncio.CancelledError:
            await state.status(task_id, "stopped")
            raise
        except Exception as exc:
            log.warning("Routine source %s unavailable (%s)", task_id, type(exc).__name__)
            await state.status(task_id, "error", f"Source unavailable ({type(exc).__name__})")

    async def close(self) -> None:
        tasks = tuple(self._all_tasks)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
