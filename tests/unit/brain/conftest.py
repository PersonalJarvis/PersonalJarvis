"""Isolate the process-wide CapabilityRegistry across brain tests.

Some brain tests seed or register capabilities into the global singleton
(``jarvis.core.capabilities.get_registry()``) — e.g. via ``seed_registry`` or
an ``MCPToolAdapter``. The routing suite's unsupported-intent tests assume a
fresh registry (they pass in isolation), so leaked capabilities from an
earlier test make a full-directory run order-dependent (pre-existing flakiness;
the same class fixed for tests/unit/marketplace). Snapshot + restore the
registry's ``_caps`` around every brain test so no test leaks into another.
"""
import pytest

from jarvis.core.capabilities import get_registry


@pytest.fixture(autouse=True)
def _isolate_capability_registry():
    reg = get_registry()
    with reg._lock:  # noqa: SLF001 — test-only snapshot of the singleton
        snapshot = dict(reg._caps)  # noqa: SLF001
    try:
        yield
    finally:
        with reg._lock:  # noqa: SLF001
            reg._caps.clear()  # noqa: SLF001
            reg._caps.update(snapshot)  # noqa: SLF001
