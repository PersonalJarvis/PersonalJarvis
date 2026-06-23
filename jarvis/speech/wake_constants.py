"""Single source of truth for the configurable wake-word vocabulary.

Pure stdlib — NO ``jarvis.*`` imports, NO heavy third-party imports — so the
matcher module (``wake_phrase``), the Pydantic config (``core/config.py``), and
the tests can all import this without dragging in ``faster_whisper``,
``sounddevice``, or the openWakeWord runtime.

Why this module exists:
- ``WAKE_ENGINES`` is the SoT for the five-layer enum (Python ↔ TOML ↔ Pydantic
  ↔ TS ↔ UI). The TS mirror (``frontend/src/constants/wakeEngines.ts``) is held
  in lockstep by ``tests/unit/speech/test_wake_engine_parity.py``.
- ``JARVIS_WAKE_PATTERN`` is the strict legacy "hey/hi/hallo + jarv" regex,
  moved here so ``rolling_whisper_wake.DEFAULT_PATTERN`` and the prefix verifier
  re-export ONE definition instead of duplicating the literal (BUG-008 drift).
- ``KNOWN_OWW_MODELS`` maps a normalised phrase onto an openWakeWord pretrained
  model name, and ``resolve_oww_model_path`` finds that model's ONNX on disk
  (bundled in-repo for hey_rhasspy/hey_jarvis, otherwise from the installed package).
  The shipped out-of-box bundled fallback is ``hey_rhasspy`` (neutral, CPU-only
  offline model). ``hey_jarvis`` is kept so a user who types "Jarvis" still gets
  the offline model — just not as a silent default.
"""
from __future__ import annotations

import importlib.util
import re
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Five-layer enum — keep in lockstep with frontend/src/constants/wakeEngines.ts
# ---------------------------------------------------------------------------
#: ``auto``        — resolve the best engine for the phrase automatically.
#: ``openwakeword``— neural pretrained model (CPU, instant, fixed vocabulary).
#: ``stt_match``   — local-Whisper transcript match (any phrase, needs whisper).
#: ``custom_onnx`` — a user-supplied/trained .onnx model (CPU, any phrase).
WAKE_ENGINES: tuple[str, ...] = ("auto", "openwakeword", "stt_match", "custom_onnx")

DEFAULT_WAKE_PHRASE = ""  # empty = neutral pre-onboarding default; user must opt in

# Strict legacy wake pattern (was rolling_whisper_wake.DEFAULT_PATTERN). Matches
# "hey/hi/hallo" + a jarv-family stem; a bare "Jarvis" or a Whisper
# hallucination must NOT match (BUG-009). This is the single definition; the
# rolling-whisper backstop and the prefix verifier both re-export it.
JARVIS_WAKE_PATTERN = re.compile(
    r"\bh(ey|i|allo)\W+(jarv\w{1,5}|charv\w{1,5}|tscharv\w{1,5}|dschärw\w{1,5})\b",
    re.IGNORECASE,
)

# Common wake prefixes stripped when deriving the phrase *core* for model
# lookup and canonical keyword names. The matcher may still keep an explicit
# prefix when the configured phrase includes one.
WAKE_PREFIXES: frozenset[str] = frozenset(
    {"hey", "hi", "ok", "okay", "hello", "hallo", "yo", "hej"}
)

# Normalised core phrase -> openWakeWord pretrained model name. We enumerate
# only our own brand ("jarvis") and the bundled open-source default
# ("rhasspy"); ANY other phrase is resolved dynamically against whatever
# pretrained models the installed ``openwakeword`` package actually exposes
# (see ``match_known_oww_model``), so the shipped product does not bake a list
# of third-party wake-word brands into its source.
KNOWN_OWW_MODELS: dict[str, str] = {
    "jarvis": "hey_jarvis",
    "rhasspy": "hey_rhasspy",
}

# openWakeWord also ships non-wake models we must never route a phrase to.
_NON_WAKE_OWW_MODELS: frozenset[str] = frozenset({"timer", "weather"})

# Quick-pick phrases the Settings UI could offer as one-click suggestions.
# Currently empty: the shipped product does not pre-advertise any specific wake
# name. Users type a phrase of their choice; the bundled offline model is
# "Hey Rhasspy", and any phrase with a matching pretrained model works offline.
INSTANT_WAKE_PHRASES: tuple[str, ...] = ()

