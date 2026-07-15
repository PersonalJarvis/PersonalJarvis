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
