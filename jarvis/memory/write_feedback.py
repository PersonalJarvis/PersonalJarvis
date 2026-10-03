"""Visible receipts for an agent's persistent memory writes.

A write to one of an agent's memory files (``MEMORY.md``, ``USER.md``,
``SOUL.md``, the legacy ``core_memory.json``) announces itself in up to two
steps that share one ``update_id``:

* ``pending``   — the write is about to start; nothing is saved yet;
* ``saved``     — the file was replaced on disk (only after the write returned);
* ``unchanged`` — the write finished but changed nothing (a duplicate);
* ``failed``    — the write raised; whatever the file held before still holds.

The UI (the lead pet's thought bubble in the Jarvis Verse) shows "Updating
memory · MEMORY.md" for ``pending`` and claims success only on ``saved``.

Only the bare file name, the agent id and the operation cross this boundary —
never a path, the entry text or an error message — so the event is safe to
forward to every open window.

This module is low-level on purpose (no bus import): the memory layers call
:func:`announce_write`; the app wires a sink with :func:`connect` once its
event loop and bus exist. Without a sink every call is a cheap no-op, so a
headless test or a CLI write never needs a bus.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final

log = logging.getLogger(__name__)

PHASES: Final[tuple[str, ...]] = ("pending", "saved", "unchanged", "failed")
#: A memory file is a bare name like ``MEMORY.md``; anything else is dropped.
_FILE_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_AGENT_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_OPERATIONS: Final[frozenset[str]] = frozenset({"add", "replace", "remove", "edit"})

Sink = Callable[[str, str, str, str, str], None]
"""``sink(update_id, agent_id, file, phase, operation)``."""

_sink: Sink | None = None
_sink_lock = threading.Lock()


def safe_file_name(name: str) -> str:
    """The bare file name of ``name``, or ``""`` when it is not one we can show."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return base if _FILE_NAME.match(base) and base not in {".", ".."} else ""


def set_sink(sink: Sink | None) -> None:
    global _sink
    with _sink_lock:
        _sink = sink


def _emit(update_id: str, agent_id: str, file: str, phase: str, operation: str) -> None:
    sink = _sink
    if sink is None:
        return
    try:
        sink(update_id, agent_id, file, phase, operation)
    except Exception:  # noqa: BLE001 — a receipt must never fail or undo the write it describes
        log.debug("memory write receipt not delivered (%s)", phase, exc_info=True)


@dataclass
class WriteReceipt:
    """Filled in by the writer inside :func:`announce_write`."""

    #: Set to ``False`` when the write turned out to change nothing.
    changed: bool = True
    #: Set when the target file is only known after the write (auto-classified).
    file: str = ""


@contextmanager
def announce_write(agent_id: str, file: str, *, operation: str = "edit") -> Iterator[WriteReceipt]:
    """Announce one memory write around the code that performs it.

    ``pending`` goes out before the body runs; ``saved`` / ``unchanged`` only
    after it returned, ``failed`` when it raised (the exception propagates
    unchanged). An agent id or file name that does not look like one is never
    forwarded; the write itself always runs.
    """
    receipt = WriteReceipt(file=safe_file_name(file))
    agent = str(agent_id or "")
    op = operation if operation in _OPERATIONS else "edit"
    if _sink is None or not _AGENT_ID.match(agent):
        yield receipt
        return
    update_id = uuid.uuid4().hex
    _emit(update_id, agent, receipt.file, "pending", op)
    try:
        yield receipt
    except BaseException:
        _emit(update_id, agent, safe_file_name(receipt.file), "failed", op)
        raise
    settled = "saved" if receipt.changed else "unchanged"
    _emit(update_id, agent, safe_file_name(receipt.file), settled, op)


def connect(bus: Any, loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Publish every receipt as a ``MemoryFileWrite`` event on ``bus``.

    Thread-safe: memory writes run in worker threads (``asyncio.to_thread``),
    so each event is handed to ``loop`` in call order. A closed loop (shutdown)
    drops the receipt quietly — nobody is left to show it.
    """
    from jarvis.core.events import MemoryFileWrite

    target = loop or asyncio.get_running_loop()
    pending: set[asyncio.Future[Any]] = set()

    def _publish(event: Any) -> None:
        task = asyncio.ensure_future(bus.publish(event))
        pending.add(task)
        task.add_done_callback(pending.discard)

    def _sink_impl(update_id: str, agent_id: str, file: str, phase: str, operation: str) -> None:
        event = MemoryFileWrite(
            source_layer="memory.write_feedback",
            update_id=update_id,
            agent_id=agent_id,
            file=file,
            phase=phase,
            operation=operation,
        )
        try:
            target.call_soon_threadsafe(_publish, event)
        except RuntimeError:
            log.debug("memory write receipt dropped: the event loop is closed")

    set_sink(_sink_impl)


def disconnect() -> None:
    set_sink(None)
