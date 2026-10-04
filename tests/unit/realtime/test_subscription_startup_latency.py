"""Startup overlap for the additive subscription adapter when it is installed."""

import asyncio

import pytest

fakes = pytest.importorskip("tests.fakes.fake_subscription_live_wire")


async def test_subscription_answer_precedes_control_connection():
    wire = fakes.FakeSubscriptionLiveWire()
    provider = wire.provider()
    config = wire.config()
    attaching = asyncio.Event()
    release = asyncio.Event()
    answers = []

    async def answer(sdp):
        answers.append(sdp)

    async def connect(*args, **kwargs):
        attaching.set()
        await release.wait()
        return wire.socket

    config.on_transport_ready = answer
    provider._websocket_connect = connect
    task = asyncio.create_task(provider.open_session(config))
    try:
        await asyncio.wait_for(attaching.wait(), 2)
        assert answers == [fakes.SDP]
        assert not task.done()
        assert len(wire.requests) == 1
    finally:
        release.set()
        result = await task
        await result.close()


async def test_cancelled_subscription_signalling_retires_the_same_allocation():
    wire = fakes.FakeSubscriptionLiveWire()
    config = wire.config()

    async def answer(sdp):
        raise asyncio.CancelledError()

    config.on_transport_ready = answer
    with pytest.raises(asyncio.CancelledError):
        await wire.provider().open_session(config)
    assert len(wire.requests) == 1
    assert wire.socket.sent == [{"type": "session.close"}]
    assert wire.socket.closed == 1
