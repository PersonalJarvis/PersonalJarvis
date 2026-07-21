"""Vosk model fetch: idempotent, layout matches resolve_vosk_model_path,
hash-checked, never fatal offline."""
import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

from jarvis.speech import wake_model_fetch as wmf
from jarvis.speech.wake_constants import resolve_vosk_model_path


def _fake_model_zip() -> bytes:
    """A minimal zip that resolve_vosk_model_path will accept as a model:
    top folder vosk-model-small-de-0.15/ with an am/ dir + conf/model.conf."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("vosk-model-small-de-0.15/am/final.mdl", b"x")
        z.writestr("vosk-model-small-de-0.15/conf/model.conf", b"y")
    return buf.getvalue()


def test_lang_normalization():
    assert wmf.vosk_lang_for("de-DE") == "de"
    assert wmf.vosk_lang_for("de") == "de"
    assert wmf.vosk_lang_for("es") == "es"
    assert wmf.vosk_lang_for("auto") == "en"   # DEFAULT_LOCALE fallback
    assert wmf.vosk_lang_for(None) == "en"
    assert wmf.vosk_lang_for("fr") == "en"     # unsupported -> default


def test_ensure_downloads_extracts_and_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    data = _fake_model_zip()
    # Pin the fake zip's hash so the fetch's fail-closed check passes here.
    monkeypatch.setitem(
        wmf.VOSK_MODELS, "de",
        wmf.VoskModelSpec(
            zip_name="vosk-model-small-de-0.15.zip",
            sha256=hashlib.sha256(data).hexdigest(),
        ),
    )

    def fake_get(url: str) -> bytes:
        assert url.endswith("vosk-model-small-de-0.15.zip")
        return data

    out = wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    assert out is not None
    # The layout must be exactly what the resolver accepts.
    resolved = resolve_vosk_model_path("de")  # honors JARVIS__MEMORY__DATA_DIR
    assert resolved is not None
    assert Path(resolved).name.startswith("vosk-model-small-de")


def test_ensure_is_idempotent_noop_when_present(tmp_path, monkeypatch):
    calls = {"n": 0}
    data = _fake_model_zip()

    def fake_get(url: str) -> bytes:
        calls["n"] += 1
        return data

    monkeypatch.setitem(
        wmf.VOSK_MODELS, "de",
        wmf.VoskModelSpec("vosk-model-small-de-0.15.zip",
                          hashlib.sha256(data).hexdigest()),
    )
    wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    assert calls["n"] == 1, "second call must be a no-op (already present)"


def test_hash_mismatch_rejects_and_is_nonfatal(tmp_path):
    def fake_get(url: str) -> bytes:
        return b"corrupt-not-a-zip"

    out = wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    assert out is None  # rejected, never installed, never raised


def test_true_hash_mismatch_rejects_and_is_nonfatal(tmp_path, monkeypatch):
    """Valid zip bytes with mismatched sha256 in spec — fail-closed guard."""
    data = _fake_model_zip()

    def fake_get(url: str) -> bytes:
        return data

    # Patch spec with a real-but-wrong hash (not empty, so fail-closed check triggers).
    monkeypatch.setitem(
        wmf.VOSK_MODELS, "de",
        wmf.VoskModelSpec("vosk-model-small-de-0.15.zip", sha256="0" * 64),
    )
    out = wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    assert out is None, "valid zip with mismatched sha256 must be rejected"
    assert not wmf.vosk_model_present("de", data_dir=str(tmp_path)), "model must not be installed"


def test_offline_failure_is_nonfatal(tmp_path):
    def boom(url: str) -> bytes:
        raise OSError("network down")

    out = wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=boom)
    assert out is None


# ---------------------------------------------------------------------------
# resolve_wake_language: trigger.wake_word.language pin (if concrete) ->
# stt.language (if concrete) -> ui.language -> DEFAULT_LOCALE
# ---------------------------------------------------------------------------


def _pin(language: str) -> SimpleNamespace:
    return SimpleNamespace(wake_word=SimpleNamespace(language=language))


def test_resolve_wake_language_pin_beats_stt_and_ui():
    """The explicit wake-word pin is the user's independent choice — it wins
    over BOTH the recognition language and the app display language (the
    decoupling mandate 2026-07-21: app in English, wake word in German)."""
    cfg = SimpleNamespace(
        trigger=_pin("de"),
        stt=SimpleNamespace(language="es"),
        ui=SimpleNamespace(language="en"),
    )
    assert wmf.resolve_wake_language(cfg) == "de"


def test_resolve_wake_language_ui_change_never_moves_a_pinned_wake_language():
    """Switching the app display language must not move a pinned wake word."""
    for ui_lang in ("en", "de", "es"):
        cfg = SimpleNamespace(
            trigger=_pin("de"),
            stt=SimpleNamespace(language="auto"),
            ui=SimpleNamespace(language=ui_lang),
        )
        assert wmf.resolve_wake_language(cfg) == "de"


def test_resolve_wake_language_auto_pin_keeps_legacy_cascade():
    """language='auto' (the default) -> the legacy stt -> ui cascade stands,
    so untouched installs behave exactly as before."""
    cfg = SimpleNamespace(
        trigger=_pin("auto"),
        stt=SimpleNamespace(language="auto"),
        ui=SimpleNamespace(language="es"),
    )
    assert wmf.resolve_wake_language(cfg) == "es"


def test_resolve_wake_language_unsupported_pin_falls_through():
    """A hand-edited unsupported pin ('fr') must not brick — fall through."""
    cfg = SimpleNamespace(
        trigger=_pin("fr"),
        stt=SimpleNamespace(language="es"),
        ui=SimpleNamespace(language="de"),
    )
    assert wmf.resolve_wake_language(cfg) == "es"


def test_resolve_wake_language_uses_concrete_stt_language():
    """An explicit, supported stt.language always wins -- the user forced it."""
    cfg = SimpleNamespace(
        stt=SimpleNamespace(language="es"), ui=SimpleNamespace(language="de")
    )
    assert wmf.resolve_wake_language(cfg) == "es"


def test_resolve_wake_language_falls_back_to_ui_language_when_stt_is_auto():
    """stt.language left at 'auto' -> use the onboarding-chosen ui.language."""
    cfg = SimpleNamespace(
        stt=SimpleNamespace(language="auto"), ui=SimpleNamespace(language="de")
    )
    assert wmf.resolve_wake_language(cfg) == "de"


def test_resolve_wake_language_falls_back_to_default_locale_when_both_auto():
    cfg = SimpleNamespace(
        stt=SimpleNamespace(language="auto"), ui=SimpleNamespace(language="auto")
    )
    assert wmf.resolve_wake_language(cfg) == "en"


def test_resolve_wake_language_falls_back_to_default_locale_when_absent():
    """No stt/ui sections at all (e.g. a stripped-down cfg) -> DEFAULT_LOCALE."""
    cfg = SimpleNamespace()
    assert wmf.resolve_wake_language(cfg) == "en"


def test_resolve_wake_language_unsupported_concrete_stt_falls_through_to_ui():
    """stt.language='fr' (unsupported concrete code) -> fall through to ui.language."""
    cfg = SimpleNamespace(
        stt=SimpleNamespace(language="fr"), ui=SimpleNamespace(language="de")
    )
    assert wmf.resolve_wake_language(cfg) == "de"


def test_resolve_wake_language_never_raises_on_non_string_language():
    """stt.language=123 (non-string) -> must not raise, fall through to ui.language."""
    cfg = SimpleNamespace(
        stt=SimpleNamespace(language=123), ui=SimpleNamespace(language="es")
    )
    assert wmf.resolve_wake_language(cfg) == "es"


def test_resolve_wake_language_never_raises_on_bare_object():
    class Bare:
        pass

    assert wmf.resolve_wake_language(Bare()) == "en"
    assert wmf.resolve_wake_language(None) == "en"
    assert wmf.resolve_wake_language(object()) == "en"
