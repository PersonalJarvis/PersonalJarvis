"""Tiny test kit (no third-party deps). Only depends on the shared event contract.

`FlightLog` is the wildcard "flight recorder" — exactly the production pattern
where `subscribe_all` records every event for replay/inspection.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable

from optimistic.events import Event


class FlightLog:
    """Records every event published on the bus, in order."""

    def __init__(self, bus) -> None:
        self.events: list[Event] = []
        bus.subscribe_all(self._record)

    async def _record(self, ev: Event) -> None:
        self.events.append(ev)

    def has(self, etype: type) -> bool:
        return any(isinstance(e, etype) for e in self.events)

    def of(self, etype: type) -> list[Event]:
        return [e for e in self.events if isinstance(e, etype)]

    def index(self, etype: type) -> int:
        for i, e in enumerate(self.events):
            if isinstance(e, etype):
                return i
        raise AssertionError(f"{etype.__name__} not found in flight log")


def percentile(values: Iterable[float], p: float) -> float:
    s = sorted(values)
    if not s:
        raise ValueError("percentile() of empty sequence")
    idx = int(round((p / 100.0) * (len(s) - 1)))
    return s[max(0, min(len(s) - 1, idx))]


def run(coro):
    """Run an async scenario without depending on pytest-asyncio (cloud-first: no extra dep)."""
    return asyncio.run(coro)


# --- v2: in-process SSE streaming test client --------------------------------
# httpx.ASGITransport buffers the whole response body, which never returns for an
# infinite SSE stream. StreamingASGIClient drives the ASGI app directly and yields
# body chunks as they arrive. (Pattern discovered by the v2 SSE sub-agent.)


class StreamingASGIClient:
    """Minimal in-process ASGI driver that streams response bytes chunk by chunk."""

    def __init__(self, app) -> None:
        self._app = app

    def stream(self, method, path, *, query_string="", headers=None):
        return _StreamContext(self._app, method, path, query_string, headers or [])


class _StreamContext:
    def __init__(self, app, method, path, query_string, headers):
        self._app = app
        self._method = method
        self._path = path
        self._qs = query_string.encode() if isinstance(query_string, str) else query_string
        self._headers = headers
        self._chunks: asyncio.Queue = asyncio.Queue()
        self._task = None

    async def __aenter__(self):
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": self._method.upper(),
            "headers": self._headers,
            "scheme": "http",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": self._qs,
            "server": ("localhost", 8000),
            "client": ("127.0.0.1", 1234),
            "root_path": "",
        }
        q = self._chunks

        async def receive():
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.body":
                body = msg.get("body", b"")
                if body:
                    await q.put(body)
                if not msg.get("more_body", False):
                    await q.put(None)

        self._task = asyncio.create_task(self._app(scope, receive, send))
        return self

    async def __aexit__(self, *_):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def __aiter__(self):
        while True:
            chunk = await self._chunks.get()
            if chunk is None:
                return
            yield chunk


async def read_sse_events(
    client, session_id, *, publish_cb, until_event=None, max_events=1, timeout=5.0
):
    """Open an SSE stream, trigger publish_cb, collect events; return [{event, data}]."""
    collected: list[dict] = []
    done = asyncio.Event()

    async def _reader():
        buf = b""
        name = None
        async with client.stream(
            "GET", "/api/stream", query_string=f"session_id={session_id}"
        ) as ctx:
            async for chunk in ctx:
                buf += chunk
                while b"\r\n" in buf or b"\n" in buf:
                    if b"\r\n" in buf:
                        line_b, buf = buf.split(b"\r\n", 1)
                    else:
                        line_b, buf = buf.split(b"\n", 1)
                    line = line_b.decode("utf-8", "replace")
                    if line.startswith("event:"):
                        name = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        raw = line[len("data:"):].strip()
                        try:
                            payload = json.loads(raw)
                        except Exception:
                            payload = raw
                        if name is not None:
                            collected.append({"event": name, "data": payload})
                            ev_name, name = name, None
                            if (until_event and ev_name == until_event) or (
                                not until_event and len(collected) >= max_events
                            ):
                                done.set()
                                return
                    elif line == "" or line.startswith(":"):
                        name = None

    task = asyncio.create_task(_reader())
    await asyncio.sleep(0.05)
    await publish_cb()
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    except TimeoutError:
        pass
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    return collected
