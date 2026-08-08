"""The polish pass as it is reachable over REST: Restore parity and the dry run.

Two contracts live here.

**Restore must produce the same text the live delivery would have.** It
re-transcribes the kept audio, and until it ran the identical chain — resolve
the language, remove fillers, repair the punctuation our own segment boundaries
broke, polish — pressing "Restore" on a failed dictation gave the user a
different sentence than the one they would have got had the provider answered
the first time. That is not a small inconsistency: the whole promise of the
button is "give me back what I said".

**The dry run is the only way to see the pass at all.** It is invisible when it
works and silently falls back to the raw text when it does not, so "is it on,
who answers, and what does it cost me" has no other answer. It also has to be
safe on a host that holds no key for it, which is most hosts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.dictation.polish import POLISH_STATUSES, PolishOutcome
from jarvis.ui.web.dictation_routes import router as dictation_router


@pytest.fixture(autouse=True)
def _sandbox_user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


@pytest.fixture
def app() -> FastAPI:
    from jarvis.core.config import DictationConfig, TriggerConfig

    application = FastAPI()
    application.include_router(dictation_router)
    application.state.config = SimpleNamespace(
        trigger=TriggerConfig(), dictation=DictationConfig(polish=False)
    )
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@dataclass
class _Transcript:
    text: str
    language: str = "en"
    #: What the provider decoded before its own cleanup filter ran. Empty for a
    #: provider that does not filter, which is what the fallback below covers.
    raw_text: str = ""


class _FakeSTT:
    def __init__(
        self, text: str, language: str = "en", *, raw_text: str = ""
    ) -> None:
        self._text = text
        self._language = language
        self._raw_text = raw_text
        self.calls: list[str | None] = []

    async def transcribe_pcm(self, pcm: bytes, language: str | None = None) -> Any:
        self.calls.append(language)
        return _Transcript(
            text=self._text, language=self._language, raw_text=self._raw_text
        )


def _install_pipeline(monkeypatch: pytest.MonkeyPatch, **attrs: Any) -> Any:
    pipeline = SimpleNamespace(**attrs)
    monkeypatch.setattr(
        "jarvis.core.runtime_refs.get_speech_pipeline", lambda: pipeline
    )
    return pipeline


def _history() -> Any:
    from jarvis.dictation.history import DictationHistory

    return DictationHistory()


def _failed_with_audio() -> Any:
    """A dictation that produced nothing, with its audio sidecar kept."""
    from jarvis.dictation.audio import save_dictation_audio

    history = _history()
    entry = history.add(
        raw_text="", text="", outcome="failed", error="provider 401", duration_s=6.0
    )
    assert entry is not None
    path = save_dictation_audio(
        entry.id, b"\x00\x01" * 8_000, directory=history.audio_dir
    )
    assert path is not None
    updated = history.update(entry.id, audio_path=str(path))
    assert updated is not None
    return updated


# ----------------------------------------------------------------------
# Restore runs the identical chain
# ----------------------------------------------------------------------


def test_restore_repairs_the_punctuation_the_segmenting_broke(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-transcription comes back with the same segment-join damage the
    live path would have seen, and must be repaired the same way."""
    stt = _FakeSTT("We talked about it. ... and then the report went out.")
    _install_pipeline(monkeypatch, _utterance_stt=stt)
    entry = _failed_with_audio()

    body = client.post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["retranscribed"] is True
    assert "...." not in body["entry"]["text"]
    assert " ... and" not in body["entry"]["text"]
    # The raw column keeps what the provider returned, damage included — that
    # is what makes the repair auditable.
    assert "..." in body["entry"]["raw_text"]


