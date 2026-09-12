"""Browser media ownership shared by desktop wake, hotkeys and web controls."""

from __future__ import annotations

import asyncio
from typing import Any

_active: dict[str, Any] = {}
_opening: set[str] = set()
_watchers: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()
_detached: set[asyncio.Task] = set()


def retain_work(jobs: tuple[asyncio.Task, ...], ledger: Any) -> None:
    """Already-started tools may finish after speech closes; retain their receipts."""

    async def finish() -> None:
        try:
            await asyncio.gather(*jobs, return_exceptions=True)
        finally:
            await asyncio.to_thread(ledger.close)

    task = asyncio.create_task(finish(), name="live-detached-work")
    _detached.add(task)
    task.add_done_callback(_detached.discard)


def _notify() -> None:
    for loop, event in tuple(_watchers):
        if not loop.is_closed():
            loop.call_soon_threadsafe(event.set)


def claim(session_id: str) -> None:
    if _opening or _active:
        raise RuntimeError("A voice session is already active in another window.")
    _opening.add(session_id)


def register(session: Any) -> None:
    _opening.discard(session.session_id)
    _active[session.session_id] = session
    _notify()


def unregister(session_id: str) -> None:
    _opening.discard(session_id)
    _active.pop(session_id, None)
    _notify()


def active() -> tuple[Any, ...]:
    return tuple(_active.values())


async def close_all(reason: str = "hotkey") -> None:
    await asyncio.gather(*(session.end(reason=reason) for session in active()))


async def run_browser_call(bus: Any, hangup: asyncio.Event) -> str:
    """Wake hands media to the WebView; the desktop never feeds speaker echo back."""
    from jarvis.core.events import BrowserVoiceRequested

    changed = asyncio.Event()
    watcher = (asyncio.get_running_loop(), changed)
    _watchers.add(watcher)

    async def wait_change() -> None:
        tasks = [asyncio.create_task(changed.wait()), asyncio.create_task(hangup.wait())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        changed.clear()

    await bus.publish(BrowserVoiceRequested(action="start"))
    # UI permission and device setup can take time. No background billed connection.
    try:
        async with asyncio.timeout(45):
            while not active() and not hangup.is_set():
                await wait_change()
        while active() and not hangup.is_set():
            await wait_change()
    except TimeoutError:
        return "error"
    finally:
        _watchers.discard(watcher)
        if hangup.is_set():
            await bus.publish(BrowserVoiceRequested(action="stop"))
            await close_all()
    return "hotkey" if hangup.is_set() else "client_stop"
