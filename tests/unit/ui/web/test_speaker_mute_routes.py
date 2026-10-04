"""The speaker API shares the native button's state and leaves mic/volume alone."""

import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.speech.pipeline import SpeechPipeline
from jarvis.ui.web.settings_routes import router


class Player:
    _volume = 0.6
    muted = False

    def set_muted(self, muted):
        self.muted = muted

    def set_volume(self, volume):
        self._volume = volume


def test_api_native_toggle_and_volume_share_one_state(monkeypatch):
    from jarvis.core import runtime_refs
    from ui.orb.controls import toggle_speaker_mute

    pipeline = SpeechPipeline.__new__(SpeechPipeline)
    pipeline._speaker_mute_lock = threading.RLock()
    pipeline._speaker_muted = False
    pipeline._player = Player()
    pipeline._muted = True
    monkeypatch.setattr(runtime_refs, "get_speech_pipeline", lambda: pipeline)
    app = FastAPI()
    app.state.speech_pipeline = pipeline
    app.state.config = SimpleNamespace(tts=SimpleNamespace(volume=0.6))
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/settings/speaker-mute").json() == {
            "muted": False, "volume": 0.6, "revision": 0,
        }
        assert client.post("/api/settings/speaker-mute").json()["muted"] is True
        assert pipeline._player.muted
        pipeline.set_tts_volume(0.3)
        assert pipeline._player.muted
        assert client.get("/api/settings/speaker-mute").json()["volume"] == 0.3
        assert toggle_speaker_mute(source="bar") is False
        assert client.get("/api/settings/speaker-mute").json()["muted"] is False
        assert pipeline._player._volume == 0.3
        assert pipeline.is_muted
        pipeline.set_tts_volume(0.0)
        assert client.post("/api/settings/speaker-mute").json()["muted"] is True
        assert client.post("/api/settings/speaker-mute").json()["muted"] is False
        assert pipeline._player._volume == 0.0  # Speaker controls never raise the volume.


def test_unavailable_pipeline_does_not_claim_success(monkeypatch):
    from jarvis.core import runtime_refs

    monkeypatch.setattr(runtime_refs, "get_speech_pipeline", lambda: None)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/settings/speaker-mute").status_code == 503
        assert client.post("/api/settings/speaker-mute").status_code == 503
