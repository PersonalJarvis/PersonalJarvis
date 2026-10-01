"""Managed UI failures retain their details and their Continue/Quit behavior."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from jarvis.ui import winforms_errors as errors


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def fire(self, args):
        for handler in self.handlers[:]:
            handler(None, args)


@pytest.fixture
def forms(monkeypatch):
    monkeypatch.setattr(errors, "_handlers", {})
    current = SimpleNamespace(ManagedThreadId=42)
    monkeypatch.setitem(sys.modules, "System.Threading", SimpleNamespace(
        Thread=SimpleNamespace(CurrentThread=current),
    ))
    calls = []

    class Dialog:
        def __init__(self, exception):
            calls.append(("dialog", exception))

        def ShowDialog(self):
            return api.result

        def Dispose(self):
            calls.append(("dispose", None))

    api = SimpleNamespace(
        Application=SimpleNamespace(
            ThreadException=Event(), ThreadExit=Event(),
            Exit=lambda: calls.append(("exit", None)),
        ),
        ThreadExceptionDialog=Dialog,
        DialogResult=SimpleNamespace(Abort="abort"),
        result="ignore", calls=calls, current=current,
    )
    return api


def test_managed_details_logged_before_continue_dialog(forms, monkeypatch, caplog):
    exception = SimpleNamespace(ToString=lambda: "OuterException\nInnerException\n at CloseWindow")
    original = errors._present_exception

    def present(api, exc):
        assert "InnerException\n at CloseWindow" in caplog.text
        original(api, exc)

    monkeypatch.setattr(errors, "_present_exception", present)
    errors._subscribe(forms, 42)
    forms.Application.ThreadException.fire(SimpleNamespace(Exception=exception))
    assert forms.calls == [("dialog", exception), ("dispose", None)]


def test_quit_runs_application_exit_after_disposing_dialog(forms):
    forms.result = "abort"
    exception = SimpleNamespace(ToString=lambda: "Close failed")
    errors._subscribe(forms, 42)
    forms.Application.ThreadException.fire(SimpleNamespace(Exception=exception))
    assert forms.calls == [("dialog", exception), ("dispose", None), ("exit", None)]


def test_managed_error_details_redact_credentials(caplog):
    credential_example = "example-credential-value"
    exception = SimpleNamespace(
        ToString=lambda: f"InvalidOperationException: password={credential_example}"
    )
    errors._report_exception(exception)
    assert credential_example not in caplog.text
    assert "<redacted:labelled_secret>" in caplog.text


def test_partial_subscription_is_rolled_back(forms):
    class UnavailableEvent:
        def __iadd__(self, _handler):
            raise RuntimeError("event unavailable")

    forms.Application.ThreadExit = UnavailableEvent()
    with pytest.raises(RuntimeError, match="event unavailable"):
        errors._subscribe(forms, 42)
    assert not forms.Application.ThreadException.handlers
    assert not errors._handlers


def test_logging_failure_still_presents_original_exception(forms, monkeypatch):
    def broken(_exc):
        raise OSError("log unavailable")

    monkeypatch.setattr(errors, "_report_exception", broken)
    exception = object()
    errors._subscribe(forms, 42)
    forms.Application.ThreadException.fire(SimpleNamespace(Exception=exception))
    assert forms.calls[0] == ("dialog", exception)


def test_duplicate_windows_do_not_duplicate_handler_and_thread_exit_releases_it(forms):
    errors._subscribe(forms, 42)
    errors._subscribe(forms, 42)
    assert len(forms.Application.ThreadException.handlers) == 1
    forms.current.ManagedThreadId = 99
    forms.Application.ThreadExit.fire(None)
    assert 42 in errors._handlers
    forms.current.ManagedThreadId = 42
    forms.Application.ThreadExit.fire(None)
    assert not errors._handlers
    assert not forms.Application.ThreadException.handlers
    assert not forms.Application.ThreadExit.handlers


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_non_windows_does_not_touch_native_dependencies(monkeypatch, platform):
    monkeypatch.setattr(errors.sys, "platform", platform)
    errors.register_winforms_error_logging(object())


def test_install_waits_for_synchronous_gui_event(monkeypatch):
    monkeypatch.setattr(errors.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(errors, "_install_on_gui_thread", lambda: calls.append("installed"))
    event = Event()
    window = SimpleNamespace(events=SimpleNamespace(before_show=event))
    errors.register_winforms_error_logging(window)
    assert calls == []
    event.handlers[0]()
    assert calls == ["installed"]
