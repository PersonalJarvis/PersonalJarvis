"""GET/PUT /api/settings/audio-devices — enumerate + persist + live-apply.

The Settings device pickers: GET lists one entry per physical device plus the
current [audio] selection; PUT persists a device NAME (or the "auto-headset"
sentinel) via config_writer and live-applies it to the running SpeechPipeline
when its PortAudio table contains that device. A fresh-probe-only selection is
saved for the next app start. Headless hosts degrade to available=false /
restart_required=true — never a 500.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.audio.devices import AudioDeviceInfo
from jarvis.ui.web import settings_routes
from jarvis.ui.web.settings_routes import router


@pytest.fixture(autouse=True)
def _assume_requested_device_is_in_live_table(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "_audio_device_available_in_live_table",
        lambda value, *, output: True,
    )


def _client(*, output_device="auto-headset", input_device="auto-headset", pipeline=None):
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        audio=SimpleNamespace(
            output_device=output_device, input_device=input_device
        )
    )
    if pipeline is not None:
        app.state.speech_pipeline = pipeline
    return TestClient(app)


def _fake_lists(monkeypatch, outputs, inputs):
    import jarvis.audio.devices as dv

    def fresh_options(*, fresh=False):
        assert fresh is True
        return outputs, inputs

    monkeypatch.setattr(
        dv,
        "list_device_options",
        fresh_options,
    )


def test_get_lists_devices_and_selection(monkeypatch):
    _fake_lists(
        monkeypatch,
        outputs=[
            AudioDeviceInfo(name="Speakers (Realtek HD Audio)", is_default=True),
            AudioDeviceInfo(name="PRO X Gaming Headset", is_default=False),
        ],
        inputs=[AudioDeviceInfo(name="Microphone (PRO X)", is_default=True)],
    )

    r = _client(output_device="PRO X Gaming Headset").get(
        "/api/settings/audio-devices"
    )

    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["auto_value"] == "auto-headset"
    assert body["outputs"][0] == {
        "name": "Speakers (Realtek HD Audio)",
        "is_default": True,
    }
    assert body["inputs"] == [{"name": "Microphone (PRO X)", "is_default": True}]
    assert body["selected_output"] == "PRO X Gaming Headset"
    assert body["selected_input"] == "auto-headset"


def test_get_headless_reports_unavailable(monkeypatch):
    _fake_lists(monkeypatch, outputs=[], inputs=[])

    r = _client().get("/api/settings/audio-devices")

    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["outputs"] == []
    assert body["inputs"] == []


def test_put_persists_and_live_applies(monkeypatch):
    persisted: dict[str, str] = {}
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(
        cw,
        "set_audio_device",
        lambda kind, value, **k: persisted.setdefault(kind, value),
    )

    applied: dict[str, str | None] = {}

    class FakePipeline:
        def set_audio_devices(self, *, input_device=None, output_device=None):
            applied["input"] = input_device
            applied["output"] = output_device

    client = _client(pipeline=FakePipeline())
    r = client.put(
        "/api/settings/audio-devices",
        json={"output_device": "PRO X Gaming Headset", "persist": True},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["persisted"] is True
    assert body["applied_live"] is True
    assert body["restart_required"] is False
    assert body["selected_output"] == "PRO X Gaming Headset"
    assert persisted == {"output": "PRO X Gaming Headset"}
    assert applied == {"input": None, "output": "PRO X Gaming Headset"}


def test_put_input_only_and_auto_reset(monkeypatch):
    persisted: dict[str, str] = {}
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(
        cw,
        "set_audio_device",
        lambda kind, value, **k: persisted.setdefault(kind, value),
    )

    client = _client(input_device="Blue Yeti")
    r = client.put(
        "/api/settings/audio-devices",
        json={"input_device": "auto-headset"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["selected_input"] == "auto-headset"
    assert persisted == {"input": "auto-headset"}
    # No pipeline on app.state → persisted only, applies on next start.
    assert body["applied_live"] is False
    assert body["restart_required"] is True


def test_put_hotplugged_device_waits_for_safe_next_start(monkeypatch):
    """A fresh-probe-only index must never be applied to the stale live table."""
    persisted: dict[str, str] = {}
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(
        cw,
        "set_audio_device",
        lambda kind, value, **kwargs: persisted.setdefault(kind, value),
    )
    monkeypatch.setattr(
        settings_routes,
        "_audio_device_available_in_live_table",
        lambda value, *, output: value != "External Headphones",
    )

    applied: list[dict[str, str]] = []

    class FakePipeline:
        def set_audio_devices(self, **devices):
            applied.append(devices)

    response = _client(pipeline=FakePipeline()).put(
        "/api/settings/audio-devices",
        json={"output_device": "External Headphones", "persist": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["selected_output"] == "External Headphones"
    assert body["persisted"] is True
    assert body["applied_live"] is False
    assert body["restart_required"] is True
    assert persisted == {"output": "External Headphones"}
    assert applied == []


def test_put_requires_at_least_one_side():
    r = _client().put("/api/settings/audio-devices", json={"persist": True})
    assert r.status_code == 422


def test_put_survives_persist_failure(monkeypatch):
    import jarvis.core.config_writer as cw

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(cw, "set_audio_device", boom)

    r = _client().put(
        "/api/settings/audio-devices",
        json={"output_device": "PRO X Gaming Headset"},
    )

    assert r.status_code == 200
    assert r.json()["persisted"] is False