_NORMALISE_RE = re.compile(r"[^0-9a-zäöüß]+")
_MATCH_NORMALISE_RE = re.compile(r"[^0-9a-z]+")


def _strip_diacritics(text: str) -> str:
    """Return an ASCII-ish form for STT matching, not display."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.replace("ß", "ss")


def normalize_phrase(phrase: str) -> list[str]:
    """Lower-case, strip punctuation, split into word tokens.

    Keeps German umlauts/ß. Empty/whitespace input returns ``[]``.
    """
    cleaned = _NORMALISE_RE.sub(" ", (phrase or "").lower()).strip()
    return cleaned.split() if cleaned else []


def normalize_phrase_for_match(phrase: str) -> list[str]:
    """Tokenise for fuzzy wake matching with accents folded away."""
    folded = _strip_diacritics(phrase).lower()
    cleaned = _MATCH_NORMALISE_RE.sub(" ", folded).strip()
    return cleaned.split() if cleaned else []


def phrase_core(phrase: str) -> list[str]:
    """Return the phrase tokens with leading wake-prefixes removed.

    ``"Hey Athena"`` -> ``["athena"]``; ``"Computer"`` -> ``["computer"]``.
    If every token is a prefix (e.g. ``"hey"``) the tokens are kept as-is so we
    never return an empty core for a non-empty phrase.
    """
    tokens = normalize_phrase(phrase)
    core = list(tokens)
    while core and core[0] in WAKE_PREFIXES:
        core.pop(0)
    return core or tokens


def phrase_core_for_match(phrase: str) -> list[str]:
    """Return wake-prefix-stripped tokens for accent-insensitive matching."""
    tokens = normalize_phrase_for_match(phrase)
    core = list(tokens)
    while core and core[0] in WAKE_PREFIXES:
        core.pop(0)
    return core or tokens


def match_known_oww_model(phrase: str) -> str | None:
    """Map a phrase onto a pretrained openWakeWord model name, or ``None``.

    Checks our own enumerated names first ("jarvis"/"rhasspy"), then probes the
    installed openWakeWord package at runtime for a matching pretrained model —
    so a user who types any word that happens to ship a model still gets it,
    without the source enumerating third-party wake-word trademarks. Non-wake
    models (timer/weather) are never routed to.
    """
    core = phrase_core(phrase)
    if not core:
        return None
    key = " ".join(core)
    if key in KNOWN_OWW_MODELS:
        return KNOWN_OWW_MODELS[key]
    slug = key.replace(" ", "_")
    if slug in _NON_WAKE_OWW_MODELS:
        return None
    for candidate in (slug, f"hey_{slug}"):
        if resolve_oww_model_path(candidate) is not None:
            return candidate
    return None


# ---------------------------------------------------------------------------
# OWW model file resolution
# ---------------------------------------------------------------------------

def _bundled_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "wakeword"


def _package_models_dir() -> Path | None:
    """Locate the installed openWakeWord ``resources/models`` dir WITHOUT
    importing the (numpy-heavy) package — uses the module spec only."""
    spec = importlib.util.find_spec("openwakeword")
    if spec is None or not spec.submodule_search_locations:
        return None
    base = Path(next(iter(spec.submodule_search_locations)))
    models = base / "resources" / "models"
    return models if models.is_dir() else None


def resolve_oww_model_path(model_name: str) -> str | None:
    """Absolute path to the ``<model_name>_v0.1.onnx`` wake model, or ``None``.

    Prefers the in-repo bundle (offline first-boot for hey_jarvis); otherwise
    falls back to the file shipped inside the installed openWakeWord package.
    """
    filename = f"{model_name}_v0.1.onnx"
    bundled = _bundled_dir() / filename
    if bundled.is_file():
        return str(bundled)
    pkg = _package_models_dir()
    if pkg is not None:
        candidate = pkg / filename
        if candidate.is_file():
            return str(candidate)
    return None


__all__ = [
    "WAKE_ENGINES",
    "DEFAULT_WAKE_PHRASE",
    "JARVIS_WAKE_PATTERN",
    "WAKE_PREFIXES",
    "KNOWN_OWW_MODELS",
    "INSTANT_WAKE_PHRASES",
    "normalize_phrase",
    "normalize_phrase_for_match",
    "phrase_core",
    "phrase_core_for_match",
    "match_known_oww_model",
    "resolve_oww_model_path",
]
