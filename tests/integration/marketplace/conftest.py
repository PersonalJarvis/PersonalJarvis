"""Isolate the process-wide CapabilityRegistry for marketplace integration tests.

See the unit-test sibling conftest for the rationale: constructing an
``MCPToolAdapter`` registers a ``Capability`` into the global singleton; we
snapshot + restore around every test so it never leaks into other modules.
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
