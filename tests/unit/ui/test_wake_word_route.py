"""GET/PUT /api/settings/wake-word — the custom-wake-word REST surface."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.config import WakeWordConfig
from jarvis.ui.web.settings_routes import router


class _FakePipeline:
    """Records the live-applied wake plan (mirrors SpeechPipeline.set_wake_plan)."""

    def __init__(self) -> None:
        self.applied = None
        self.activation: bool | None = None

    def set_wake_plan(self, plan: object) -> None:
        self.applied = plan

    def set_wake_activation(self, enabled: bool) -> None:
        self.activation = enabled


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


def test_get_returns_current_and_options() -> None:
    body = _client().get("/api/settings/wake-word").json()
    assert body["phrase"] == ""
    assert body["engine"] == "auto"
    # The independent wake-word language pin defaults to the legacy cascade.
    assert body["language"] == "auto"
    assert "auto" in body["engines"] and "stt_match" in body["engines"]
    assert "Hey Jarvis" not in body["instant_phrases"]  # instant quick-picks are empty
    assert isinstance(body["local_whisper_available"], bool)
    # The Sensitivity slider was removed 2026-07-10 — the GET payload no
    # longer surfaces a sensitivity value.
    assert "sensitivity" not in body


def test_put_brand_phrase_never_resolves_to_a_pretrained_model() -> None:
    # Design 2026-07-07: no bundled/pretrained brand models. A brand word is
    # an ordinary phrase served by the generic chain (or an honest degrade on
    # a box without a local engine) — never by a named openwakeword model.
    resp = _client().put(
        "/api/settings/wake-word",
        json={"phrase": "Alexa", "engine": "auto", "persist": False},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["resolved_engine"] in ("stt_match", "vosk_kws", "none")
    assert body["restart_required"] is True
    assert body["persisted"] is False


def test_put_live_applies_to_running_pipeline() -> None:
    # The fix for "only Hey Jarvis works": a save must reconfigure the live
    # pipeline (no restart) when one is running.
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


# ---------------------------------------------------------------------------
# GET/PUT /api/settings/wake-language — the INDEPENDENT wake-word language pin
# (decoupling mandate 2026-07-21: it must never follow the app display
# language, and pinning it must never touch [ui].language or [stt].language).
# ---------------------------------------------------------------------------


def test_get_wake_language_reports_pin_effective_and_options() -> None:
    body = _client().get("/api/settings/wake-language").json()
    assert body["language"] == "auto"
    assert body["options"] == ["auto", "de", "en", "es"]
    # With no pin and no stt/ui signal the cascade lands on the default.
    assert body["effective_language"] == "en"


def test_put_wake_language_pins_without_touching_ui_or_stt(monkeypatch) -> None:
    from jarvis.core import config_writer
    from jarvis.speech import wake_model_fetch

    persisted: list[str] = []
    monkeypatch.setattr(
        config_writer, "set_wake_language", lambda lang, path=None: persisted.append(lang)
    )
    # The matching model is "already on disk" — no background provisioning task.
    monkeypatch.setattr(wake_model_fetch, "vosk_model_present", lambda *_a, **_k: True)

    pipe = _FakePipeline()
    client = _client(wake_word=WakeWordConfig(phrase="Hey Nova"), pipeline=pipe)
    # An app in ENGLISH with default recognition — the exact coupling scenario.
    client.app.state.config.ui = SimpleNamespace(language="en")
    client.app.state.config.stt = SimpleNamespace(language="auto")

    resp = client.put("/api/settings/wake-language", json={"language": "de"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["language"] == "de"
    assert body["persisted"] is True
    assert body["applied_live"] is True
    assert body["restart_required"] is False
    assert persisted == ["de"]
    # The pin landed in-memory and immediately decides the effective language …
    assert client.app.state.config.trigger.wake_word.language == "de"
    assert client.get("/api/settings/wake-language").json()["effective_language"] == "de"
    # … while the app display language and the recognition language are untouched.
    assert client.app.state.config.ui.language == "en"
    assert client.app.state.config.stt.language == "auto"
    # And the live pipeline got a re-resolved plan.
    assert pipe.applied is not None


def test_put_wake_language_rejects_unknown_code() -> None:
    resp = _client().put("/api/settings/wake-language", json={"language": "klingon"})
    assert resp.status_code == 400


def test_activation_live_applies_without_restart(monkeypatch) -> None:
    from jarvis.core import config_writer

    monkeypatch.setattr(config_writer, "set_wake_word_enabled", lambda _enabled: None)
    pipe = _FakePipeline()
    response = _client(pipeline=pipe).post(
        "/api/settings/wake-word/activation", json={"enabled": True}
    )

    assert response.status_code == 200
    assert response.json()["applied_live"] is True
    assert response.json()["restart_required"] is False
    assert pipe.activation is True


def test_activation_without_voice_pipeline_is_persisted_for_next_start(monkeypatch) -> None:
    from jarvis.core import config_writer

    persisted: list[bool] = []
    monkeypatch.setattr(config_writer, "set_wake_word_enabled", persisted.append)
    response = _client().post(
        "/api/settings/wake-word/activation", json={"enabled": False}
    )

    assert response.status_code == 200
    assert response.json()["applied_live"] is False
    assert response.json()["restart_required"] is True
    assert persisted == [False]


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
    # When the client omits fuzzy_match_ratio, the persist call must receive
    # None for it (set_wake_word then skips writing → preserves the existing
    # toml value). This is the exact bug the reviewer caught. sensitivity is
    # no longer part of the set_wake_word signature at all (removed 2026-07-10).
    from jarvis.core import config_writer

    captured: dict = {}

    def _fake_set_wake_word(
        phrase, *, engine=None, custom_model_path=None,
        fuzzy_match_ratio=None, path=None,
    ):
        captured.update(
            phrase=phrase,
            engine=engine,
            custom_model_path=custom_model_path,
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
    assert "sensitivity" not in captured


def test_put_with_legacy_sensitivity_is_accepted_but_ignored() -> None:
    # Back-compat guard (2026-07-10): an old client/CLI body carrying
    # 'sensitivity' must not 422, and the value must not surface anywhere in
    # the response — it is simply dropped.
    resp = _client().put(
        "/api/settings/wake-word",
        json={
            "phrase": "Computer",
            "engine": "auto",
            "sensitivity": 0.9,
            "persist": False,
        },
    )
    assert resp.status_code == 200
    assert "sensitivity" not in resp.json()
