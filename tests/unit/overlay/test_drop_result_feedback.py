"""The bar must tell the user what became of the file they dropped on it.

A drop travels one way — surface → bridge → backend — and the intake that
decides whether the content became context runs later, on the asyncio loop. So
without a return leg the bar had nothing to confirm with and stayed silent
after every drop, accepted or not. These pin the return leg itself and the two
ways it used to be defeated: a settled idle bar that had stopped painting, and
a verdict that never expired.
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from jarvis.overlay import drop_bridge
from jarvis.ui.jarvisbar import renderer
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


@pytest.fixture(autouse=True)
def _clean_bridge() -> Any:
    """Never leak a process-global handler or sink across tests."""
    drop_bridge.set_drop_handler(None)
    drop_bridge.set_drop_result_sink(None)
    yield
    drop_bridge.set_drop_handler(None)
    drop_bridge.set_drop_result_sink(None)


# --------------------------------------------------------------------- #
# The return leg                                                        #
# --------------------------------------------------------------------- #
def test_a_verdict_reaches_the_registered_sink() -> None:
    seen: list[bool] = []
    drop_bridge.set_drop_result_sink(seen.append)

    assert drop_bridge.report_drop_result(True) is True
    assert drop_bridge.report_drop_result(False) is True
    assert seen == [True, False]


def test_reporting_without_a_sink_is_a_quiet_no_op() -> None:
    """Headless, or a bar that never registered a drop target."""
    assert drop_bridge.report_drop_result(True) is False


def test_a_broken_sink_never_propagates_into_the_backend_turn() -> None:
    def _boom(_accepted: bool) -> None:
        raise RuntimeError("bar is mid-teardown")

    drop_bridge.set_drop_result_sink(_boom)

    # Still reports as delivered — a cosmetic animation failing must not make
    # the intake that produced it look failed.
    assert drop_bridge.report_drop_result(True) is True


# --------------------------------------------------------------------- #
# The bar's own drop state machine                                      #
# --------------------------------------------------------------------- #
def _bar() -> JarvisBarOverlay:
    """An unstarted bar: the drop state machine is plain attributes, so it is
    exercisable without a Tk root or a display."""
    return JarvisBarOverlay()


def test_a_hovering_payload_arms_the_bar() -> None:
    bar = _bar()
    bar._set_drop_active(True)

    assert bar._current_drop_visual() == renderer.DROP_STATE_ARMED


def test_a_drag_that_leaves_without_landing_disarms_the_bar() -> None:
    bar = _bar()
    bar._set_drop_active(True)
    bar._set_drop_active(False)

    assert bar._current_drop_visual() == renderer.DROP_STATE_NONE


def test_the_landing_drop_does_not_erase_its_own_confirmation() -> None:
    """Ordering trap: the drop reports drag-state False, and the verdict can
    arrive before that callback runs. Clearing on it unconditionally would wipe
    the confirmation the instant it appeared."""
    bar = _bar()
    bar._set_drop_active(True)
    bar._set_drop_visual(renderer.DROP_STATE_OK)  # verdict lands first
    bar._set_drop_active(False)  # ...then the drag stands down

    assert bar._current_drop_visual() == renderer.DROP_STATE_OK


def test_a_finished_confirmation_expires_on_its_own() -> None:
    bar = _bar()
    bar._set_drop_visual(renderer.DROP_STATE_OK)
    # Backdate the start so the whole timeline has elapsed.
    bar._drop_visual_t0 = time.perf_counter() - renderer.DROP_CONFIRM_TOTAL_S - 0.1

    assert bar._current_drop_visual() == renderer.DROP_STATE_NONE


def test_a_rejected_drop_is_shown_rather_than_swallowed() -> None:
    """Nothing usable in the payload is still an answer the user needs."""
    bar = _bar()
    bar._set_drop_visual(renderer.DROP_STATE_REJECTED)

    assert bar._current_drop_visual() == renderer.DROP_STATE_REJECTED


# --------------------------------------------------------------------- #
# The idle-repaint trap                                                 #
# --------------------------------------------------------------------- #
def test_a_drop_state_change_wakes_a_settled_bar() -> None:
    """THE defect this feature would otherwise have shipped with.

    The bar stops repainting after ~30 static idle ticks. A confirmation
    animation lives entirely inside those skipped frames, so unless a drop
    invalidates the fast path the tick is computed and never drawn.
    """
    bar = _bar()
    bar._static_tick_key = ("idle", False, False, renderer.DROP_STATE_NONE)
    bar._static_tick_count = 999

    bar._set_drop_visual(renderer.DROP_STATE_ARMED)

    assert bar._static_tick_key is None
    assert bar._static_tick_count == 0


def test_the_drop_state_is_part_of_the_static_frame_key() -> None:
    """Second half of the same guard: even if the counter survived, the key
    itself must change so the settle count restarts on every transition."""
    bar = _bar()
    assert bar._static_tick_key is None  # 4-tuple shape, seeded by the loop

    # The renderer's own contract: a frame with a drop differs from one without.
    r = renderer.JarvisBarRenderer()
    for _ in range(12):
        r.render(0.0, "idle", 0.0)
    plain = r.render(0.0, "idle", 0.0)
    armed = r.render(0.0, "idle", 0.0, drop_state=renderer.DROP_STATE_ARMED)

    assert plain.tobytes() != armed.tobytes()


def test_notify_drop_result_on_an_unstarted_bar_is_harmless() -> None:
    """The verdict can arrive after a surface swap or during teardown."""
    bar = _bar()
    bar.notify_drop_result(True)  # must not raise

    # Nothing was queued onto a root that does not exist.
    assert bar._drop_visual == renderer.DROP_STATE_NONE
