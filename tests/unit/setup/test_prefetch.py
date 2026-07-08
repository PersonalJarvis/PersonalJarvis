"""prefetch_all: resolves the same models the runtime uses; degrades cleanly."""
from jarvis.setup import prefetch


def test_reports_bundled_wakeword(monkeypatch) -> None:
    lines: list[str] = []
    monkeypatch.setattr(prefetch, "_wakeword_bundle_present", lambda: True)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: False)
    # Hermetic: the vosk wake-model seam must not touch the real config or network.
    monkeypatch.setattr(prefetch, "_ensure_vosk", lambda *_a, **_kw: object(), raising=False)
    rc = prefetch.prefetch_all(echo=lines.append)
    assert rc == 0
    assert any("wake-word models" in line for line in lines)
    assert any("skipped" in line.lower() for line in lines)  # whisper skipped


def test_downloads_wake_model_when_faster_whisper_present(monkeypatch) -> None:
    downloaded: list[str] = []
    monkeypatch.setattr(prefetch, "_wakeword_bundle_present", lambda: True)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(prefetch, "_download_whisper_model", downloaded.append)
    monkeypatch.setattr(prefetch, "_whisper_models_needed", lambda: ["base"])
    monkeypatch.setattr(prefetch, "_ensure_vosk", lambda *_a, **_kw: object(), raising=False)
    rc = prefetch.prefetch_all(echo=lambda _line: None)
    assert rc == 0
    assert downloaded == ["base"]


def test_download_failure_is_nonfatal(monkeypatch) -> None:
    def _boom(_name: str) -> None:
        raise OSError("mirror down")

    monkeypatch.setattr(prefetch, "_wakeword_bundle_present", lambda: True)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(prefetch, "_download_whisper_model", _boom)
    monkeypatch.setattr(prefetch, "_whisper_models_needed", lambda: ["base"])
    monkeypatch.setattr(prefetch, "_ensure_vosk", lambda *_a, **_kw: object(), raising=False)
    lines: list[str] = []
    rc = prefetch.prefetch_all(echo=lines.append)
    assert rc == 1
    assert any("first launch" in line for line in lines)  # honest fallback note


def test_whisper_models_needed_includes_utterance_model_for_local_provider(
    monkeypatch,
) -> None:
    class _Stt:
        wake_model = "base"
        provider = "faster-whisper"
        model = "large-v3-turbo"

    class _Cfg:
        stt = _Stt()

    monkeypatch.setattr(prefetch, "_load_config", lambda: _Cfg())
    assert prefetch._whisper_models_needed() == ["base", "large-v3-turbo"]


def test_whisper_models_needed_wake_only_for_cloud_provider(monkeypatch) -> None:
    class _Stt:
        wake_model = "base"
        provider = "groq-api"
        model = "large-v3-turbo"

    class _Cfg:
        stt = _Stt()

    monkeypatch.setattr(prefetch, "_load_config", lambda: _Cfg())
    assert prefetch._whisper_models_needed() == ["base"]
