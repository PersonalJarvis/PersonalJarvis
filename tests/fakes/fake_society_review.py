"""Deterministic providers and executor receipts for society lifecycle tests."""

import asyncio
from types import SimpleNamespace

from jarvis.core.protocols import BrainDelta


class SummaryProvider:
    context_window = 4096

    def __init__(self, empty=False):
        self.calls = []
        self.empty = empty

    async def complete(self, request):
        self.calls.append(request)
        yield BrainDelta(
            content=""
            if self.empty
            else "The user assigned the Gmail role. Drafts only; format remains open. Source seq 1."
        )


class MemoryExecutor:
    def __init__(self):
        self.calls = []
        self.completed = asyncio.Event()

    async def execute(self, tool, args, **kwargs):
        self.calls.append((tool.name, args, kwargs))
        result = await tool.execute(args, SimpleNamespace(trace_id=kwargs["trace_id"]))
        self.completed.set()
        return result
