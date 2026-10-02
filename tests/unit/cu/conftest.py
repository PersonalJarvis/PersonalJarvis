"""Computer-Use unit-test fixtures.

The capture path reads the LIVE macOS Screen-Recording TCC state: the perception
entry asks for a missing grant (``_ensure_screen_recording_for_perception``),
the helpers read it silently (``_helper_capture_blocked``), the engine's per-action
gate raises without asking (``_require_macos_screen_recording_permission``) and a
blank frame is sanity-checked (``_verify_perception_frame``). These unit tests
drive capture/engine logic through injected fake grabbers — the real TCC
state of the host must not decide their outcome (CI runners and dev shells
have no grant, so every capture-touching test would fail on real darwin
hosts while passing everywhere else). The permission behavior itself is covered
by the ``real_tcc_gate`` tests, which run the real permission service on a
``FakeTCC`` and so stay deterministic on every host.

The engine's per-action guard (``engine._blocked_before_dispatch``: a deep Screen
Recording read plus the system-consent window list) is neutralised by the
``patched`` fixture of ``test_engine_loop.py``, not here: the tests of the guard
call ``_dispatch_tool`` directly through a ``FakeTCC`` world and need it live.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _screen_recording_gate_open(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neutralize the live TCC reads; a no-op off darwin by design.

    Tests that exercise the permission behavior itself opt back in with
    ``@pytest.mark.real_tcc_gate`` (they fake the permission port and the
    platform explicitly, so they stay deterministic on every host).
    """
    if request.node.get_closest_marker("real_tcc_gate"):
        return
    import jarvis.cu.capture as capture

    monkeypatch.setattr(
        capture, "_require_macos_screen_recording_permission", lambda: None
    )
    monkeypatch.setattr(
        capture, "_ensure_screen_recording_for_perception", lambda: None
    )
    monkeypatch.setattr(capture, "_verify_perception_frame", lambda _raw: None)
    monkeypatch.setattr(capture, "_helper_capture_blocked", lambda: False)


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
