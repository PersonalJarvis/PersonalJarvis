"""A trained custom_onnx wake model is calibrated differently from the pretrained
openWakeWord models, so it needs its own threshold band.

Live forensic 2026-07-01: a user-trained neural model fires on normal-volume
speech at ~0.9 and on breath/other words well below, while the pretrained OWW
models fire at ~0.15-0.23. Feeding a custom model the low 0.06-0.30 sensitivity
band false-fires; feeding it the amplify-only wake AGC lifts quiet BREATH to full
scale and false-fires at ~1.0. This pins the calibrated custom-model threshold
band (the AGC-off wiring lives in the pipeline OWW construction). Pure Python, so
it is identical on Windows, Linux and macOS.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _no_vosk_model(monkeypatch):
    """Isolate from any per-install Vosk model: this module pins the custom_onnx
    vs stt_match/none chain. The vosk_kws chain has its own suite in
    test_wake_plan_vosk.py."""
    import jarvis.speech.wake_phrase as wp

    monkeypatch.setattr(wp, "resolve_vosk_model_path", lambda *_: None)


def _cfg(
    model_path: str,
    sensitivity: float = 0.5,
    phrase: str = "Hey Nico",
    engine: str = "custom_onnx",
) -> SimpleNamespace:
    return SimpleNamespace(
        phrase=phrase,
        engine=engine,
        custom_model_path=model_path,
        sensitivity=sensitivity,
        fuzzy_match_ratio=0.8,
    )


def test_custom_onnx_uses_calibrated_higher_threshold(tmp_path) -> None:
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "hey_nico.onnx"
    model.write_bytes(b"onnx")  # resolve only checks the path is a file
    plan = resolve_wake_plan(_cfg(str(model)), local_whisper_available=False)
    assert plan.engine == "custom_onnx"
    # A calibrated custom model uses a HIGH threshold (~0.5 at mid sensitivity),
    # NOT the 0.15 pretrained band that false-fires on breath/other words.
    assert plan.threshold >= 0.45, plan.threshold


def test_custom_onnx_threshold_scales_strict_to_sensitive(tmp_path) -> None:
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "m.onnx"
    model.write_bytes(b"onnx")

    def thr(s: float) -> float:
        return resolve_wake_plan(_cfg(str(model), s), local_whisper_available=False).threshold

    strict, sensitive = thr(0.0), thr(1.0)
    assert strict > sensitive, (strict, sensitive)   # 0.0 = least likely to fire
    assert 0.60 <= strict <= 0.75
    assert 0.25 <= sensitive <= 0.35


# ---------------------------------------------------------------------------
# A stale custom model must NOT hijack a NEW wake phrase.
#
# Live bug 2026-07-02: the user changed the wake phrase to "Hey Fable"
# (engine Auto) in Settings, but jarvis.toml still carried
# custom_model_path=hey_nico.onnx from the earlier trained phrase — and the
# resolver's "any custom path wins" rule kept the NICO model as the detector,
# so the new phrase was deaf ("only Hey Nico still works"). Rule now:
# a custom model is auto-adopted only when it BELONGS to the configured
# phrase (model filename tokens, sound-folded); an explicit
# engine="custom_onnx" still forces it (user's own naming is their choice).
# ---------------------------------------------------------------------------


def test_stale_custom_model_does_not_hijack_new_phrase(tmp_path) -> None:
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "hey_nico.onnx"
    model.write_bytes(b"onnx")
    plan = resolve_wake_plan(
        _cfg(str(model), phrase="Hey Fable", engine="auto"),
        local_whisper_available=True,
    )
    assert plan.engine == "stt_match", plan
    # The regular any-phrase path, not a degraded fallback.
    assert plan.degraded is False
    assert plan.matcher.search("hey fable") is not None
    assert "different phrase" in plan.message.lower()


def test_matching_custom_model_still_adopted_on_auto(tmp_path) -> None:
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "hey_nico.onnx"
    model.write_bytes(b"onnx")
    plan = resolve_wake_plan(
        _cfg(str(model), phrase="Hey Nico", engine="auto"),
        local_whisper_available=True,
    )
    assert plan.engine == "custom_onnx"
    assert plan.oww_model_path == str(model)


def test_sound_folded_spelling_still_owns_the_model(tmp_path) -> None:
    # "Hey Niko" vs hey_nico.onnx: sound-folding makes the tokens compare
    # equal, so a spelling variant of the SAME word keeps the trained model.
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "hey_nico.onnx"
    model.write_bytes(b"onnx")
    plan = resolve_wake_plan(
        _cfg(str(model), phrase="Hey Niko", engine="auto"),
        local_whisper_available=True,
    )
    assert plan.engine == "custom_onnx"


def test_explicit_custom_engine_keeps_arbitrarily_named_model(tmp_path) -> None:
    # engine="custom_onnx" is the user's explicit choice — a model file named
    # differently from the phrase (their own training, their own naming) must
    # still be honoured.
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "my_wake.onnx"
    model.write_bytes(b"onnx")
    plan = resolve_wake_plan(
        _cfg(str(model), phrase="Friday", engine="custom_onnx"),
        local_whisper_available=True,
    )
    assert plan.engine == "custom_onnx"
    assert plan.oww_model_path == str(model)


def test_stale_custom_model_lets_other_phrases_through_generically(tmp_path) -> None:
    # A stale custom model must not block the generic chain either: with the
    # phrase set to "Hey Jarvis", the generic engine serves it (design
    # 2026-07-07: no bundled model wins anything).
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "hey_nico.onnx"
    model.write_bytes(b"onnx")
    plan = resolve_wake_plan(
        _cfg(str(model), phrase="Hey Jarvis", engine="auto"),
        local_whisper_available=True,
    )
    assert plan.engine == "stt_match"
    assert plan.oww_model_path is None
    assert plan.degraded is False


def test_stale_custom_model_without_whisper_is_hotkey_only(tmp_path) -> None:
    # Mismatching model + no local Whisper: the wake word is OFF (hotkey-only,
    # product rule 2026-07-04) with a clear message — never a silent branded
    # fallback and never a dead listener.
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "hey_nico.onnx"
    model.write_bytes(b"onnx")
    plan = resolve_wake_plan(
        _cfg(str(model), phrase="Hey Fable", engine="auto"),
        local_whisper_available=False,
    )
    assert plan.engine == "none"
    assert plan.wake_available is False
    assert plan.degraded is True


def test_custom_onnx_requires_prefix_verify(tmp_path) -> None:
    # Live forensic 2026-07-01 (false-positive storm): a user-trained custom
    # model is a WEAK discriminator — it scored breath/ambient/other speech up
    # to 1.000 and fired several times a minute even at threshold 0.50. Trusting
    # the model alone ("it IS its own discriminator") was the root cause, so a
    # custom_onnx hit MUST run the second-stage STT verify against the phrase's
    # own sound-folded fuzzy matcher (works for ANY configured wake word).
    from jarvis.speech.wake_phrase import resolve_wake_plan

    model = tmp_path / "m.onnx"
    model.write_bytes(b"onnx")
    plan = resolve_wake_plan(_cfg(str(model)), local_whisper_available=False)
    assert plan.verify_prefix is True
