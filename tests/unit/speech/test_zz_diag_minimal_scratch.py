"""Minimal repro: does an async generator waiting on a flag resume under pytest?"""
from __future__ import annotations

import asyncio


class _Flag:
    warm = False


async def _gen(flag: _Flag):
    while not flag.warm:
        await asyncio.sleep(0.05)
    while True:
        await asyncio.sleep(0.01)
        yield "tick"


async def test_minimal_generator_resumes_after_flag_flip(capsys) -> None:
    flag = _Flag()
    ticks = []

    async def _drain() -> None:
        async for t in _gen(flag):
            ticks.append(t)

    d = asyncio.create_task(_drain())
    await asyncio.sleep(0.3)
    cold = len(ticks)
    flag.warm = True
    await asyncio.sleep(0.5)
    warm = len(ticks)
    d.cancel()
    try:
        await d
    except asyncio.CancelledError:
        pass
    with capsys.disabled():
        print(f"\nMINIMAL cold_ticks={cold} warm_ticks={warm}")
    assert cold == 0
    assert warm > 0, "generator never resumed after the flag flip"