def test_restore_starts_from_the_providers_raw_decode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider that cleans its own transcript must not shortcut the chain.

    Every provider now filters its text before returning it, and dictation is
    the one caller that must not read that: Restore re-runs the SAME chain the
    live lane runs — filler removal under the user's switch, punctuation
    repair, polish — so starting from an already-cleaned string would apply the
    cleanup twice and give the user back different words than the dictation
    produced. The raw column would stop being raw with it, which is what makes
    the repair auditable in the first place.
    """
    stt = _FakeSTT(
        "We talked about it.",  # what the provider's own filter produced
        raw_text="Umm, we talked about it. ... and then the report went out.",
    )
    _install_pipeline(monkeypatch, _utterance_stt=stt)
    entry = _failed_with_audio()

    body = client.post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["retranscribed"] is True
    # The raw column is the provider's decode, not its cleaned answer.
    assert body["entry"]["raw_text"].startswith("Umm, we talked about it.")
    # And the chain ran over that decode: the second sentence only exists in
    # the raw string, so its presence proves Restore did not read ``text``.
    assert "report went out" in body["entry"]["text"]
    assert not body["entry"]["text"].startswith("Umm,")


def test_restore_falls_back_to_text_for_a_provider_without_a_raw_decode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Providers that set no ``raw_text`` behave exactly as they did before."""
    stt = _FakeSTT("Umm, we talked about it.")
    _install_pipeline(monkeypatch, _utterance_stt=stt)
    entry = _failed_with_audio()

    body = client.post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["entry"]["raw_text"] == "Umm, we talked about it."
    assert body["entry"]["text"] == "We talked about it."


