"""Computer-Use unit-test fixtures.

The capture path probes the LIVE macOS Screen-Recording TCC state before
every grab (``_require_macos_screen_recording_permission``). These unit tests
drive capture/engine logic through injected fake grabbers — the real TCC
state of the host must not decide their outcome (CI runners and dev shells
have no grant, so every capture-touching test would fail on real darwin
hosts while passing everywhere else). The gate's own behavior is covered by
``tests/unit/cu/test_capture_permission_gate``-style tests that patch the
permission port explicitly.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _screen_recording_gate_open(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neutralize the live TCC probe; a no-op off darwin by design.

    Tests that exercise the gate itself opt back in with
    ``@pytest.mark.real_tcc_gate`` (they fake the permission port and the
    platform explicitly, so they stay deterministic on every host).
    """
    if request.node.get_closest_marker("real_tcc_gate"):
        return
    import jarvis.cu.capture as capture

    monkeypatch.setattr(
        capture, "_require_macos_screen_recording_permission", lambda: None
    )


@pytest.fixture(autouse=True)
def _forget_learned_token_headroom() -> None:
    """Clear the per-model token-headroom memory between tests.

    ``brain_call`` remembers process-wide which (provider, model) pairs cannot
    finish their JSON inside the small per-call cap, so the next step starts at
    the headroom instead of re-discovering it. That memory is deliberately
    global; leaking it across tests would make a truncation test's outcome
    depend on execution order.
    """
    from jarvis.cu.brain_call import _reset_headroom_memory_for_tests

    _reset_headroom_memory_for_tests()
    yield
    _reset_headroom_memory_for_tests()
