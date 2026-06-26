"""A warming FastBootstrap must NOT hold a WS handshake open; it must
accept-then-close with code 1013 so the client gets a fast, readable
"try again later" and reconnects (instead of the browser timing out the
pending handshake -> escalating backoff -> a long spurious 'OFFLINE')."""
from __future__ import annotations

import asyncio
import logging

import pytest
import websockets

from jarvis.ui.web.fast_bootstrap import FastBootstrap


@pytest.mark.asyncio
async def test_warming_ws_is_fast_closed_with_1013() -> None:
    logging.disable(logging.CRITICAL)
    bs = FastBootstrap()
    await bs.serve("127.0.0.1", 47995)  # NOT set_app -> warming
    try:
        # Must connect quickly (handshake accepted) -- the old hold made this
        # time out. open_timeout well under the old 120s hold proves no-hold.
        async with websockets.connect(
            "ws://127.0.0.1:47995/ws", open_timeout=3
        ) as ws:
            with pytest.raises(websockets.ConnectionClosed) as exc:
                await asyncio.wait_for(ws.recv(), timeout=3)
            assert exc.value.code == 1013
    finally:
        await bs.stop()
        logging.disable(logging.NOTSET)
