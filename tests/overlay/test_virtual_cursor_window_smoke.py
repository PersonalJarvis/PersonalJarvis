"""Smoke test for the Tk virtual-cursor window.

Opt-in only: Tk runs in a daemon thread, and pytest keeps the process alive
afterwards, so finalising the Tcl interpreter from the main thread panics
("async handler deleted by the wrong thread"). That is harmless in production
(the process exits hard) but crashes the test runner — so this test only runs
when ``JARVIS_GUI_TEST=1`` is set and there is an interactive desktop. The
display-independent behaviour is fully covered by ``test_virtual_cursor.py``;
for a visual check run ``python scripts/virtual_cursor_demo.py``.
"""
from __future__ import annotations

import os
import time

import pytest

from jarvis.overlay.virtual_cursor import get_virtual_cursor, set_virtual_cursor

pytestmark = pytest.mark.skipif(
    os.environ.get("JARVIS_GUI_TEST") != "1",
    reason="GUI smoke test — set JARVIS_GUI_TEST=1 on an interactive desktop to run",
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    set_virtual_cursor(None)
    yield
    set_virtual_cursor(None)


def test_window_starts_registers_and_draws_without_error():
    from ui.orb.virtual_cursor_window import TkVirtualCursor

    cur = TkVirtualCursor()
    started = cur.start(timeout_s=8.0)
    if not started:
        pytest.skip("no display / Tk unavailable on this host (headless)")

    try:
        # Becomes the process-wide cursor so the click tools feed it.
        assert get_virtual_cursor() is cur
        # Drive a move and a click through the public API from this thread.
        cur.show_move(400, 300)
        cur.show_click(640, 360, button="left", double=False)
        cur.show_click(200, 200, button="left", double=True)
        time.sleep(0.2)  # let a few render ticks run
    finally:
        cur.shutdown()

    # After shutdown the no-op object is restored.
    from jarvis.overlay.virtual_cursor import NullVirtualCursor

    assert isinstance(get_virtual_cursor(), NullVirtualCursor)
