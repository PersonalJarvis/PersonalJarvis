"""Fail-open capture guard around CU frame grabs."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from jarvis.cu.indicator import capture_guard


@pytest.fixture(autouse=True)
def _clean_hook():
    capture_guard.unregister_hook()
    yield
    capture_guard.unregister_hook()


def test_no_hook_is_a_passthrough() -> None:
    with capture_guard.indicator_suppressed():
        pass  # must not raise, must not block


def test_hook_wraps_the_grab() -> None:
    events: list[str] = []

    @contextmanager
    def hook():
        events.append("blank")
        try:
            yield
        finally:
            events.append("unblank")

    capture_guard.register_hook(hook)
    with capture_guard.indicator_suppressed():
        events.append("grab")
    assert events == ["blank", "grab", "unblank"]


def test_hook_factory_raising_fails_open() -> None:
    def broken_hook():
        raise RuntimeError("sidecar gone")

    capture_guard.register_hook(broken_hook)
    ran = False
    with capture_guard.indicator_suppressed():
        ran = True
    assert ran is True


def test_unregister_restores_passthrough() -> None:
    @contextmanager
    def hook():
        raise AssertionError("must not be called after unregister")
        yield  # pragma: no cover

    capture_guard.register_hook(hook)
    capture_guard.unregister_hook()
    with capture_guard.indicator_suppressed():
        pass


def test_unblank_failure_does_not_kill_the_grab() -> None:
    """A hook whose __exit__ raises (dead sidecar, late ack) must be
    swallowed. The previous ``with cm: yield`` + ``except: yield`` shape
    resumed the generator into a SECOND yield here, which @contextmanager
    turned into ``RuntimeError: generator didn't stop`` — killing the very
    frame grab the guard exists to protect (macOS/Linux path)."""

    @contextmanager
    def hook():
        yield
        raise RuntimeError("sidecar died before the unblank ack")

    capture_guard.register_hook(hook)
    ran = False
    with capture_guard.indicator_suppressed():
        ran = True
    assert ran is True


def test_grab_exception_propagates_and_still_unblanks() -> None:
    """A failing grab BODY must surface its own error (never a masked
    RuntimeError from the guard), and the indicator must still be restored."""
    events: list[str] = []

    @contextmanager
    def hook():
        events.append("blank")
        try:
            yield
        finally:
            events.append("unblank")

    capture_guard.register_hook(hook)
    with pytest.raises(ValueError, match="grab exploded"):
        with capture_guard.indicator_suppressed():
            raise ValueError("grab exploded")
    assert events == ["blank", "unblank"]


def test_blank_failure_fails_open_and_still_grabs() -> None:
    """A hook whose __enter__ raises must fail open: the grab runs, no
    error escapes, and no half-entered context is left behind."""

    @contextmanager
    def hook():
        raise RuntimeError("blank IPC broke")
        yield  # pragma: no cover

    capture_guard.register_hook(hook)
    ran = False
    with capture_guard.indicator_suppressed():
        ran = True
    assert ran is True
