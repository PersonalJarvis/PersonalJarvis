"""Tests for optimistic/sse.py — SSEHub + build_sse_router.

TDD: written BEFORE sse.py exists. All tests must fail with ImportError first,
then go green once the implementation is in place.

Testing approach
----------------
httpx.ASGITransport buffers the entire response body before returning the
Response object — this makes it incompatible with infinite SSE streams (the
stream never ends, so httpx never returns). We therefore bypass it for the
streaming tests and invoke the ASGI app directly via a minimal
``_StreamingASGIClient`` that exposes the bytes as they arrive, in-process.

For the one test that only needs HTTP headers we still use httpx.ASGITransport
with a manual cancel (the request starts, we grab the headers before body
accumulation, then cancel).

asyncio.run() in every test function — NO pytest-asyncio dependency.

SSE wire format (sse_starlette, CRLF separator):
    event: <name>\r\n
    data: <json>\r\n
    \r\n

The shared ``_read_sse_events`` coroutine parses this format. The orchestrator's
E2E tests can copy this reader pattern.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI

from optimistic.bus import EventBus
from optimistic.events import (
    AckEmitted,
    CorrectionReason,
    WorkerCompleted,
    WorkerCorrectionNeeded,
    WorkerStarted,
)
from optimistic.sse import SSEHub, build_sse_router

# ---------------------------------------------------------------------------
# Helpers — streaming ASGI test client
# ---------------------------------------------------------------------------

class _StreamingASGIClient:
    """Minimal in-process ASGI driver that streams response bytes chunk by chunk.

    Bypasses httpx.ASGITransport's body-buffering limitation for SSE streams.
    The ``stream`` method returns an async generator over raw bytes chunks.
    Each chunk corresponds to one "http.response.body" ASGI message.
    """

    def __init__(self, app) -> None:
        self._app = app

    def stream(
        self,
        method: str,
        path: str,
        *,
        query_string: str = "",
        headers: list[tuple[bytes, bytes]] | None = None,
    ):
        """Return an async context manager yielding raw bytes chunks."""
        return _StreamContext(self._app, method, path, query_string, headers or [])


class _StreamContext:
    """Async context manager returned by _StreamingASGIClient.stream()."""

    def __init__(self, app, method, path, query_string, headers):
        self._app = app
        self._method = method
        self._path = path
        self._qs = query_string.encode() if isinstance(query_string, str) else query_string
        self._headers = headers
        self._chunk_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._app_task: asyncio.Task | None = None

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
        chunk_queue = self._chunk_queue

        async def receive():
            # Signal disconnect only when the app task is being cancelled.
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.body":
                body = msg.get("body", b"")
                if body:
                    await chunk_queue.put(body)
                if not msg.get("more_body", False):
                    await chunk_queue.put(None)  # EOF sentinel

        self._app_task = asyncio.create_task(self._app(scope, receive, send))
        return self

    async def __aexit__(self, *_):
        if self._app_task:
            self._app_task.cancel()
            try:
                await self._app_task
            except (asyncio.CancelledError, Exception):
                pass

    async def __aiter__(self):
        """Yield raw bytes chunks as they arrive from the ASGI app."""
        while True:
            chunk = await self._chunk_queue.get()
            if chunk is None:
                return
            yield chunk


def _build_app() -> tuple[FastAPI, EventBus, SSEHub]:
    """Build a minimal FastAPI app wired to a real EventBus + SSEHub."""
    app = FastAPI()
    bus = EventBus()
    hub = SSEHub(bus)
    app.include_router(build_sse_router(hub))
    return app, bus, hub


async def _read_sse_events(
    client: _StreamingASGIClient,
    session_id: str,
    *,
    publish_cb,
    max_events: int = 1,
    timeout: float = 5.0,
) -> list[dict]:
    """
    Open an SSE stream for session_id (using the direct ASGI client),
    invoke publish_cb to trigger events, collect up to max_events, and return.

    Returns a list of {"event": str, "data": dict} dicts.

    This is the canonical reader pattern. The orchestrator's E2E mirrors it.

    Protocol
    --------
    1. Open the SSE stream in a background task.
    2. Sleep 0.05 s so the generator registers its queue with the SSEHub.
    3. Call publish_cb() to trigger the event(s).
    4. Wait up to `timeout` seconds for the reader to collect `max_events`.
    5. Cancel the ASGI app task and return whatever was collected.
    """
    collected: list[dict] = []
    done_event = asyncio.Event()

    async def _reader():
        """Parse SSE lines from raw byte chunks and populate `collected`."""
        buf = b""
        current_event_name: str | None = None

        async with client.stream(
            "GET", "/api/stream", query_string=f"session_id={session_id}"
        ) as ctx:
            async for chunk in ctx:
                buf += chunk
                # Process all complete lines in the buffer.
                while b"\r\n" in buf or b"\n" in buf:
                    if b"\r\n" in buf:
                        line_bytes, buf = buf.split(b"\r\n", 1)
                    else:
                        line_bytes, buf = buf.split(b"\n", 1)

                    line = line_bytes.decode("utf-8", errors="replace")

                    if line.startswith("event:"):
                        current_event_name = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        raw = line[len("data:"):].strip()
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            payload = raw
                        if current_event_name is not None:
                            collected.append(
                                {"event": current_event_name, "data": payload}
                            )
                            current_event_name = None
                            if len(collected) >= max_events:
                                done_event.set()
                                return
                    elif line == "" or line.startswith(":"):
                        # blank line = end of event block; : = comment/ping
                        current_event_name = None

    reader_task = asyncio.create_task(_reader())

    # Give the reader time to open the ASGI stream and register the queue.
    await asyncio.sleep(0.05)

    # Trigger the event(s).
    await publish_cb()

    # Wait for collection or timeout.
    try:
        await asyncio.wait_for(done_event.wait(), timeout=timeout)
    except TimeoutError:
        pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass

    return collected


# ---------------------------------------------------------------------------
# Test 1: WorkerCompleted → "answer" event on the matching session stream
# ---------------------------------------------------------------------------

def test_worker_completed_delivers_answer_to_matching_session():
    """A WorkerCompleted published for s1 must arrive as an 'answer' SSE event
    on the s1 stream, carrying result text and mission_id."""

    async def scenario():
        app, bus, hub = _build_app()
        client = _StreamingASGIClient(app)

        async def _publish():
            await bus.publish(
                WorkerCompleted(
                    result="hallo welt",
                    mission_id="mission-abc",
                    session_id="s1",
                )
            )

        events = await _read_sse_events(client, "s1", publish_cb=_publish)

        assert len(events) == 1, f"expected 1 event, got: {events}"
        ev = events[0]
        assert ev["event"] == "answer"
        assert ev["data"]["text"] == "hallo welt"
        assert ev["data"]["mission_id"] == "mission-abc"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 2: Event for s2 is NOT delivered to s1 stream
# ---------------------------------------------------------------------------

def test_event_for_other_session_not_delivered():
    """A WorkerCompleted published for session s2 must NOT appear in the s1
    stream — per-session routing is the core isolation guarantee."""

    async def scenario():
        app, bus, hub = _build_app()
        client = _StreamingASGIClient(app)

        async def _publish():
            await bus.publish(
                WorkerCompleted(
                    result="not for s1",
                    mission_id="mission-s2",
                    session_id="s2",
                )
            )

        # Short timeout: expect 0 events on s1.
        events = await _read_sse_events(
            client, "s1", publish_cb=_publish, timeout=0.3
        )

        assert events == [], (
            f"s1 stream must not receive events for s2, got: {events}"
        )

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 3: hub.push delivers a "correction" event to s1 stream
# ---------------------------------------------------------------------------

def test_hub_push_delivers_correction_to_session():
    """hub.push('s1', 'correction', {'text': 'x'}) must reach the s1 SSE
    stream as a 'correction' event with the given data payload."""

    async def scenario():
        app, bus, hub = _build_app()
        client = _StreamingASGIClient(app)

        async def _push():
            await hub.push("s1", "correction", {"text": "x"})

        events = await _read_sse_events(client, "s1", publish_cb=_push)

        assert len(events) == 1, f"expected 1 event, got: {events}"
        ev = events[0]
        assert ev["event"] == "correction"
        assert ev["data"]["text"] == "x"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 4: AckEmitted → "ack" event on the matching session stream
# ---------------------------------------------------------------------------

def test_ack_emitted_delivers_ack_event():
    """AckEmitted published on bus for s1 must arrive as an 'ack' SSE event."""

    async def scenario():
        app, bus, hub = _build_app()
        client = _StreamingASGIClient(app)

        async def _publish():
            await bus.publish(AckEmitted(text="Geht klar!", session_id="s1"))

        events = await _read_sse_events(client, "s1", publish_cb=_publish)

        assert len(events) == 1, f"expected 1 'ack' event, got: {events}"
        ev = events[0]
        assert ev["event"] == "ack"
        assert ev["data"]["text"] == "Geht klar!"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 5: WorkerStarted → "worker_started" event on the matching session stream
# ---------------------------------------------------------------------------

def test_worker_started_delivers_worker_started_event():
    """WorkerStarted published on bus for s1 must arrive as a 'worker_started'
    SSE event carrying mission_id."""

    async def scenario():
        app, bus, hub = _build_app()
        client = _StreamingASGIClient(app)

        async def _publish():
            await bus.publish(
                WorkerStarted(mission_id="mission-xyz", session_id="s1")
            )

        events = await _read_sse_events(client, "s1", publish_cb=_publish)

        assert len(events) == 1, f"expected 1 'worker_started' event, got: {events}"
        ev = events[0]
        assert ev["event"] == "worker_started"
        assert ev["data"]["mission_id"] == "mission-xyz"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 6: WorkerCorrectionNeeded is NOT streamed (invisible until VAD flush)
# ---------------------------------------------------------------------------

def test_worker_correction_needed_is_not_streamed():
    """WorkerCorrectionNeeded must be invisible on the SSE stream — it is only
    delivered explicitly via hub.push after a VAD turn boundary (AD-OE5)."""

    async def scenario():
        app, bus, hub = _build_app()
        client = _StreamingASGIClient(app)

        async def _publish():
            await bus.publish(
                WorkerCorrectionNeeded(
                    mission_id="mission-oops",
                    reason=CorrectionReason.MISSING_INFO,
                    detail="no address for Max",
                    session_id="s1",
                )
            )

        events = await _read_sse_events(
            client, "s1", publish_cb=_publish, timeout=0.3
        )

        assert events == [], (
            f"WorkerCorrectionNeeded must not appear on SSE stream, got: {events}"
        )

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 7: Multiple concurrent sessions receive independent events
# ---------------------------------------------------------------------------

def test_multiple_sessions_receive_independent_events():
    """With two concurrent SSE streams open (s1 and s2), publishing to each
    session delivers to the right stream only."""

    async def scenario():
        app, bus, hub = _build_app()
        s1_client = _StreamingASGIClient(app)
        s2_client = _StreamingASGIClient(app)

        s1_events: list[dict] = []
        s2_events: list[dict] = []
        s1_done = asyncio.Event()
        s2_done = asyncio.Event()

        async def _reader(cli, session_id, collected, done_ev):
            async with cli.stream(
                "GET", "/api/stream", query_string=f"session_id={session_id}"
            ) as ctx:
                buf = b""
                current_name: str | None = None
                async for chunk in ctx:
                    buf += chunk
                    while b"\r\n" in buf or b"\n" in buf:
                        if b"\r\n" in buf:
                            line_bytes, buf = buf.split(b"\r\n", 1)
                        else:
                            line_bytes, buf = buf.split(b"\n", 1)
                        line = line_bytes.decode("utf-8", errors="replace")
                        if line.startswith("event:"):
                            current_name = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            raw = line[len("data:"):].strip()
                            try:
                                payload = json.loads(raw)
                            except json.JSONDecodeError:
                                payload = raw
                            if current_name is not None:
                                collected.append(
                                    {"event": current_name, "data": payload}
                                )
                                current_name = None
                                if len(collected) >= 1:
                                    done_ev.set()
                                    return
                        elif line == "" or line.startswith(":"):
                            current_name = None

        t1 = asyncio.create_task(_reader(s1_client, "s1", s1_events, s1_done))
        t2 = asyncio.create_task(_reader(s2_client, "s2", s2_events, s2_done))

        # Let both readers open their streams and register queues.
        await asyncio.sleep(0.05)

        # Publish cross-session events.
        await bus.publish(
            WorkerCompleted(result="for-s1", mission_id="m1", session_id="s1")
        )
        await bus.publish(
            WorkerCompleted(result="for-s2", mission_id="m2", session_id="s2")
        )

        await asyncio.wait_for(s1_done.wait(), timeout=5.0)
        await asyncio.wait_for(s2_done.wait(), timeout=5.0)

        for t in (t1, t2):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        assert len(s1_events) == 1, f"s1 got: {s1_events}"
        assert s1_events[0]["data"]["text"] == "for-s1"
        assert len(s2_events) == 1, f"s2 got: {s2_events}"
        assert s2_events[0]["data"]["text"] == "for-s2"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 8: GET /api/stream endpoint returns text/event-stream content-type
# ---------------------------------------------------------------------------

def test_stream_endpoint_returns_event_stream_content_type():
    """GET /api/stream must respond 200 with text/event-stream content-type.

    Uses httpx.ASGITransport for the headers check. We start the request in
    a background task, immediately grab the streaming response headers, then
    cancel the task before httpx tries to buffer the (infinite) body.
    """

    async def scenario():
        app, bus, hub = _build_app()

        status_code_holder: list[int] = []
        ct_holder: list[str] = []
        headers_seen = asyncio.Event()

        # Use the direct ASGI interface to read the response.start message.
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "headers": [],
            "scheme": "http",
            "path": "/api/stream",
            "raw_path": b"/api/stream",
            "query_string": b"session_id=probe",
            "server": ("localhost", 8000),
            "client": ("127.0.0.1", 1234),
            "root_path": "",
        }

        async def receive():
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                status_code_holder.append(msg["status"])
                raw_headers = dict(msg.get("headers", []))
                ct = raw_headers.get(b"content-type", b"").decode()
                ct_holder.append(ct)
                headers_seen.set()

        app_task = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(headers_seen.wait(), timeout=5.0)
        app_task.cancel()
        try:
            await app_task
        except (asyncio.CancelledError, Exception):
            pass

        assert status_code_holder[0] == 200
        assert "text/event-stream" in ct_holder[0]

    asyncio.run(scenario())
