"""Unit tests for :mod:`jarvis.ui.web.wiki_ws`.

These tests stand up a minimal FastAPI app with the wiki-ws router and
a real :class:`EventBus` on ``app.state``. We exercise the WS forwarding
path with FastAPI's :class:`TestClient`, which gives synchronous WS
semantics via the ``websocket_connect`` context manager.

Anti-patterns avoided:
- AP-5: no mocked bus or transport; real :class:`EventBus`, real WS.
- AP-6: the test app uses the shared bus instance throughout.
- AP-8: every test waits for explicit observable state (received JSON
  or unsubscribe side-effect) rather than ``asyncio.sleep``.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.events import WikiPageChanged
from jarvis.ui.web.wiki_ws import router as wiki_ws_router


def _make_app() -> tuple[FastAPI, EventBus]:
    """Construct a FastAPI app with the wiki-ws router and a fresh bus."""
    app = FastAPI()
    bus = EventBus()
    app.state.bus = bus
    app.include_router(wiki_ws_router)
    return app, bus


@pytest.fixture
def client_bus() -> Iterator[tuple[TestClient, EventBus]]:
    """A TestClient against a fresh app + bus."""
    app, bus = _make_app()
    with TestClient(app) as client:
        yield client, bus


def _publish_from_thread(client: TestClient, bus: EventBus, event: Any) -> None:
    """Publish on the bus from the test thread, on the server's own loop.

    The original implementation called ``asyncio.run(bus.publish(event))``
    from the test thread, which spawned a fresh event loop. The WS
    endpoint on the server thread owns an ``asyncio.Queue`` bound to the
    server loop; calling ``queue.put_nowait`` from the test loop scheduled
    the waiter wake-up via ``call_soon`` on the foreign loop, which is
    not thread-safe — the server loop never woke up and ``receive_json``
    blocked forever (four of five tests hung). Fixed 2026-05-17.

    Fix: route the publish through ``client.portal`` — a starlette/anyio
    :class:`anyio.from_thread.BlockingPortal` exposed by FastAPI's
    :class:`TestClient`. ``portal.call(bus.publish, event)`` runs the
    coroutine on the **server** loop, so the queue's loop affinity is
    respected and the WS handler wakes naturally.
    """
    client.portal.call(bus.publish, event)


def test_publishes_forwarded_to_single_client(client_bus):
    """One event on the bus arrives as one JSON frame on one client."""
    client, bus = client_bus
    with client.websocket_connect("/api/wiki/live") as ws:
        _publish_from_thread(
            client,
            bus,
            WikiPageChanged(slug="harald", path="entities/harald.md", kind="modified"),
        )
        msg = ws.receive_json()
        assert msg == {
            "type": "page_changed",
            "slug": "harald",
            "path": "entities/harald.md",
            "kind": "modified",
        }


def test_multiple_clients_receive_the_same_event(client_bus):
    """Three connected clients each receive the same single event."""
    client, bus = client_bus
    with contextlib.ExitStack() as stack:
        sockets = [
            stack.enter_context(client.websocket_connect("/api/wiki/live"))
            for _ in range(3)
        ]
        _publish_from_thread(
            client,
            bus,
            WikiPageChanged(slug="ruben", path="entities/ruben.md", kind="created"),
        )
        for ws in sockets:
            msg = ws.receive_json()
            assert msg["slug"] == "ruben"
            assert msg["path"] == "entities/ruben.md"
            assert msg["kind"] == "created"
            assert msg["type"] == "page_changed"


def test_disconnect_unsubscribes_from_bus(client_bus):
    """A client that closes its socket must not leave a subscriber behind."""
    client, bus = client_bus

    def _count_subs() -> int:
        return len(bus._subscribers.get(WikiPageChanged, []))  # noqa: SLF001

    assert _count_subs() == 0

    with client.websocket_connect("/api/wiki/live") as ws:
        # Push one event so the WS handler has run through accept ->
        # subscribe -> send_json at least once.
        _publish_from_thread(
            client,
            bus,
            WikiPageChanged(slug="x", path="entities/x.md", kind="created"),
        )
        msg = ws.receive_json()
        assert msg["slug"] == "x"
        # Subscriber is alive while the socket is open.
        assert _count_subs() == 1

    # After the context manager closes the socket, the finally-block in
    # the endpoint must remove our subscriber. The cleanup runs on the
    # server loop — give it a moment to complete.
    deadline = 0
    while _count_subs() != 0 and deadline < 50:
        # Poll the subscriber dict; the TestClient runs the cleanup on a
        # background loop and we cannot block waiting on it.
        deadline += 1
        import time as _t
        _t.sleep(0.05)
    assert _count_subs() == 0, (
        "WS disconnect must unsubscribe the WikiPageChanged subscriber"
    )


def test_disconnect_without_any_event_still_unsubscribes(client_bus):
    """FIX 3: an idle tab that closes before any wiki event ever fires must
    still be unsubscribed promptly.

    Before the two-task race (a reader awaiting ``ws.receive_text()``
    racing the queue forward via ``asyncio.wait``), the forwarding loop
    only ever woke up on ``queue.get()`` — so a tab closed with no further
    wiki events left its subscriber (and task) leaked for the rest of the
    server's lifetime.
    """
    client, bus = client_bus

    def _count_subs() -> int:
        return len(bus._subscribers.get(WikiPageChanged, []))  # noqa: SLF001

    assert _count_subs() == 0

    with client.websocket_connect("/api/wiki/live") as _ws:
        # Poll for the subscription to become visible before closing, so we
        # don't race the server coroutine's own startup.
        deadline = 0
        while _count_subs() == 0 and deadline < 50:
            deadline += 1
            import time as _t
            _t.sleep(0.02)
        assert _count_subs() == 1

    # No WikiPageChanged was ever published — the only signal available to
    # the endpoint is the client closing the socket.
    deadline = 0
    while _count_subs() != 0 and deadline < 50:
        deadline += 1
        import time as _t
        _t.sleep(0.05)
    assert _count_subs() == 0, (
        "an idle WS with no wiki events must still unsubscribe on disconnect"
    )


def test_unrelated_event_does_not_appear(client_bus):
    """Events of a different type are not forwarded."""
    from jarvis.core.events import SystemStateChanged

    client, bus = client_bus
    with client.websocket_connect("/api/wiki/live") as ws:
        # Publish an unrelated event; the WS subscriber must ignore it.
        _publish_from_thread(
            client,
            bus,
            SystemStateChanged(new_state="LISTENING", previous="IDLE"),
        )
        # Now publish a real WikiPageChanged so we have a positive
        # signal to wait on, proving the unrelated one didn't slip in
        # first.
        _publish_from_thread(
            client,
            bus,
            WikiPageChanged(slug="harald", path="entities/harald.md", kind="modified"),
        )
        msg = ws.receive_json()
        assert msg["slug"] == "harald"
        # No further events were published, so the receive queue
        # should be empty. We can't usefully assert "nothing arrives"
        # without a timeout knob on receive_json, so we just confirm
        # the first event was the wiki one (the unrelated event was
        # not forwarded — if it had been, this would have been the
        # first frame received above).


def test_bus_missing_closes_immediately():
    """When ``app.state.bus`` is None, the WS closes without sending data."""
    app = FastAPI()
    app.state.bus = None
    app.include_router(wiki_ws_router)
    with TestClient(app) as client:
        # The server closes with code 1011 after accept(). The
        # TestClient surfaces this either by yielding an immediate
        # disconnect on receive_json or by raising on context entry —
        # depending on Starlette version.
        try:
            with client.websocket_connect("/api/wiki/live") as ws:
                with pytest.raises(Exception):
                    ws.receive_json()
        except Exception:
            # Acceptable: some Starlette versions surface the close as
            # an exception on enter.
            pass


__all__: list[str] = []
