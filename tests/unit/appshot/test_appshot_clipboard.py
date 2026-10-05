"""An appshot the user takes lands on the clipboard, ready for Ctrl/Cmd+V."""

from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.appshot import service
from jarvis.appshot.store import Appshot
from jarvis.platform import clipboard_image


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (20, 120, 220)).save(buffer, "PNG")
    return buffer.getvalue()


def _shot(trigger: str = "hotkey") -> Appshot:
    return Appshot(
        id="c0ffee00",
        image=_png(),
        mime="image/png",
        width=40,
        height=30,
        label="selected area",
        app_name="Editor",
        note="APPSHOT: evidence",
        ui_text="",
        trigger=trigger,
        taken_at=1.0,
    )


def _config(copy: bool = True) -> SimpleNamespace:
    return SimpleNamespace(appshot=SimpleNamespace(copy_to_clipboard=copy))


@pytest.fixture
def copied(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    pasted: list[bytes] = []

    def fake_copy(png: bytes) -> clipboard_image.CopyResult:
        pasted.append(png)
        return clipboard_image.CopyResult(True, formats=("PNG",))

    async def no_master(shot: Appshot) -> Appshot:
        return shot

    monkeypatch.setattr(clipboard_image, "copy_image", fake_copy)
    monkeypatch.setattr(service, "finished", no_master)
    return pasted


async def _settle() -> None:
    while service._COPIES:  # noqa: SLF001
        await asyncio.gather(*service._COPIES)  # noqa: SLF001


@pytest.mark.parametrize("trigger", ["hotkey", "button"])
async def test_a_shortcut_or_button_appshot_is_copied(copied: list[bytes], trigger: str) -> None:
    service._start_clipboard_copy(_shot(trigger), trigger, _config(), "done")  # noqa: SLF001
    await _settle()

    assert copied == [_png()]


@pytest.mark.parametrize("trigger", ["voice", "tool"])
async def test_a_look_the_assistant_took_never_replaces_the_clipboard(
    copied: list[bytes], trigger: str
) -> None:
    service._start_clipboard_copy(_shot(trigger), trigger, _config(), "done")  # noqa: SLF001
    await _settle()

    assert copied == []


async def test_the_setting_switches_it_off(copied: list[bytes]) -> None:
    service._start_clipboard_copy(_shot(), "hotkey", _config(copy=False), "done")  # noqa: SLF001
    await _settle()

    assert copied == []


async def test_the_pickers_own_copy_is_not_done_twice(copied: list[bytes]) -> None:
    service._start_clipboard_copy(_shot(), "hotkey", _config(), "copy")  # noqa: SLF001
    await _settle()

    assert copied == []


async def test_the_lossless_picture_is_what_gets_copied(
    monkeypatch: pytest.MonkeyPatch, copied: list[bytes]
) -> None:
    master = _png() + b"lossless"

    async def finished(shot: Appshot) -> Appshot:
        return replace(shot, original_png=master)

    monkeypatch.setattr(service, "finished", finished)
    service._start_clipboard_copy(_shot(), "hotkey", _config(), "done")  # noqa: SLF001
    await _settle()

    assert copied == [master]


def test_copy_to_clipboard_defaults_on_and_is_a_settings_key() -> None:
    from jarvis.core.config import AppshotConfig
    from jarvis.core.config_writer import APPSHOT_SETTING_KEYS

    assert AppshotConfig().copy_to_clipboard is True
    assert "copy_to_clipboard" in APPSHOT_SETTING_KEYS


def test_the_settings_route_reads_and_writes_the_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from jarvis.core import config_writer
    from jarvis.ui.web import appshot_routes

    written: list[dict] = []
    monkeypatch.setattr(
        config_writer, "set_appshot_settings", lambda values, **_kw: written.append(values)
    )
    monkeypatch.setattr(appshot_routes, "_settings_payload", lambda: {"copy_to_clipboard": False})
    app = FastAPI()
    app.include_router(appshot_routes.router)

    response = TestClient(app).put("/api/appshot/settings", json={"copy_to_clipboard": False})

    assert response.status_code == 200, response.text
    assert written == [{"copy_to_clipboard": False}]
