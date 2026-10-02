"""The in-session runner must not cache a permission refusal as "no input backend".

``ScreenServer._get_actuator`` used to store ``str(exc)`` of the FIRST failure
forever, so after one ``PermissionNeededError`` the runner never asked
``get_actuator()`` again, even once the person had allowed Accessibility. A
permission refusal is a state that changes, not unavailability: it is retried per
action. Real unavailability (Wayland, headless) stays cached.
"""

from __future__ import annotations

import pytest

import jarvis.cu.actuate as actuate
import jarvis.cu.capture as cu_capture
from jarvis.cu.actuate import ActuationUnavailable, PermissionNeededError


@pytest.fixture
def screen_server_cls(monkeypatch: pytest.MonkeyPatch):
    """``ScreenServer``, importable even while ``jarvis.agent_screen.port`` is stale.

    ``jarvis/agent_screen/port.py`` imports ``RawCapture`` and ``raw_pixels`` from
    ``jarvis.cu.capture``, names that module no longer exports (they are private
    since e982c073c), so importing ANY ``jarvis.agent_screen`` module fails on the
    base commit. The runner under test never touches them; supplying the names here
    keeps this test about the actuator cache. Remove the shim once ``port.py`` is
    fixed.
    """
    for name in ("RawCapture", "raw_pixels"):
        if not hasattr(cu_capture, name):
            monkeypatch.setattr(cu_capture, name, object, raising=False)
    from jarvis.agent_screen.runner.server import ScreenServer  # noqa: PLC0415

    return ScreenServer


class _Result:
    """The slice of an ``EnsureResult`` the error reads."""

    agent_detail = "[permission_needed:accessibility] Do not answer the dialog; stop."


class _StubActuator:
    def __init__(self) -> None:
        self.typed: list[str] = []

    def type_text(self, text: str) -> None:
        self.typed.append(text)


class _ScriptedGetActuator:
    """``get_actuator`` that raises the scripted errors in order, then succeeds."""

    def __init__(self, *errors: Exception) -> None:
        self._errors = list(errors)
        self.calls = 0
        self.actuator = _StubActuator()

    def __call__(self) -> _StubActuator:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self.actuator


def test_permission_refusal_is_retried_on_the_next_action(
    monkeypatch: pytest.MonkeyPatch, screen_server_cls
) -> None:
    scripted = _ScriptedGetActuator(PermissionNeededError(_Result()))
    monkeypatch.setattr(actuate, "get_actuator", scripted)
    server = screen_server_cls()

    with pytest.raises(PermissionNeededError):
        server._get_actuator()
    assert server._actuator_error == ""

    # The person allowed Accessibility in the meantime: the next action works.
    assert server._get_actuator() is scripted.actuator
    assert scripted.calls == 2


def test_refused_action_reports_the_agent_text_then_succeeds_after_the_grant(
    monkeypatch: pytest.MonkeyPatch, screen_server_cls
) -> None:
    scripted = _ScriptedGetActuator(PermissionNeededError(_Result()))
    monkeypatch.setattr(actuate, "get_actuator", scripted)
    server = screen_server_cls()
    params = {"action": "type_text", "params": {"text": "hi"}}

    refused, _ = server.act(params)
    assert refused["ok"] is False
    assert "[permission_needed:accessibility]" in refused["error"]

    allowed, _ = server.act(params)
    assert allowed["ok"] is True
    assert scripted.actuator.typed == ["hi"]


def test_real_unavailability_stays_cached(
    monkeypatch: pytest.MonkeyPatch, screen_server_cls
) -> None:
    scripted = _ScriptedGetActuator(ActuationUnavailable("No display server is available."))
    monkeypatch.setattr(actuate, "get_actuator", scripted)
    server = screen_server_cls()

    for _ in range(3):
        with pytest.raises(RuntimeError, match="No display server is available"):
            server._get_actuator()
    assert scripted.calls == 1


def test_a_failing_import_is_cached_with_the_real_import_error(
    monkeypatch: pytest.MonkeyPatch, screen_server_cls
) -> None:
    # A missing name makes ``from jarvis.cu.actuate import ...`` raise ImportError
    # before ``PermissionNeededError`` is bound: the error must not turn into an
    # UnboundLocalError, and it is cached like any other unavailability.
    monkeypatch.delattr(actuate, "get_actuator")
    server = screen_server_cls()

    for _ in range(2):
        with pytest.raises(RuntimeError, match="cannot import name 'get_actuator'") as excinfo:
            server._get_actuator()
        assert "UnboundLocalError" not in str(excinfo.value)
    assert "cannot import name 'get_actuator'" in server._actuator_error


def test_a_working_actuator_is_reused(monkeypatch: pytest.MonkeyPatch, screen_server_cls) -> None:
    scripted = _ScriptedGetActuator()
    monkeypatch.setattr(actuate, "get_actuator", scripted)
    server = screen_server_cls()

    assert server._get_actuator() is server._get_actuator()
    assert scripted.calls == 1


def test_permission_needed_error_is_exported_from_the_actuate_package() -> None:
    assert actuate.PermissionNeededError is PermissionNeededError
    assert "PermissionNeededError" in actuate.__all__
    assert issubclass(PermissionNeededError, ActuationUnavailable)
