"""The system folder window opens where the SERVER runs — so only locally.

Anyone reaching the UI from a phone, a second laptop, or a tunnelled URL would
otherwise pop a modal window onto a screen nobody is watching, where it would sit
and wait. Worse, they could not see or answer it, so the request would hang until
its timeout while the desktop's owner found a dialog they never asked for.

The route therefore answers the question twice: the capability probe says "not
available, and here is why" so no button ever appears, and the action itself
refuses outright in case one does.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.agentic_ide import native_picker
from jarvis.ui.web import agentic_ide_routes as routes


def _request() -> SimpleNamespace:
    return SimpleNamespace(scope={})


async def test_a_remote_browser_is_told_the_window_would_open_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "is_loopback_request", lambda _scope: False)

    probe = await routes.native_picker_support(_request())

    assert probe.available is False
    assert probe.reason and "computer running Jarvis" in probe.reason


async def test_a_remote_browser_cannot_open_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "is_loopback_request", lambda _scope: False)

    def _explode(**_kwargs: object) -> None:
        raise AssertionError("no window may be opened for a remote caller")

    monkeypatch.setattr(native_picker, "choose_folder", _explode)

    with pytest.raises(HTTPException) as excinfo:
        await routes.open_native_picker(_request(), routes.NativePickRequest())

    assert excinfo.value.status_code == 403


async def test_locally_the_probe_reports_what_the_machine_can_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "is_loopback_request", lambda _scope: True)
    monkeypatch.setattr(
        native_picker, "support", lambda: native_picker.PickerSupport(True, "osascript")
    )

    probe = await routes.native_picker_support(_request())

    assert probe.available is True
    assert probe.backend == "osascript"


async def test_the_chosen_folder_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "is_loopback_request", lambda _scope: True)
    monkeypatch.setattr(
        native_picker,
        "choose_folder",
        lambda *, start: native_picker.PickResult(path=f"/picked/{start}"),
    )

    result = await routes.open_native_picker(
        _request(), routes.NativePickRequest(start="/home/ruben")
    )

    assert result.path == "/picked//home/ruben"
    assert result.cancelled is False


async def test_cancelling_is_a_normal_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "is_loopback_request", lambda _scope: True)
    monkeypatch.setattr(
        native_picker, "choose_folder", lambda **_k: native_picker.PickResult(cancelled=True)
    )

    result = await routes.open_native_picker(_request(), routes.NativePickRequest())

    assert result.cancelled is True
    assert result.path is None
    assert result.error is None


async def test_a_second_window_is_refused_while_one_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stacked invisible dialogs are worse than a refusal: the user ends up
    clicking through windows they never asked for."""
    monkeypatch.setattr(routes, "is_loopback_request", lambda _scope: True)
    await routes._native_picker_lock.acquire()
    try:
        with pytest.raises(HTTPException) as excinfo:
            await routes.open_native_picker(_request(), routes.NativePickRequest())
    finally:
        routes._native_picker_lock.release()

    assert excinfo.value.status_code == 409
