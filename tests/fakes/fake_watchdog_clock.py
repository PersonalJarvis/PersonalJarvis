"""Controlled heartbeat delivery for watchdog state-machine tests."""

from collections.abc import Callable, Iterable


class ScriptedWatchdogClock:
    """Advance one interval per wait, delivering queued beats on healthy ticks."""

    def __init__(self, healthy_ticks: Iterable[bool]) -> None:
        self._ticks = list(healthy_ticks)
        self._index = 0
        self._now = 0.0
        self._pending: list[Callable[[], None]] = []

    def monotonic(self) -> float:
        return self._now

    def call_soon_threadsafe(self, callback: Callable[[], None]) -> None:
        self._pending.append(callback)

    def is_running(self) -> bool:
        # A stalled loop is still running; it is stuck inside one callback.
        return True

    def is_closed(self) -> bool:
        return False

    def is_set(self) -> bool:
        return self._index == len(self._ticks)

    def wait(self, interval: float) -> bool:
        self._now += interval
        healthy = self._ticks[self._index]
        self._index += 1
        if healthy:
            pending, self._pending = self._pending, []
            for callback in pending:
                callback()
        return False
