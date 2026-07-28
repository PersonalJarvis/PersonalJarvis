"""REST route tests for the dictation API.

The layer these cover used to be the untested one, which is how the UI ended up
calling four endpoints that did not exist. So the assertions here are written
against the contract the frontend actually consumes, not against the
implementation:

* ``choices`` must carry a language list, or the dropdown renders empty.
* the wire shape must never contain ``audio_path`` — a filesystem path in a
  JSON body is an information leak that buys the client nothing.
* Restore must degrade to an honest sentence on a host with no speech-to-text
  provider, never a 500.
* every route must answer sanely with no speech pipeline at all (headless).

The fixture boots a bare ``FastAPI()`` with only this router, and sandboxes
``LOCALAPPDATA`` so the history, the counter sidecar and the audio directory all
land in ``tmp_path`` instead of the developer's real profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.dictation_routes import router as dictation_router


@pytest.fixture(autouse=True)
def _sandbox_user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``user_data_dir()`` at the temp directory for the whole test."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


@pytest.fixture
def app() -> FastAPI:
    """A bare app carrying a real config, so no route sees a stand-in model."""
    from jarvis.core.config import DictationConfig, TriggerConfig

    application = FastAPI()
    application.include_router(dictation_router)
    application.state.config = SimpleNamespace(
        trigger=TriggerConfig(), dictation=DictationConfig()
    )
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ----------------------------------------------------------------------
# Fakes — a speech pipeline that only has to answer one question
# ----------------------------------------------------------------------


@dataclass
class _FakeTranscript:
    text: str
    language: str = "en"


class _FakeSTT:
    """The narrow slice of an STT provider a restore actually touches."""

    def __init__(self, text: str = "call the studio", *, accepts_language: bool = True):
        self._text = text
        self._accepts_language = accepts_language
        self.calls: list[tuple[int, str | None]] = []

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Any:
        if language is not None and not self._accepts_language:
            raise TypeError("transcribe_pcm() got an unexpected keyword 'language'")
        self.calls.append((len(pcm), language))
        return _FakeTranscript(text=self._text)


class _BrokenSTT:
    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Any:
        raise RuntimeError("provider returned 401")


def _install_pipeline(monkeypatch: pytest.MonkeyPatch, stt: Any) -> Any:
    """Make ``_pipeline()`` return a pipeline exposing ``stt``."""
    pipeline = SimpleNamespace(_utterance_stt=stt)
    monkeypatch.setattr(
        "jarvis.core.runtime_refs.get_speech_pipeline", lambda: pipeline
    )
    return pipeline


# ----------------------------------------------------------------------
# Helpers — build a history the way the pipeline would have
# ----------------------------------------------------------------------


def _history() -> Any:
    from jarvis.dictation.history import DictationHistory

    return DictationHistory()


def _add(**fields: Any) -> Any:
    """Record one entry through the real store (defaults: a good dictation)."""
    payload: dict[str, Any] = {
        "raw_text": "so uh send the report",
        "text": "send the report",
        "language": "en",
        "duration_s": 4.2,
        "outcome": "inserted",
        "method": "clipboard",
    }
    payload.update(fields)
    entry = _history().add(**payload)
    assert entry is not None
    return entry


def _add_failed_with_audio(text: str = "") -> Any:
    """A dictation that produced nothing, with its audio sidecar kept."""
    from jarvis.dictation.audio import save_dictation_audio

    history = _history()
    entry = _add(raw_text=text, text=text, outcome="failed", error="provider 401")
    # Half a second of (silent) 16 kHz mono int16 — the shape the capture path
    # hands over. The fake provider does not care what is in it.
    path = save_dictation_audio(
        entry.id, b"\x00\x01" * 8_000, directory=history.audio_dir
    )
    assert path is not None
    updated = history.update(entry.id, audio_path=str(path))
    assert updated is not None
    return updated


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------


def test_settings_report_every_persisted_key(client: TestClient) -> None:
    from jarvis.core.config_writer import DICTATION_SETTING_KEYS

    body = client.get("/api/dictation/settings").json()

    assert DICTATION_SETTING_KEYS, "the key tuple went empty — the test is blind"
    missing = [k for k in DICTATION_SETTING_KEYS if k not in body["settings"]]
    assert missing == []
    assert body["settings"]["language"] == "auto"
    assert body["settings"]["keep_failed_audio"] is True
    assert body["settings"]["audio_retention_days"] == 7
    assert body["settings"]["audio_max_files"] == 20


def test_settings_offer_a_language_list_the_dropdown_can_render(
    client: TestClient,
) -> None:
    """A missing ``choices`` entry is an empty dropdown, not a crash — hence a test."""
    from jarvis.core.config import DICTATION_LANGUAGES

    choices = client.get("/api/dictation/settings").json()["choices"]

    assert choices["language"] == list(DICTATION_LANGUAGES)
    assert "auto" in choices["language"]
    # Every key with a fixed value set has a list; a new one must land here too.
    for key in ("mode", "target", "insert_method", "paste_chord", "language"):
        assert choices[key], f"choices.{key} is empty"


def test_put_settings_accepts_the_new_keys(client: TestClient) -> None:
    body = client.put(
        "/api/dictation/settings",
        json={
            "language": "de",
            "keep_failed_audio": False,
            "audio_retention_days": 3,
            "audio_max_files": 5,
            "persist": False,
        },
    ).json()

    assert body["ok"] is True
    assert body["settings"]["language"] == "de"
    # False must survive: a filter that drops falsy values would make the
    # privacy switch impossible to turn OFF.
    assert body["settings"]["keep_failed_audio"] is False
    assert body["settings"]["audio_retention_days"] == 3
    assert body["settings"]["audio_max_files"] == 5


def test_put_settings_persists_the_new_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_dictation_setting",
        lambda key, value: written.append((key, value)),
    )

    body = client.put(
        "/api/dictation/settings", json={"language": "es", "persist": True}
    ).json()

    assert body["persisted"] is True
    assert ("language", "es") in written


def test_put_settings_rejects_an_unknown_language(client: TestClient) -> None:
    """The config coerces an unknown value to ``auto`` rather than failing."""
    body = client.put(
        "/api/dictation/settings", json={"language": "klingon", "persist": False}
    ).json()

    assert body["settings"]["language"] == "auto"


# ----------------------------------------------------------------------
# Status
# ----------------------------------------------------------------------


def test_status_reports_the_hands_free_shortcut(app: FastAPI) -> None:
    app.state.config.trigger.hotkey_dictate_toggle = "ctrl+shift+d"
    app.state.config.trigger.hotkey_dictate = "ctrl+shift+space"

    body = TestClient(app).get("/api/dictation/status").json()

    assert body["hotkey"] == "ctrl+shift+space"
    assert body["hotkey_toggle"] == "ctrl+shift+d"


def test_status_reports_an_unbound_hands_free_key_as_empty(client: TestClient) -> None:
    body = client.get("/api/dictation/status").json()

    assert "hotkey_toggle" in body
    assert isinstance(body["hotkey_toggle"], str)


# ----------------------------------------------------------------------
# History
# ----------------------------------------------------------------------


def test_history_hides_discarded_entries_by_default(client: TestClient) -> None:
    kept = _add()
    dropped = _add(text="book the flight", raw_text="book the flight")
    _history().set_discarded(dropped.id, True)

    body = client.get("/api/dictation/history").json()

    ids = [e["id"] for e in body["entries"]]
    assert kept.id in ids
    assert dropped.id not in ids
    assert body["count"] == 1


def test_history_includes_discarded_entries_when_asked(client: TestClient) -> None:
    """The UI opts in — a filtered-out row could never reach its Restore button."""
    kept = _add()
    dropped = _add(text="book the flight", raw_text="book the flight")
    _history().set_discarded(dropped.id, True)

    body = client.get("/api/dictation/history?include_discarded=true").json()

    ids = [e["id"] for e in body["entries"]]
    assert kept.id in ids
    assert dropped.id in ids
    row = next(e for e in body["entries"] if e["id"] == dropped.id)
    assert row["discarded"] is True


def test_history_never_exposes_the_audio_path(client: TestClient) -> None:
    entry = _add_failed_with_audio()
    assert entry.audio_path, "the fixture did not actually store audio"

    body = client.get("/api/dictation/history?include_discarded=true").json()
    row = next(e for e in body["entries"] if e["id"] == entry.id)

    assert "audio_path" not in row
    assert row["audio_available"] is True
    assert row["error"] == "provider 401"
    assert row["word_count"] == 0


def test_history_respects_the_limit(client: TestClient) -> None:
    for index in range(3):
        _add(text=f"line {index}", raw_text=f"line {index}")

    body = client.get("/api/dictation/history?limit=2").json()

    assert body["count"] == 2


# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------


def test_stats_report_lifetime_totals_from_the_sidecar(client: TestClient) -> None:
    _add()  # "send the report" -> three words
    _add(text="book the flight", raw_text="book the flight", duration_s=2.0)

    body = client.get("/api/dictation/stats").json()

    assert body["source"] == "lifetime"
    assert body["totals"]["dictations"] == 2
    assert body["totals"]["words"] == 6
    assert body["totals"]["wpm"] > 0
    assert body["today"]["dictations"] == 2
    assert body["streak"]["current_days"] == 1
    assert body["by_day"][0]["words"] == 6


def test_stats_say_so_when_they_are_only_a_window(client: TestClient) -> None:
    """An install predating the sidecar gets real numbers, honestly labelled."""
    _add()
    history = _history()
    history.stats_path.unlink()  # as if the counters had never existed

    body = client.get("/api/dictation/stats").json()

    assert body["source"] == "window"
    assert body["totals"]["words"] == 3
    # The UI names the period from this, so it must reflect the real retention.
    assert body["window"]["days"] == 30
    assert body["window"]["max_entries"] == 200


def test_stats_answer_on_an_install_that_has_never_dictated(
    client: TestClient,
) -> None:
    body = client.get("/api/dictation/stats").json()

    assert body["source"] == "window"
    assert body["totals"] == {"dictations": 0, "words": 0, "seconds": 0.0, "wpm": 0.0}
    assert body["streak"]["current_days"] == 0
    assert body["by_day"] == []


# ----------------------------------------------------------------------
# Discard
# ----------------------------------------------------------------------


def test_discard_soft_deletes_the_entry(client: TestClient) -> None:
    entry = _add()

    body = client.post(f"/api/dictation/history/{entry.id}/discard").json()

    assert body["ok"] is True
    assert body["entry"]["discarded"] is True
    assert "audio_path" not in body["entry"]
    # Still on disk — that is the whole difference from DELETE.
    stored = _history().get(entry.id)
    assert stored is not None
    assert stored.discarded is True


def test_discard_404s_on_an_unknown_id(client: TestClient) -> None:
    response = client.post("/api/dictation/history/nope/discard")

    assert response.status_code == 404


# ----------------------------------------------------------------------
# Restore
# ----------------------------------------------------------------------


def test_restore_undiscards_an_entry_that_still_has_its_text(
    client: TestClient,
) -> None:
    entry = _add()
    _history().set_discarded(entry.id, True)

    body = client.post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["ok"] is True
    assert body["entry"]["discarded"] is False
    assert body["entry"]["text"] == "send the report"
    # Nothing was re-transcribed: there was nothing to win back.
    assert body["retranscribed"] is False
    assert body["detail"] is None


def test_restore_retranscribes_from_the_kept_audio(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stt = _FakeSTT("call the studio")
    _install_pipeline(monkeypatch, stt)
    entry = _add_failed_with_audio()
    _history().set_discarded(entry.id, True)

    body = client.post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["retranscribed"] is True
    assert body["detail"] is None
    assert body["entry"]["text"] == "call the studio"
    assert body["entry"]["raw_text"] == "call the studio"
    assert body["entry"]["word_count"] == 3
    assert body["entry"]["discarded"] is False
    assert body["entry"]["error"] is None
    # The outcome stays "failed": the words came back, the delivery never
    # happened, and rewriting it would invent an insertion.
    assert body["entry"]["outcome"] == "failed"
    assert stt.calls and stt.calls[0][0] > 0


def test_restore_passes_the_pinned_language_to_the_provider(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    stt = _FakeSTT("ruf das studio an")
    _install_pipeline(monkeypatch, stt)
    app.state.config.dictation.language = "de"
    entry = _add_failed_with_audio()

    TestClient(app).post(f"/api/dictation/history/{entry.id}/restore")

    assert stt.calls[0][1] == "de"


def test_restore_falls_back_when_the_provider_predates_the_language_keyword(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare ``transcribe_pcm(pcm)`` is allowed by the contract; honour it."""
    stt = _FakeSTT("call the studio", accepts_language=False)
    _install_pipeline(monkeypatch, stt)
    app.state.config.dictation.language = "de"
    entry = _add_failed_with_audio()

    body = TestClient(app).post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["retranscribed"] is True
    assert body["entry"]["text"] == "call the studio"


