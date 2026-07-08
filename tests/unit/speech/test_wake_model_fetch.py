"""Vosk model fetch: idempotent, layout matches resolve_vosk_model_path,
hash-checked, never fatal offline."""
import hashlib
import io
import zipfile
from pathlib import Path

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

    def fake_get(url: str) -> bytes:
        calls["n"] += 1
        return _fake_model_zip()

    monkeypatch.setitem(
        wmf.VOSK_MODELS, "de",
        wmf.VoskModelSpec("vosk-model-small-de-0.15.zip",
                          hashlib.sha256(_fake_model_zip()).hexdigest()),
    )
    wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    assert calls["n"] == 1, "second call must be a no-op (already present)"


def test_hash_mismatch_rejects_and_is_nonfatal(tmp_path):
    def fake_get(url: str) -> bytes:
        return b"corrupt-not-a-zip"

    out = wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=fake_get)
    assert out is None  # rejected, never installed, never raised


def test_offline_failure_is_nonfatal(tmp_path):
    def boom(url: str) -> bytes:
        raise OSError("network down")

    out = wmf.ensure_vosk_model("de", data_dir=str(tmp_path), http_get=boom)
    assert out is None
