"""The appshot after the shutter: editor window, corner card, edited delivery.

A click on the corner card opens the editor in a window of its own (no
raised, resized main window); Done hands the edited picture to the assistant
and slides it back into the corner; the card's hover buttons copy and save
through the main process; ``[appshot].card_seconds`` sets how long it rests.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.appshot import card_actions, editor_window
from jarvis.appshot.store import Appshot, AppshotStore


def _png(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (20, 120, 220)).save(buffer, "PNG")
    return buffer.getvalue()


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (200, 30, 30)).save(buffer, "JPEG")
    return buffer.getvalue()


def _shot(shot_id: str = "a1b2c3d4", delivered_to: str = "message") -> Appshot:
    return Appshot(
        id=shot_id,
        image=_png(),
        mime="image/png",
        width=40,
        height=30,
        label="active window",
        app_name="Editor",
        note="APPSHOT: evidence",
        ui_text="",
        trigger="hotkey",
        taken_at=1.0,
        delivered_to=delivered_to,
    )


# -- the editor window -----------------------------------------------------------


@pytest.fixture
def opener():
    calls: list[str] = []

    def open_window(query: str) -> dict:
        calls.append(query)
        return {"ok": True}

    editor_window.register_window_opener(open_window)
    yield calls
    editor_window.register_window_opener(None)


async def test_the_editor_opens_in_its_own_window(opener) -> None:
    assert await editor_window.open_editor_window("a1b2c3d4") is True
    assert opener == ["appshot=a1b2c3d4"]


async def test_no_shell_means_the_page_editor() -> None:
    editor_window.register_window_opener(None)
    assert await editor_window.open_editor_window("a1b2c3d4") is False


async def test_only_an_appshot_id_reaches_the_window_url(opener) -> None:
    assert await editor_window.open_editor_window("x&view=settings") is False
    assert opener == []


async def test_a_shell_that_cannot_open_a_window_falls_back() -> None:
    editor_window.register_window_opener(lambda _query: {"ok": False, "reason": "no_live_window"})
    try:
        assert await editor_window.open_editor_window("a1b2c3d4") is False
    finally:
        editor_window.register_window_opener(None)


def test_the_route_reports_where_the_editor_opened(opener) -> None:
    from jarvis.ui.web.appshot_routes import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    response = client.post("/api/appshot/open-editor", json={"id": "a1b2c3d4"})
    assert response.status_code == 200
    assert response.json() == {"window": True}


# -- how long the card rests, and its wording ----------------------------------------


@pytest.mark.parametrize(("seconds", "expected"), [(6, 6000), (0, 0), (30, 30000), (-3, 0)])
def test_card_rest_time_follows_the_setting(seconds: int, expected: int) -> None:
    config = SimpleNamespace(appshot=SimpleNamespace(card_seconds=seconds))
    assert card_actions.card_rest_ms(config) == expected


def test_card_seconds_is_validated_and_defaults_to_six() -> None:
    from pydantic import ValidationError

    from jarvis.core.config import AppshotConfig

    assert AppshotConfig().card_seconds == 6
    assert AppshotConfig(card_seconds=0).card_seconds == 0
    with pytest.raises(ValidationError):
        AppshotConfig(card_seconds=-1)


def test_card_labels_follow_the_ui_language() -> None:
    german = card_actions.card_labels(SimpleNamespace(ui=SimpleNamespace(language="de")))
    unknown = card_actions.card_labels(SimpleNamespace(ui=SimpleNamespace(language="xx")))
    assert german["copy"] != unknown["copy"]
    assert set(german) == set(unknown)
    assert unknown["save"] == "Save"


# -- the card's hover buttons ------------------------------------------------------


def test_a_jpeg_appshot_is_copied_and_saved_as_png() -> None:
    png = card_actions.as_png(_jpeg())
    assert png.startswith(b"\x89PNG")
    assert card_actions.as_png(_png()) == _png()


def test_save_writes_a_fresh_file_each_time(tmp_path) -> None:
    first = card_actions.save_to_downloads(_png(), folder=tmp_path, now=1_000_000.0)
    second = card_actions.save_to_downloads(_png(), folder=tmp_path, now=1_000_000.0)
    assert first != second and first.exists() and second.exists()
    assert first.suffix == ".png"


async def test_copy_from_the_card_uses_the_native_clipboard(monkeypatch) -> None:
    from jarvis.appshot import store as store_module
    from jarvis.platform import clipboard_image

    store = AppshotStore()
    store.remember(_shot(), keep_s=60)
    monkeypatch.setattr(store_module, "_STORE", store)
    copied: list[bytes] = []
    monkeypatch.setattr(clipboard_image, "write_png", lambda png: copied.append(png) or True)
    monkeypatch.setattr(
        "jarvis.core.config.load_config", lambda: SimpleNamespace(ui=SimpleNamespace(language="en"))
    )

    assert await card_actions.run_card_action("copy") == "Copied"
    assert copied == [_png()]


async def test_a_card_action_without_a_held_appshot_says_so(monkeypatch) -> None:
    from jarvis.appshot import store as store_module

    monkeypatch.setattr(store_module, "_STORE", AppshotStore())
    monkeypatch.setattr(
        "jarvis.core.config.load_config", lambda: SimpleNamespace(ui=SimpleNamespace(language="en"))
    )
    assert await card_actions.run_card_action("save") == "No longer kept"


# -- what the assistant gets after Done -----------------------------------------


def _config(target: str = "auto") -> SimpleNamespace:
    return SimpleNamespace(
        appshot=SimpleNamespace(target=target),
        screen_context=SimpleNamespace(ttl_s=120.0),
    )


@pytest.fixture
def held(monkeypatch):
    from jarvis.appshot import service
    from jarvis.appshot import store as store_module

    store = AppshotStore()
    monkeypatch.setattr(store_module, "_STORE", store)
    monkeypatch.setattr(service, "get_store", lambda: store)
    state = {"config": _config(), "call": False, "sent": []}

    async def fake_live(image: bytes, mime: str, note: str) -> bool:
        if state["call"]:
            state["sent"].append(note)
        return state["call"]

    monkeypatch.setattr(service, "_load_config", lambda: state["config"])
    monkeypatch.setattr("jarvis.appshot.delivery.deliver_to_live", fake_live)
    return store, state


async def test_an_edit_goes_with_the_next_message_and_says_it_was_edited(held) -> None:
    from jarvis.appshot.service import EDIT_NOTE, deliver_edit

    store, _state = held
    shot = _shot(delivered_to="message")
    store.remember(shot, keep_s=60)

    assert await deliver_edit(shot) == "message"
    pending = store.take_pending()
    assert pending is not None and pending.id == shot.id
    assert pending.note.startswith("APPSHOT: evidence") and EDIT_NOTE in pending.note


async def test_an_edit_reaches_a_running_call_even_after_the_original_did(held) -> None:
    from jarvis.appshot.service import deliver_edit

    store, state = held
    state["call"] = True
    shot = _shot(delivered_to="voice")
    store.remember(shot, keep_s=60)
    store.park(shot, ttl_s=60)

    assert await deliver_edit(shot) == "voice"
    assert len(state["sent"]) == 1
    # The waiting original must not follow into the next message as well.
    assert store.take_pending() is None


async def test_an_edit_of_an_already_sent_appshot_waits_for_the_next_message(held) -> None:
    from jarvis.appshot.service import deliver_edit

    store, _state = held
    shot = _shot(delivered_to="turn")
    store.remember(shot, keep_s=60)

    assert await deliver_edit(shot) == "message"
    assert store.peek_pending() is not None


async def test_a_voice_only_target_without_a_call_keeps_the_edit_to_itself(held) -> None:
    from jarvis.appshot.service import deliver_edit

    store, state = held
    state["config"] = _config("voice")
    shot = _shot()
    store.remember(shot, keep_s=60)

    assert await deliver_edit(shot) == "none"
    assert store.peek_pending() is None


async def test_editing_twice_does_not_stack_the_note(held) -> None:
    from jarvis.appshot.service import EDIT_NOTE, deliver_edit

    store, _state = held
    shot = _shot()
    store.remember(shot, keep_s=60)
    await deliver_edit(shot)
    once = store.peek_pending()
    assert once is not None
    await deliver_edit(once)
    twice = store.peek_pending()
    assert twice is not None and twice.note.count(EDIT_NOTE) == 1


# -- the sidecar protocol ---------------------------------------------------------


def test_the_card_commands_and_events_are_known() -> None:
    from jarvis.cu.indicator import protocol

    assert protocol.CMD_CARD in protocol.ALL_COMMANDS
    assert protocol.CMD_CARD_STATUS in protocol.ALL_COMMANDS
    line = protocol.encode_event(protocol.EVENT_CARD_ACTION, action="copy")
    assert protocol.decode_event(line) == {"event": "card_action", "action": "copy"}