def test_restore_degrades_honestly_with_no_provider_reachable(
    client: TestClient,
) -> None:
    """Headless: the entry comes back, the words do not, and it says why."""
    entry = _add_failed_with_audio()
    _history().set_discarded(entry.id, True)

    response = client.post(f"/api/dictation/history/{entry.id}/restore")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["retranscribed"] is False
    assert body["detail"]
    assert "speech-to-text" in body["detail"]
    assert body["entry"]["discarded"] is False


def test_restore_reports_a_provider_failure_without_500ing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_pipeline(monkeypatch, _BrokenSTT())
    entry = _add_failed_with_audio()

    response = client.post(f"/api/dictation/history/{entry.id}/restore")

    assert response.status_code == 200
    body = response.json()
    assert body["retranscribed"] is False
    assert "401" in body["detail"]
    assert body["entry"]["discarded"] is False


def test_restore_409s_when_there_is_nothing_to_recover(client: TestClient) -> None:
    entry = _add(raw_text="", text="", outcome="cancelled")

    response = client.post(f"/api/dictation/history/{entry.id}/restore")

    assert response.status_code == 409
    assert "nothing to restore" in response.json()["detail"].lower()


def test_restore_404s_on_an_unknown_id(client: TestClient) -> None:
    assert client.post("/api/dictation/history/nope/restore").status_code == 404