def test_restore_polishes_when_the_pass_is_on(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.dictation.polish as polish

    app.state.config.dictation.polish = True
    seen: list[str] = []

    async def _fake(raw: str, **_kwargs: Any) -> PolishOutcome:
        seen.append(raw)
        return PolishOutcome(
            text="The report went out on Tuesday.",
            status="applied",
            provider="groq",
            latency_ms=200,
        )

    monkeypatch.setattr(polish, "polish_transcript", _fake)
    stt = _FakeSTT("so um the report went out on tuesday")
    _install_pipeline(monkeypatch, _utterance_stt=stt)
    entry = _failed_with_audio()

    body = TestClient(app).post(f"/api/dictation/history/{entry.id}/restore").json()

    assert seen, "the polish pass was never asked"
    assert body["entry"]["text"] == "The report went out on Tuesday."
    # Raw stays raw. Without this the user has no way back to their own words
    # once a polish pass has rewritten a restored dictation.
    assert body["entry"]["raw_text"] == "so um the report went out on tuesday"


def test_restore_delivers_the_words_when_the_polish_pass_fails(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.dictation.polish as polish

    app.state.config.dictation.polish = True

    async def _boom(raw: str, **_kwargs: Any) -> PolishOutcome:
        raise RuntimeError("the module blew up")

    monkeypatch.setattr(polish, "polish_transcript", _boom)
    _install_pipeline(monkeypatch, _utterance_stt=_FakeSTT("call the studio back"))
    entry = _failed_with_audio()

    body = TestClient(app).post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["retranscribed"] is True
    assert body["entry"]["text"] == "call the studio back"


def test_restore_stores_one_language_code_not_a_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cloud Whisper APIs report the English NAME. Four spellings for two
    languages is what the live history had, and a consumer doing
    ``{"de": ...}.get(lang)`` misses on every one of them."""
    stt = _FakeSTT("Das Dokument geht gleich raus.", language="German")  # i18n-allow: fixture
    _install_pipeline(monkeypatch, _utterance_stt=stt)
    entry = _failed_with_audio()

    body = client.post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["entry"]["language"] == "de"


def test_restore_keeps_a_language_the_resolver_cannot_place(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coercing it would relabel a Japanese dictation as English, which is worse
    than the drift being fixed."""
    stt = _FakeSTT("ドキュメントを送ります", language="ja-JP")  # i18n-allow: fixture
    _install_pipeline(monkeypatch, _utterance_stt=stt)
    entry = _failed_with_audio()

    body = client.post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["entry"]["language"] == "ja"


def test_restore_uses_the_dictation_provider_when_there_is_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dictation lane holds its own instance — no voice bias prompt, its own
    fallback chain. Reaching past it would transcribe the same audio under
    different decoder priming than the dictation that produced it."""
    voice = _FakeSTT("the voice provider answered")
    dictation = _FakeSTT("the dictation provider answered")
    _install_pipeline(
        monkeypatch, _utterance_stt=voice, _dictation_stt=lambda: dictation
    )
    entry = _failed_with_audio()

    body = client.post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["entry"]["raw_text"] == "the dictation provider answered"
    assert voice.calls == []


def test_restore_falls_back_to_the_voice_provider_on_an_older_pipeline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This may only ever add correctness, never remove a working path."""
    voice = _FakeSTT("the voice provider answered")

    def _broken() -> Any:
        raise RuntimeError("no config on this instance")

    _install_pipeline(monkeypatch, _utterance_stt=voice, _dictation_stt=_broken)
    entry = _failed_with_audio()

    body = client.post(f"/api/dictation/history/{entry.id}/restore").json()

    assert body["entry"]["raw_text"] == "the voice provider answered"


# ----------------------------------------------------------------------
# The dry run
# ----------------------------------------------------------------------


def test_the_dry_run_reports_a_switched_off_pass_instead_of_refusing(
    client: TestClient,
) -> None:
    """"You switched it off" is a complete answer to "why is nothing being
    polished"; a 409 would render an error for a working config."""
    body = client.post("/api/dictation/polish/test").json()

    assert body["status"] == "off"
    assert body["sample_out"] == body["sample_in"]
    assert body["latency_ms"] == 0


def test_the_dry_run_is_honest_on_a_host_with_no_key(
    app: FastAPI,
) -> None:
    """Most installs. The sample comes back unchanged and says why — the same
    answer a real dictation would get (AP-23)."""
    app.state.config.dictation.polish = True

    body = TestClient(app).post("/api/dictation/polish/test").json()

    assert body["status"] == "unavailable"
    assert body["status"] in POLISH_STATUSES
    assert body["sample_out"] == body["sample_in"]
    assert body["provider"] == ""


def test_the_dry_run_reports_who_answered_and_what_it_cost(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.dictation.polish as polish

    app.state.config.dictation.polish = True
    captured: list[dict[str, Any]] = []

    async def _fake(raw: str, **kwargs: Any) -> PolishOutcome:
        captured.append({"raw": raw, **kwargs})
        return PolishOutcome(
            text="So I think we should ship the report on Wednesday.",
            status="applied",
            provider="groq",
            model="llama-3.1-8b-instant",
            latency_ms=318,
        )

    monkeypatch.setattr(polish, "polish_transcript", _fake)

    body = TestClient(app).post("/api/dictation/polish/test").json()

    assert body["status"] == "applied"
    assert body["provider"] == "groq"
    assert body["model"] == "llama-3.1-8b-instant"
    assert body["latency_ms"] == 318
    assert body["sample_out"] != body["sample_in"]
    # The sample is a real transcript shape, not a demo sentence: it has to
    # exercise what the pass is for.
    assert "..." in captured[0]["raw"]


def test_the_dry_run_never_500s_when_the_pass_is_unreachable(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.dictation.polish as polish

    app.state.config.dictation.polish = True

    async def _slow(raw: str, **_kwargs: Any) -> PolishOutcome:
        await asyncio.sleep(0)
        return PolishOutcome(text=raw, status="provider_error", reason="unexpected")

    monkeypatch.setattr(polish, "polish_transcript", _slow)

    response = TestClient(app).post("/api/dictation/polish/test")

    assert response.status_code == 200
    assert response.json()["status"] == "provider_error"
    assert response.json()["reason"] == "unexpected"


def test_the_dry_run_is_reachable_from_the_cli_surface(app: FastAPI) -> None:
    """Tagged ``dictation``, so it becomes a ``jarvis api dictation`` command
    for free — the CLI-first contract (CLAUDE.md §5)."""
    spec = app.openapi()["paths"]["/api/dictation/polish/test"]["post"]

    assert spec["tags"] == ["dictation"]
    # Non-destructive: it writes nothing and touches no history row.
    assert "x-jarvis-dangerous" not in spec


def test_the_settings_route_offers_the_polish_dropdowns(client: TestClient) -> None:
    """A key in ``DICTATION_SETTING_KEYS`` with no ``choices`` entry renders an
    empty dropdown the user cannot pick anything out of."""
    from jarvis.core.config import POLISH_STYLES
    from jarvis.dictation.polish_client import POLISH_FAMILIES

    choices = client.get("/api/dictation/settings").json()["choices"]

    assert choices["polish_style"] == list(POLISH_STYLES)
    assert choices["polish_provider"][0] == "auto"
    assert {f.id for f in POLISH_FAMILIES} <= set(choices["polish_provider"])


def test_the_settings_route_persists_a_polish_change(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FastAPI DROPS body keys the model does not declare, so a missing field
    here is a toggle that appears to save and is gone on the next restart."""
    from jarvis.core import config_writer

    written: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        config_writer,
        "set_dictation_setting",
        lambda key, value, **_kw: written.append((key, value)),
    )

    body = TestClient(app).put(
        "/api/dictation/settings",
        json={"polish": True, "polish_provider": "gemini", "polish_style": "email"},
    )

    assert body.status_code == 200
    assert body.json()["settings"]["polish"] is True
    assert body.json()["settings"]["polish_provider"] == "gemini"
    assert body.json()["settings"]["polish_style"] == "email"
    assert ("polish", True) in written
    assert ("polish_provider", "gemini") in written
    assert ("polish_style", "email") in written


def test_the_settings_route_accepts_a_provider_card_id_as_a_pin(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider CARD is "openai-polish"; the config stores "openai".

    A client holding a card sent the card id, the config accepted the string
    unchecked, and ``resolve_polish_chain`` then ignored the unknown family in
    favour of the auto order — a 200, a success toast, and the previously
    active provider still active. The route translates the card vocabulary
    instead, so the pin the user clicked is the pin that gets stored.
    """
    from jarvis.core import config_writer

    written: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        config_writer,
        "set_dictation_setting",
        lambda key, value, **_kw: written.append((key, value)),
    )

    body = TestClient(app).put(
        "/api/dictation/settings", json={"polish_provider": "openai-polish"}
    )

    assert body.status_code == 200
    assert body.json()["settings"]["polish_provider"] == "openai"
    assert ("polish_provider", "openai") in written


def test_the_settings_route_refuses_an_unknown_polish_provider(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pin nothing answers to resolves exactly like ``auto``, so accepting it
    would report success for a setting that does nothing (AP-31). The config
    model stays permissive on purpose (a hand-edited file must still boot,
    AP-16) — being loud about it is this route's job, like ``paste_chord``."""
    from jarvis.core import config_writer

    written: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        config_writer,
        "set_dictation_setting",
        lambda key, value, **_kw: written.append((key, value)),
    )

    body = TestClient(app).put(
        "/api/dictation/settings", json={"polish_provider": "not-a-provider"}
    )

    assert body.status_code == 400
    # The message names what was refused AND what would work.
    assert "not-a-provider" in body.json()["detail"]
    assert "auto" in body.json()["detail"]
    assert written == []


def test_the_settings_route_still_takes_auto(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``auto`` is not a family — it is the key-aware chain that crosses
    families on its own (AP-22), and the default. It must survive the check."""
    from jarvis.core import config_writer

    written: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        config_writer,
        "set_dictation_setting",
        lambda key, value, **_kw: written.append((key, value)),
    )

    body = TestClient(app).put(
        "/api/dictation/settings", json={"polish_provider": "auto"}
    )

    assert body.status_code == 200
    assert body.json()["settings"]["polish_provider"] == "auto"
    assert ("polish_provider", "auto") in written


# --------------------------------------------------------------------------
# Switching translation on and off has to take effect NOW, not on restart
# --------------------------------------------------------------------------


def test_translation_switches_take_effect_without_a_restart(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported failure: switch it off, and dictations keep translating.

    Two objects have to learn about a saved setting for it to be live — the
    config the REST layer holds and the copy the RUNNING pipeline reads on the
    delivery path — and a save that updates only the first produces exactly the
    symptom the user described: the settings screen shows the new value, and
    every dictation keeps behaving like the old one until the app is restarted.

    Asserted through ``resolve_translate_target`` rather than by reading the
    attribute, because that function is what the delivery path actually asks.
    """
    from jarvis.core import config_writer
    from jarvis.dictation.polish import resolve_translate_target

    monkeypatch.setattr(
        config_writer, "set_dictation_setting", lambda key, value, **_kw: None
    )
    pipeline = SimpleNamespace(_dictation_cfg=app.state.config.dictation)
    monkeypatch.setattr(
        "jarvis.ui.web.dictation_routes._pipeline", lambda: pipeline
    )
    client = TestClient(app)

    on = client.put(
        "/api/dictation/settings", json={"translate": True, "translate_target": "zh"}
    )
    assert on.status_code == 200
    assert on.json()["applied_live"] is True
    assert resolve_translate_target(pipeline._dictation_cfg) == "zh"

    # Re-pointing the target is live too — the reported "I chose Chinese and
    # kept getting English".
    client.put("/api/dictation/settings", json={"translate_target": "ja"})
    assert resolve_translate_target(pipeline._dictation_cfg) == "ja"

    off = client.put("/api/dictation/settings", json={"translate": False})
    assert off.status_code == 200
    assert resolve_translate_target(pipeline._dictation_cfg) == ""


def test_a_saved_translation_setting_survives_the_restart_too(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live is only half of it: the keys must reach jarvis.toml as well.

    A key missing from ``DICTATION_SETTING_KEYS`` is a switch that works
    perfectly until the next restart and then silently reverts.
    """
    from jarvis.core import config_writer

    written: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        config_writer,
        "set_dictation_setting",
        lambda key, value, **_kw: written.append((key, value)),
    )

    body = TestClient(app).put(
        "/api/dictation/settings", json={"translate": True, "translate_target": "zh"}
    )

    assert body.status_code == 200
    assert ("translate", True) in written
    assert ("translate_target", "zh") in written


def test_switching_translation_off_round_trips_through_disk_and_reload(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The false value is data, not an omitted optional field.

    This is the restart regression in its complete shape: start from an enabled
    file, send the same payload as the desktop switch, then construct a fresh
    config from disk. Mocking the writer or asserting only the live object would
    miss the exact failure the user sees after relaunching the app.
    """
    from jarvis.core import config_writer
    from jarvis.core.config import load_config

    config_file = tmp_path / "jarvis.toml"
    config_file.write_text("[dictation]\ntranslate = true\n", encoding="utf-8")
    real_set = config_writer.set_dictation_setting
    monkeypatch.setattr(
        config_writer,
        "set_dictation_setting",
        lambda key, value, **_kw: real_set(key, value, path=config_file),
    )
    app.state.config.dictation.translate = True

    response = TestClient(app).put(
        "/api/dictation/settings", json={"translate": False}
    )

    assert response.status_code == 200
    assert response.json()["persisted"] is True
    assert response.json()["settings"]["translate"] is False
    assert load_config(config_file).dictation.translate is False


def test_failed_translation_save_is_not_applied_only_until_restart(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed disk write must not masquerade as a successful switch.

    Previously the live config changed first and the write failure was reduced
    to ``persisted=false`` in an otherwise successful response. The UI ignored
    that flag, so the toggle looked off until restart loaded the still-on file.
    """
    from jarvis.core import config_writer

    app.state.config.dictation.translate = True

    def _fail_write(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("simulated write denial")

    monkeypatch.setattr(config_writer, "set_dictation_setting", _fail_write)

    response = TestClient(app).put(
        "/api/dictation/settings", json={"translate": False}
    )

    assert response.status_code == 500
    assert "could not be saved" in response.json()["detail"]
    assert app.state.config.dictation.translate is True
