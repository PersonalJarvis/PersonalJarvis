"""Download + extract the per-language Vosk KWS model at setup / first run.

Fills the never-built fetch step the vosk_kws engine assumes: the model lands in
``<data>/wake_models/vosk/<lang>/`` exactly where ``resolve_vosk_model_path``
looks. Every failure is NON-FATAL (offline mirror, corrupt zip, hash mismatch):
the caller keeps going and the wake word degrades honestly. Apache-2.0 models
from alphacephei.com, SHA-256 pinned once known (fail-closed on mismatch; an
empty hash is treated as "not yet pinned" and accepted unverified).

Pure-ish: stdlib only at import time; ``httpx`` (the repo's HTTP standard) is
imported lazily inside ``_http_get`` so importing this module stays light.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("jarvis.wake.model_fetch")

_BASE_URL = "https://alphacephei.com/vosk/models/"
# DEFAULT_LOCALE fallback (jarvis/core/turn_language.py) kept as a literal so this
# module needs no heavy import; supported app languages are en/de/es.
_DEFAULT_LANG = "en"


@dataclass(frozen=True)
class VoskModelSpec:
    zip_name: str
    sha256: str  # empty = not yet pinned (accepted unverified); fill in as a follow-up


# SHA-256 values pinned 2026-07-08 by downloading each zip from _BASE_URL and
# hashing it (see the module docstring for the empty-hash fallback semantics
# this replaces). Re-pin here if alphacephei.com ever republishes a zip_name
# with different bytes.
VOSK_MODELS: dict[str, VoskModelSpec] = {
    "en": VoskModelSpec(
        "vosk-model-small-en-us-0.15.zip",
        "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
    ),
    "de": VoskModelSpec(
        "vosk-model-small-de-0.15.zip",
        "b7e53c90b1f0a38456f4cd62b366ecd58803cd97cd42b06438e2c131713d5e43",
    ),
    "es": VoskModelSpec(
        "vosk-model-small-es-0.42.zip",
        "09b239888f633ef2f0b4e09736e3d9936acfd810bc65d53fad45261762c6511f",
    ),
}


def vosk_lang_for(language: str | None) -> str:
    """Normalize a config language ('de-DE', 'auto', None) to a supported key."""
    lang = (language or "").strip().lower().split("-")[0]
    return lang if lang in VOSK_MODELS else _DEFAULT_LANG


def _models_root(data_dir: str | None) -> Path:
    """Same layout ``wake_constants._vosk_models_root`` resolves — do not diverge."""
    base = data_dir or os.environ.get("JARVIS__MEMORY__DATA_DIR") or "data"
    return Path(base) / "wake_models" / "vosk"


def _lang_dir_has_model(lang_dir: Path) -> bool:
    """Mirrors resolve_vosk_model_path's own "is this a model dir" check."""
    if not lang_dir.is_dir():
        return False
    if (lang_dir / "am").is_dir() or (lang_dir / "conf" / "model.conf").is_file():
        return True
    return any(
        (sub / "am").is_dir() or (sub / "conf" / "model.conf").is_file()
        for sub in lang_dir.iterdir()
        if sub.is_dir()
    )


def vosk_model_present(language: str | None, *, data_dir: str | None = None) -> bool:
    lang = vosk_lang_for(language)
    return _lang_dir_has_model(_models_root(data_dir) / lang)


def _http_get(url: str) -> bytes:
    import httpx  # lazy: keep base import light

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def ensure_vosk_model(
    language: str | None,
    *,
    data_dir: str | None = None,
    http_get: Callable[[str], bytes] | None = None,
    echo: Callable[[str], None] = print,
) -> Path | None:
    """Idempotently ensure the Vosk model for ``language`` is on disk.

    Returns the language dir on success (or when already present), else ``None``.
    NEVER raises for network / IO / hash errors — the wake word degrades honestly.
    """
    lang = vosk_lang_for(language)
    lang_dir = _models_root(data_dir) / lang
    if _lang_dir_has_model(lang_dir):
        return lang_dir

    spec = VOSK_MODELS[lang]
    url = _BASE_URL + spec.zip_name
    getter = http_get or _http_get
    try:
        echo(f"downloading wake model '{spec.zip_name}' (one-time, ~40 MB)")
        blob = getter(url)
        digest = hashlib.sha256(blob).hexdigest()
        if spec.sha256 and digest != spec.sha256:
            echo(
                f"wake model '{spec.zip_name}' hash mismatch "
                f"(expected {spec.sha256[:12]}..., got {digest[:12]}...); skipping"
            )
            log.warning("Vosk model hash mismatch for %s -- not installed.", lang)
            return None

        # Extract to a sibling temp dir, then atomically move into place.
        lang_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(lang_dir.parent)) as td:
            tdir = Path(td)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                zf.extractall(tdir)
            if not _lang_dir_has_model(tdir):
                echo(f"wake model '{spec.zip_name}' extracted but not a valid model; skipping")
                return None
            if lang_dir.exists():
                shutil.rmtree(lang_dir, ignore_errors=True)
            shutil.move(str(tdir), str(lang_dir))
        echo(f"wake model for '{lang}': ready")
        return lang_dir
    except Exception as exc:  # noqa: BLE001 - any failure here is honest, never fatal
        echo(
            f"wake model for '{lang}' could not be downloaded ({exc}); "
            "the wake word will use the fallback path until it succeeds"
        )
        log.warning("Vosk model fetch failed for %s: %s", lang, exc)
        return None


__all__ = [
    "VoskModelSpec",
    "VOSK_MODELS",
    "vosk_lang_for",
    "vosk_model_present",
    "ensure_vosk_model",
]
