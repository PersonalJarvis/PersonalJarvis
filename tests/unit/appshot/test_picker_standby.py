"""Renderer standby frees pixel references without constructing a GUI."""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWidgets")

from jarvis.appshot.picker import renderer  # noqa: E402


def test_release_capture_drops_frozen_pixels_and_blur_cache():
    window = SimpleNamespace(
        _frozen=object(), _patches=object(), _markup=object(), _scale=lambda: 1
    )
    renderer._SelectWindow.release_capture(window)
    assert window._frozen is None
    assert window._patches._frozen is None
    assert window._patches._cache == {}
    assert window._markup.sel is None


def test_resident_finish_releases_every_screen_without_quitting(monkeypatch):
    calls = []
    window = SimpleNamespace(
        hide=lambda: calls.append("hide"),
        release_capture=lambda: calls.append("release"),
        close=lambda: calls.append("close"),
        deleteLater=lambda: calls.append("delete"),
    )
    owner = SimpleNamespace(
        _app=SimpleNamespace(
            processEvents=lambda: calls.append("flush"), quit=lambda: calls.append("quit")
        ),
        _windows=[window], _done=False, _resident=True, _started=True,
        _focused=window, marking=window,
    )
    monkeypatch.setattr(renderer, "_emit", lambda payload: calls.append(payload))
    renderer.Picker.finish(owner, None, None)
    assert calls == [
        "hide", "flush", "release", "close", "delete",
        {"event": "selection", "cancelled": True},
    ]
    assert owner._windows == [] and owner._focused is None and owner.marking is None
    assert not owner._started
    renderer.Picker.finish(owner, None, None)
    assert len(calls) == 6, "duplicate finish cannot emit a second selection"
