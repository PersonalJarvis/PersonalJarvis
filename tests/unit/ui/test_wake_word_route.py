"""GET/PUT /api/settings/wake-word — the custom-wake-word REST surface."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.config import WakeWordConfig
from jarvis.speech import wake_constants as wc
from jarvis.speech import wake_phrase as wp
from jarvis.ui.web.settings_routes import router


class _FakePipeline:
    """Records the live-applied wake plan (mirrors SpeechPipeline.set_wake_plan)."""

    def __init__(self) -> None:
        self.applied = None

    def set_wake_plan(self, plan: object) -> None:
        self.applied = plan


def _client(
    wake_word: WakeWordConfig | None = None,
    pipeline: object | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        trigger=SimpleNamespace(wake_word=wake_word or WakeWordConfig())
    )
    if pipeline is not None:
        app.state.speech_pipeline = pipeline
    return TestClient(app)


def _pretend_oww_models_exist(monkeypatch, *model_names: str) -> None:
    models = set(model_names)
    original_resolve = wc.resolve_oww_model_path

    def fake_resolve(model_name: str) -> str | None:
        if model_name in models:
            return f"C:/fake-openwakeword/{model_name}_v0.1.onnx"
        return original_resolve(model_name)

    monkeypatch.setattr(wc, "resolve_oww_model_path", fake_resolve)
    monkeypatch.setattr(wp, "resolve_oww_model_path", fake_resolve)


def test_get_returns_current_and_options() -> None:
    body = _client().get("/api/settings/wake-word").json()
    assert body["phrase"] == ""
    assert body["engine"] == "auto"
    assert "auto" in body["engines"] and "stt_match" in body["engines"]
    assert "Hey Jarvis" not in body["instant_phrases"]  # instant quick-picks are empty
    assert isinstance(body["local_whisper_available"], bool)


def test_put_known_phrase_resolves_to_openwakeword(monkeypatch) -> None:
    _pretend_oww_models_exist(monkeypatch, "alexa")

    resp = _client().put(
        "/api/settings/wake-word",
        json={"phrase": "Alexa", "engine": "auto", "persist": False},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["resolved_engine"] == "openwakeword"
    assert body["degraded"] is False
    assert body["restart_required"] is True
    assert body["persisted"] is False


def test_put_live_applies_to_running_pipeline(monkeypatch) -> None:
    # The fix for "only Hey Jarvis works": a save must reconfigure the live
    # pipeline (no restart) when one is running.
    _pretend_oww_models_exist(monkeypatch, "alexa")
    pipe = _FakePipeline()
    resp = _client(pipeline=pipe).put(
        "/api/settings/wake-word",
        json={"phrase": "Alexa", "engine": "auto", "persist": False},
    )
    body = resp.json()
    assert body["applied_live"] is True
    assert body["restart_required"] is False
    assert pipe.applied is not None
    assert pipe.applied.oww_keyword == "alexa"


def test_put_without_pipeline_reports_restart_required() -> None:
    body = _client().put(
        "/api/settings/wake-word",
        json={"phrase": "Alexa", "engine": "auto", "persist": False},
    ).json()
    assert body["applied_live"] is False
    assert body["restart_required"] is True


def test_put_rejects_unknown_engine() -> None:
    resp = _client().put(
        "/api/settings/wake-word",
        json={"phrase": "Computer", "engine": "porcupine", "persist": False},
    )
    assert resp.status_code == 400


def test_put_in_memory_update_reflects_in_get() -> None:
    client = _client()
    client.put(
        "/api/settings/wake-word",
        json={"phrase": "Athena", "engine": "auto", "persist": False},
    )
    body = client.get("/api/settings/wake-word").json()
    assert body["phrase"] == "Athena"


def test_omitted_tuning_fields_default_to_none() -> None:
    # Regression guard: a concrete default (e.g. 0.5) would make every UI save
    # clobber a hand-edited jarvis.toml. Optional fields must default to None.
    from jarvis.ui.web.settings_routes import WakeWordBody

    body = WakeWordBody(phrase="Computer")
    assert body.fuzzy_match_ratio is None
    assert body.sensitivity is None
    assert body.custom_model_path is None


def test_put_omits_tuning_fields_so_persist_does_not_clobber(monkeypatch) -> None:
    # When the client omits fuzzy_match_ratio/sensitivity, the persist call must
    # receive None for them (set_wake_word then skips writing → preserves the
    # existing toml value). This is the exact bug the reviewer caught.
    from jarvis.core import config_writer

    captured: dict = {}

    def _fake_set_wake_word(
        phrase, *, engine=None, custom_model_path=None,
        sensitivity=None, fuzzy_match_ratio=None, path=None,
    ):
        captured.update(
            phrase=phrase,
            engine=engine,
            custom_model_path=custom_model_path,
            sensitivity=sensitivity,
            fuzzy_match_ratio=fuzzy_match_ratio,
        )

    monkeypatch.setattr(config_writer, "set_wake_word", _fake_set_wake_word)

    resp = _client().put(
        "/api/settings/wake-word",
        json={"phrase": "Computer", "engine": "auto", "persist": True},
    )
    assert resp.status_code == 200
    assert resp.json()["persisted"] is True
    assert captured["phrase"] == "Computer"
    assert captured["fuzzy_match_ratio"] is None  # NOT 0.5 — would clobber 0.8
    assert captured["sensitivity"] is None
