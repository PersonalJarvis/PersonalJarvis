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


def _patch_bundled(
    monkeypatch,
    *,
    wake: bool = True,
    vad: bool = True,
    wake_runtime: bool = True,
    silero_runtime: bool = True,
    webrtc: bool = True,
) -> None:
    monkeypatch.setattr(mr, "_wake_backbone_present", lambda: wake)
    monkeypatch.setattr(mr, "_vad_present", lambda: vad)
    monkeypatch.setattr(mr, "_neural_wake_runtime_available", lambda: wake_runtime)
    monkeypatch.setattr(mr, "_silero_runtime_available", lambda: silero_runtime)
    monkeypatch.setattr(mr, "_webrtc_vad_available", lambda: webrtc)
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


def test_full_profile_requires_every_onboarding_wake_model(monkeypatch) -> None:
    _patch_bundled(monkeypatch)
    monkeypatch.setattr(mr, "_supported_wake_languages", lambda: ("en", "de", "es"))
    monkeypatch.setattr(mr, "_vosk_present", lambda lang, _data: lang != "es")
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(mr, "_whisper_models_needed", lambda _cfg: ["base"])
    monkeypatch.setattr(mr, "_whisper_cached", lambda _name: True)

    items = mr.voice_model_report(_Cfg(), full_profile=True)

    wake = [i for i in items if "custom-wake model" in i.label]
    assert [i.label for i in wake] == [
        "custom-wake model 'en'",
        "custom-wake model 'de'",
        "custom-wake model 'es'",
    ]
    assert all(i.required for i in wake)
    assert mr.report_complete(items) is False


def test_full_profile_requires_local_whisper_and_cached_wake_model(monkeypatch) -> None:
    _patch_bundled(monkeypatch)
    monkeypatch.setattr(mr, "_supported_wake_languages", lambda: ("en", "de", "es"))
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(mr, "_whisper_models_needed", lambda _cfg: ["base"])
    monkeypatch.setattr(mr, "_whisper_cached", lambda _name: False)

    items = mr.voice_model_report(_Cfg(), full_profile=True)

    local = next(i for i in items if "local speech model 'base'" in i.label)
    assert local.required is True
    assert local.present is False
    assert mr.report_complete(items) is False


def test_full_profile_verifies_base_when_config_cannot_load(monkeypatch) -> None:
    _patch_bundled(monkeypatch)
    monkeypatch.setattr(mr, "_load_config", lambda: None)
    monkeypatch.setattr(mr, "_supported_wake_languages", lambda: ("en", "de", "es"))
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: True)
    checked: list[str] = []
    monkeypatch.setattr(
        mr,
        "_whisper_cached",
        lambda name: checked.append(name) or True,
    )

    items = mr.voice_model_report(full_profile=True)

    assert checked == ["base"]
    local = next(i for i in items if "local speech model 'base'" in i.label)
    assert local.required is True and local.present is True


def test_full_profile_fails_closed_when_language_catalog_cannot_load(monkeypatch) -> None:
    _patch_bundled(monkeypatch)
    monkeypatch.setattr(
        mr,
        "_supported_wake_languages",
        lambda: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
    )
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(mr, "_whisper_models_needed", lambda _cfg: ["base"])
    monkeypatch.setattr(mr, "_whisper_cached", lambda _name: True)

    items = mr.voice_model_report(_Cfg(), full_profile=True)

    catalog = next(i for i in items if "language catalog" in i.label)
    assert catalog.required is True and catalog.present is False
    assert mr.report_complete(items) is False


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

    _patch_bundled(monkeypatch)
    monkeypatch.setattr(mr, "_wake_backbone_present", _boom)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())  # must not raise

    wake = next(i for i in items if "wake word" in i.label)
    assert wake.present is False


def test_intel_mac_degrade_is_complete_with_honest_details(monkeypatch) -> None:
    # Assets shipped but no neural runtimes (e.g. Intel Mac): the install is
    # COMPLETE — wake degrades to vosk_kws, VAD degrades to WebRTC VAD.
    _patch_bundled(monkeypatch, wake_runtime=False, silero_runtime=False, webrtc=True)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())

    assert mr.report_complete(items) is True
    wake = next(i for i in items if "wake word" in i.label)
    vad = next(i for i in items if "end-of-speech" in i.label)
    assert wake.present is True and "vosk_kws" in wake.detail
    assert vad.present is True and "WebRTC VAD" in vad.detail
    text = "\n".join(mr.format_report(items))
    assert "✗" not in text  # degraded tiers render check-marks, never crosses


def test_no_neural_runtime_and_no_webrtc_degrades_to_energy(monkeypatch) -> None:
    _patch_bundled(monkeypatch, silero_runtime=False, webrtc=False)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())

    vad = next(i for i in items if "end-of-speech" in i.label)
    assert vad.present is True and "energy" in vad.detail
    assert mr.report_complete(items) is True


def test_asset_missing_with_runtime_available_stays_incomplete(monkeypatch) -> None:
    # Regression: a runnable platform with a dropped asset is a REAL failure.
    _patch_bundled(monkeypatch, wake=False, vad=False)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())

    assert mr.report_complete(items) is False
    wake = next(i for i in items if "wake word" in i.label)
    vad = next(i for i in items if "end-of-speech" in i.label)
    assert wake.present is False and "MISSING" in wake.detail
    assert vad.present is False and "MISSING" in vad.detail


def test_asset_missing_without_runtime_is_platform_degrade_not_failure(monkeypatch) -> None:
    # No runtime means the asset is unusable anyway — the degrade tier carries
    # the voice path, so the install must NOT read incomplete.
    _patch_bundled(monkeypatch, wake=False, vad=False, wake_runtime=False, silero_runtime=False)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())

    assert mr.report_complete(items) is True


def test_runtime_probe_failure_reads_as_degraded_never_raises(monkeypatch) -> None:
    # A crashing runtime probe reads as "runtime unavailable" -> honest degrade.
    def _boom() -> bool:
        raise RuntimeError("probe exploded")

    _patch_bundled(monkeypatch)
    monkeypatch.setattr(mr, "_neural_wake_runtime_available", _boom)
    monkeypatch.setattr(mr, "_silero_runtime_available", _boom)
    monkeypatch.setattr(mr, "_webrtc_vad_available", _boom)
    monkeypatch.setattr(mr, "_vosk_present", lambda *_a, **_kw: True)
    monkeypatch.setattr(mr, "_faster_whisper_available", lambda: False)

    items = mr.voice_model_report(_Cfg())  # must not raise

    wake = next(i for i in items if "wake word" in i.label)
    vad = next(i for i in items if "end-of-speech" in i.label)
    assert wake.present is True and "vosk_kws" in wake.detail
    assert vad.present is True and "energy" in vad.detail  # webrtc probe crashed too
    assert mr.report_complete(items) is True
