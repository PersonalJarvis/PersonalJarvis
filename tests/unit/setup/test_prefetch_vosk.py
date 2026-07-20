"""prefetch_all: provisions the per-language Vosk (any-word wake) model too."""
from jarvis.setup import prefetch


def _cfg(language: str | None):
    return type("C", (), {"stt": type("S", (), {"language": language})()})()


def test_prefetch_calls_vosk_ensure(monkeypatch):
    seen = {}

    def fake_ensure(language, **kw):
        seen["lang"] = language
        return object()

    monkeypatch.setattr(prefetch, "_ensure_vosk", fake_ensure, raising=False)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: False)
    monkeypatch.setattr(prefetch, "_load_config", lambda: _cfg("de"))
    lines = []
    rc = prefetch.prefetch_all(echo=lines.append)
    assert seen.get("lang") == "de"
    assert rc == 0


def test_full_prefetch_downloads_every_onboarding_language(monkeypatch):
    """The desktop installer runs before the user chooses a UI language."""
    seen: list[str | None] = []

    monkeypatch.setattr(
        prefetch,
        "_ensure_vosk",
        lambda language, **_kw: seen.append(language) or object(),
        raising=False,
    )
    monkeypatch.setattr(
        prefetch,
        "_supported_vosk_languages",
        lambda: ("en", "de", "es"),
    )
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: False)

    rc = prefetch.prefetch_all(
        echo=lambda _line: None,
        all_wake_languages=True,
    )

    assert rc == 0
    assert seen == ["en", "de", "es"]


def test_full_prefetch_continues_after_one_language_fails(monkeypatch):
    seen: list[str | None] = []

    def fake_ensure(language, **_kw):
        seen.append(language)
        return None if language == "de" else object()

    monkeypatch.setattr(prefetch, "_ensure_vosk", fake_ensure, raising=False)
    monkeypatch.setattr(
        prefetch,
        "_supported_vosk_languages",
        lambda: ("en", "de", "es"),
    )
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: False)

    rc = prefetch.prefetch_all(
        echo=lambda _line: None,
        all_wake_languages=True,
    )

    assert rc == 1
    assert seen == ["en", "de", "es"]


def test_vosk_none_result_marks_prefetch_failed(monkeypatch):
    """A non-fatal fetch miss (offline mirror, etc.) must flip the honest rc."""
    monkeypatch.setattr(prefetch, "_ensure_vosk", lambda *_a, **_kw: None, raising=False)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: False)
    monkeypatch.setattr(prefetch, "_load_config", lambda: _cfg("en"))
    rc = prefetch.prefetch_all(echo=lambda _line: None)
    assert rc == 1


def test_vosk_exception_is_nonfatal(monkeypatch):
    """An unexpected raise from the fetch seam must never abort prefetch."""

    def _boom(*_a, **_kw):
        raise OSError("mirror down")

    monkeypatch.setattr(prefetch, "_ensure_vosk", _boom, raising=False)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: False)
    monkeypatch.setattr(prefetch, "_load_config", lambda: _cfg("en"))
    lines = []
    rc = prefetch.prefetch_all(echo=lines.append)
    assert rc == 1
    assert any("wake model" in line for line in lines)


def test_vosk_language_guards_config_read_failure(monkeypatch):
    """A broken config read must never brick prefetch — resolves to None."""

    def _raise_cfg():
        raise RuntimeError("config missing")

    monkeypatch.setattr(prefetch, "_load_config", _raise_cfg)
    assert prefetch._vosk_language() is None