# ----------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------


def test_delete_entry_also_removes_the_audio_sidecar(client: TestClient) -> None:
    entry = _add_failed_with_audio()
    sidecar = Path(entry.audio_path)
    assert sidecar.is_file()

    body = client.delete(f"/api/dictation/history/{entry.id}").json()

    assert body["removed"] is True
    assert not sidecar.exists()


def test_delete_an_absent_entry_is_not_an_error(client: TestClient) -> None:
    response = client.delete("/api/dictation/history/nope")

    assert response.status_code == 200
    assert response.json()["removed"] is False


def test_clear_history_purges_audio_and_resets_the_counters(
    client: TestClient,
) -> None:
    _add()
    entry = _add_failed_with_audio()
    sidecar = Path(entry.audio_path)
    history = _history()
    assert history.stats_path.is_file()

    body = client.delete("/api/dictation/history").json()

    assert body["ok"] is True
    assert client.get("/api/dictation/history").json()["entries"] == []
    assert not sidecar.exists()
    # The streak resets with the history — the UI copy promises exactly this.
    stats = client.get("/api/dictation/stats").json()
    assert stats["totals"]["words"] == 0
    assert stats["streak"]["current_days"] == 0


# ----------------------------------------------------------------------
# Headless
# ----------------------------------------------------------------------


def test_every_read_route_answers_without_a_speech_pipeline(
    client: TestClient,
) -> None:
    """A headless VPS has no mic and no pipeline; nothing here may 500."""
    for path in (
        "/api/dictation/status",
        "/api/dictation/history",
        "/api/dictation/stats",
        "/api/dictation/settings",
    ):
        assert client.get(path).status_code == 200, path

    status = client.get("/api/dictation/status").json()
    assert status["available"] is False
    assert status["reason"]

    # Stopping nothing is not an error; starting without a mic says so.
    assert client.post("/api/dictation/stop").json() == {
        "ok": True,
        "stopped": False,
        "active": False,
    }
    assert client.post("/api/dictation/start", json={"target": "auto"}).status_code == 503


def test_routes_survive_an_app_with_no_config_at_all() -> None:
    """The launcher's early window: routes mounted before config lands."""
    bare = FastAPI()
    bare.include_router(dictation_router)
    bare_client = TestClient(bare)

    assert bare_client.get("/api/dictation/status").json()["hotkey_toggle"] == ""
    assert bare_client.get("/api/dictation/settings").json()["choices"]["language"]
    assert bare_client.get("/api/dictation/stats").json()["window"]["days"] == 30
