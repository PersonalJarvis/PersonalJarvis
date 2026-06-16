from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import ResponseGenerated


@pytest.mark.asyncio
async def test_response_side_effects_preserve_trace_id() -> None:
    bus = EventBus()
    seen: list[ResponseGenerated] = []

    async def _capture(event: ResponseGenerated) -> None:
        seen.append(event)

    bus.subscribe(ResponseGenerated, _capture)
    manager = BrainManager(config=JarvisConfig(), bus=bus, tools={})
    trace_id = uuid4()

    await manager._record_response_side_effects(  # noqa: SLF001
        user_text="Hello",
        response_text="Hello back",
        use_history=False,
        trace_id=trace_id,
    )

    assert len(seen) == 1
    assert seen[0].trace_id == trace_id
