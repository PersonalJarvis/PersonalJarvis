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
        reg._caps.clear()  # noqa: SLF001
    try:
        yield
    finally:
        with reg._lock:  # noqa: SLF001
            reg._caps.clear()  # noqa: SLF001
            reg._caps.update(snapshot)  # noqa: SLF001


@pytest.fixture
def wired_computer_use():
    """Declare Computer-Use WIRED for a test that drives the CU fast path.

    The fast path refuses before dispatch when no ``ComputerUseContext`` exists
    (GT-19) — the same guard the LLM tool path has carried since 2026-07-06.
    Tests that assert a DISPATCH therefore have to state that this machine has
    Computer-Use. They drive a fake tool executor and never reach the real
    harness, so sentinel dependencies are enough. Mirrors the autouse fixture in
    ``tests/unit/plugins/tool/test_computer_use_tool.py``. Request it explicitly;
    tests of the unwired path must NOT get it by accident.
    """
    from jarvis.harness.computer_use_context import (
        ComputerUseContext,
        set_computer_use_context,
    )

    set_computer_use_context(
        ComputerUseContext(
            vision_engine=object(), brain_manager=object(), tool_executor=object(),
        ),
    )
    yield
    set_computer_use_context(None)
