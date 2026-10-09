"""Browser media ownership shared by desktop wake, hotkeys and web controls."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

_active: dict[str, Any] = {}
_owners: dict[str, asyncio.AbstractEventLoop] = {}
_opening: set[str] = set()
_watchers: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()
_detached: set[asyncio.Task] = set()
_request_lock = threading.Lock()


@dataclass
class _BrowserStart:
    loop: asyncio.AbstractEventLoop
    changed: asyncio.Event
    session_id: str
    owner_key: str
    failed: bool = False
    registered: bool = False


_pending_browser_starts: dict[str, _BrowserStart] = {}
_browser_call_requests: dict[str, str] = {}


def browser_startup_failed(request_id: str) -> bool:
    """Fail only the still-pending native request named by its browser owner."""
    with _request_lock:
        pending = _pending_browser_starts.get(request_id)
        if (pending is None or _active or pending.loop.is_closed()
                or _browser_call_requests.get(pending.owner_key) != request_id):
            return False
        _pending_browser_starts.pop(request_id, None)
        pending.failed = True
        try:
            pending.loop.call_soon_threadsafe(pending.changed.set)
        except RuntimeError:
            # The owning event loop shut down after the closed check.
            return False
        return True


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
    with _request_lock:
        _opening.discard(session.session_id)
        _active[session.session_id] = session
        _owners[session.session_id] = asyncio.get_running_loop()
        for request_id, pending in tuple(_pending_browser_starts.items()):
            if not pending.session_id or pending.session_id == session.session_id:
                # Registration and an early disconnect can both happen before
                # the native waiter resumes. Remember the transition instead
                # of waiting for a session which has already come and gone.
                pending.registered = True
                _pending_browser_starts.pop(request_id, None)
    _notify()


def unregister(session_id: str) -> None:
    with _request_lock:
        _opening.discard(session_id)
        _active.pop(session_id, None)
        _owners.pop(session_id, None)
    _notify()


def active() -> tuple[Any, ...]:
    return tuple(_active.values())


async def on_session_loop(session: Any, operation: Any, **kwargs: Any) -> Any:
    """Execute an active browser session operation on its owning event loop."""
    owner = _owners.get(session.session_id)
    if owner is None or not owner.is_running() or owner.is_closed():
        raise RuntimeError("The browser voice session is no longer available.")

    async def invoke() -> Any:
        if _active.get(session.session_id) is not session:
            raise RuntimeError("The browser voice session changed before delivery.")
        return await operation(**kwargs)

    if asyncio.get_running_loop() is owner:
        return await invoke()
    future = asyncio.run_coroutine_threadsafe(invoke(), owner)
    return await asyncio.wrap_future(future)


def owns_microphone(*, except_session_id: str | None = None) -> bool:
    """A pending or active browser call must not be replaced by a wake start."""
    return any(sid != except_session_id for sid in (*_opening, *_active))


async def close_all(reason: str = "hotkey") -> None:
    await asyncio.gather(
        *(on_session_loop(session, session.end, reason=reason) for session in active())
    )


async def run_browser_call(
    bus: Any,
    hangup: asyncio.Event,
    *,
    timeout_s: float = 45.0,
    input_buffer: Any = None,
    session_id: str = "",
) -> str:
    """Wake hands media to the WebView; the desktop never feeds speaker echo back."""
    from jarvis.core.events import BrowserVoiceRequested
    from jarvis.live import startup

    changed = asyncio.Event()
    watcher = (asyncio.get_running_loop(), changed)
    _watchers.add(watcher)
    request_id = str(uuid4())
    owner_key = session_id or request_id
    pending = _BrowserStart(watcher[0], changed, session_id, owner_key)
    with _request_lock:
        previous_id = _browser_call_requests.get(owner_key)
        previous = _pending_browser_starts.pop(previous_id, None)
        _browser_call_requests[owner_key] = request_id
        _pending_browser_starts[request_id] = pending
        if previous is not None:
            previous.failed = True
    _notify()

    def owns_request() -> bool:
        with _request_lock:
            return _browser_call_requests.get(owner_key) == request_id

    def own_sessions() -> tuple[Any, ...]:
        return tuple(session for session in active()
                     if not session_id or session.session_id == session_id)

    async def wait_change() -> None:
        tasks = [asyncio.create_task(changed.wait()), asyncio.create_task(hangup.wait())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        changed.clear()

    try:
        if hangup.is_set():
            return "hotkey"
        if input_buffer is not None and session_id:
            startup.offer(session_id, input_buffer)
        await bus.publish(BrowserVoiceRequested(action="start", request_id=request_id))
        # UI permission and device setup can take time. No idle billed connection.
        async with asyncio.timeout(timeout_s):
            while (not pending.registered and not own_sessions()
                   and not hangup.is_set() and owns_request()):
                if pending.failed or (session_id and active()):
                    return "error"
                await wait_change()
        if pending.failed:
            return "error"
        while own_sessions() and not hangup.is_set() and owns_request():
            await wait_change()
    except TimeoutError:
        # The caller receives an explicit error outcome for this bounded wait.
        return "error"
    finally:
        with _request_lock:
            _pending_browser_starts.pop(request_id, None)
            still_owned = _browser_call_requests.get(owner_key) == request_id
            if still_owned:
                sessions_to_close = own_sessions()
                startup.discard(session_id)
                _browser_call_requests.pop(owner_key, None)
        _watchers.discard(watcher)
        if still_owned:
            # Correlated retraction cannot stop a newer browser request. Only
            # close this native session; unrelated browser calls remain owned.
            await bus.publish(BrowserVoiceRequested(action="stop", request_id=request_id))
            await asyncio.gather(*(
                on_session_loop(session, session.end, reason="hotkey")
                for session in sessions_to_close
                if _active.get(session.session_id) is session
            ))
    return "hotkey" if hangup.is_set() else "client_stop"
