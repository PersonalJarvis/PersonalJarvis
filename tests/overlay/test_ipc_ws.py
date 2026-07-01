"""WS server (jarvis.overlay.server + bridge) and WS client (overlay.ipc_ws).

End-to-end round-trip over 127.0.0.1, plus backpressure/drop-policy
tests directly against the bridge outbox.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from overlay.ipc_ws import (
    BACKOFF_SCHEDULE,
    JITTER_FRACTION,
    WSClient,
    _backoff_with_jitter,
)
from overlay.schema import (
    ClickEnvelope,
    ClickPayload,
    HeartbeatEnvelope,
    HeartbeatPayload,
    IPCMessage,
    StateEnvelope,
    StatePayload,
)
from jarvis.overlay.bridge import OverlayBridge, _BoundedOutbox
from jarvis.overlay.server import IPCServerHandle, start_ipc_server

# pytest-asyncio ``asyncio_mode = "auto"`` (in pyproject.toml) marks
# async functions automatically — no module pytestmark needed.


# -----------------------------------------------------------------------------
# Backoff
# -----------------------------------------------------------------------------


def test_backoff_slot_progression() -> None:
    rng = random.Random(0)
    delays = [_backoff_with_jitter(i, rng=rng) for i in range(10)]
    assert len(delays) == 10
    # Slot 0..5 ramps up linearly, from 6 on capped at 30.
    assert all(d > 0 for d in delays)
    cap_band = (30 * (1 - JITTER_FRACTION), 30 * (1 + JITTER_FRACTION))
    assert cap_band[0] <= delays[-1] <= cap_band[1]


def test_backoff_jitter_within_bounds() -> None:
    rng = random.Random(42)
    for slot, base in enumerate(BACKOFF_SCHEDULE):
        for _ in range(20):
            d = _backoff_with_jitter(slot, rng=rng)
            assert base * (1 - JITTER_FRACTION) - 1e-9 <= d <= base * (1 + JITTER_FRACTION) + 1e-9


# -----------------------------------------------------------------------------
# Bounded outbox / drop policy (§10.4)
# -----------------------------------------------------------------------------


async def test_outbox_accepts_under_limit() -> None:
    box = _BoundedOutbox(maxsize=4)
    for i in range(4):
        assert box.put("click", f'{{"i":{i}}}'.encode()) is True
    assert len(box) == 4
    assert box.dropped == 0


async def test_outbox_drops_oldest_non_state_first() -> None:
    box = _BoundedOutbox(maxsize=3)
    box.put("state", b"S0")  # state
    box.put("cursor", b"C1")  # non-state, oldest non-state
    box.put("heartbeat", b"H2")  # non-state
    # Full. A new entry (regardless of type) must drop C1 first.
    assert box.put("click", b"X3") is True
    out = []
    while len(box):
        out.append(await box.get())
    assert b"S0" in out
    assert b"H2" in out
    assert b"X3" in out
    assert b"C1" not in out
    assert box.dropped == 1


async def test_outbox_drops_state_only_if_no_non_state() -> None:
    box = _BoundedOutbox(maxsize=2)
    box.put("state", b"S0")
    box.put("state", b"S1")
    # Full, all state. New state -> drop the oldest state.
    assert box.put("state", b"S2") is True
    out = [await box.get(), await box.get()]
    assert b"S0" not in out
    assert b"S1" in out and b"S2" in out


async def test_outbox_rejects_new_non_state_when_only_state_in_buf() -> None:
    box = _BoundedOutbox(maxsize=2)
    box.put("state", b"S0")
    box.put("state", b"S1")
    # Full, all state. New non-state -> gets dropped itself.
    assert box.put("cursor", b"C2") is False
    assert box.dropped == 1
    out = [await box.get(), await box.get()]
    assert b"S0" in out and b"S1" in out


# -----------------------------------------------------------------------------
# Bridge: Outbox + State-Cache
# -----------------------------------------------------------------------------


async def test_bridge_caches_last_state_for_resync() -> None:
    bridge = OverlayBridge()
    await bridge.start()
    try:
        bridge.emit_state("typing", intensity=0.7)
        assert bridge.last_state_envelope is not None
        assert bridge.last_state_envelope.payload.state == "typing"
        bridge.emit_state("idle")
        assert bridge.last_state_envelope.payload.state == "idle"
    finally:
        await bridge.stop()


async def test_bridge_send_nowait_returns_bool() -> None:
    bridge = OverlayBridge(outbound_queue_max=2)
    await bridge.start()
    try:
        # Two state frames fit in.
        e1 = StateEnvelope(payload=StatePayload(state="idle"))
        e2 = StateEnvelope(payload=StatePayload(state="typing"))
        assert bridge.send_nowait(e1) is True
        assert bridge.send_nowait(e2) is True
        # Third state -> evicts the oldest state, accepted.
        assert bridge.send_nowait(e1) is True
    finally:
        await bridge.stop()


# -----------------------------------------------------------------------------
# E2E: Server + Client
# -----------------------------------------------------------------------------


async def _start_test_server(port: int = 0) -> IPCServerHandle:
    return await start_ipc_server(
        host="127.0.0.1",
        port=18000,
        port_range_max=18020,
    )


async def test_server_picks_free_port() -> None:
    handle = await _start_test_server()
    try:
        assert 18000 <= handle.port <= 18020
    finally:
        await handle.stop()


async def test_server_raises_when_no_port_free() -> None:
    """When port_range_max < port -> immediate ValueError."""
    with pytest.raises(ValueError):
        await start_ipc_server(host="127.0.0.1", port=18100, port_range_max=18099)


async def test_client_connects_and_receives_state_resync() -> None:
    """Plan §10.5: first frame after connect is the last known state."""
    handle = await _start_test_server()
    handle.bridge.emit_state("clicking", intensity=1.0, reason="tool")
    try:
        received: list = []
        recv_event = asyncio.Event()

        async def on_msg(m: object) -> None:
            received.append(m)
            recv_event.set()

        client = WSClient(
            host="127.0.0.1",
            ports=[handle.port],
            heartbeat_interval_s=0.5,
            heartbeat_timeout_s=5.0,
            on_message=on_msg,
        )
        run_task = asyncio.create_task(client.run())
        try:
            connected = await client.wait_connected(timeout=3.0)
            assert connected, "client did not connect"
            await asyncio.wait_for(recv_event.wait(), timeout=3.0)
            assert any(
                isinstance(m, StateEnvelope) and m.payload.state == "clicking"
                for m in received
            )
        finally:
            await client.aclose()
            try:
                await asyncio.wait_for(run_task, timeout=2.0)
            except asyncio.TimeoutError:
                run_task.cancel()
    finally:
        await handle.stop()


async def test_client_reconnects_after_server_restart() -> None:
    """Server dies -> client reconnects with backoff (§10.5)."""
    handle1 = await _start_test_server()
    port = handle1.port

    async def on_msg(_m: object) -> None:
        pass

    client = WSClient(
        host="127.0.0.1",
        ports=[port],
        heartbeat_interval_s=0.2,
        heartbeat_timeout_s=2.0,
        on_message=on_msg,
        rng=random.Random(0),
    )
    run_task = asyncio.create_task(client.run())
    try:
        assert await client.wait_connected(timeout=3.0)
        first_count = client.connection_count
        # Stop the server.
        await handle1.stop()
        # Bring it back up with the same port.
        await asyncio.sleep(0.3)
        handle2 = await start_ipc_server(
            host="127.0.0.1", port=port, port_range_max=port
        )
        try:
            # Give the client time to reconnect (backoff <= 1.2s + jitter).
            for _ in range(40):
                await asyncio.sleep(0.1)
                if client.connection_count > first_count:
                    break
            assert client.connection_count > first_count
        finally:
            await handle2.stop()
    finally:
        await client.aclose()
        try:
            await asyncio.wait_for(run_task, timeout=2.0)
        except asyncio.TimeoutError:
            run_task.cancel()


async def test_invalid_json_dropped_and_logged(caplog) -> None:
    """Server must log + drop invalid JSON, not crash.

    We send garbage and still expect at least one valid
    StateEnvelope afterward (pushed after the garbage). Other frames
    (heartbeat, resync) may occur in between.
    """
    import websockets

    handle = await _start_test_server()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{handle.port}/overlay") as ws:
            await ws.send("definitely-not-json")
            handle.bridge.emit_state("idle")
            # Drain until the next StateEnvelope (max ~3s of heartbeats).
            seen_state = False
            for _ in range(30):
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    break
                msg = IPCMessage.validate_json(frame)
                if isinstance(msg, StateEnvelope) and msg.payload.state == "idle":
                    seen_state = True
                    break
            assert seen_state, "no StateEnvelope received after the garbage frame"
    finally:
        await handle.stop()


async def test_client_sends_heartbeats() -> None:
    """Client sends heartbeats that the server validates."""
    received_types: list[str] = []
    handle = await _start_test_server()

    async def collect(msg: object) -> None:
        received_types.append(getattr(msg, "type", "?"))

    handle.bridge.add_inbound_handler(collect)

    client = WSClient(
        host="127.0.0.1",
        ports=[handle.port],
        heartbeat_interval_s=0.1,
        heartbeat_timeout_s=5.0,
    )
    run_task = asyncio.create_task(client.run())
    try:
        assert await client.wait_connected(timeout=3.0)
        # Wait until at least one heartbeat has been received.
        for _ in range(40):
            await asyncio.sleep(0.05)
            if "heartbeat" in received_types:
                break
        assert "heartbeat" in received_types
    finally:
        await client.aclose()
        try:
            await asyncio.wait_for(run_task, timeout=2.0)
        except asyncio.TimeoutError:
            run_task.cancel()
        await handle.stop()
