"""Appshots on every surface: speech vocabulary, voice calls, config, REST."""

from __future__ import annotations

import base64
import tomllib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.config_writer import set_appshot_settings
from jarvis.live.native import NativeLiveVoiceSession
from jarvis.live.session import LiveVoiceSession
from jarvis.screen_context.intent import classify, mentions_appshot
from jarvis.screen_context.models import VisualIntent

# ------------------------------------------------------------------ speech


@pytest.mark.parametrize(
    "utterance",
    [
        "take an appshot",
        "Mach mal einen Appshot",  # i18n-allow: German speech-input test data
        "mach einen App Shot und sag mir was da steht",  # i18n-allow: DE input
        "Appshot bitte",  # i18n-allow: German speech-input test data
        "hazme un app shot",
    ],
)
def test_naming_the_feature_takes_the_front_window(utterance: str) -> None:
    assert classify(utterance, locale="en").intent is VisualIntent.WINDOW
    assert mentions_appshot(utterance)


@pytest.mark.parametrize(
    "utterance",
    [
        "what is an appshot?",
        "how do appshots work",
        "Was ist ein Appshot?",  # i18n-allow: German speech-input test data
        "Öffne die Appshots Einstellungen",  # i18n-allow: DE input
        "geh zu Appshots",  # i18n-allow: German speech-input test data
        "open the appshot settings",
        "the snapshot looks fine",
        "happy shots all round",
    ],
)
def test_asking_about_or_opening_appshots_takes_nothing(utterance: str) -> None:
    assert classify(utterance, locale="en").intent is VisualIntent.NONE


# ------------------------------------------------------------- voice calls


class Recorder:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.images: list[tuple[bytes, str]] = []
        self.answer_sdp = ""

    async def send(self, event: dict) -> None:
        self.sent.append(event)

    async def send_image(self, image: bytes, mime: str) -> None:
        self.images.append((image, mime))


async def _noop(*_args, **_kwargs) -> None:
    return None


def _config():
    return SimpleNamespace(brain=SimpleNamespace(reply_language="en"))


async def test_gpt_live_puts_the_appshot_into_the_backend_silently() -> None:
    session = LiveVoiceSession(
        session_id="s",
        send_binary=_noop,
        send_json=_noop,
        providers=[SimpleNamespace(name="openai")],
        config=_config(),
    )
    session._connection = Recorder()

    assert await session.attach_appshot(b"jpeg", "image/jpeg", "APPSHOT: note")

    kinds = [event["type"] for event in session._connection.sent]
    assert kinds == ["session.thinking.append", "response.item.create"]
    assert "response.create" not in kinds, "the user's next words are the question"
    content = session._connection.sent[1]["item"]["content"]
    assert content[0] == {"type": "input_text", "text": "APPSHOT: note"}
    assert content[1]["image_url"] == "data:image/jpeg;base64," + base64.b64encode(
        b"jpeg"
    ).decode("ascii")


async def test_gpt_live_declines_while_it_is_recovering() -> None:
    session = LiveVoiceSession(
        session_id="s",
        send_binary=_noop,
        send_json=_noop,
        providers=[SimpleNamespace(name="openai")],
        config=_config(),
    )
    session._connection = Recorder()
    session._recovering = True

    assert not await session.attach_appshot(b"jpeg", "image/jpeg", "note")
    assert session._connection.sent == []


async def test_native_live_sends_a_frame_and_no_text_turn() -> None:
    session = NativeLiveVoiceSession(
        session_id="s",
        send_binary=_noop,
        send_json=_noop,
        providers=[SimpleNamespace(name="gemini")],
        config=_config(),
    )
    session._connection = Recorder()

    assert await session.attach_appshot(b"png", "image/png", "note")
    assert session._connection.images == [(b"png", "image/png")]
    assert session._connection.sent == []


async def test_native_live_without_image_input_declines() -> None:
    session = NativeLiveVoiceSession(
        session_id="s",
        send_binary=_noop,
        send_json=_noop,
        providers=[SimpleNamespace(name="local")],
        config=_config(),
    )
    session._connection = SimpleNamespace(answer_sdp="")

    assert not await session.attach_appshot(b"png", "image/png", "note")


# ------------------------------------------------------------------ config


def test_appshot_patch_writes_one_table(tmp_path) -> None:
    path = tmp_path / "jarvis.toml"
    path.write_text("[ui]\nsound_effects = true\n", encoding="utf-8")

    set_appshot_settings({"hotkey": "alt+alt", "target": "message", "sound": False}, path=path)

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["appshot"] == {"hotkey": "alt+alt", "target": "message", "sound": False}
    assert data["ui"] == {"sound_effects": True}


def test_appshot_patch_rejects_unknown_keys_before_writing(tmp_path) -> None:
    path = tmp_path / "jarvis.toml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="unknown appshot"):
        set_appshot_settings({"enabled": True}, path=path)
    assert path.read_text(encoding="utf-8") == ""


# -------------------------------------------------------------------- REST


@pytest.fixture
def client() -> TestClient:
    from jarvis.ui.web.appshot_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.bus = None
    return TestClient(app)


def test_an_empty_settings_patch_is_refused_without_writing(client) -> None:
    assert client.put("/api/appshot/settings", json={}).status_code == 400


def test_an_unknown_destination_is_refused(client) -> None:
    assert client.put("/api/appshot/settings", json={"target": "email"}).status_code == 422


def test_take_reports_a_refusal_as_data(client, monkeypatch) -> None:
    import jarvis.appshot.service as service

    async def refuse(**_kwargs):
        return service.AppshotResult(status="refused", reason_code="disabled", message="off")

    monkeypatch.setattr(service, "take_appshot", refuse)
    body = client.post("/api/appshot/take", json={"delay_s": 0}).json()
    assert body == {"ok": False, "reason": "disabled", "message": "off"}


def test_the_parked_appshot_is_claimed_once(client) -> None:
    from jarvis.appshot.store import Appshot, get_store

    store = get_store()
    store.clear()
    store.park(
        Appshot(
            id="c1",
            image=b"jpeg",
            mime="image/jpeg",
            width=1,
            height=1,
            label="active window",
            app_name="",
            note="",
            ui_text="",
            trigger="hotkey",
            taken_at=0.0,
        ),
        ttl_s=60,
    )
    try:
        assert client.get("/api/appshot/pending").json()["appshot"]["id"] == "c1"
        first = client.post("/api/appshot/pending/claim", json={"id": "c1"})
        assert first.status_code == 200 and first.content == b"jpeg"
        assert first.headers["cache-control"] == "no-store"
        assert client.post("/api/appshot/pending/claim", json={"id": "c1"}).status_code == 404
    finally:
        store.clear()
