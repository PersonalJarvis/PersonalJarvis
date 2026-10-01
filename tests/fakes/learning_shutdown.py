"""Controlled real-notebook writes for learning lifecycle qualification."""

import asyncio
import threading


class GatedNotebook:
    def __init__(self, notebook):
        self.notebook = notebook
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, receipt, status):
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("Test did not release the notebook writer")
        self.notebook.complete(receipt, status)

    def context(self, *args, **kwargs):
        return self.notebook.context(*args, **kwargs)

    def read(self):
        return self.notebook.read()


class WaitingReviewer:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return {"memories": [], "lessons": [], "skill": None}
