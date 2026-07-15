"""Action-time foreground binding for the visible Windows click glide."""
from __future__ import annotations

import types

import pytest

from jarvis.plugins.tool import click as click_mod


def test_windows_glide_refuses_button_down_after_foreground_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = []

    class _Actuator:
        def __init__(self) -> None:
            self.current = (0, 0)
            self.clicked = False
            instances.append(self)

        def move(self, x: int, y: int) -> None:
            self.current = (x, y)

        def cursor_pos(self) -> tuple[int, int]:
            return self.current

        def click_at_cursor(self, **_kwargs) -> None:
            self.clicked = True

    monkeypatch.setattr(click_mod, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(click_mod, "glide_os_cursor", lambda _x, _y: None)
    monkeypatch.setattr(click_mod, "_window_signature_matches", lambda _expected: False)
    monkeypatch.setattr(
        click_mod,
        "get_virtual_cursor",
        lambda: types.SimpleNamespace(show_click=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.windows.WindowsActuator",
        _Actuator,
    )
    monkeypatch.setattr("jarvis.cu.geometry.list_monitors", lambda: [])

    with pytest.raises(OSError, match="foreground window changed"):
        click_mod._click_windows(
            100,
            200,
            "left",
            False,
            expected_window_signature=("handle", 11, (0, 0, 800, 600)),
        )

    assert len(instances) == 1
    assert instances[0].clicked is False
