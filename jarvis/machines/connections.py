"""Shared per-event-loop connection rate budget for connector and SSH handshakes."""

import asyncio
import time
from weakref import WeakKeyDictionary

_budgets: WeakKeyDictionary = WeakKeyDictionary()


async def wait_for_connection() -> None:
    loop = asyncio.get_running_loop()
    if loop not in _budgets:
        _budgets[loop] = [asyncio.Lock(), 0.0]
    budget = _budgets[loop]
    async with budget[0]:
        delay = budget[1] - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        budget[1] = time.monotonic() + 0.2
