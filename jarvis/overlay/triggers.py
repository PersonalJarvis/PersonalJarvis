"""Trigger API for the main Jarvis action path. Plan §8.

Three ways to instrument actions:
  1. ``@overlay_action(kind, duration_hint_ms=...)`` — decorator for
     async functions.
  2. ``@overlay_action_sync(kind, duration_hint_ms=...)`` — same
     semantics for sync functions.
  3. ``async with overlay_action_scope(kind, duration_hint_ms=...)``  —
     context manager for inline use.

Plan §8.4 decorator contract:
- Emit ``action_started`` event BEFORE the function call.
- Emit ``action_ended`` event AFTER the function call (in finally — also
  on exception).
- On exception: ADDITIONALLY emit ``error`` event with recoverable=True.

Sub-agent behavior (Plan §8.7): when ``JARVIS_DEPTH > 0``, the
decorator logic still fires — but the OverlayBridge instance is a
no-op stub, so events go into the void. Caller code is structurally
unaware of this.

Current action kinds (Plan §8.2):

| ActionKind        | Trigger-Source                           |
|-------------------|------------------------------------------|
| CLICK             | pyautogui.click, Browser-Use click       |
| TYPING            | pyautogui.typewrite, Browser-Use type    |
| MOVE              | pyautogui.moveTo (mit duration > 0)      |
| NAVIGATE          | Browser-Use navigation                   |
| HOTKEY            | pyautogui.hotkey                         |
| SCROLL            | pyautogui.scroll                         |
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, Optional, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class ActionKind(str, Enum):
    """Derived from Plan §6.1 — what triggers the glow."""

    CLICK = "click"
    TYPING = "type"  # Plan §6.3 wire string is 'type', not 'typing'
    MOVE = "move"
    NAVIGATE = "navigate"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    BROWSER = "navigate"  # backward-compat alias for Phase-9.1 code


def _get_bridge() -> Any:
    """Lazy lookup of the singleton to avoid circular imports.

    In tests the singleton is set via ``jarvis.overlay.set_overlay()``.
    In production it is resolved on the first decorator call via the
    ``get_overlay()`` accessor.

    When None: the decorator falls back to a very lightweight no-op stub
    that simply runs the function.
    """
    from jarvis.overlay import get_overlay  # spaet importieren

    return get_overlay()


def overlay_action(
    kind: ActionKind | str,
    *,
    duration_hint_ms: Optional[int] = None,
) -> Callable[[F], F]:
    """Async-Decorator. Plan §8.4 Contract."""

    kind_str = kind.value if isinstance(kind, ActionKind) else str(kind)

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            bridge = _get_bridge()
            start_ns = time.monotonic_ns()
            action_id = ""
            if bridge is not None:
                try:
                    action_id = bridge.emit_action_started(
                        kind_str, duration_hint_ms=duration_hint_ms
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("overlay_action emit_started raised")
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                if bridge is not None:
                    try:
                        bridge.emit_error(
                            f"{type(exc).__name__}: {exc}",
                            recoverable=True,
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug("overlay_action emit_error failed", exc_info=True)
                raise
            finally:
                if bridge is not None and action_id:
                    duration_ms = max(
                        0, (time.monotonic_ns() - start_ns) // 1_000_000
                    )
                    try:
                        bridge.emit_action_ended(
                            action_id,
                            succeeded=True,
                            duration_actual_ms=int(duration_ms),
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug("overlay_action emit_ended failed", exc_info=True)

        return wrapper  # type: ignore[return-value]

    return decorator


def overlay_action_sync(
    kind: ActionKind | str,
    *,
    duration_hint_ms: Optional[int] = None,
) -> Callable[[F], F]:
    """Sync decorator. Plan §8.4 contract.

    Same semantics as ``overlay_action``, but without ``await``. Applied
    to pyautogui wrappers in jarvis/control/{mouse,keyboard}.py —
    pyautogui itself is synchronous.
    """

    kind_str = kind.value if isinstance(kind, ActionKind) else str(kind)

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bridge = _get_bridge()
            start_ns = time.monotonic_ns()
            action_id = ""
            if bridge is not None:
                try:
                    action_id = bridge.emit_action_started(
                        kind_str, duration_hint_ms=duration_hint_ms
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("overlay_action_sync emit_started raised")
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if bridge is not None:
                    try:
                        bridge.emit_error(
                            f"{type(exc).__name__}: {exc}",
                            recoverable=True,
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "overlay_action_sync emit_error failed", exc_info=True
                        )
                raise
            finally:
                if bridge is not None and action_id:
                    duration_ms = max(
                        0, (time.monotonic_ns() - start_ns) // 1_000_000
                    )
                    try:
                        bridge.emit_action_ended(
                            action_id,
                            succeeded=True,
                            duration_actual_ms=int(duration_ms),
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "overlay_action_sync emit_ended failed", exc_info=True
                        )

        return wrapper  # type: ignore[return-value]

    return decorator


@asynccontextmanager
async def overlay_action_scope(
    kind: ActionKind | str,
    *,
    duration_hint_ms: Optional[int] = None,
) -> AsyncIterator[str]:
    """Async context manager. Plan §8.5 contract.

    Yields the ``action_id`` so the caller can, for example,
    reference it via ``emit_click(...)``.

    Example::

        async with overlay_action_scope(ActionKind.TYPING, duration_hint_ms=2000):
            await asyncio.to_thread(pyautogui.typewrite, text)
    """
    kind_str = kind.value if isinstance(kind, ActionKind) else str(kind)
    bridge = _get_bridge()
    action_id = ""
    start_ns = time.monotonic_ns()
    if bridge is not None:
        try:
            action_id = bridge.emit_action_started(
                kind_str, duration_hint_ms=duration_hint_ms
            )
        except Exception:  # noqa: BLE001
            logger.exception("overlay_action_scope emit_started raised")
    try:
        yield action_id
    except Exception as exc:
        if bridge is not None:
            try:
                bridge.emit_error(
                    f"{type(exc).__name__}: {exc}",
                    recoverable=True,
                )
            except Exception:  # noqa: BLE001
                logger.debug("overlay_action_scope emit_error failed", exc_info=True)
        raise
    finally:
        if bridge is not None and action_id:
            duration_ms = max(0, (time.monotonic_ns() - start_ns) // 1_000_000)
            try:
                bridge.emit_action_ended(
                    action_id,
                    succeeded=True,
                    duration_actual_ms=int(duration_ms),
                )
            except Exception:  # noqa: BLE001
                logger.debug("overlay_action_scope emit_ended failed", exc_info=True)


@contextmanager
def overlay_action_scope_sync(
    kind: ActionKind | str,
    *,
    duration_hint_ms: Optional[int] = None,
) -> Iterator[str]:
    """Sync context manager. Plan §8.5 — for sync callers that are not
    in an async loop."""
    kind_str = kind.value if isinstance(kind, ActionKind) else str(kind)
    bridge = _get_bridge()
    action_id = ""
    start_ns = time.monotonic_ns()
    if bridge is not None:
        try:
            action_id = bridge.emit_action_started(
                kind_str, duration_hint_ms=duration_hint_ms
            )
        except Exception:  # noqa: BLE001
            logger.exception("overlay_action_scope_sync emit_started raised")
    try:
        yield action_id
    except Exception as exc:
        if bridge is not None:
            try:
                bridge.emit_error(
                    f"{type(exc).__name__}: {exc}",
                    recoverable=True,
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "overlay_action_scope_sync emit_error failed", exc_info=True
                )
        raise
    finally:
        if bridge is not None and action_id:
            duration_ms = max(0, (time.monotonic_ns() - start_ns) // 1_000_000)
            try:
                bridge.emit_action_ended(
                    action_id,
                    succeeded=True,
                    duration_actual_ms=int(duration_ms),
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "overlay_action_scope_sync emit_ended failed", exc_info=True
                )


__all__ = [
    "ActionKind",
    "overlay_action",
    "overlay_action_scope",
    "overlay_action_scope_sync",
    "overlay_action_sync",
]
