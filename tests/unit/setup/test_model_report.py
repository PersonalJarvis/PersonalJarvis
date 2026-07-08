"""voice_model_report: honest on-disk verification of what the voice path needs.

Every probe is a module-level seam so these tests inject fakes without touching
a real config, the network, or the model cache. Mirrors the hermetic style of
test_prefetch.py.
"""

from jarvis.setup import model_report as mr


class _Stt:
    wake_model = "base"
    provider = "groq-api"  # cloud provider -> only the wake model is "needed"
    model = "large-v3-turbo"


class _Cfg:
    stt = _Stt()


def _patch_bundled(monkeypatch, *, wake: bool = True, vad: bool = True) -> None:
    monkeypatch.setattr(mr, "_wake_backbone_present", lambda: wake)
    monkeypatch.setattr(mr, "_vad_present", lambda: vad)
    monkeypatch.setattr(mr, "_wake_language", lambda _cfg: "de")


def test_all_required_present_is_complete(monkeypatch) -> None:
    _patch_bundled(monkeypatch)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())

    assert mr.report_complete(items) is True
    wake = next(i for i in items if "wake word" in i.label)
    assert wake.present is True and wake.required is True


def test_missing_bundled_backbone_marks_incomplete(monkeypatch) -> None:
    # A partial download that dropped the neural backbone must NOT read complete.
    _patch_bundled(monkeypatch, wake=False)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())

    assert mr.report_complete(items) is False
    wake = next(i for i in items if "wake word" in i.label)
    assert wake.present is False and wake.required is True


def test_absent_local_whisper_is_optional_not_a_failure(monkeypatch) -> None:
    # Cloud speech is the default: no local Whisper is by-design, not incomplete.
    _patch_bundled(monkeypatch)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())

    local = next(i for i in items if "local speech" in i.label)
    assert local.required is False
    assert local.present is False
    assert "cloud speech is the default" in local.detail
    assert mr.report_complete(items) is True


def test_pending_vosk_download_is_optional_not_a_failure(monkeypatch) -> None:
    _patch_bundled(monkeypatch)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: False)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())

    vosk = next(i for i in items if "custom-wake" in i.label)
    assert vosk.required is False and vosk.present is False
    assert mr.report_complete(items) is True  # required set is still satisfied


def test_installed_whisper_reports_cache_hit_per_model(monkeypatch) -> None:
    _patch_bundled(monkeypatch)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(mr, "_whisper_models_needed", lambda _cfg: ["base"])
    monkeypatch.setattr(mr, "_whisper_cached", lambda name: name == "base")

    items = mr.voice_model_report(_Cfg())

    whisper = next(i for i in items if "local speech model 'base'" in i.label)
    assert whisper.present is True


def test_format_report_marks_present_missing_and_optional(monkeypatch) -> None:
    _patch_bundled(monkeypatch, wake=False)  # required + absent -> hard cross
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: False)  # optional + absent -> dash
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())
    text = "\n".join(mr.format_report(items))

    assert "✓" in text  # a present item (VAD) renders a check
    assert "✗" in text  # the missing required backbone renders a cross
    assert "—" in text  # the pending optional item renders a dash


def test_probe_failure_reads_as_absent_never_raises(monkeypatch) -> None:
    # A probe that blows up must degrade to "absent", never crash the report.
    def _boom() -> bool:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(mr, "_wake_backbone_present", _boom)
    monkeypatch.setattr(mr, "_vad_present", lambda: True)
    monkeypatch.setattr(mr, "_wake_language", lambda _cfg: "de")
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())  # must not raise

    wake = next(i for i in items if "wake word" in i.label)
    assert wake.present is False
